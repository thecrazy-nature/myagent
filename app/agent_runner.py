"""Launch the verified Hermes runtime and observe its real Tool Calling session."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .view_models import (
    build_array_design_view,
    build_conversation_view,
    build_task_view,
    parse_session_tool_calls,
)
from .run_governance import (
    build_completion_governance,
    build_submission_snapshot,
    read_session_usage,
    usage_delta,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PROXY = "http://127.0.0.1:7897"
EXPECTED_NO_PROXY = "localhost,127.0.0.1,::1"


@dataclass(frozen=True)
class StatusUpdate:
    stage: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProxyCheck:
    available: bool
    message: str


class AgentRunnerError(RuntimeError):
    def __init__(
        self, category: str, message: str, *, session_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.category = category
        self.session_id = session_id


def check_proxy(timeout_sec: float = 1.0) -> ProxyCheck:
    expected = {
        "HTTP_PROXY": EXPECTED_PROXY,
        "HTTPS_PROXY": EXPECTED_PROXY,
        "NO_PROXY": EXPECTED_NO_PROXY,
    }
    wrong = [name for name, value in expected.items() if os.environ.get(name) != value]
    if wrong:
        return ProxyCheck(False, "当前进程缺少或错误设置了代理变量：" + ", ".join(wrong))
    try:
        with socket.create_connection(("127.0.0.1", 7897), timeout=timeout_sec):
            pass
    except OSError as exception:
        return ProxyCheck(False, f"无法连接 Clash 代理 127.0.0.1:7897：{exception}")
    return ProxyCheck(True, "Hermes 网络代理 127.0.0.1:7897 可用。")


def locate_hermes_python() -> Path:
    configured = os.environ.get("HERMES_PYTHON")
    candidates = [Path(configured)] if configured else []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise AgentRunnerError("Infrastructure Error", "未找到 Hermes Python 运行环境。")


def locate_hermes_home() -> Path:
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise AgentRunnerError("Infrastructure Error", "无法读取 LOCALAPPDATA 环境变量。")
    return (Path(local_app_data) / "hermes").resolve()


def build_hermes_command(
    hermes_python: Path,
    prompt_path: Path,
    source: str,
    run_budget_sec: int,
    resume_session_id: str | None = None,
) -> list[str]:
    command = [
        str(hermes_python),
        "-m",
        "hermes_cli.main",
        "chat",
        "--query-file",
        str(prompt_path),
        "--oneshot",
        "--quiet",
        "--toolsets",
        "em_focus",
        "--reasoning",
        "minimal",
        "--source",
        source,
        "--max-turns",
        "30",
        "--run-budget",
        str(run_budget_sec),
    ]
    if resume_session_id:
        command.extend(["--resume", resume_session_id])
    return command


def run_agent_task(
    task_text: str,
    *,
    submission_mode: str,
    on_status: Callable[[StatusUpdate], None] | None = None,
    run_budget_sec: int = 900,
    task_kind: str = "auto",
    resume_session_id: str | None = None,
    control: Callable[[subprocess.Popen[str], str | None, float], None] | None = None,
    submission_governance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit unparsed natural language to Hermes and return its observable result."""

    if not isinstance(task_text, str) or not task_text.strip():
        raise AgentRunnerError("Agent Error", "智能体任务文本不能为空。")
    proxy = check_proxy()
    if not proxy.available:
        raise AgentRunnerError(
            "Infrastructure Error",
            "Hermes 网络代理不可用。请启动 Clash，并运行 .\\proxy-on.ps1。"
            + proxy.message,
        )
    hermes_python = locate_hermes_python()
    hermes_home = locate_hermes_home()
    database_path = hermes_home / "state.db"
    if not database_path.is_file():
        raise AgentRunnerError("Infrastructure Error", f"缺少 Hermes 状态数据库：{database_path}")

    source = f"streamlit_ui_{uuid.uuid4().hex}"
    before = set(_source_session_ids(database_path, source))
    prior_message_count = 0
    session_id: str | None = None
    prior_usage: dict[str, Any] | None = None
    is_new_session = resume_session_id is None
    if resume_session_id is not None:
        if not isinstance(resume_session_id, str) or not resume_session_id.strip():
            raise AgentRunnerError("Agent Error", "Hermes 会话 ID 无效。")
        prior_session = _export_session(hermes_python, resume_session_id)
        prior_message_count = len(prior_session.get("messages", []))
        session_id = resume_session_id
        prior_usage = read_session_usage(database_path, resume_session_id)
    _emit(on_status, "initializing", "正在初始化 Hermes 智能体……")
    prompt_path = _write_prompt_file(task_text)
    environment = os.environ.copy()
    environment["HERMES_ENABLE_PROJECT_PLUGINS"] = "true"
    command = build_hermes_command(
        hermes_python, prompt_path, source, run_budget_sec, resume_session_id
    )
    started = time.perf_counter()
    process: subprocess.Popen[str] | None = None
    observed_calls: set[str] = set()
    observed_results: set[str] = set()
    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        _emit(
            on_status,
            "planning",
            "正在理解自然语言任务并规划工具调用……",
            {"agent_process_pid": process.pid},
        )
        deadline = time.monotonic() + run_budget_sec + 120
        while process.poll() is None:
            elapsed = time.perf_counter() - started
            if time.monotonic() > deadline:
                process.kill()
                process.wait(timeout=10)
                raise AgentRunnerError(
                    "Infrastructure Error",
                    "Hermes 智能体运行时间超过预算。",
                    session_id=session_id,
                )
            if session_id is None:
                session_id = _new_source_session(database_path, source, before)
                if session_id:
                    _emit(
                        on_status,
                        "planning",
                        "Hermes 会话已持久化。",
                        {"session_id": session_id, "agent_process_pid": process.pid},
                    )
            if control:
                control(process, session_id, elapsed)
            if session_id:
                live_session = _read_live_session(database_path, session_id)
                live_session["messages"] = live_session.get("messages", [])[prior_message_count:]
                _emit_observed_progress(
                    parse_session_tool_calls(live_session),
                    observed_calls,
                    observed_results,
                    on_status,
                )
            time.sleep(0.5)
        stdout, stderr = process.communicate(timeout=10)
    except AgentRunnerError:
        if process is not None and process.poll() is None:
            process.terminate()
        raise
    except OSError as exception:
        raise AgentRunnerError("Infrastructure Error", f"无法启动 Hermes：{exception}") from exception
    finally:
        prompt_path.unlink(missing_ok=True)

    elapsed = time.perf_counter() - started
    if session_id is None:
        for _ in range(20):
            session_id = _new_source_session(database_path, source, before)
            if session_id:
                break
            time.sleep(0.25)
    if not session_id:
        raise AgentRunnerError(
            "Infrastructure Error",
            f"Hermes 未保存会话。stderr={stderr.strip()}",
        )
    session = _export_session(hermes_python, session_id)
    turn_session = dict(session)
    turn_session["messages"] = session.get("messages", [])[prior_message_count:]
    resolved_task_kind = detect_task_kind(turn_session) if task_kind == "auto" else task_kind
    if resolved_task_kind == "array_design":
        view = build_array_design_view(turn_session, PROJECT_ROOT)
    elif resolved_task_kind == "focus":
        view = build_task_view(turn_session, PROJECT_ROOT)
    elif resolved_task_kind == "conversation":
        view = build_conversation_view(turn_session)
    else:
        raise AgentRunnerError("Agent Error", f"不支持的任务类型：{resolved_task_kind}")
    view.update(
        {
            "submitted_task": task_text,
            "submission_mode": submission_mode,
            "end_to_end_runtime_sec": elapsed,
            "process_returncode": process.returncode if process else None,
            "process_stderr": stderr,
            "task_kind": resolved_task_kind,
        }
    )
    current_usage = read_session_usage(database_path, session_id)
    accounting = usage_delta(prior_usage, current_usage, new_session=is_new_session)
    view["governance"] = build_completion_governance(
        submission_governance or build_submission_snapshot(PROJECT_ROOT),
        accounting,
        view,
    )
    if process and process.returncode != 0:
        view["status"] = "FAILED"
        view["error_category"] = "Infrastructure Error"
        view["error_message"] = f"Hermes 以代码 {process.returncode} 退出：{stderr.strip()}"
    _write_ui_metadata(view, source)
    succeeded = view["status"] in {"SUCCESS", "SAVED"}
    _emit(on_status, "completed", "任务已完成。" if succeeded else "任务已结束，并报告了失败结果。")
    return view


