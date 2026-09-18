"""Streamlit UI for the focusing and autonomous array-design Agents."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
import uuid

import pandas as pd
import streamlit as st

from app.agent_runner import (
    AgentRunnerError,
    StatusUpdate,
    read_hermes_default_model,
    run_agent_task,
)
from app.focus_artifacts import (
    artifact_paths,
    build_method_card,
    export_bundle,
    field_frame,
    field_png,
    load_field_data,
    load_plane_png,
    plane_png,
    profile_frames,
    report_markdown,
)
from app.job_store import (
    ACTIVE_STATUSES,
    JobStoreError,
    create_job,
    create_recovery_job,
    create_repeat_job,
    list_jobs,
    load_job,
    mark_notification_seen,
    request_action,
)
from app.run_governance import build_submission_snapshot
from app.scenario import (
    POLARIZATION_LABELS,
    assign_user_harmonics,
    build_scenario_prompt,
    convert_frequency,
    target_bounds_mm,
    target_validation_errors,
)
from app.view_models import (
    load_array_design_record,
    load_focus_task_record,
    load_metasurface_design_record,
    load_recent_designs,
    load_recent_metasurface_designs,
    load_recent_tasks,
)
from array_design.geometry import generate_geometry

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
DEFAULT_METASURFACE_TASK = (
    "请按快捷配置设计一个固定平面的 0/1 透射可编程超表面。\n"
    "先用真实 MATLAB 生成几何光学 0/1 聚焦基线，再优化二进制控制码并记录评估指标；\n"
    "保存逐单元控制码，最后通过 MATLAB 生成 CST 控制码布局模型。"
)
DEFAULT_AGENT_TASK = DEFAULT_FOCUS_TASK


@st.cache_data(ttl=30, show_spinner=False)
def _current_governance_snapshot() -> dict[str, Any]:
    return build_submission_snapshot(PROJECT_ROOT)


@st.cache_data(ttl=30, show_spinner=False)
def _hermes_default_model() -> dict[str, str | None]:
    return read_hermes_default_model()


def render_current_governance() -> None:
    snapshot = _current_governance_snapshot()
    st.warning(
        "数据边界：提交消息后，自然语言、实验配置和紧凑 Tool 结果会发送到外部模型服务；"
        "完整二维场矩阵、MAT 文件、MATLAB 日志和认证凭据保留在本机。",
        icon="🔐",
    )
    with st.expander("运行治理与版本（提交时会固化到任务记录）", expanded=True):
        versions = snapshot.get("versions", {})
        runtime = snapshot.get("runtime", {})
        columns = st.columns(4)
        columns[0].metric("应用版本", _display(versions.get("application_version")))
        columns[1].metric("Prompt 版本", _display(versions.get("prompt_contract_version")))
        columns[2].metric("Tool schema 版本", _display(versions.get("tool_schema_version")))
        columns[3].metric("Hermes 版本", _display(runtime.get("hermes_version")))
        st.caption(
            "实际模型 ID、token、费用状态和 MATLAB 版本只能在真实任务完成后从运行证据读取；"
            "模型提供方未公开精确 revision 时会明确显示为“未提供”。"
        )
        st.caption(
            f"Prompt SHA-256：{_short_hash((snapshot.get('source_hashes') or {}).get('prompt_sha256'))} ｜ "
            f"Tool schema SHA-256：{_short_hash((snapshot.get('source_hashes') or {}).get('tool_schema_sha256'))}"
        )


def render_run_governance(
    governance: Any,
    comparison: Any = None,
    *,
    heading: str = "运行治理",
) -> None:
    if not isinstance(governance, dict):
        return
    versions = governance.get("versions") or {}
    runtime = governance.get("runtime") or {}
    llm = governance.get("llm") or {}
    matlab = governance.get("matlab") or {}
    randomness = governance.get("randomness") or {}
    external = governance.get("external_data") or {}
    st.subheader(heading)
    first = st.columns(4)
    first[0].metric("实际模型 ID", _display(llm.get("model")))
    first[1].metric("模型 revision", _display(llm.get("model_revision")))
    first[2].metric("Hermes 版本", _display(runtime.get("hermes_version")))
    first[3].metric("Prompt / Tool", (
        f"{_display(versions.get('prompt_contract_version'))} / "
        f"{_display(versions.get('tool_schema_version'))}"
    ))
    second = st.columns(5)
    second[0].metric("MATLAB", _matlab_label(matlab))
    second[1].metric("输入 token", _integer_display(llm.get("input_tokens")))
    second[2].metric("输出 token", _integer_display(llm.get("output_tokens")))
    second[3].metric("缓存读取 token", _integer_display(llm.get("cache_read_tokens")))
    second[4].metric("Reasoning token", _integer_display(llm.get("reasoning_tokens")))
    st.caption(
        f"API 调用：{_integer_display(llm.get('api_call_count'))} ｜ "
        f"缓存写入 token：{_integer_display(llm.get('cache_write_tokens'))} ｜ "
        f"供应方：{_display(llm.get('billing_provider'))} ｜ "
        f"接口：{_display(llm.get('billing_base_url'))} ｜ "
        f"费用：{_cost_label(llm)}"
    )
    st.caption(
        f"随机种子：{_display(matlab.get('random_seed', randomness.get('focus_seed')))} ｜ "
        f"MATLAB RNG：{_display(matlab.get('rng_algorithm', randomness.get('matlab_rng_algorithm')))} ｜ "
        f"Prompt SHA-256：{_short_hash((governance.get('source_hashes') or {}).get('prompt_sha256'))} ｜ "
        f"Tool SHA-256：{_short_hash((governance.get('source_hashes') or {}).get('tool_schema_sha256'))}"
    )
    st.info(
        f"外部数据：{external.get('notice', '未记录')} "
        f"完整二维场是否发送：{'是' if external.get('full_field_sent_to_model') else '否'}。"
    )
    with st.expander("查看完整治理证据（不含消息正文和认证凭据）", expanded=False):
        st.json(_without_git_metadata(governance))
    render_reproducibility_comparison(comparison)


def render_reproducibility_comparison(comparison: Any) -> None:
    if not isinstance(comparison, dict):
        return
    st.markdown("#### 相同任务重复运行一致性")
    if comparison.get("status") == "pending":
        st.info(comparison.get("message", "重复任务尚未完成。"))
        return
    rows = st.columns(4)
    rows[0].metric("输入与源码相同", _boolean_text(
        comparison.get("same_task_text") and comparison.get("same_source_snapshot")
    ))
    rows[1].metric("Agent 轨迹一致", _boolean_text(comparison.get("agent_trajectory_consistent")))
    rows[2].metric("MATLAB 数值一致", _boolean_text(comparison.get("matlab_numerically_consistent")))
    rows[3].metric("最终结果一致", _boolean_text(comparison.get("final_outcome_consistent")))
    delta = comparison.get("maximum_matlab_numeric_delta")
    st.caption(
        f"MATLAB 最大数值差：{delta:.3g}" if isinstance(delta, (int, float))
        else "MATLAB 最大数值差：不可计算"
    )
    st.caption(comparison.get("note", ""))


def main() -> None:
    st.set_page_config(page_title="电磁科研智能体", page_icon="📡", layout="wide")
    st.title("电磁科研智能体")
    st.caption("自然语言 → Hermes 智能体 → 确定性工作流工具 → 真实 MATLAB")
    agent_tab, jobs_tab, results_tab, settings_tab = st.tabs(
        ["智能体对话", "任务中心", "结果可视化", "系统设置"]
    )
    with agent_tab:
        render_agent_page()
    with jobs_tab:
        render_task_center()
    with results_tab:
        render_results_page()
    with settings_tab:
        render_settings_page()


def render_agent_page() -> None:
    """Multi-turn Hermes chat with an explicit scientific scenario builder."""
    title, action = st.columns([5, 1])
    title.header("电磁科研对话")
    if action.button("新建对话", key="new_conversation", width="stretch"):
        for key in (
            "chat_messages", "hermes_session_id", "latest_agent_result",
            "conversation_id", "chat_completed_jobs",
        ):
            st.session_state.pop(key, None)
        st.rerun()
    st.caption(
        "可以连续追问或修改要求；Hermes 会自主决定回答问题、运行聚焦、优化阵列几何，"
        "或设计固定平面可编程超表面的相位控制码。"
    )
    render_prompt_guide()
    model_selection = selected_model_overrides()
    scenario = render_scenario_controls()
    scenario_error = scenario.pop("_validation_error", None)

    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = [
            {
                "role": "assistant",
                "content": (
                    "你好，我可以回答模型问题，也可以根据上方实验配置调用真实 MATLAB 完成单用户或多用户近场聚焦。"
                ),
            }
        ]
    if "conversation_id" not in st.session_state:
        st.session_state["conversation_id"] = f"conversation_{uuid.uuid4().hex}"
    completed_jobs = set(st.session_state.get("chat_completed_jobs", []))
    conversation_jobs = sorted(
        (
            job for job in list_jobs(100)
            if job.get("conversation_id") == st.session_state["conversation_id"]
        ),
        key=lambda item: str(item.get("created_at", "")),
    )
    for job in conversation_jobs:
        result = job.get("result")
        if job.get("job_id") in completed_jobs or not isinstance(result, dict):
            continue
        response = result.get("agent_final_response") or job.get("message") or "任务已结束。"
        existing_message = next(
            (
                message for message in st.session_state["chat_messages"]
                if message.get("job_id") == job.get("job_id")
            ),
            None,
        )
        if existing_message is None:
            st.session_state["chat_messages"].append({
                "role": "assistant", "content": response, "job_id": job.get("job_id")
            })
        else:
            existing_message["content"] = response
        st.session_state["latest_agent_result"] = result
        if isinstance(job.get("hermes_session_id"), str):
            st.session_state["hermes_session_id"] = job["hermes_session_id"]
        completed_jobs.add(job["job_id"])
    st.session_state["chat_completed_jobs"] = sorted(completed_jobs)
    for message in st.session_state["chat_messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("job_id"):
                st.caption(f"关联任务：{message['job_id']}")

    prompt = st.chat_input(
        "描述任务或继续追问，例如：按当前配置运行一次聚焦，并比较各用户误差",
        key="agent_chat_input",
        disabled=bool(scenario_error),
    )
    if prompt:
        st.session_state["chat_messages"].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        task_text = build_scenario_prompt(prompt, **scenario)
        with st.chat_message("assistant"):
            job = create_job(
                task_text,
                conversation_id=st.session_state["conversation_id"],
                resume_session_id=st.session_state.get("hermes_session_id"),
                model_override=model_selection["model"],
                provider_override=model_selection["provider"],
            )
            response = (
                f"已加入后台队列：`{job['job_id']}`。你可以继续添加任务，"
                "并在“任务中心”查看进度、暂停、恢复或取消；关闭浏览器不会中断后台任务。"
            )
            st.markdown(response)
        st.session_state["chat_messages"].append(
            {"role": "assistant", "content": response, "job_id": job["job_id"]}
        )
    render_conversation_tasks(st.session_state["conversation_id"])
    result = st.session_state.get("latest_agent_result")
    if isinstance(result, dict) and not conversation_jobs:
        # Compatibility for an in-memory result created before durable task cards existed.
        if result.get("task_kind") == "focus":
            render_focus_result(result, show_governance=False)
        elif result.get("task_kind") == "array_design":
            render_design_result(result, show_governance=False)
        elif result.get("task_kind") == "metasurface_design":
            render_metasurface_result(
                result,
                key_prefix="latest_metasurface",
                show_governance=False,
            )
        elif result.get("task_kind") == "conversation":
            st.markdown(result.get("agent_final_response") or "Hermes 没有生成最终回复。")


def render_settings_page() -> None:
    st.header("系统设置")
    st.caption("模型和运行环境配置集中在这里，不占用科研对话区域。")
    render_model_controls()
    render_current_governance()


def render_model_controls() -> dict[str, str | None]:
    """Choose a real Hermes CLI model override for subsequently queued turns."""

    configured = _hermes_default_model()
    default_label = (
        f"{configured.get('provider') or '未知供应方'} / "
        f"{configured.get('model') or '未读取到模型 ID'}"
    )
    with st.expander("模型设置", expanded=True):
        choice = st.selectbox(
            "本轮 Hermes 模型",
            ["跟随 Hermes 默认配置", "DeepSeek V4 Flash", "自定义模型"],
            key="hermes_model_choice",
        )
        if choice == "跟随 Hermes 默认配置":
            st.caption(f"当前 Hermes 默认配置：{default_label}")
            return {"model": None, "provider": None}
        if choice == "DeepSeek V4 Flash":
            st.caption("本轮任务将通过 Hermes CLI 指定 deepseek / deepseek-v4-flash。")
            return {"model": "deepseek-v4-flash", "provider": "deepseek"}
        model_column, provider_column = st.columns(2)
        model = model_column.text_input(
            "模型 ID",
            value=configured.get("model") or "",
            key="hermes_custom_model",
            placeholder="例如 deepseek-chat",
        ).strip()
        provider = provider_column.text_input(
            "供应方 ID",
            value=configured.get("provider") or "",
            key="hermes_custom_provider",
            placeholder="例如 deepseek",
        ).strip()
        st.caption("选择只影响之后提交的任务；真实使用的模型会在任务完成后的运行治理中记录。")
        return {"model": model or None, "provider": provider or None}


def selected_model_overrides() -> dict[str, str | None]:
    choice = st.session_state.get("hermes_model_choice", "跟随 Hermes 默认配置")
    if choice == "DeepSeek V4 Flash":
        return {"model": "deepseek-v4-flash", "provider": "deepseek"}
    if choice == "自定义模型":
        model = str(st.session_state.get("hermes_custom_model") or "").strip()
        provider = str(st.session_state.get("hermes_custom_provider") or "").strip()
        return {"model": model or None, "provider": provider or None}
    return {"model": None, "provider": None}


def render_scenario_controls() -> dict[str, Any]:
    """Collect one mutually exclusive array or metasurface quick configuration."""

    with st.expander("实验快捷配置", expanded=True):
        preset_label = st.radio(
            "任务类型（二选一）",
            ["Array 阵列聚焦", "0/1 透射超表面"],
            horizontal=True,
            key="scenario_workflow_preset",
        )
        workflow_preset = "metasurface" if preset_label.startswith("0/1") else "array"
        carrier_value_column, carrier_unit_column = st.columns(2)
        carrier_value = carrier_value_column.number_input(
            "工作频率数值" if workflow_preset == "metasurface" else "载波频率数值",
            min_value=0.001,
            value=28.0,
            step=0.1,
            format="%.6f",
            key="scenario_frequency_value",
        )
        carrier_unit = carrier_unit_column.selectbox(
            "工作频率单位" if workflow_preset == "metasurface" else "载波频率单位",
            ["GHz", "MHz"],
            key="scenario_frequency_unit",
        )
        frequency_ghz = convert_frequency(carrier_value, carrier_unit, "GHz")
        validation_errors: list[str] = []
        if not 1 <= frequency_ghz <= 100:
            validation_errors.append("工作频率换算后必须在 1～100 GHz 范围内")

        if workflow_preset == "array":
            modulation_value_column, modulation_unit_column = st.columns(2)
            modulation_value = modulation_value_column.number_input(
                "调制频率数值", min_value=0.001, value=200.0, step=0.1,
                format="%.6f", key="scenario_modulation_frequency_value",
            )
            modulation_unit = modulation_unit_column.selectbox(
                "调制频率单位", ["MHz", "kHz"], key="scenario_modulation_frequency_unit",
            )
            modulation_frequency_mhz = convert_frequency(modulation_value, modulation_unit, "MHz")
            if not 1 <= modulation_frequency_mhz <= 1000:
                validation_errors.append("调制频率换算后必须在 1～1000 MHz 范围内")
            if modulation_frequency_mhz >= frequency_ghz * 1000:
                validation_errors.append("调制频率必须低于载波频率")
            second, third, fourth = st.columns(3)
            element_count = second.selectbox(
                "阵元数量", [64, 144, 256, 400], index=2,
                format_func=lambda value: f"{int(value ** 0.5)}×{int(value ** 0.5)}（{value}）",
                key="scenario_elements",
            )
            polarization = third.selectbox(
                "极化", list(POLARIZATION_LABELS),
                format_func=lambda value: POLARIZATION_LABELS[value], key="scenario_polarization",
            )
            user_count = int(fourth.number_input(
                "用户数量", min_value=1, max_value=4, value=1, step=1, key="scenario_user_count",
            ))
            planned_orders = assign_user_harmonics(user_count)
            st.info("逐用户谐波计划：" + "；".join(
                f"用户 {index + 1} → q={order}（{frequency_ghz + order * modulation_frequency_mhz / 1000:.4f} GHz）"
                for index, order in enumerate(planned_orders)
            ))
            bounds = target_bounds_mm(frequency_ghz)
            st.caption(
                f"当前频率下可计算区域：X={bounds['x'][0]:.1f}～{bounds['x'][1]:.1f} mm，"
                f"Y=0 mm，Z={bounds['z'][0]:.1f}～{bounds['z'][1]:.1f} mm。"
            )
            if polarization != "scalar":
                st.warning("当前 MATLAB 使用标量点源模型：极化会保存，但不会改变聚焦数值。")
            st.markdown("**各用户目标点位**")
            defaults = [(0.0, 0.0, 100.0), (-45.0, 0.0, 100.0), (45.0, 0.0, 100.0), (0.0, 0.0, 62.5)]
            targets: list[list[float]] = []
            for index in range(user_count):
                x_column, y_column, z_column = st.columns(3)
                x = x_column.number_input(f"用户 {index + 1} · X / mm", value=defaults[index][0], key=f"scenario_user_{index + 1}_x")
                y = y_column.number_input(f"用户 {index + 1} · Y / mm", value=0.0, disabled=True, key=f"scenario_user_{index + 1}_y")
                z = z_column.number_input(f"用户 {index + 1} · Z / mm", value=defaults[index][2], key=f"scenario_user_{index + 1}_z")
                targets.append([float(x), float(y), float(z)])
            if 1 <= frequency_ghz <= 100:
                validation_errors.extend(target_validation_errors(targets, frequency_ghz))
            constraint, budget = st.columns(2)
            tolerance_mm = constraint.number_input("每个用户的最大定位误差 / mm", min_value=0.1, value=5.0, step=0.5, key="scenario_tolerance")
            max_refinements = int(budget.number_input("最多修正次数", min_value=0, max_value=5, value=2, step=1, key="scenario_refinements"))
            metasurface_config = None
        else:
            st.caption("当前仅支持矩形平面阵；单元尺寸用于标量传播与 CST 布局，不等同于完整单元结构。")
            unit_x_col, unit_y_col, unit_z_col = st.columns(3)
            unit_x = unit_x_col.number_input("单元 X 尺寸 / mm", min_value=0.01, value=5.0, step=0.1, key="meta_unit_x")
            unit_y = unit_y_col.number_input("单元 Y 尺寸 / mm", min_value=0.01, value=5.0, step=0.1, key="meta_unit_y")
            unit_z = unit_z_col.number_input("单元厚度 / mm", min_value=0.01, value=1.0, step=0.1, key="meta_unit_z")
            nx_col, ny_col, source_col = st.columns(3)
            nx = int(nx_col.number_input("X 向单元数", min_value=4, max_value=40, value=16, step=1, key="meta_nx"))
            ny = int(ny_col.number_input("Y 向单元数", min_value=4, max_value=40, value=16, step=1, key="meta_ny"))
            incident_wave = source_col.selectbox(
                "接收波类型", ["plane_wave", "horn_spherical_wave"],
                format_func=lambda value: "平面波" if value == "plane_wave" else "喇叭天线球面波",
                key="meta_incident_wave",
            )
            feed_x_col, feed_y_col, feed_z_col = st.columns(3)
            feed_x = feed_x_col.number_input("喇叭相位中心 X / mm", value=0.0, disabled=incident_wave == "plane_wave", key="meta_feed_x")
            feed_y = feed_y_col.number_input("喇叭相位中心 Y / mm", value=0.0, disabled=incident_wave == "plane_wave", key="meta_feed_y")
            feed_z = feed_z_col.number_input("喇叭相位中心 Z / mm", max_value=-0.01, value=-100.0, disabled=incident_wave == "plane_wave", key="meta_feed_z")
            st.markdown("**0/1 单元透射响应（线性幅度与相位）**")
            a0_col, p0_col, a1_col, p1_col = st.columns(4)
            a0 = a0_col.number_input("状态 0 幅度", min_value=0.0, max_value=1.0, value=1.0, step=0.01, key="meta_a0")
            p0 = p0_col.number_input("状态 0 相位 / deg", value=0.0, step=1.0, key="meta_p0")
            a1 = a1_col.number_input("状态 1 幅度", min_value=0.0, max_value=1.0, value=1.0, step=0.01, key="meta_a1")
            p1 = p1_col.number_input("状态 1 相位 / deg", value=180.0, step=1.0, key="meta_p1")
            target_x_col, target_y_col, target_z_col = st.columns(3)
            tx = target_x_col.number_input("焦点 X / mm", value=0.0, key="meta_target_x")
            ty = target_y_col.number_input("焦点 Y / mm", value=0.0, key="meta_target_y")
            tz = target_z_col.number_input("焦点 Z / mm", min_value=0.01, value=100.0, key="meta_target_z")
            constraint, budget = st.columns(2)
            tolerance_mm = constraint.number_input("最大定位误差 / mm", min_value=0.1, value=5.0, step=0.5, key="meta_tolerance")
            candidate_budget = int(budget.number_input("候选预算", min_value=1, max_value=8, value=2, step=1, key="meta_candidate_budget"))
            if nx * ny > 1600:
                validation_errors.append("超表面单元总数不得超过 1600")
            wavelength_mm = 300.0 / max(float(frequency_ghz), 1e-12)
            if float(unit_x) > 2 * wavelength_mm or float(unit_y) > 2 * wavelength_mm:
                validation_errors.append("单元 X/Y 尺寸不得超过当前模型的 2 个波长")
            targets = [[float(tx), float(ty), float(tz)]]
            modulation_frequency_mhz = 200.0
            element_count = nx * ny
            polarization = "scalar"
            max_refinements = 0
            metasurface_config = {
                "focus_target_mm": targets[0],
                "unit_size_mm": [float(unit_x), float(unit_y), float(unit_z)],
                "array_size": [nx, ny],
                "incident_wave": incident_wave,
                "horn_feed_position_mm": [float(feed_x), float(feed_y), float(feed_z)],
                "binary_states": {
                    "state_0": {"amplitude": float(a0), "phase_deg": float(p0)},
                    "state_1": {"amplitude": float(a1), "phase_deg": float(p1)},
                },
                "candidate_budget": candidate_budget,
                "objective_weights": {"focus_accuracy": 0.40, "energy_concentration": 0.35, "sidelobe_suppression": 0.25},
                "evaluation_policy": "record_only",
            }
            if a0 == 0 and a1 == 0:
                validation_errors.append("状态 0 和状态 1 不能同时为零透射幅度")
            st.info("验收接口当前为 record_only：展示定位、能量集中度、旁瓣和透射能量代理，但不自动判定整体通过。")

        validation_error = "；".join(validation_errors) or None
        if validation_error:
            st.error(validation_error + "。请修正实验配置后再提交任务。")
    return {
        "targets_mm": targets,
        "frequency_ghz": float(frequency_ghz),
        "modulation_frequency_mhz": float(modulation_frequency_mhz),
        "polarization": polarization,
        "element_count": int(element_count),
        "tolerance_mm": float(tolerance_mm),
        "max_refinements": max_refinements,
        "workflow_preset": workflow_preset,
        "metasurface_config": metasurface_config,
        "_validation_error": validation_error,
    }


def render_prompt_guide() -> None:
    with st.expander("能力说明与提问示例", expanded=False):
        st.markdown(
            "下面是当前 MATLAB 核心的真实能力边界。你可以直接描述科研目标，"
            "不需要记住 Tool 名称；若要求超出范围，智能体应明确报告，而不是擅自改参数。"
        )
        st.dataframe(
            [
                {"项目": "载波频率", "当前设置": "用户自由输入；单位可选 GHz / MHz；换算后支持 1～100 GHz"},
                {"项目": "调制频率", "当前设置": "用户自由输入；单位可选 MHz / kHz；换算后支持 1～1000 MHz，用于 fc+q·fm"},
                {"项目": "目标用户", "当前设置": "1～4 个界面目标；每位用户绑定不同谐波阶次 q"},
                {"项目": "阵元数量", "当前设置": "8×8 / 12×12 / 16×16 / 20×20 平面阵列"},
                {"项目": "极化", "当前设置": "可选择并保存；当前标量模型不计算极化差异"},
                {"项目": "聚焦激励", "当前设置": "逐谐波独立 axial-null 复权重；尚未投影为同一组硬件脉冲时序"},
                {"项目": "阵列几何设计", "当前设置": "仍使用冻结的 28 GHz / 256 阵元 baseline 公平约束"},
                {"项目": "可编程超表面", "当前设置": "0/1 透射单元；平面波或喇叭球面波；几何光学基线、二进制优化、控制码与 CST 布局"},
                {"项目": "场评估", "当前设置": "XOZ 主评估面 201×201；经过实际焦点的 YOZ 与 XOY 切面各 101×101"},
            ],
            width="stretch",
            hide_index=True,
        )
        st.markdown(
            "**可以这样说：**“按当前配置运行聚焦”、“把用户 2 改到另一点后重跑”、"
            "“解释上次失败原因”、阵列几何优化要求，或平面可编程超表面相位码设计要求。"
        )
        focus_example, design_example, metasurface_example = st.columns(3)
        focus_example.caption("聚焦任务示例")
        focus_example.code(DEFAULT_FOCUS_TASK, language=None)
        design_example.caption("阵列几何设计示例")
        design_example.code(DEFAULT_DESIGN_TASK, language=None)
        metasurface_example.caption("可编程超表面设计示例")
        metasurface_example.code(DEFAULT_METASURFACE_TASK, language=None)


@st.fragment(run_every=2.0)
def render_conversation_tasks(conversation_id: str) -> None:
    """Render durable task progress and evidence inside the owning conversation."""

    jobs = sorted(
        (
            job for job in list_jobs(1000)
            if job.get("conversation_id") == conversation_id
        ),
        key=lambda item: str(item.get("created_at", "")),
        reverse=True,
    )
    st.subheader("本对话的任务")
    st.caption("这里与后台任务中心读取同一份持久化记录；每 2 秒刷新，关闭浏览器不会中断任务。")
    if not jobs:
        st.info("当前对话还没有提交任务。")
        return

    for index, job in enumerate(jobs):
        job_id = str(job["job_id"])
        with st.container(border=True):
            st.markdown(f"#### {job_id}")
            status, stage, elapsed, remaining = st.columns(4)
            status.metric("状态", _job_status_text(job.get("status")))
            stage.metric("当前阶段", _display(job.get("stage")))
            elapsed.metric("已运行", f"{float(job.get('elapsed_sec') or 0):.1f} s")
            remaining.metric(
                "预计剩余", f"{float(job.get('estimated_remaining_sec') or 0):.1f} s"
            )
            if job.get("status") in {"running", "pause_requested", "resume_requested"}:
                spent = float(job.get("elapsed_sec") or 0)
                left = float(job.get("estimated_remaining_sec") or 0)
                st.progress(
                    min(spent / (spent + left), 0.99) if spent + left > 0 else 0.0,
                    text=str(job.get("message") or "任务运行中"),
                )
            else:
                st.caption(str(job.get("message") or ""))

            pause, resume, cancel, recover = st.columns(4)
            try:
                if pause.button(
                    "暂停",
                    key=f"chat_pause_{job_id}",
                    disabled=job.get("status") not in {"queued", "running", "resume_requested"},
                    width="stretch",
                ):
                    request_action(job_id, "pause")
                    st.rerun(scope="fragment")
                if resume.button(
                    "恢复",
                    key=f"chat_resume_{job_id}",
                    disabled=job.get("status") != "paused",
                    width="stretch",
                ):
                    request_action(job_id, "resume")
                    st.rerun(scope="fragment")
                if cancel.button(
                    "取消",
                    key=f"chat_cancel_{job_id}",
                    disabled=job.get("status") in {"completed", "failed", "cancelled"},
                    width="stretch",
                ):
                    request_action(job_id, "cancel")
                    st.rerun(scope="fragment")
                if recover.button(
                    "从断点恢复",
                    key=f"chat_recover_{job_id}",
                    disabled=job.get("status") not in {"failed", "cancelled"},
                    width="stretch",
                ):
                    create_recovery_job(job_id)
                    st.rerun(scope="fragment")
            except JobStoreError as exception:
                st.error(str(exception))

            show_details = st.toggle(
                "展开任务详情",
                value=index == 0,
                key=f"chat_details_{job_id}",
            )
            if show_details:
                _render_conversation_task_details(job)


def _render_conversation_task_details(job: dict[str, Any]) -> None:
    result = job.get("result")
    summary_tab, process_tab, visualization_tab = st.tabs(
        ["智能体总结", "任务进度与 Tool 轨迹", "任务可视化"]
    )
    with summary_tab:
        if isinstance(result, dict) and result.get("error_category"):
            st.error(
                f"{_category_text(result['error_category'], result.get('error_message'))}："
                f"{_error_message_text(result.get('error_message'))}"
            )
        if isinstance(result, dict):
            st.markdown(result.get("agent_final_response") or "Hermes 没有生成最终回复。")
            submitted = result.get("submitted_task")
        else:
            st.info("任务尚未生成最终总结。")
            submitted = job.get("task_text")
        with st.expander("查看提交给 Hermes 的完整任务上下文", expanded=False):
            st.code(str(submitted or job.get("task_text") or ""), language=None)

    with process_tab:
        if isinstance(result, dict):
            render_trajectory(result.get("trajectory", []))
            iterations = result.get("iterations")
            if isinstance(iterations, list) and iterations:
                st.dataframe(_localize_rows(iterations), width="stretch", hide_index=True)
        history = job.get("history")
        if isinstance(history, list) and history:
            st.caption("后台状态时间线")
            st.dataframe(history, width="stretch", hide_index=True)

    with visualization_tab:
        if not isinstance(result, dict):
            st.info("任务完成并持久化结果后，这里会自动显示可视化。")
            return
        if result.get("task_kind") == "focus":
            metrics = st.columns(5)
            metrics[0].metric("结果", _status_text(result.get("status")))
            metrics[1].metric("期望目标", _vector(result.get("desired_target_mm")))
            metrics[2].metric("实际峰值", _vector(result.get("actual_peak_mm")))
            metrics[3].metric("最终误差", _millimetres(result.get("final_error_mm")))
            metrics[4].metric("MATLAB 调用", result.get("matlab_calls", 0))
            iterations = result.get("iterations") if isinstance(result.get("iterations"), list) else []
            if iterations:
                render_focus_position_plot(iterations)
            state = result.get("agent_state")
            if isinstance(state, dict):
                render_focus_field_evidence(
                    state,
                    key_prefix=f"conversation_{job.get('job_id')}",
                    governance=result.get("governance"),
                )
        elif result.get("task_kind") == "array_design":
            selected = result.get("selected_design") or {}
            baseline = result.get("baseline") or {}
            baseline_geometry = baseline.get("geometry") or {}
            selected_geometry = selected.get("geometry") or {}
            if baseline_geometry.get("element_positions_mm") and selected_geometry.get("element_positions_mm"):
                render_geometry_plots(baseline_geometry, selected_geometry)
            baseline_metrics = result.get("baseline_metrics")
            designed_metrics = result.get("designed_metrics")
            if isinstance(baseline_metrics, dict) and isinstance(designed_metrics, dict):
                st.dataframe(
                    _comparison_rows(baseline_metrics, designed_metrics),
                    width="stretch",
                    hide_index=True,
                )
                render_profile_plots(baseline_metrics, designed_metrics)
        elif result.get("task_kind") == "metasurface_design":
            render_metasurface_result(
                result,
                key_prefix=f"conversation_metasurface_{job.get('job_id')}",
                show_governance=False,
            )
        else:
            st.info("本任务是纯对话，没有 MATLAB 数值结果可视化。")


@st.fragment(run_every=2.0)
def render_task_center() -> None:
    """Auto-refresh the durable queue without restarting Agent or MATLAB work."""

    st.header("后台任务中心")
    st.caption(
        "任务由独立 worker 串行执行并写入 runs/ui_jobs；浏览器关闭后仍继续。"
        "剩余时间是依据历史任务时长得到的估计值，不是 MATLAB 的确定性进度。"
    )
    all_jobs = list_jobs(1000)
    for job in all_jobs:
        if job.get("notification_pending"):
            outcome = "完成" if job.get("status") == "completed" else "结束"
            st.toast(f"任务 {job['job_id']} 已{outcome}：{job.get('message', '')}")
            mark_notification_seen(str(job["job_id"]))
    if not all_jobs:
        st.info("队列为空。请在“智能体对话”中提交任务。")
        return

    overview = st.columns(5)
    overview[0].metric("任务总数", len(all_jobs))
    overview[1].metric("排队", sum(job.get("status") == "queued" for job in all_jobs))
    overview[2].metric(
        "运行／暂停",
        sum(job.get("status") in ACTIVE_STATUSES | {"paused"} for job in all_jobs),
    )
    overview[3].metric("已完成", sum(job.get("status") == "completed" for job in all_jobs))
    overview[4].metric(
        "失败／取消",
        sum(job.get("status") in {"failed", "cancelled"} for job in all_jobs),
    )
    conversation_ids = sorted({str(job.get("conversation_id")) for job in all_jobs})
    conversation_filter, status_filter = st.columns(2)
    selected_conversation = conversation_filter.selectbox(
        "按对话筛选",
        ["全部对话", *conversation_ids],
        key="task_center_conversation_filter",
    )
    selected_status = status_filter.selectbox(
        "按状态筛选",
        ["全部状态", "排队中", "运行／暂停", "已完成", "失败／取消"],
        key="task_center_status_filter",
    )
    jobs = [
        job for job in all_jobs
        if (selected_conversation == "全部对话" or job.get("conversation_id") == selected_conversation)
        and _matches_job_status_filter(job, selected_status)
    ]
    if not jobs:
        st.info("当前筛选条件下没有任务。")
        return

    queued_ids = [
        str(job["job_id"])
        for job in sorted(all_jobs, key=lambda item: str(item.get("created_at", "")))
        if job.get("status") == "queued"
    ]

    st.dataframe(
        [
            {
                "任务 ID": job.get("job_id"),
                "所属对话": job.get("conversation_id"),
                "队列序号": (
                    str(queued_ids.index(str(job.get("job_id"))) + 1)
                    if str(job.get("job_id")) in queued_ids else "—"
                ),
                "模型": _job_model_label(job),
                "状态": _job_status_text(job.get("status")),
                "阶段": job.get("stage"),
                "耗时／s": round(float(job.get("elapsed_sec") or 0), 1),
                "预计剩余／s": round(float(job.get("estimated_remaining_sec") or 0), 1),
                "MATLAB 进程": ", ".join(
                    f"{item.get('name')}({item.get('pid')})"
                    for item in job.get("matlab_processes") or []
                    if isinstance(item, dict)
                ) or "无",
                "创建时间": job.get("created_at"),
            }
            for job in jobs
        ],
        width="stretch",
        hide_index=True,
    )
    selected_id = st.selectbox(
        "选择后台任务",
        [str(job["job_id"]) for job in jobs],
        format_func=lambda job_id: next(
            f"{job_id} ｜{_job_status_text(item.get('status'))} ｜{item.get('message', '')}"
            for item in jobs if item.get("job_id") == job_id
        ),
        key="task_center_selected_job",
    )
    selected = load_job(selected_id)
    elapsed = float(selected.get("elapsed_sec") or 0)
    remaining = float(selected.get("estimated_remaining_sec") or 0)
    total = elapsed + remaining
    if selected.get("status") in {"running", "pause_requested", "resume_requested"}:
        st.progress(min(elapsed / total, 0.99) if total > 0 else 0.0, text=selected.get("message"))
    elif selected.get("status") == "paused":
        st.warning(selected.get("message"))
    else:
        st.info(selected.get("message"))

    pause, resume, cancel, retry, repeat, load = st.columns(6)
    try:
        if pause.button(
            "暂停", key=f"pause_{selected_id}", width="stretch",
            disabled=selected.get("status") not in {"queued", "running", "resume_requested"},
        ):
            request_action(selected_id, "pause"); st.rerun(scope="fragment")
        if resume.button(
            "恢复", key=f"resume_{selected_id}", width="stretch",
            disabled=selected.get("status") != "paused",
        ):
            request_action(selected_id, "resume"); st.rerun(scope="fragment")
        if cancel.button(
            "取消", key=f"cancel_{selected_id}", width="stretch",
            disabled=selected.get("status") in {"completed", "failed", "cancelled"},
        ):
            request_action(selected_id, "cancel"); st.rerun(scope="fragment")
        if retry.button(
            "从断点恢复", key=f"retry_{selected_id}", width="stretch",
            disabled=selected.get("status") not in {"failed", "cancelled"},
        ):
            create_recovery_job(selected_id); st.rerun(scope="fragment")
        if repeat.button(
            "重复运行", key=f"repeat_{selected_id}", width="stretch",
            disabled=(
                selected.get("status") != "completed"
                or not isinstance(selected.get("result"), dict)
                or selected["result"].get("task_kind")
                not in {"focus", "array_design", "metasurface_design"}
            ),
            help="以相同文本和当前代码快照开启全新 Hermes 会话；会再次消耗模型额度并运行 MATLAB。",
        ):
            repeated = create_repeat_job(selected_id)
            st.toast(f"一致性重复任务已加入队列：{repeated['job_id']}")
            st.rerun(scope="fragment")
        if load.button(
            "接回当前对话", key=f"load_{selected_id}", width="stretch",
            disabled=not isinstance(selected.get("hermes_session_id"), str),
        ):
            st.session_state["hermes_session_id"] = selected.get("hermes_session_id")
            st.session_state["conversation_id"] = selected.get("conversation_id")
            st.session_state.pop("chat_messages", None)
            st.session_state.pop("chat_completed_jobs", None)
            st.session_state.pop("latest_agent_result", None)
            st.toast("后续消息将继续该 Hermes 会话。")
            st.rerun()
    except JobStoreError as exception:
        st.error(str(exception))

    result = selected.get("result")
    if not isinstance(result, dict):
        render_run_governance(
            selected.get("submission_governance"),
            selected.get("reproducibility_comparison"),
            heading="本任务提交快照",
        )
    if isinstance(result, dict):
        if result.get("task_kind") == "focus":
            render_focus_result(result, show_field=False)
        elif result.get("task_kind") == "array_design":
            render_design_result(result)
        elif result.get("task_kind") == "metasurface_design":
            render_metasurface_result(result, key_prefix="task_center_metasurface")
        else:
            render_run_governance(result.get("governance"), heading="本次运行治理")
            st.subheader("Hermes 回复")
            st.markdown(result.get("agent_final_response") or "没有可显示的回复。")
        render_reproducibility_comparison(selected.get("reproducibility_comparison"))
    if selected.get("error"):
        st.error(selected["error"].get("message", "任务失败。"))
    with st.expander("任务状态与控制历史", expanded=False):
        st.json(_without_git_metadata({
            key: value for key, value in selected.items() if key != "result"
        }))


def render_results_page() -> None:
    """Browse persisted results without invoking Hermes or MATLAB again."""
    st.header("历史结果可视化")
    st.caption("本页面只读取 runs 目录中的已保存结果，不会重新运行 Hermes 或 MATLAB。")
    result_type = st.radio(
        "结果类型",
        ["聚焦任务", "阵列几何设计", "可编程超表面设计"],
        horizontal=True,
        key="viewer_result_type",
    )
    if result_type == "聚焦任务":
        render_focus_result_viewer()
    elif result_type == "阵列几何设计":
        render_array_design_viewer()
    else:
        render_metasurface_design_viewer()


def render_focus_result_viewer() -> None:
    recent = load_recent_tasks(PROJECT_ROOT, limit=50)
    if not recent:
        st.info("尚未找到可查看的聚焦任务。")
        return
    selected_id = st.selectbox(
        "选择聚焦任务",
        [str(item["agent_task_id"]) for item in recent],
        format_func=lambda task_id: _focus_option_label(task_id, recent),
        key="viewer_focus_task",
    )
    record = load_focus_task_record(PROJECT_ROOT, selected_id)
    if not record:
        st.error("无法读取所选聚焦任务的状态文件。")
        return
    state = record["state"]
    iterations = record["iterations"]
    evaluations = [
        event for event in state.get("history", [])
        if isinstance(event, dict) and event.get("event") == "evaluation"
    ]
    final_evaluation = evaluations[-1] if evaluations else {}
    last_iteration = iterations[-1] if iterations else {}
    columns = st.columns(5)
    columns[0].metric("任务状态", _status_text(state.get("status", "UNKNOWN")))
    columns[1].metric("期望目标", _vector(state.get("desired_target_mm")))
    columns[2].metric("最终实际峰值", _vector(last_iteration.get("Actual Peak")))
    columns[3].metric("最终误差", _millimetres(final_evaluation.get("focus_error_mm")))
    columns[4].metric("MATLAB 实验次数", len(iterations))
    original_task = record["metadata"].get("original_task")
    if original_task:
        with st.expander("原始自然语言任务", expanded=False):
            st.code(original_task, language=None)
    render_run_governance(record["metadata"].get("governance"), heading="本次运行治理")
    st.subheader("目标、命令与实际峰值")
    render_focus_position_plot(iterations)
    st.dataframe(_localize_rows(iterations), width="stretch", hide_index=True)
    st.subheader("聚焦阵列的真实阵元位置")
    last_simulation = next(
        (
            event for event in reversed(state.get("history", []))
            if isinstance(event, dict) and event.get("event") == "simulation"
        ),
        {},
    )
    positions = last_simulation.get("element_positions_mm")
    if isinstance(positions, list) and positions:
        render_single_geometry(positions)
    else:
        baseline = generate_geometry("baseline")
        render_single_geometry(baseline["element_positions_mm"])
    render_focus_field_evidence(
        state, key_prefix="viewer", governance=record["metadata"].get("governance")
    )
    render_multi_task_overlay(recent)
    with st.expander("完整任务状态", expanded=False):
        st.json(state)


def render_array_design_viewer() -> None:
    recent = load_recent_designs(PROJECT_ROOT, limit=50)
    if not recent:
        st.info("尚未找到可查看的阵列设计。")
        return
    selected_id = st.selectbox(
        "选择阵列设计",
        [str(item["design_task_id"]) for item in recent],
        format_func=lambda task_id: _design_option_label(task_id, recent),
        key="viewer_array_design",
    )
    state = load_array_design_record(PROJECT_ROOT, selected_id)
    if not state:
        st.error("无法读取所选阵列设计的状态文件。")
        return
    selected = state.get("selected_design") or {}
    baseline = state.get("baseline") or {}
    render_design_result({
        "status": "SAVED" if selected else "FAILED",
        "error_category": None if selected else "Agent Error",
        "error_message": None if selected else "该历史任务尚未保存最终阵列。",
        "design_state": state,
        "selected_design": selected,
        "baseline": baseline,
        "baseline_metrics": baseline.get("metrics"),
        "designed_metrics": selected.get("metrics"),
        "trajectory": state.get("hermes_tool_trajectory") or [],
        "agent_final_response": state.get("hermes_final_response") or "",
        "search_rounds": sum(
            event.get("event") == "search_geometry"
            for event in state.get("search_trajectory", []) if isinstance(event, dict)
        ),
        "matlab_processes": len(state.get("search_trajectory", [])),
        "governance": state.get("governance"),
    })


def render_metasurface_design_viewer() -> None:
    recent = load_recent_metasurface_designs(PROJECT_ROOT, limit=50)
    if not recent:
        st.info("尚未找到可查看的可编程超表面设计。")
        return
    selected_id = st.selectbox(
        "选择可编程超表面设计",
        [str(item["metasurface_task_id"]) for item in recent],
        format_func=lambda task_id: _metasurface_option_label(task_id, recent),
        key="viewer_metasurface_design",
    )
    state = load_metasurface_design_record(PROJECT_ROOT, selected_id)
    if not state:
        st.error("无法读取所选可编程超表面设计。")
        return
    selected = state.get("selected_design")
    render_metasurface_result({
        "status": "SAVED" if selected else "FAILED",
        "error_category": None if selected else "Agent Error",
        "error_message": None if selected else "该任务尚未保存最终相位控制码。",
        "metasurface_state": state,
        "unprogrammed_reference": state.get("unprogrammed_reference"),
        "continuous_reference": state.get("continuous_reference"),
        "geometrical_optics_baseline": state.get("geometrical_optics_baseline"),
        "selected_design": selected,
        "cst_model": state.get("cst_model"),
        "candidate_evaluations": len(state.get("evaluated_candidates") or {}),
        "matlab_processes": len(state.get("optimization_trajectory") or []),
        "trajectory": state.get("hermes_tool_trajectory") or [],
        "agent_final_response": state.get("hermes_final_response") or "",
        "governance": state.get("governance"),
    }, key_prefix="viewer_metasurface")


def _run(task_text: str, resume_session_id: str | None = None) -> dict[str, Any]:
    status_box = st.status("正在初始化 Hermes 智能体……", expanded=True)
    progress_log = status_box.empty()
    messages: list[str] = []
    def update(status: StatusUpdate) -> None:
        messages.append(status.message)
        progress_log.markdown("\n\n".join(f"- {message}" for message in messages))
        status_box.update(label=status.message, state="running", expanded=True)
    try:
        result = run_agent_task(
            task_text,
            submission_mode="natural_language",
            on_status=update,
            task_kind="auto",
            resume_session_id=resume_session_id,
        )
        status_box.update(label=_status_text(result["status"]), state="complete" if result["status"] in {"SUCCESS", "SAVED"} else "error", expanded=False)
        return result
    except AgentRunnerError as exception:
        status_box.update(
            label=_category_text(exception.category, str(exception)),
            state="error",
            expanded=True,
        )
        return {
            "status": "FAILED",
            "task_kind": "unknown",
            "error_category": exception.category,
            "error_message": str(exception),
            "trajectory": [],
            "agent_final_response": "",
        }


def render_focus_result(
    result: dict[str, Any],
    *,
    show_field: bool = True,
    key_prefix: str = "latest",
    show_governance: bool = True,
) -> None:
    st.divider()
    if result.get("error_category"):
        st.error(
            f"{_category_text(result['error_category'], result.get('error_message'))}："
            f"{_error_message_text(result.get('error_message'))}"
        )
    else:
        st.success("已满足用户要求的科学约束。")
    if show_governance:
        render_run_governance(result.get("governance"), heading="本次运行治理")
    for warning in result.get("infrastructure_warnings", []):
        st.warning(f"已恢复的基础设施警告：{warning}")
    columns = st.columns(6)
    columns[0].metric("状态", _status_text(result.get("status", "UNKNOWN")))
    columns[1].metric("期望目标", _vector(result.get("desired_target_mm")))
    columns[2].metric("实际峰值", _vector(result.get("actual_peak_mm")))
    columns[3].metric("最终误差", _millimetres(result.get("final_error_mm")))
    columns[4].metric("MATLAB 调用次数", result.get("matlab_calls", 0))
    columns[5].metric("重新规划次数", result.get("replanning_count", 0))
    state = result.get("agent_state") if isinstance(result.get("agent_state"), dict) else {}
    scenario_columns = st.columns(4)
    scenario_columns[0].metric("用户数量", state.get("user_count", 1))
    frequency = state.get("frequency_hz")
    scenario_columns[1].metric(
        "载波频率", f"{float(frequency) / 1e9:g} GHz" if isinstance(frequency, (int, float)) else "无"
    )
    scenario_columns[2].metric("阵元数量", state.get("element_count", 256))
    scenario_columns[3].metric(
        "极化场景", POLARIZATION_LABELS.get(str(state.get("polarization", "scalar")), "无")
    )
    evaluations = [
        event for event in state.get("history", [])
        if isinstance(event, dict) and event.get("event") == "evaluation"
    ]
    if evaluations and isinstance(evaluations[-1].get("user_results"), list):
        st.subheader("各用户最终聚焦结果")
        st.dataframe(
            pd.DataFrame(evaluations[-1]["user_results"]).rename(
                columns={
                    "user": "用户",
                    "desired_target_mm": "期望目标／mm",
                    "actual_peak_mm": "实际峰值／mm",
                    "focus_error_mm": "定位误差／mm",
                    "success": "满足约束",
                }
            ),
            width="stretch",
            hide_index=True,
        )
    if show_field:
        render_focus_field_evidence(
            state, key_prefix=key_prefix, governance=result.get("governance")
        )
    render_trajectory(result.get("trajectory", []))
    st.subheader("迭代历史")
    st.dataframe(_localize_rows(result.get("iterations", [])), width="stretch", hide_index=True)
    st.subheader("智能体总结")
    st.markdown(result.get("agent_final_response") or "Hermes 没有生成最终回复。")


def render_focus_field_evidence(
    state: dict[str, Any], *, key_prefix: str = "field", governance: Any = None
) -> None:
    simulations = [
        event for event in state.get("history", [])
        if isinstance(event, dict) and event.get("event") == "simulation"
    ]
    if not simulations:
        return
    st.subheader("完整二维场与方法证据")
    iteration_index = st.selectbox(
        "选择仿真迭代",
        list(range(len(simulations))),
        index=len(simulations) - 1,
        format_func=lambda index: f"第 {index + 1} 次 · {simulations[index].get('simulation_run_id')}",
        key=f"{key_prefix}_field_iteration_{state.get('agent_task_id', id(state))}",
    )
    simulation = simulations[int(iteration_index)]
    st.markdown("#### 本次方法卡片")
    st.dataframe(build_method_card(state, simulation), width="stretch", hide_index=True)
    field_data = load_field_data(PROJECT_ROOT, simulation)
    if field_data is None:
        st.info("这次迭代来自旧版本，尚未持久化二维场矩阵；不会重新计算或伪造热力图。")
        return
    user_count = len(field_data.get("user_harmonic_orders", []))
    user_index = int(st.selectbox(
        "选择用户／谐波",
        list(range(user_count)),
        format_func=lambda index: (
            f"用户 {index + 1} · q={field_data['user_harmonic_orders'][index]} · "
            f"{_user_frequency_ghz(field_data, index):.4f} GHz"
        ),
        key=f"{key_prefix}_field_user_{state.get('agent_task_id', id(state))}_{iteration_index}",
    ))
    if len(simulations) > 1:
        st.markdown("#### Agent 修正前基线与最终结果")
        st.info(
            "基线是 Hermes 尚未依据观测进行 refine 前的第一次真实 MATLAB 仿真；"
            "最终结果是最后一次真实 MATLAB 仿真。这里展示的是 Agent 工作流带来的命令修正效果，"
            "不是把 LLM 描述成电磁场求解器。"
        )
        st.dataframe(
            _focus_baseline_comparison_rows(state, simulations, user_index),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info(
            "本任务只执行了一次 MATLAB 仿真，因此第一次结果同时也是最终结果；"
            "本次没有发生可量化的 Agent 反馈修正。"
        )
    fwhm = _list_value(simulation.get("fwhm_x_mm_by_user"), user_index)
    dof = _list_value(simulation.get("dof_z_mm_by_user"), user_index)
    pslr = _list_value(simulation.get("peak_to_sidelobe_ratio_db_by_user"), user_index)
    peak_power = _list_value(simulation.get("peak_power_by_user"), user_index)
    metrics = st.columns(4)
    metrics[0].metric("X 向 FWHM", _millimetres(fwhm))
    metrics[1].metric("Z 向 DOF", _millimetres(dof))
    metrics[2].metric("峰旁比", f"{pslr:.2f} dB" if isinstance(pslr, (int, float)) else "无")
    metrics[3].metric("峰值功率", f"{peak_power:.4g}" if isinstance(peak_power, (int, float)) else "无")
    st.caption(
        "峰旁比定义：目标局部峰功率 ÷ FWHM×DOF 主瓣矩形之外的最大功率。"
        "该值为负时，表示场内存在比目标局部峰更强的其他峰。"
    )
    dynamic_range_db = float(
        st.select_slider(
            "热力图显示动态范围／dB",
            options=[6, 9, 12, 20, 30, 40, 60],
            value=12,
            key=(
                f"{key_prefix}_field_dynamic_range_"
                f"{state.get('agent_task_id', id(state))}_{iteration_index}_{user_index}"
            ),
            help="默认只突出接近峰值的主瓣；扩大范围可查看更弱的相干旁瓣和干涉条纹。",
        )
    )
    st.caption(
        f"当前显示 0～−{dynamic_range_db:g} dB；白色实线是 −3 dB 轮廓。"
        "全域条带属于真实相干干涉与旁瓣，缩小动态范围或查看局部图可更清楚地识别焦斑。"
    )

    has_orthogonal_planes = all(
        key in field_data
        for key in (
            "y_mm", "yz_z_mm", "yz_plane_x_mm_by_user",
            "normalized_power_yz_by_user", "xy_x_mm", "xy_y_mm",
            "xy_plane_z_mm_by_user", "normalized_power_xy_by_user",
        )
    )
    if has_orthogonal_planes:
        st.markdown("#### 三个真实正交场切面")
        selected_plane = st.radio(
            "选择要显示的切面",
            ["XOZ 主评估面", "YOZ 主切面", "XOY 焦平面"],
            horizontal=True,
            key=(
                f"{key_prefix}_field_plane_"
                f"{state.get('agent_task_id', id(state))}_{iteration_index}_{user_index}"
            ),
        )
        if selected_plane == "XOZ 主评估面":
            _render_xz_field_maps(
                field_data, simulation, user_index, state, key_prefix,
                int(iteration_index), dynamic_range_db,
            )
        elif selected_plane == "YOZ 主切面":
            fixed_x = float(field_data["yz_plane_x_mm_by_user"][user_index])
            st.caption(
                f"真实 MATLAB YOZ 主切面，固定 X={fixed_x:g} mm。"
            )
            st.image(
                load_plane_png(
                    PROJECT_ROOT, simulation, user_index, "yz",
                    dynamic_range_db=dynamic_range_db,
                ),
                width="stretch",
            )
        else:
            fixed_z = float(field_data["xy_plane_z_mm_by_user"][user_index])
            st.caption(
                f"真实 MATLAB XOY 焦平面，固定 Z={fixed_z:g} mm（本用户实际峰值深度）。"
            )
            st.image(
                load_plane_png(
                    PROJECT_ROOT, simulation, user_index, "xy",
                    dynamic_range_db=dynamic_range_db,
                ),
                width="stretch",
            )
        st.caption(
            "三个切面分别按本用户在该切面内的最大功率归一化；页面一次只渲染所选切面。"
            "定位误差和现有 FWHM/DOF 定义仍来自 XOZ 主评估面。"
        )
    else:
        _render_xz_field_maps(
            field_data, simulation, user_index, state, key_prefix,
            int(iteration_index), dynamic_range_db,
        )
        st.info("该历史结果生成于正交切面功能加入之前，仅包含 XOZ 场；不会伪造 YOZ/XOY 数据。")

    _, axial = profile_frames(field_data, user_index)
    st.caption("轴向剖面与焦深（实际峰值 X 截面）")
    st.line_chart(axial.set_index("Z／mm"))

    show_comparison = len(simulations) > 1 and st.toggle(
        "显示 Agent 修正前基线与最终 XOZ 场图",
        value=True,
        key=f"{key_prefix}_field_compare_{state.get('agent_task_id', id(state))}",
    )
    if show_comparison:
        first_data = load_field_data(PROJECT_ROOT, simulations[0])
        last_data = load_field_data(PROJECT_ROOT, simulations[-1])
        if first_data and last_data and user_index < len(first_data.get("user_harmonic_orders", [])):
            st.markdown("#### 迭代前后场图")
            before, after = st.columns(2)
            before.caption("第一次 MATLAB 迭代")
            before.image(
                load_plane_png(
                    PROJECT_ROOT, simulations[0], user_index, "xz",
                    dynamic_range_db=dynamic_range_db,
                ),
                width="stretch",
            )
            after.caption("最后一次 MATLAB 迭代")
            after.image(
                load_plane_png(
                    PROJECT_ROOT, simulations[-1], user_index, "xz",
                    dynamic_range_db=dynamic_range_db,
                ),
                width="stretch",
            )

    with st.expander("一键导出", expanded=False):
        prepare_exports = st.toggle(
            "准备导出文件",
            value=False,
            key=(
                f"{key_prefix}_prepare_exports_"
                f"{simulation.get('simulation_run_id')}_{user_index}"
            ),
            help="MAT/ZIP 文件较大，仅在需要下载时生成，避免每次刷新页面都重复压缩。",
        )
        if not prepare_exports:
            st.caption("打开此开关后才读取 MAT 并生成 PNG、CSV 和 ZIP。")
            return
        paths = artifact_paths(PROJECT_ROOT, simulation)
        csv_bytes = field_frame(field_data, user_index).to_csv(index=False).encode("utf-8-sig")
        first, second, third, fourth, fifth = st.columns(5)
        first.download_button(
            "导出 PNG", field_png(field_data, user_index),
            file_name=f"user_{user_index + 1}_field.png", mime="image/png",
            key=f"{key_prefix}_png_{simulation.get('simulation_run_id')}_{user_index}", width="stretch",
        )
        second.download_button(
            "导出 CSV", csv_bytes,
            file_name=f"user_{user_index + 1}_field.csv", mime="text/csv",
            key=f"{key_prefix}_csv_{simulation.get('simulation_run_id')}_{user_index}", width="stretch",
        )
        mat_path = paths.get("field_mat")
        third.download_button(
            "导出 MAT", mat_path.read_bytes() if mat_path else b"",
            file_name="field_data.mat", mime="application/octet-stream",
            disabled=mat_path is None,
            key=f"{key_prefix}_mat_{simulation.get('simulation_run_id')}", width="stretch",
        )
        report = report_markdown(state, simulation, governance).encode("utf-8")
        fourth.download_button(
            "导出报告", report, file_name="report.md", mime="text/markdown",
            key=f"{key_prefix}_report_{simulation.get('simulation_run_id')}", width="stretch",
        )
        fifth.download_button(
            "导出全部 ZIP", export_bundle(PROJECT_ROOT, state, simulation, governance),
            file_name=f"{state.get('agent_task_id', 'focus')}_artifacts.zip",
            mime="application/zip", key=f"{key_prefix}_zip_{simulation.get('simulation_run_id')}",
            width="stretch",
        )


def render_multi_task_overlay(recent: list[dict[str, Any]]) -> None:
    selected = st.multiselect(
        "多任务叠加比较（各任务最后一次迭代、用户 1 横向剖面）",
        [str(item["agent_task_id"]) for item in recent],
        max_selections=6,
        key="focus_overlay_tasks",
    )
    series: list[pd.DataFrame] = []
    for task_id in selected:
        record = load_focus_task_record(PROJECT_ROOT, task_id)
        simulations = [
            event for event in (record or {}).get("state", {}).get("history", [])
            if isinstance(event, dict) and event.get("event") == "simulation"
        ]
        if not simulations:
            continue
        field_data = load_field_data(PROJECT_ROOT, simulations[-1])
        if field_data:
            lateral, _ = profile_frames(field_data, 0)
            series.append(lateral[["X／mm", "归一化功率"]].rename(columns={"归一化功率": task_id}))
    if series:
        combined = series[0]
        for frame in series[1:]:
            combined = combined.merge(frame, on="X／mm", how="outer")
        st.line_chart(combined.sort_values("X／mm").set_index("X／mm"))


def _render_xz_field_maps(
    field_data: dict[str, Any],
    simulation: dict[str, Any],
    user_index: int,
    state: dict[str, Any],
    key_prefix: str,
    iteration_index: int,
    dynamic_range_db: float,
) -> None:
    full, local = st.columns(2)
    full.caption("真实 MATLAB 归一化功率热力图（全 XOZ 区域，Y=0）")
    full.image(
        load_plane_png(
            PROJECT_ROOT, simulation, user_index, "xz",
            dynamic_range_db=dynamic_range_db,
        ),
        width="stretch",
    )
    actual = field_data["actual_peak_points_mm"][user_index]
    x_span = max(abs(float(value) - float(actual[0])) for value in field_data["x_mm"])
    z_span = max(abs(float(value) - float(actual[2])) for value in field_data["z_mm"])
    maximum_radius = max(2.0, min(x_span, z_span))
    radius = local.slider(
        "实际焦点附近放大半径／mm",
        min_value=1.0,
        max_value=float(maximum_radius),
        value=float(min(20.0, maximum_radius)),
        step=1.0,
        key=(
            f"{key_prefix}_local_radius_{state.get('agent_task_id', id(state))}_"
            f"{iteration_index}_{user_index}"
        ),
    )
    local.caption("实际焦点附近局部放大")
    local.image(
        load_plane_png(
            PROJECT_ROOT,
            simulation,
            user_index,
            "xz",
            local_radius_mm=radius,
            dynamic_range_db=dynamic_range_db,
        ),
        width="stretch",
    )


def render_design_result(
    result: dict[str, Any], *, show_governance: bool = True
) -> None:
    st.divider()
    if result.get("error_category"):
        st.error(
            f"{_category_text(result['error_category'], result.get('error_message'))}："
            f"{_error_message_text(result.get('error_message'))}"
        )
    else:
        st.success("最终阵列几何及其完整验证证据已保存。")
    if show_governance:
        render_run_governance(result.get("governance"), heading="本次运行治理")
    state = result.get("design_state") or {}
    selected = result.get("selected_design") or {}
    columns = st.columns(5)
    columns[0].metric("状态", _status_text(result.get("status", "UNKNOWN")))
    columns[1].metric("聚焦目标", _vector(state.get("focus_target_mm")))
    columns[2].metric("选定几何族", (selected.get("geometry") or {}).get("family", "无"))
    columns[3].metric("搜索轮数", result.get("search_rounds", 0))
    columns[4].metric("MATLAB 进程数", result.get("matlab_processes", 0))
    baseline, designed = result.get("baseline_metrics"), result.get("designed_metrics")
    if isinstance(baseline, dict) and isinstance(designed, dict):
        st.subheader("基准阵列与最终设计对比")
        st.dataframe(_comparison_rows(baseline, designed), width="stretch", hide_index=True)
        baseline_geometry = ((result.get("baseline") or {}).get("geometry") or {})
        designed_geometry = selected.get("geometry") or {}
        if baseline_geometry.get("element_positions_mm") and designed_geometry.get("element_positions_mm"):
            render_geometry_plots(baseline_geometry, designed_geometry)
        render_profile_plots(baseline, designed)
    candidates = state.get("evaluated_geometries")
    if isinstance(candidates, dict) and candidates:
        st.subheader("已评估候选方案排行榜")
        st.dataframe(_candidate_rows(candidates), width="stretch", hide_index=True)
    render_trajectory(result.get("trajectory", []))
    st.subheader("智能体科学总结")
    st.markdown(result.get("agent_final_response") or "Hermes 没有生成最终回复。")


def render_metasurface_result(
    result: dict[str, Any], *, key_prefix: str = "metasurface", show_governance: bool = True
) -> None:
    st.divider()
    if result.get("error_category"):
        st.error(
            f"{_category_text(result['error_category'], result.get('error_message'))}："
            f"{_error_message_text(result.get('error_message'))}"
        )
    else:
        st.success("0/1 透射超表面控制码、真实 MATLAB 证据及可用的 CST 布局产物已记录。")
    if show_governance:
        render_run_governance(result.get("governance"), heading="本次运行治理")
    state = result.get("metasurface_state") or {}
    selected = result.get("selected_design") or {}
    configuration = selected.get("configuration") or {}
    summary = st.columns(6)
    summary[0].metric("状态", _status_text(result.get("status", "UNKNOWN")))
    summary[1].metric("目标", _vector(state.get("focus_target_mm")))
    summary[2].metric("载波", f"{float(state.get('frequency_ghz', 0)):g} GHz")
    summary[3].metric("平面阵列", "×".join(str(v) for v in state.get("array_size", [])) or "无")
    summary[4].metric("入射波", "平面波" if state.get("incident_wave") == "plane_wave" else "喇叭球面波")
    summary[5].metric("MATLAB 进程", result.get("matlab_processes", 0))
    if selected and (
        designed_command := (selected.get("metrics") or {}).get("command_target_mm")
    ):
        st.caption(
            f"期望焦点始终为 {_vector(state.get('focus_target_mm'))}；"
            f"MATLAB 为补偿近场原始功率峰值偏移，内部相位合成命令点为 {_vector(designed_command)}。"
        )

    unprogrammed_record = result.get("unprogrammed_reference") or {}
    reference_record = result.get("continuous_reference") or {}
    baseline_record = result.get("geometrical_optics_baseline") or {}
    unprogrammed = unprogrammed_record.get("metrics") or {}
    reference = reference_record.get("metrics") or {}
    baseline = baseline_record.get("metrics") or {}
    designed = selected.get("metrics") or {}
    if all(isinstance(item, dict) and item for item in (unprogrammed, baseline, reference, designed)):
        st.subheader("未编程参考、几何光学基线、连续相位参考与优化结果")
        st.caption(
            "AI 优化前的正式基线是几何光学路径补偿量化得到的 0/1 控制码；"
            "未编程面只是附加参考，连续相位共轭只用于比较目标点功率。"
        )
        st.dataframe(
            _metasurface_comparison_rows(unprogrammed, baseline, reference, designed),
            width="stretch",
            hide_index=True,
        )
        before, after = st.columns(2)
        before.caption("AI 优化前：几何光学 0/1 聚焦基线")
        before.image(
            plane_png(
                _metasurface_field_payload(state, baseline), 0, "xz",
                dynamic_range_db=20,
            ),
            width="stretch",
        )
        after.caption("Hermes 选择优化设置、MATLAB 优化后：0/1 控制码")
        after.image(
            plane_png(
                _metasurface_field_payload(state, designed), 0, "xz",
                dynamic_range_db=20,
            ),
            width="stretch",
        )

        positions = designed.get("element_positions_mm")
        codes = designed.get("phase_codes")
        phases = designed.get("phase_deg")
        if all(isinstance(item, list) for item in (positions, codes, phases)):
            st.subheader("最终平面超表面 0/1 控制码")
            control = pd.DataFrame({
                "X／mm": [point[0] for point in positions],
                "Y／mm": [point[1] for point in positions],
                "相位码": [int(value) for value in codes],
                "相位／deg": [float(value) for value in phases],
            })
            left, right = st.columns([2, 1])
            left.caption("阵元空间相位分布")
            left.scatter_chart(
                control, x="X／mm", y="Y／mm", color="相位／deg", size=55
            )
            right.caption("相位状态使用数量")
            histogram = (
                control.groupby("相位码", as_index=False)
                .size()
                .rename(columns={"size": "阵元数"})
            )
            right.bar_chart(histogram.set_index("相位码"))
            with st.expander("查看并下载逐阵元控制表", expanded=False):
                st.dataframe(control, width="stretch", hide_index=True)
                st.download_button(
                    "导出控制码 CSV",
                    control.to_csv(index=False).encode("utf-8-sig"),
                    file_name="metasurface_control_codes.csv",
                    mime="text/csv",
                    width="stretch",
                    key=f"{key_prefix}_control_codes_csv_{state.get('metasurface_task_id', 'task')}",
                )

    st.subheader("本次方法卡片")
    st.dataframe(
        [
            {"部分": "Hermes 智能体", "职责": "理解任务、选择已注册优化器/迭代上限/旁瓣权重，依据 MATLAB 观测选择候选并诚实停止。"},
            {"部分": "几何光学基线", "职责": "补偿入射传播和单元到焦点的路径相位，再就近量化为用户给定的 0/1 透射状态。"},
            {"部分": "MATLAB 优化器", "职责": "以几何光学控制码为初值执行二进制坐标下降，计算标量近场和全部展示指标。"},
            {"部分": "验收接口", "职责": "当前仅记录指标；overall_pass 为空，等待研究者选择并版本化通过标准。"},
            {"部分": "CST 自动化", "职责": "MATLAB 通过 CST OLE 生成带状态标签的控制码布局；当前不是可求解的全波单元模型。"},
            {"部分": "模型边界", "职责": "尚未包含真实单元结构、材料、S 参数、互耦、带宽、制造误差及全波极化响应。"},
        ],
        width="stretch",
        hide_index=True,
    )
    candidates = state.get("evaluated_candidates")
    if isinstance(candidates, dict) and candidates:
        st.subheader("Hermes 已要求 MATLAB 评估的候选")
        st.dataframe(
            _metasurface_candidate_rows(candidates),
            width="stretch",
            hide_index=True,
        )
    characteristics = state.get("binary_state_characteristics")
    if isinstance(characteristics, dict):
        st.subheader("0/1 单元幅相与能量代理")
        st.json(characteristics, expanded=False)
    assessment = (selected.get("assessment") or {}) if isinstance(selected, dict) else {}
    if assessment:
        st.warning("自动验收规则待定：当前只记录各项指标，不宣称整体通过。")
        st.json(assessment, expanded=False)
    cst_model = result.get("cst_model") or state.get("cst_model")
    if isinstance(cst_model, dict):
        st.subheader("CST 建模产物")
        st.caption("该文件是 0/1 控制码布局骨架；补齐单元结构、材料、端口和边界后才能进行全波求解。")
        if cst_model.get("cst_launch_requested") and not cst_model.get("cst_launched"):
            st.warning(
                "CST 自动启动失败，但 VBA 布局已经保存，超表面计算结果未丢失。"
                f"错误：{cst_model.get('launch_error_message') or '未知 CST OLE 错误'}"
            )
        st.json(cst_model, expanded=False)
    render_trajectory(result.get("trajectory", []))
    st.subheader("智能体科学总结")
    st.markdown(result.get("agent_final_response") or "Hermes 没有生成最终回复。")


def render_geometry_plots(baseline: dict[str, Any], designed: dict[str, Any]) -> None:
    st.subheader("真实阵元坐标")
    first, second = baseline["element_positions_mm"], designed["element_positions_mm"]
    xy, xz, projected = st.columns(3)
    frames = []
    for positions, label in ((first, "基准阵列"), (second, "最终设计")):
        frames.extend({"X／mm": p[0], "Y／mm": p[1], "Z／mm": p[2], "几何方案": label} for p in positions)
    frame = pd.DataFrame(frames)
    xy.caption("XY 投影")
    xy.scatter_chart(frame, x="X／mm", y="Y／mm", color="几何方案", size=12)
    xz.caption("XZ 投影")
    xz.scatter_chart(frame, x="X／mm", y="Z／mm", color="几何方案", size=12)
    three = pd.DataFrame({
        "投影横坐标／mm": [p[0] - 0.5 * p[1] for p in second],
        "投影纵坐标／mm": [p[2] + 0.25 * (p[0] + p[1]) for p in second],
    })
    projected.caption("最终曲面阵列 · 三维正交投影")
    projected.scatter_chart(three, x="投影横坐标／mm", y="投影纵坐标／mm", size=12)


def render_profile_plots(baseline: dict[str, Any], designed: dict[str, Any]) -> None:
    if not all(isinstance(item.get(name), dict) for item in (baseline, designed) for name in ("lateral_profile", "axial_profile")):
        return
    st.subheader("真实 MATLAB 聚焦剖面")
    left, right = st.columns(2)
    lateral_frame = pd.DataFrame({
        "x_mm": baseline["lateral_profile"]["position_mm"],
        "基准阵列": baseline["lateral_profile"]["normalized_power"],
        "最终设计": designed["lateral_profile"]["normalized_power"],
        "半功率阈值": 0.5,
    }).set_index("x_mm")
    axial_frame = pd.DataFrame({
        "z_mm": baseline["axial_profile"]["position_mm"],
        "基准阵列": baseline["axial_profile"]["normalized_power"],
        "最终设计": designed["axial_profile"]["normalized_power"],
        "半功率阈值": 0.5,
    }).set_index("z_mm")
    left.caption("横向剖面（XZ 平面中的 X 方向）"); left.line_chart(lateral_frame)
    right.caption("轴向剖面／焦深（Z 方向）"); right.line_chart(axial_frame)


def render_focus_position_plot(iterations: list[dict[str, Any]]) -> None:
    points = []
    for iteration in iterations:
        number = iteration.get("Iteration")
        multi_fields = (
            ("Desired Targets", "期望目标"),
            ("Commanded Targets", "MATLAB 命令目标"),
            ("Actual Peaks", "实际峰值"),
        )
        has_multi = any(isinstance(iteration.get(key), list) for key, _ in multi_fields)
        if has_multi:
            for key, label in multi_fields:
                values = iteration.get(key)
                if not isinstance(values, list):
                    continue
                for user_index, value in enumerate(values, 1):
                    if isinstance(value, list) and len(value) == 3:
                        points.append({
                            "X／mm": value[0], "Z／mm": value[2],
                            "位置类型": f"用户 {user_index} · {label}", "迭代": number,
                        })
        else:
            for key, label in (
                ("Desired Target", "期望目标"),
                ("Commanded Target", "MATLAB 命令目标"),
                ("Actual Peak", "实际峰值"),
            ):
                value = iteration.get(key)
                if isinstance(value, list) and len(value) == 3:
                    points.append({"X／mm": value[0], "Z／mm": value[2], "位置类型": label, "迭代": number})
    if not points:
        st.info("该任务没有可绘制的完整仿真迭代。")
        return
    st.scatter_chart(pd.DataFrame(points), x="X／mm", y="Z／mm", color="位置类型", size=80)


def render_single_geometry(positions: list[list[float]]) -> None:
    frame = pd.DataFrame(positions, columns=["X／mm", "Y／mm", "Z／mm"])
    left, right = st.columns(2)
    left.caption("阵元 XY 投影")
    left.scatter_chart(frame, x="X／mm", y="Y／mm", size=14)
    right.caption("阵元 XZ 投影")
    right.scatter_chart(frame, x="X／mm", y="Z／mm", size=14)
    with st.expander(f"查看全部 {len(frame)} 个阵元坐标", expanded=False):
        coordinates = frame.copy()
        coordinates.insert(0, "阵元序号", range(1, len(frame) + 1))
        st.dataframe(coordinates, width="stretch", hide_index=True)


def _focus_option_label(task_id: str, tasks: list[dict[str, Any]]) -> str:
    task = next((item for item in tasks if item.get("agent_task_id") == task_id), {})
    return f"{task_id} ｜目标 {_vector(task.get('desired_target_mm'))} ｜{_status_text(task.get('status'))}"


def _design_option_label(design_id: str, designs: list[dict[str, Any]]) -> str:
    design = next((item for item in designs if item.get("design_task_id") == design_id), {})
    return f"{design_id} ｜目标 {_vector(design.get('focus_target_mm'))} ｜{_status_text(design.get('status'))}"


def _metasurface_option_label(
    task_id: str, designs: list[dict[str, Any]]
) -> str:
    design = next(
        (item for item in designs if item.get("metasurface_task_id") == task_id),
        {},
    )
    return (
        f"{task_id} ｜目标 {_vector(design.get('focus_target_mm'))} ｜"
        f"{_status_text(design.get('status'))}"
    )


def render_trajectory(trajectory: list[dict[str, Any]]) -> None:
    st.subheader("智能体工具调用轨迹")
    if not trajectory:
        st.info("尚未记录领域工具调用。")
    for index, call in enumerate(trajectory, 1):
        tool_name = str(call.get("tool_name"))
        with st.expander(f"{index}. {_tool_name(tool_name)}（{tool_name}）· {_tool_outcome(call.get('observation'))}"):
            left, right = st.columns(2)
            left.caption("调用参数")
            left.json(call.get("arguments", {}))
            right.caption("结构化返回值")
            right.json(call.get("observation") or {})


def render_recent_focus_tasks() -> None:
    st.divider(); st.subheader("最近的聚焦任务")
    recent = load_recent_tasks(PROJECT_ROOT)
    st.dataframe(_localize_rows(recent), width="stretch", hide_index=True) if recent else st.info("尚未找到已保存的聚焦任务。")


def render_recent_array_designs() -> None:
    st.divider(); st.subheader("最近的阵列设计")
    recent = load_recent_designs(PROJECT_ROOT)
    st.dataframe(_localize_rows(recent), width="stretch", hide_index=True) if recent else st.info("尚未找到已保存的阵列设计。")


def _comparison_rows(baseline: dict[str, Any], designed: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [("聚焦误差／mm", "focus_error_mm", False), ("X 向半功率宽度／mm", "fwhm_x_mm", False),
             ("Z 向焦深／mm", "dof_z_mm", False), ("能量集中度（XZ）", "energy_concentration_ratio", True),
             ("峰值功率／模型单位", "peak_power", True)]
    rows = []
    for label, key, higher in specs:
        base, current = float(baseline[key]), float(designed[key])
        change = 0.0 if base == 0 else 100 * ((current - base) / base if higher else (base - current) / base)
        rows.append({"指标": label, "基准阵列": base, "最终设计": current, "改善幅度／%": change})
    return rows


def _metasurface_comparison_rows(
    unprogrammed: dict[str, Any], baseline: dict[str, Any],
    reference: dict[str, Any], designed: dict[str, Any]
) -> list[dict[str, Any]]:
    specs = [
        ("定位误差／mm", "focus_error_mm", "越小越好"),
        ("目标点功率／模型单位", "target_power", "越大越好"),
        ("目标点／全局峰值／dB", "target_to_global_db", "越接近 0 越好"),
        ("X 向 FWHM／mm", "fwhm_x_mm", "越小越好"),
        ("Z 向 DOF／mm", "dof_z_mm", "越小越好"),
        ("XZ 能量集中度", "energy_concentration_ratio", "越大越好"),
        ("峰旁比／dB", "peak_to_sidelobe_ratio_db", "越大越好"),
    ]
    return [
        {
            "指标": label,
            "未编程参考": unprogrammed.get(key),
            "几何光学 0/1 基线": baseline.get(key),
            "连续相位参考": reference.get(key),
            "最终 0/1 优化设计": designed.get(key),
            "判读方向": direction,
        }
        for label, key, direction in specs
    ]


def _metasurface_candidate_rows(candidates: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for candidate_id, record in candidates.items():
        configuration = record.get("configuration") or {}
        metrics = record.get("metrics") or {}
        rows.append({
            "候选 ID": candidate_id,
            "优化器": configuration.get("optimizer"),
            "旁瓣权重": configuration.get("guard_weight"),
            "迭代上限": configuration.get("max_iterations"),
            "随机种子": configuration.get("seed"),
            "定位误差／mm": metrics.get("focus_error_mm"),
            "目标／全局峰值／dB": metrics.get("target_to_global_db"),
            "峰旁比／dB": metrics.get("peak_to_sidelobe_ratio_db"),
            "目标函数": record.get("objective_score"),
            "满足定位约束": record.get("hard_focus_constraint_satisfied"),
        })
    return sorted(rows, key=lambda row: (float(row["目标函数"] or 1e9), str(row["候选 ID"])))


def _metasurface_field_payload(
    state: dict[str, Any], metrics: dict[str, Any]
) -> dict[str, Any]:
    return {
        "x_mm": metrics["x_mm"],
        "z_mm": metrics["z_mm"],
        "normalized_power_by_user": metrics["normalized_power_xz"],
        "requested_focus_points_mm": [state["focus_target_mm"]],
        "actual_peak_points_mm": [metrics["actual_peak_mm"]],
        "user_harmonic_orders": [0],
        "user_count": 1,
    }


def _focus_baseline_comparison_rows(
    state: dict[str, Any], simulations: list[dict[str, Any]], user_index: int
) -> list[dict[str, Any]]:
    evaluations = {
        event.get("simulation_run_id"): event
        for event in state.get("history", [])
        if isinstance(event, dict) and event.get("event") == "evaluation"
    }
    baseline, final = simulations[0], simulations[-1]

    def value(simulation: dict[str, Any], key: str) -> float | None:
        candidate = _list_value(simulation.get(key), user_index)
        return candidate if isinstance(candidate, (int, float)) else None

    def focus_error(simulation: dict[str, Any]) -> float | None:
        evaluation = evaluations.get(simulation.get("simulation_run_id"), {})
        per_user = _list_value(evaluation.get("focus_errors_mm"), user_index)
        if isinstance(per_user, (int, float)):
            return float(per_user)
        aggregate = evaluation.get("focus_error_mm")
        return float(aggregate) if isinstance(aggregate, (int, float)) else None

    specs = [
        ("定位误差／mm", focus_error(baseline), focus_error(final), "越小越好"),
        ("X 向 FWHM／mm", value(baseline, "fwhm_x_mm_by_user"), value(final, "fwhm_x_mm_by_user"), "越小越好"),
        ("Z 向 DOF／mm", value(baseline, "dof_z_mm_by_user"), value(final, "dof_z_mm_by_user"), "越小越好"),
        ("峰旁比／dB", value(baseline, "peak_to_sidelobe_ratio_db_by_user"), value(final, "peak_to_sidelobe_ratio_db_by_user"), "越大越好"),
        ("峰值功率／模型单位", value(baseline, "peak_power_by_user"), value(final, "peak_power_by_user"), "越大越好"),
    ]
    rows: list[dict[str, Any]] = []
    for label, before, after, direction in specs:
        delta = None
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            delta = after - before
        rows.append({
            "指标": label,
            "Agent 修正前基线": before,
            "最终结果": after,
            "最终－基线": delta,
            "判读方向": direction,
        })
    return rows


def _candidate_rows(candidates: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for candidate in candidates.values():
        geometry, metrics = candidate.get("geometry") or {}, candidate.get("metrics") or {}
        rows.append({
            "几何方案 ID": geometry.get("geometry_id"),
            "几何族": geometry.get("family"),
            "参数": geometry.get("parameters"),
            "聚焦误差／mm": metrics.get("focus_error_mm"),
            "X 向半功率宽度／mm": metrics.get("fwhm_x_mm"),
            "Z 向焦深／mm": metrics.get("dof_z_mm"),
            "XZ 能量集中度": metrics.get("energy_concentration_ratio"),
            "目标函数评分": candidate.get("objective_score"),
            "满足硬约束": candidate.get("hard_constraint_satisfied"),
        })
    return sorted(rows, key=lambda row: (row["目标函数评分"], str(row["几何方案 ID"])))


def _vector(value: Any) -> str:
    return "无" if not isinstance(value, list) or len(value) != 3 else "[" + ", ".join(f"{float(item):g}" for item in value) + "] mm"


def _millimetres(value: Any) -> str:
    return "无" if not isinstance(value, (int, float)) else f"{float(value):.3f} mm"


def _list_value(value: Any, index: int) -> float | None:
    if isinstance(value, list) and 0 <= index < len(value):
        item = value[index]
        if isinstance(item, (int, float)):
            return float(item)
    return None


def _user_frequency_ghz(field_data: dict[str, Any], user_index: int) -> float:
    order = field_data["user_harmonic_orders"][user_index]
    plan_orders = list(field_data.get("harmonic_orders", []))
    frequencies = list(field_data.get("harmonic_frequencies_hz", []))
    try:
        return float(frequencies[plan_orders.index(order)]) / 1e9
    except (ValueError, IndexError, TypeError):
        return float("nan")


def _job_status_text(status: Any) -> str:
    return {
        "queued": "排队中", "running": "运行中", "pause_requested": "正在暂停",
        "paused": "已暂停", "resume_requested": "正在恢复", "cancel_requested": "正在取消",
        "completed": "已完成", "failed": "失败", "cancelled": "已取消",
    }.get(str(status), str(status))


def _matches_job_status_filter(job: dict[str, Any], selected: str) -> bool:
    status = str(job.get("status"))
    if selected == "全部状态":
        return True
    if selected == "排队中":
        return status == "queued"
    if selected == "运行／暂停":
        return status in ACTIVE_STATUSES | {"paused"}
    if selected == "已完成":
        return status == "completed"
    if selected == "失败／取消":
        return status in {"failed", "cancelled"}
    return True


def _tool_outcome(observation: Any) -> str:
    if not isinstance(observation, dict): return "等待结果"
    if observation.get("error") is True: return "错误"
    if observation.get("success") is False: return "未通过"
    return "已完成"


def _status_text(status: Any) -> str:
    return {
        "SUCCESS": "成功",
        "SAVED": "已保存",
        "FAILED": "失败",
        "CONSTRAINT NOT SATISFIED": "未满足科学约束",
        "UNKNOWN": "未知",
        "NOT EVALUATED": "尚未评估",
        "saved": "已保存",
        "created": "已创建",
        "baseline_evaluated": "已评估基准阵列",
        "candidate_evaluated": "已评估候选方案",
        "candidate_assessed": "候选指标已记录",
        "saved_pending_research_criteria": "已保存研究草案",
        "cst_layout_generated": "已生成 CST 布局脚本",
        "cst_layout_built": "已生成 CST 布局工程",
        "search_complete": "搜索完成",
        "focus_achieved": "聚焦成功",
        "refinement_exhausted": "修正预算已耗尽",
        "simulated": "仿真完成",
        "evaluation_failed": "评估未通过",
        "refined": "已生成修正命令",
    }.get(str(status), str(status))


def _category_text(category: Any, message: Any = None) -> str:
    if "TargetOutOfRange" in str(message):
        return "输入参数错误"
    return {
        "Agent Error": "智能体错误",
        "Infrastructure Error": "基础设施错误",
        "Input Error": "输入参数错误",
        "Scientific Failure": "科学约束未满足",
    }.get(str(category), str(category))


def _error_message_text(message: Any, fallback: str = "未知错误") -> str:
    text = str(message) if message not in {None, ""} else fallback
    return {
        "Hermes stopped while refinement budget remained.":
            "Hermes 在仍有修正预算时提前停止；请在任务中心点击“从断点恢复”。",
        "Hermes stopped before a terminal focus evaluation.":
            "Hermes 在得到终态聚焦评估前提前停止。",
    }.get(text, text)


def _tool_name(name: str) -> str:
    return {
        "create_focus_task": "创建聚焦任务",
        "get_focus_task_state": "读取聚焦任务检查点",
        "run_focus_simulation": "运行聚焦仿真",
        "evaluate_focus": "评估聚焦结果",
        "refine_focus": "修正聚焦命令",
        "create_array_design_task": "创建阵列设计任务",
        "evaluate_array_geometry": "评估阵列几何",
        "search_array_geometry": "搜索阵列几何",
        "save_array_design": "保存阵列设计",
        "create_metasurface_design_task": "创建可编程超表面设计任务",
        "evaluate_metasurface_baseline": "评估未编程、连续相位与几何光学基线",
        "optimize_metasurface_candidate": "优化 0/1 超表面控制码",
        "evaluate_metasurface_design": "记录超表面评估指标",
        "save_metasurface_design": "保存超表面设计与控制码",
        "build_metasurface_cst_model": "生成 CST 控制码布局模型",
        "em_focus_ping": "插件连通性检查",
    }.get(name, "工具调用")


def _localize_rows(rows: Any) -> Any:
    if not isinstance(rows, list):
        return rows
    translations = {
        "Iteration": "迭代次数", "Desired Target": "期望目标", "Commanded Target": "命令目标",
        "Actual Peak": "实际峰值", "Error / mm": "误差／mm", "Result": "结果",
        "User Harmonic Orders": "用户谐波阶次",
        "FWHM X / mm": "X 向 FWHM／mm", "DOF Z / mm": "Z 向 DOF／mm",
        "Local Peak / Max Sidelobe / dB": "目标局部峰／最大旁瓣／dB",
        "Simulation Run ID": "仿真运行 ID", "agent_task_id": "聚焦任务 ID",
        "original_task": "原始自然语言任务", "submission_mode": "提交方式",
        "desired_target_mm": "期望目标／mm", "status": "状态", "final_error_mm": "最终误差／mm",
        "replanning_count": "重新规划次数", "timestamp": "时间",
        "modulation_frequency_hz": "调制频率／Hz",
        "design_task_id": "设计任务 ID", "natural_language_request": "自然语言设计要求",
        "focus_target_mm": "聚焦目标／mm", "selected_family": "选定几何族",
        "objective_score": "目标函数评分",
    }
    localized = []
    for row in rows:
        if not isinstance(row, dict):
            localized.append(row)
            continue
        converted = {}
        for key, value in row.items():
            converted[translations.get(key, key)] = _status_text(value) if key in {"status", "Result"} else value
        localized.append(converted)
    return localized


def _job_model_label(job: dict[str, Any]) -> str:
    result = job.get("result")
    if isinstance(result, dict):
        governance = result.get("governance")
        if isinstance(governance, dict):
            llm = governance.get("llm")
            if isinstance(llm, dict) and llm.get("model"):
                return str(llm["model"])
    return str(job.get("model_override") or "Hermes 默认")


def _without_git_metadata(value: Any) -> Any:
    """Hide repository metadata from human-facing UI evidence."""

    cleaned = copy.deepcopy(value)

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            item.pop("git", None)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(cleaned)
    return cleaned


def _display(value: Any) -> str:
    return str(value) if value not in {None, ""} else "未提供"


def _short_hash(value: Any) -> str:
    return str(value)[:12] if isinstance(value, str) and value else "未提供"


def _integer_display(value: Any) -> str:
    return f"{int(value):,}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "未提供"


def _matlab_label(value: dict[str, Any]) -> str:
    release = value.get("release")
    version = value.get("version")
    architecture = value.get("architecture")
    if not release and not version:
        return "未运行／未提供"
    label = f"{release or version}"
    return f"{label} · {architecture}" if architecture else label


def _cost_label(llm: dict[str, Any]) -> str:
    actual = llm.get("actual_cost_usd")
    if isinstance(actual, (int, float)):
        return f"实际 ${float(actual):.6f} USD"
    if llm.get("billing_mode") == "subscription_included" or llm.get("cost_status") == "included":
        return "订阅包含；提供方未返回逐次实际费用"
    estimated = llm.get("estimated_cost_usd")
    if isinstance(estimated, (int, float)):
        return f"估算 ${float(estimated):.6f} USD（非实际账单）"
    return "提供方未返回，无法计算"


def _boolean_text(value: Any) -> str:
    return "一致" if value is True else "不一致" if value is False else "未判定"


if __name__ == "__main__":
    main()
