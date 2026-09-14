"""Streamlit UI for the focusing and autonomous array-design Agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

import pandas as pd
import streamlit as st

from app.agent_runner import AgentRunnerError, ProxyCheck, StatusUpdate, check_proxy, run_agent_task
from app.focus_artifacts import (
    artifact_paths,
    build_method_card,
    export_bundle,
    field_frame,
    field_png,
    load_field_data,
    profile_frames,
    report_markdown,
)
from app.job_store import (
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
    target_bounds_mm,
)
from app.view_models import (
    load_array_design_record,
    load_focus_task_record,
    load_recent_designs,
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
DEFAULT_AGENT_TASK = DEFAULT_FOCUS_TASK


@st.cache_data(ttl=30, show_spinner=False)
def _current_governance_snapshot() -> dict[str, Any]:
    return build_submission_snapshot(PROJECT_ROOT)


def render_current_governance() -> None:
    snapshot = _current_governance_snapshot()
    st.warning(
        "数据边界：提交消息后，自然语言、实验配置和紧凑 Tool 结果会发送到外部模型服务；"
        "完整二维场矩阵、MAT 文件、MATLAB 日志和认证凭据保留在本机。",
        icon="🔐",
    )
    with st.expander("运行治理与版本（提交时会固化到任务记录）", expanded=True):
        versions = snapshot.get("versions", {})
        git = snapshot.get("git", {})
        runtime = snapshot.get("runtime", {})
        columns = st.columns(5)
        columns[0].metric("应用版本", _display(versions.get("application_version")))
        columns[1].metric("Prompt 版本", _display(versions.get("prompt_contract_version")))
        columns[2].metric("Tool schema 版本", _display(versions.get("tool_schema_version")))
        columns[3].metric(
            "Git commit",
            f"{_display(git.get('short_commit'))}{' · dirty' if git.get('dirty') else ''}",
        )
        columns[4].metric("Hermes 版本", _display(runtime.get("hermes_version")))
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
    git = governance.get("git") or {}
    runtime = governance.get("runtime") or {}
    llm = governance.get("llm") or {}
    matlab = governance.get("matlab") or {}
    randomness = governance.get("randomness") or {}
    external = governance.get("external_data") or {}
    st.subheader(heading)
    first = st.columns(5)
    first[0].metric("实际模型 ID", _display(llm.get("model")))
    first[1].metric("模型 revision", _display(llm.get("model_revision")))
    first[2].metric("Hermes 版本", _display(runtime.get("hermes_version")))
    first[3].metric("Prompt / Tool", (
        f"{_display(versions.get('prompt_contract_version'))} / "
        f"{_display(versions.get('tool_schema_version'))}"
    ))
    first[4].metric(
        "Git commit",
        f"{_display(git.get('short_commit'))}{' · dirty' if git.get('dirty') else ''}",
    )
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
    if git.get("dirty"):
        st.info("该任务在未提交的工作树上运行；Git commit 与源码 SHA-256 已同时保存，不能只凭 commit 复现。")
    st.info(
        f"外部数据：{external.get('notice', '未记录')} "
        f"完整二维场是否发送：{'是' if external.get('full_field_sent_to_model') else '否'}。"
    )
    with st.expander("查看完整治理证据（不含消息正文和认证凭据）", expanded=False):
        st.json(governance)
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
    proxy = check_proxy()
    if proxy.available:
        st.success(proxy.message, icon="✅")
    else:
        st.error("Hermes 网络代理不可用。请启动 Clash 并运行 `.\\proxy-on.ps1`。\n\n" + proxy.message, icon="🚫")
    render_current_governance()
    agent_tab, jobs_tab, results_tab = st.tabs(["智能体对话", "任务中心", "结果可视化"])
    with agent_tab:
        render_agent_page(proxy)
    with jobs_tab:
        render_task_center()
    with results_tab:
        render_results_page()


def render_agent_page(proxy: ProxyCheck) -> None:
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
        "可以连续追问或修改要求；Hermes 会延续当前会话，并自主决定回答问题、运行聚焦，或进行阵列设计。"
    )
    render_prompt_guide()
    scenario = render_scenario_controls()

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
        st.session_state["chat_messages"].append({"role": "assistant", "content": response})
        st.session_state["latest_agent_result"] = result
        if isinstance(job.get("hermes_session_id"), str):
            st.session_state["hermes_session_id"] = job["hermes_session_id"]
        completed_jobs.add(job["job_id"])
    st.session_state["chat_completed_jobs"] = sorted(completed_jobs)
    for message in st.session_state["chat_messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input(
        "描述任务或继续追问，例如：按当前配置运行一次聚焦，并比较各用户误差",
        key="agent_chat_input",
        disabled=not proxy.available,
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
            )
            response = (
                f"已加入后台队列：`{job['job_id']}`。你可以继续添加任务，"
                "并在“任务中心”查看进度、暂停、恢复或取消；关闭浏览器不会中断后台任务。"
            )
            st.markdown(response)
        st.session_state["chat_messages"].append(
            {"role": "assistant", "content": response}
        )

    result = st.session_state.get("latest_agent_result")
    if result:
        submitted = result.get("submitted_task")
        if isinstance(submitted, str) and submitted:
            with st.expander("本轮实际提交给 Hermes 的完整上下文", expanded=False):
                st.code(submitted, language=None)
        if result.get("task_kind") == "array_design":
            st.info("Hermes 根据实际 Tool Call 将本次请求识别为：阵列几何设计。")
            render_design_result(result)
        elif result.get("task_kind") == "focus":
            st.info("Hermes 根据实际 Tool Call 将本次请求识别为：近场聚焦。")
            render_focus_result(result)
        elif result.get("task_kind") == "conversation":
            render_run_governance(result.get("governance"), heading="本轮对话运行治理")
        elif result.get("task_kind") != "conversation" and result.get("error_category"):
            st.error(f"{_category_text(result.get('error_category'))}：{result.get('error_message', '未能识别任务工作流。')}")


def render_scenario_controls() -> dict[str, Any]:
    """Collect supported solver choices without choosing the Agent workflow."""

    with st.expander("实验配置（用于聚焦任务）", expanded=True):
        first, modulation, second, third, fourth = st.columns(5)
        frequency_ghz = first.selectbox(
            "载波频率",
            [24.0, 28.0, 39.0],
            index=1,
            format_func=lambda value: f"{value:g} GHz",
            key="scenario_frequency",
        )
        modulation_frequency_mhz = modulation.selectbox(
            "调制频率",
            [100.0, 200.0, 500.0],
            index=1,
            format_func=lambda value: f"{value:g} MHz",
            key="scenario_modulation_frequency",
        )
        element_count = second.selectbox(
            "阵元数量",
            [64, 144, 256, 400],
            index=2,
            format_func=lambda value: f"{int(value ** 0.5)}×{int(value ** 0.5)}（{value}）",
            key="scenario_elements",
        )
        polarization = third.selectbox(
            "极化",
            list(POLARIZATION_LABELS),
            format_func=lambda value: POLARIZATION_LABELS[value],
            key="scenario_polarization",
        )
        user_count = int(
            fourth.number_input(
                "用户数量", min_value=1, max_value=4, value=1, step=1,
                key="scenario_user_count",
            )
        )
        planned_orders = assign_user_harmonics(user_count)
        planned_frequencies = [
            float(frequency_ghz) + order * float(modulation_frequency_mhz) / 1000
            for order in planned_orders
        ]
        st.info(
            "逐用户谐波计划："
            + "；".join(
                f"用户 {index + 1} → q={order}（{frequency:.4f} GHz）"
                for index, (order, frequency) in enumerate(
                    zip(planned_orders, planned_frequencies)
                )
            )
        )

        bounds = target_bounds_mm(frequency_ghz)
        st.caption(
            f"当前频率下可计算区域：X={bounds['x'][0]:.1f}～{bounds['x'][1]:.1f} mm，"
            f"Y=0 mm，Z={bounds['z'][0]:.1f}～{bounds['z'][1]:.1f} mm。"
        )
        if polarization != "scalar":
            st.warning(
                "当前 MATLAB 使用标量点源模型：极化选择会随实验保存，但不会改变场强和聚焦数值。"
            )

        st.markdown("**各用户目标点位**")
        defaults = [(0.0, 0.0, 100.0), (-45.0, 0.0, 100.0), (45.0, 0.0, 100.0), (0.0, 0.0, 62.5)]
        targets: list[list[float]] = []
        for index in range(user_count):
            x_column, y_column, z_column = st.columns(3)
            x = x_column.number_input(
                f"用户 {index + 1} · X / mm", value=defaults[index][0],
                key=f"scenario_user_{index + 1}_x",
            )
            y = y_column.number_input(
                f"用户 {index + 1} · Y / mm", value=0.0, disabled=True,
                key=f"scenario_user_{index + 1}_y",
            )
            z = z_column.number_input(
                f"用户 {index + 1} · Z / mm", value=defaults[index][2],
                key=f"scenario_user_{index + 1}_z",
            )
            targets.append([float(x), float(y), float(z)])

        constraint, budget = st.columns(2)
        tolerance_mm = constraint.number_input(
            "每个用户的最大定位误差 / mm", min_value=0.1, value=5.0, step=0.5,
            key="scenario_tolerance",
        )
        max_refinements = int(
            budget.number_input(
                "最多修正次数", min_value=0, max_value=5, value=2, step=1,
                key="scenario_refinements",
            )
        )
    return {
        "targets_mm": targets,
        "frequency_ghz": float(frequency_ghz),
        "modulation_frequency_mhz": float(modulation_frequency_mhz),
        "polarization": polarization,
        "element_count": int(element_count),
        "tolerance_mm": float(tolerance_mm),
        "max_refinements": max_refinements,
    }


def render_prompt_guide() -> None:
    with st.expander("能力说明与提问示例", expanded=False):
        st.markdown(
            "下面是当前 MATLAB 核心的真实能力边界。你可以直接描述科研目标，"
            "不需要记住 Tool 名称；若要求超出范围，智能体应明确报告，而不是擅自改参数。"
        )
        st.dataframe(
            [
                {"项目": "载波频率", "当前设置": "24 / 28 / 39 GHz；真实传入 MATLAB"},
                {"项目": "调制频率", "当前设置": "100 / 200 / 500 MHz；用于计算 fc+q·fm"},
                {"项目": "目标用户", "当前设置": "1～4 个界面目标；每位用户绑定不同谐波阶次 q"},
                {"项目": "阵元数量", "当前设置": "8×8 / 12×12 / 16×16 / 20×20 平面阵列"},
                {"项目": "极化", "当前设置": "可选择并保存；当前标量模型不计算极化差异"},
                {"项目": "聚焦激励", "当前设置": "逐谐波独立 axial-null 复权重；尚未投影为同一组硬件脉冲时序"},
                {"项目": "阵列几何设计", "当前设置": "仍使用冻结的 28 GHz / 256 阵元 baseline 公平约束"},
                {"项目": "场评估", "当前设置": "XZ 截面，201 × 201 网格"},
            ],
            width="stretch",
            hide_index=True,
        )
        st.markdown(
            "**可以这样说：**“按当前配置运行聚焦”、“把用户 2 改到另一点后重跑”、"
            "“解释上次失败原因”，或直接提出阵列几何优化要求。"
        )
        focus_example, design_example = st.columns(2)
        focus_example.caption("聚焦任务示例")
        focus_example.code(DEFAULT_FOCUS_TASK, language=None)
        design_example.caption("阵列几何设计示例")
        design_example.code(DEFAULT_DESIGN_TASK, language=None)


@st.fragment(run_every=2.0)
def render_task_center() -> None:
    """Auto-refresh the durable queue without restarting Agent or MATLAB work."""

    st.header("后台任务中心")
    st.caption(
        "任务由独立 worker 串行执行并写入 runs/ui_jobs；浏览器关闭后仍继续。"
        "剩余时间是依据历史任务时长得到的估计值，不是 MATLAB 的确定性进度。"
    )
    jobs = list_jobs(100)
    for job in jobs:
        if job.get("notification_pending"):
            outcome = "完成" if job.get("status") == "completed" else "结束"
            st.toast(f"任务 {job['job_id']} 已{outcome}：{job.get('message', '')}")
            mark_notification_seen(str(job["job_id"]))
    if not jobs:
        st.info("队列为空。请在“智能体对话”中提交任务。")
        return

    st.dataframe(
        [
            {
                "任务 ID": job.get("job_id"),
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
                or selected["result"].get("task_kind") not in {"focus", "array_design"}
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
            st.toast("后续消息将继续该 Hermes 会话。")
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
        else:
            render_run_governance(result.get("governance"), heading="本次运行治理")
            st.subheader("Hermes 回复")
            st.markdown(result.get("agent_final_response") or "没有可显示的回复。")
        render_reproducibility_comparison(selected.get("reproducibility_comparison"))
    if selected.get("error"):
        st.error(selected["error"].get("message", "任务失败。"))
    with st.expander("任务状态与控制历史", expanded=False):
        st.json({key: value for key, value in selected.items() if key != "result"})


def render_results_page() -> None:
    """Browse persisted results without invoking Hermes or MATLAB again."""
    st.header("历史结果可视化")
    st.caption("本页面只读取 runs 目录中的已保存结果，不会重新运行 Hermes 或 MATLAB。")
    result_type = st.radio(
        "结果类型",
        ["聚焦任务", "阵列设计"],
        horizontal=True,
        key="viewer_result_type",
    )
    if result_type == "聚焦任务":
        render_focus_result_viewer()
    else:
        render_array_design_viewer()


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
        status_box.update(label=_category_text(exception.category), state="error", expanded=True)
        return {
            "status": "FAILED",
            "task_kind": "unknown",
            "error_category": exception.category,
            "error_message": str(exception),
            "trajectory": [],
            "agent_final_response": "",
        }


def render_focus_result(
    result: dict[str, Any], *, show_field: bool = True, key_prefix: str = "latest"
) -> None:
    st.divider()
    if result.get("error_category"):
        st.error(f"{_category_text(result['error_category'])}：{result.get('error_message', '未知错误')}")
    else:
        st.success("已满足用户要求的科学约束。")
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

    full, local = st.columns(2)
    full.caption("真实 MATLAB 归一化功率热力图（全 XZ 区域）")
    _render_field_heatmap(full, field_frame(field_data, user_index))
    target = field_data["requested_focus_points_mm"][user_index]
    x_span = max(abs(float(value) - float(target[0])) for value in field_data["x_mm"])
    z_span = max(abs(float(value) - float(target[2])) for value in field_data["z_mm"])
    maximum_radius = max(2.0, min(x_span, z_span))
    radius = local.slider(
        "目标附近放大半径／mm", min_value=1.0, max_value=float(maximum_radius),
        value=float(min(20.0, maximum_radius)), step=1.0,
        key=f"{key_prefix}_local_radius_{state.get('agent_task_id', id(state))}_{iteration_index}_{user_index}",
    )
    local.caption("目标附近局部放大")
    _render_field_heatmap(local, field_frame(field_data, user_index, radius))

    lateral, axial = profile_frames(field_data, user_index)
    left, right = st.columns(2)
    left.caption("主瓣／旁瓣横向剖面（实际峰值 Z 截面）")
    left.line_chart(lateral.set_index("X／mm"))
    right.caption("轴向剖面与焦深（实际峰值 X 截面）")
    right.line_chart(axial.set_index("Z／mm"))

    if len(simulations) > 1:
        first_data = load_field_data(PROJECT_ROOT, simulations[0])
        last_data = load_field_data(PROJECT_ROOT, simulations[-1])
        if first_data and last_data and user_index < len(first_data.get("user_harmonic_orders", [])):
            st.markdown("#### 迭代前后场图")
            before, after = st.columns(2)
            before.caption("第一次 MATLAB 迭代")
            _render_field_heatmap(before, field_frame(first_data, user_index))
            after.caption("最后一次 MATLAB 迭代")
            _render_field_heatmap(after, field_frame(last_data, user_index))

    with st.expander("一键导出", expanded=False):
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


def _render_field_heatmap(container: Any, frame: pd.DataFrame) -> None:
    container.vega_lite_chart(
        frame,
        {
            "mark": {"type": "rect"},
            "encoding": {
                "x": {"field": "X／mm", "type": "quantitative", "title": "X / mm"},
                "y": {"field": "Z／mm", "type": "quantitative", "title": "Z / mm"},
                "color": {
                    "field": "相对功率／dB", "type": "quantitative", "title": "dB",
                    "scale": {"scheme": "turbo", "domain": [-30, 0]},
                },
                "tooltip": ["X／mm", "Z／mm", "相对功率／dB"],
            },
        },
        width="stretch",
    )


def render_design_result(result: dict[str, Any]) -> None:
    st.divider()
    if result.get("error_category"):
        st.error(f"{_category_text(result['error_category'])}：{result.get('error_message', '未知错误')}")
    else:
        st.success("最终阵列几何及其完整验证证据已保存。")
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
        "search_complete": "搜索完成",
        "focus_achieved": "聚焦成功",
        "refinement_exhausted": "修正预算已耗尽",
        "simulated": "仿真完成",
        "evaluation_failed": "评估未通过",
        "refined": "已生成修正命令",
    }.get(str(status), str(status))


def _category_text(category: Any) -> str:
    return {
        "Agent Error": "智能体错误",
        "Infrastructure Error": "基础设施错误",
        "Scientific Failure": "科学约束未满足",
    }.get(str(category), str(category))


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