def detect_task_kind(session: dict[str, Any]) -> str:
    """Infer the workflow solely from Hermes' actual domain Tool Calls."""
    trajectory = parse_session_tool_calls(session)
    focus_tools = {
        "create_focus_task", "get_focus_task_state", "run_focus_simulation",
        "evaluate_focus", "refine_focus",
    }
    design_tools = {
        "create_array_design_task", "evaluate_array_geometry",
        "search_array_geometry", "save_array_design",
    }
    kinds = set()
    if any(call.get("tool_name") in focus_tools for call in trajectory):
        kinds.add("focus")
    if any(call.get("tool_name") in design_tools for call in trajectory):
        kinds.add("array_design")
    if len(kinds) == 1:
        return kinds.pop()
    if len(kinds) > 1:
        raise AgentRunnerError(
            "Agent Error",
            "Hermes 在一次请求中混用了聚焦和阵列设计工作流；两类数值任务必须保持独立。",
        )
    return "conversation"


def _emit_observed_progress(
    trajectory: list[dict[str, Any]],
    observed_calls: set[str],
    observed_results: set[str],
    callback: Callable[[StatusUpdate], None] | None,
) -> None:
    call_messages = {
        "create_focus_task": ("creating", "正在创建聚焦任务……"),
        "get_focus_task_state": ("recovering", "正在读取最后一个有效任务检查点……"),
        "run_focus_simulation": ("matlab", "正在运行 MATLAB 聚焦仿真……"),
        "evaluate_focus": ("evaluating", "正在评估 MATLAB 结构化结果……"),
        "refine_focus": ("replanning", "Hermes 已选择进行工作流级修正……"),
        "create_array_design_task": ("creating", "正在创建带物理约束的阵列设计任务……"),
        "evaluate_array_geometry": ("matlab", "正在使用真实 MATLAB 评估阵列几何……"),
        "search_array_geometry": ("searching", "正在一个 MATLAB 批次中评估确定性候选……"),
        "save_array_design": ("saving", "正在保存选定阵列和完整证据……"),
    }
    for call in trajectory:
        call_id = str(call.get("call_id"))
        tool_name = str(call.get("tool_name"))
        if call_id not in observed_calls:
            stage, message = call_messages[tool_name]
            _emit(callback, stage, message)
            observed_calls.add(call_id)
        observation = call.get("observation")
        if call_id in observed_results or not isinstance(observation, dict):
            continue
        observed_results.add(call_id)
        checkpoint_id = observation.get("agent_task_id")
        if isinstance(checkpoint_id, str):
            _emit(
                callback,
                "checkpoint",
                f"已记录可恢复检查点：{checkpoint_id}",
                {"agent_task_id": checkpoint_id},
            )
        if observation.get("error") is True:
            _emit(callback, "tool_error", f"工具 {tool_name} 返回错误。")
        elif tool_name == "run_focus_simulation":
            _emit(callback, "matlab_complete", "MATLAB 仿真已完成。")
        elif tool_name == "evaluate_focus":
            if observation.get("success") is True:
                _emit(callback, "evaluated", "评估通过，正在等待 Hermes 最终回复……")
            elif observation.get("remaining_refinements", 0) > 0:
                _emit(callback, "evaluated", "评估未通过，Hermes 正在决定是否修正……")
            else:
                _emit(callback, "evaluated", "评估未通过，且修正预算已经耗尽。")


