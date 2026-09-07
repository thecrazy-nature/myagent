"""Streamlit UI for the focusing and autonomous array-design Agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from app.agent_runner import AgentRunnerError, ProxyCheck, StatusUpdate, check_proxy, run_agent_task
from app.view_models import load_recent_designs, load_recent_tasks, render_structured_task

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FOCUS_TASK = (
    "请在 x=0 mm、y=0 mm、z=100 mm 处进行近场聚焦。\n"
    "要求最终焦点定位误差不超过 5 mm。\n"
    "如果第一次没有达到要求，可以进行最多两次修正。"
)
DEFAULT_DESIGN_TASK = (
    "请针对 [0,0,100] mm 的近场焦点自动设计阵列排布。\n"
    "保持阵元数量、总输入功率、频率、单元模型和最大口径与 baseline 一致，"
    "最小阵元间距不得低于 baseline 的实际间距。\n"
    "优先缩小 X 方向横向焦斑，其次缩短 Z 方向焦深，并提高目标附近的 XZ 能量集中度。\n"
    "焦点误差不得超过 5 mm；使用 spherical-cap family，最多评估 2 个候选。\n"
    "先评估 baseline，再进行一次小规模搜索，根据真实 MATLAB 结果选择并保存最终设计。"
)


def main() -> None:
    st.set_page_config(page_title="Electromagnetic Research Agent", page_icon="📡", layout="wide")
    st.title("Electromagnetic Research Agent")
    st.caption("Natural language → Hermes Agent → deterministic workflow tools → real MATLAB")
    proxy = check_proxy()
    if proxy.available:
        st.success(proxy.message, icon="✅")
    else:
        st.error("Hermes network proxy is unavailable. Start Clash and run `.\\proxy-on.ps1`.\n\n" + proxy.message, icon="🚫")
    focus_tab, design_tab = st.tabs(["Focusing Agent", "Array Geometry Designer"])
    with focus_tab:
        render_focus_page(proxy)
    with design_tab:
        render_design_page(proxy)


def render_focus_page(proxy: ProxyCheck) -> None:
    st.header("Natural Language Focus Task")
    mode = st.radio("Input Mode", ["Natural Language Mode", "Structured Mode"], horizontal=True, key="focus_mode")
    if mode == "Natural Language Mode":
        task_text = st.text_area("Natural Language Task", value=DEFAULT_FOCUS_TASK, height=150, key="focus_text")
        submission_mode = "natural_language"
    else:
        with st.expander("Advanced / Manual Parameters", expanded=True):
            first, second, third = st.columns(3)
            x = first.number_input("Target X / mm", value=0.0, key="focus_x")
            y = second.number_input("Target Y / mm", value=0.0, key="focus_y")
            z = third.number_input("Target Z / mm", value=100.0, key="focus_z")
            tolerance = st.number_input("Tolerance / mm", min_value=0.001, value=5.0, key="focus_tolerance")
            refinements = st.number_input("Max Refinements", min_value=0, value=2, step=1, key="focus_refinements")
        task_text = render_structured_task([x, y, z], tolerance, int(refinements))
        submission_mode = "structured"
    st.subheader("Agent Task")
    st.code(task_text, language=None)
    if st.button("Run Agent", type="primary", disabled=not proxy.available or not task_text.strip(), key="run_focus", width="stretch"):
        result = _run(task_text, submission_mode, "focus")
        st.session_state["latest_focus_result"] = result
        st.session_state["latest_agent_result"] = result
    result = st.session_state.get("latest_focus_result") or st.session_state.get("latest_agent_result")
    if result:
        render_focus_result(result)
    render_recent_focus_tasks()


def render_design_page(proxy: ProxyCheck) -> None:
    st.header("Autonomous Array Geometry Design")
    st.caption("Array geometry optimization—not individual antenna-element electromagnetic redesign.")
    task_text = st.text_area(
        "Natural Language Array Design Request", value=DEFAULT_DESIGN_TASK, height=220, key="design_text",
        help="This exact request goes to Hermes. Streamlit does not choose geometries or scores.",
    )
    st.subheader("Agent Task")
    st.code(task_text, language=None)
    if st.button("Run Array Design Agent", type="primary", disabled=not proxy.available or not task_text.strip(), key="run_design", width="stretch"):
        st.session_state["latest_design_result"] = _run(task_text, "natural_language", "array_design")
    result = st.session_state.get("latest_design_result")
    if result:
        render_design_result(result)
    render_recent_array_designs()


def _run(task_text: str, submission_mode: str, task_kind: str) -> dict[str, Any]:
    status_box = st.status("Initializing Hermes Agent...", expanded=True)
    progress_log = status_box.empty()
    messages: list[str] = []
    def update(status: StatusUpdate) -> None:
        messages.append(status.message)
        progress_log.markdown("\n\n".join(f"- {message}" for message in messages))
        status_box.update(label=status.message, state="running", expanded=True)
    try:
        result = run_agent_task(task_text, submission_mode=submission_mode, on_status=update, task_kind=task_kind)
        status_box.update(label=result["status"], state="complete" if result["status"] in {"SUCCESS", "SAVED"} else "error", expanded=False)
        return result
    except AgentRunnerError as exception:
        status_box.update(label=exception.category, state="error", expanded=True)
        return {"status": "FAILED", "error_category": exception.category, "error_message": str(exception), "trajectory": [], "agent_final_response": ""}


def render_focus_result(result: dict[str, Any]) -> None:
    st.divider()
    if result.get("error_category"):
        st.error(f"{result['error_category']}: {result.get('error_message', 'Unknown failure')}")
    else:
        st.success("The requested scientific constraint was satisfied.")
    for warning in result.get("infrastructure_warnings", []):
        st.warning(f"Recovered Infrastructure Warning: {warning}")
    columns = st.columns(6)
    columns[0].metric("Status", result.get("status", "UNKNOWN"))
    columns[1].metric("Desired Target", _vector(result.get("desired_target_mm")))
    columns[2].metric("Actual Peak", _vector(result.get("actual_peak_mm")))
    columns[3].metric("Final Error", _millimetres(result.get("final_error_mm")))
    columns[4].metric("MATLAB Calls", result.get("matlab_calls", 0))
    columns[5].metric("Replanning", result.get("replanning_count", 0))
    render_trajectory(result.get("trajectory", []))
    st.subheader("Iteration History")
    st.dataframe(result.get("iterations", []), width="stretch", hide_index=True)
    st.subheader("Agent Summary")
    st.markdown(result.get("agent_final_response") or "Hermes did not produce a final response.")


def render_design_result(result: dict[str, Any]) -> None:
    st.divider()
    if result.get("error_category"):
        st.error(f"{result['error_category']}: {result.get('error_message', 'Unknown failure')}")
    else:
        st.success("The final geometry and its complete evidence were persisted.")
    state = result.get("design_state") or {}
    selected = result.get("selected_design") or {}
    columns = st.columns(5)
    columns[0].metric("Status", result.get("status", "UNKNOWN"))
    columns[1].metric("Target", _vector(state.get("focus_target_mm")))
    columns[2].metric("Selected Family", (selected.get("geometry") or {}).get("family", "N/A"))
    columns[3].metric("Search Rounds", result.get("search_rounds", 0))
    columns[4].metric("MATLAB Processes", result.get("matlab_processes", 0))
    baseline, designed = result.get("baseline_metrics"), result.get("designed_metrics")
    if isinstance(baseline, dict) and isinstance(designed, dict):
        st.subheader("Baseline vs Designed")
        st.dataframe(_comparison_rows(baseline, designed), width="stretch", hide_index=True)
        baseline_geometry = ((result.get("baseline") or {}).get("geometry") or {})
        designed_geometry = selected.get("geometry") or {}
        if baseline_geometry.get("element_positions_mm") and designed_geometry.get("element_positions_mm"):
            render_geometry_plots(baseline_geometry, designed_geometry)
        render_profile_plots(baseline, designed)
    candidates = state.get("evaluated_geometries")
    if isinstance(candidates, dict) and candidates:
        st.subheader("Evaluated Candidate Leaderboard")
        st.dataframe(_candidate_rows(candidates), width="stretch", hide_index=True)
    render_trajectory(result.get("trajectory", []))
    st.subheader("Agent Scientific Summary")
    st.markdown(result.get("agent_final_response") or "Hermes did not produce a final response.")


def render_geometry_plots(baseline: dict[str, Any], designed: dict[str, Any]) -> None:
    st.subheader("Real Element Coordinates")
    first, second = baseline["element_positions_mm"], designed["element_positions_mm"]
    xy, xz, projected = st.columns(3)
    frames = []
    for positions, label in ((first, "Baseline"), (second, "Designed")):
        frames.extend({"x_mm": p[0], "y_mm": p[1], "z_mm": p[2], "geometry": label} for p in positions)
    frame = pd.DataFrame(frames)
    xy.caption("XY projection")
    xy.scatter_chart(frame, x="x_mm", y="y_mm", color="geometry", size=12)
    xz.caption("XZ projection")
    xz.scatter_chart(frame, x="x_mm", y="z_mm", color="geometry", size=12)
    three = pd.DataFrame({
        "projected_x_mm": [p[0] - 0.5 * p[1] for p in second],
        "projected_z_mm": [p[2] + 0.25 * (p[0] + p[1]) for p in second],
    })
    projected.caption("Designed curved array · orthographic 3D projection")
    projected.scatter_chart(three, x="projected_x_mm", y="projected_z_mm", size=12)


def render_profile_plots(baseline: dict[str, Any], designed: dict[str, Any]) -> None:
    if not all(isinstance(item.get(name), dict) for item in (baseline, designed) for name in ("lateral_profile", "axial_profile")):
        return
    st.subheader("Real MATLAB Focus Profiles")
    left, right = st.columns(2)
    lateral_frame = pd.DataFrame({
        "x_mm": baseline["lateral_profile"]["position_mm"],
        "Baseline": baseline["lateral_profile"]["normalized_power"],
        "Designed": designed["lateral_profile"]["normalized_power"],
        "Half power": 0.5,
    }).set_index("x_mm")
    axial_frame = pd.DataFrame({
        "z_mm": baseline["axial_profile"]["position_mm"],
        "Baseline": baseline["axial_profile"]["normalized_power"],
        "Designed": designed["axial_profile"]["normalized_power"],
        "Half power": 0.5,
    }).set_index("z_mm")
    left.caption("Lateral profile (X in the XZ plane)"); left.line_chart(lateral_frame)
    right.caption("Axial profile / depth of focus (Z)"); right.line_chart(axial_frame)


def render_trajectory(trajectory: list[dict[str, Any]]) -> None:
    st.subheader("Agent Tool Trajectory")
    if not trajectory:
        st.info("No domain Tool Calls were recorded.")
    for index, call in enumerate(trajectory, 1):
        with st.expander(f"{index}. {call.get('tool_name')} · {_tool_outcome(call.get('observation'))}"):
            left, right = st.columns(2); left.json(call.get("arguments", {})); right.json(call.get("observation") or {})


def render_recent_focus_tasks() -> None:
    st.divider(); st.subheader("Recent Focus Tasks")
    recent = load_recent_tasks(PROJECT_ROOT)
    st.dataframe(recent, width="stretch", hide_index=True) if recent else st.info("No persisted focus tasks were found.")


def render_recent_array_designs() -> None:
    st.divider(); st.subheader("Recent Array Designs")
    recent = load_recent_designs(PROJECT_ROOT)
    st.dataframe(recent, width="stretch", hide_index=True) if recent else st.info("No persisted array designs were found.")


def _comparison_rows(baseline: dict[str, Any], designed: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [("Focus Error / mm", "focus_error_mm", False), ("Lateral FWHM X / mm", "fwhm_x_mm", False),
             ("Depth of Focus Z / mm", "dof_z_mm", False), ("Energy Concentration (XZ)", "energy_concentration_ratio", True),
             ("Peak Power / model units", "peak_power", True)]
    rows = []
    for label, key, higher in specs:
        base, current = float(baseline[key]), float(designed[key])
        change = 0.0 if base == 0 else 100 * ((current - base) / base if higher else (base - current) / base)
        rows.append({"Metric": label, "Baseline": base, "Designed": current, "Improvement / %": change})
    return rows


def _candidate_rows(candidates: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for candidate in candidates.values():
        geometry, metrics = candidate.get("geometry") or {}, candidate.get("metrics") or {}
        rows.append({
            "Geometry ID": geometry.get("geometry_id"),
            "Family": geometry.get("family"),
            "Parameters": geometry.get("parameters"),
            "Focus Error / mm": metrics.get("focus_error_mm"),
            "X FWHM / mm": metrics.get("fwhm_x_mm"),
            "Z DOF / mm": metrics.get("dof_z_mm"),
            "XZ Energy Ratio": metrics.get("energy_concentration_ratio"),
            "Objective Score": candidate.get("objective_score"),
            "Hard Constraint": candidate.get("hard_constraint_satisfied"),
        })
    return sorted(rows, key=lambda row: (row["Objective Score"], str(row["Geometry ID"])))


def _vector(value: Any) -> str:
    return "N/A" if not isinstance(value, list) or len(value) != 3 else "[" + ", ".join(f"{float(item):g}" for item in value) + "] mm"


def _millimetres(value: Any) -> str:
    return "N/A" if not isinstance(value, (int, float)) else f"{float(value):.3f} mm"


def _tool_outcome(observation: Any) -> str:
    if not isinstance(observation, dict): return "PENDING"
    if observation.get("error") is True: return "ERROR"
    if observation.get("success") is False: return "FAILED"
    return "COMPLETED"


if __name__ == "__main__":
    main()