def _read_live_session(database_path: Path, session_id: str) -> dict[str, Any]:
    with sqlite3.connect(database_path, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT role, content, tool_call_id, tool_calls, tool_name FROM messages "
            "WHERE session_id = ? AND active = 1 ORDER BY id",
            (session_id,),
        ).fetchall()
    messages: list[dict[str, Any]] = []
    for row in rows:
        try:
            tool_calls = json.loads(row["tool_calls"]) if row["tool_calls"] else None
        except json.JSONDecodeError:
            tool_calls = None
        messages.append(
            {
                "role": row["role"],
                "content": row["content"],
                "tool_call_id": row["tool_call_id"],
                "tool_calls": tool_calls,
                "tool_name": row["tool_name"],
            }
        )
    return {"id": session_id, "messages": messages}


def _source_session_ids(database_path: Path, source: str) -> list[str]:
    with sqlite3.connect(database_path, timeout=10) as connection:
        rows = connection.execute(
            "SELECT id FROM sessions WHERE source = ? ORDER BY started_at", (source,)
        ).fetchall()
    return [str(row[0]) for row in rows]


def _new_source_session(database_path: Path, source: str, before: set[str]) -> str | None:
    candidates = _source_session_ids(database_path, source)
    return next((session_id for session_id in reversed(candidates) if session_id not in before), None)


def _export_session(hermes_python: Path, session_id: str) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [
            str(hermes_python), "-m", "hermes_cli.main", "sessions", "export", "-",
            "--session-id", session_id, "--yes", "--redact",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise AgentRunnerError("Infrastructure Error", f"无法导出 Hermes 会话：{completed.stderr.strip()}")
    try:
        session = json.loads(completed.stdout)
    except json.JSONDecodeError as exception:
        raise AgentRunnerError("Infrastructure Error", f"Hermes 会话导出内容格式错误：{exception}") from exception
    if session.get("id") != session_id:
        raise AgentRunnerError("Infrastructure Error", "Hermes 导出了错误的会话。")
    return session


def _write_prompt_file(task_text: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".txt", prefix="hermes_ui_", delete=False
    )
    try:
        handle.write(task_text)
        return Path(handle.name)
    finally:
        handle.close()


def _write_ui_metadata(view: dict[str, Any], source: str) -> None:
    if view.get("task_kind") == "array_design":
        design_task_id = view.get("design_task_id")
        if isinstance(design_task_id, str):
            from array_design.state import attach_agent_metadata
            attach_agent_metadata(
                design_task_id,
                str(view.get("submitted_task", "")),
                str(view.get("session_id", "")),
                str(view.get("agent_final_response", "")),
                view.get("trajectory") if isinstance(view.get("trajectory"), list) else [],
                float(view["end_to_end_runtime_sec"]) if isinstance(view.get("end_to_end_runtime_sec"), (int, float)) else None,
                view.get("governance") if isinstance(view.get("governance"), dict) else None,
            )
        return
    agent_task_id = view.get("agent_task_id")
    if not isinstance(agent_task_id, str):
        return
    task_dir = PROJECT_ROOT / "runs" / "agent_tasks" / agent_task_id
    if not task_dir.is_dir():
        return
    path = task_dir / "ui_metadata.json"
    temporary = task_dir / f".ui_metadata.{uuid.uuid4().hex}.tmp"
    payload = {
        "schema_version": 2,
        "original_task": view.get("submitted_task"),
        "submission_mode": view.get("submission_mode"),
        "hermes_session_id": view.get("session_id"),
        "hermes_source": source,
        "end_to_end_runtime_sec": view.get("end_to_end_runtime_sec"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "governance": view.get("governance"),
    }
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _emit(
    callback: Callable[[StatusUpdate], None] | None,
    stage: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    if callback:
        callback(StatusUpdate(stage, message, details or {}))
