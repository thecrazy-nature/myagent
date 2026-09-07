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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .view_models import build_array_design_view, build_task_view, parse_session_tool_calls


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PROXY = "http://127.0.0.1:7897"
EXPECTED_NO_PROXY = "localhost,127.0.0.1,::1"


@dataclass(frozen=True)
class StatusUpdate:
    stage: str
    message: str


@dataclass(frozen=True)
class ProxyCheck:
    available: bool
    message: str


class AgentRunnerError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def check_proxy(timeout_sec: float = 1.0) -> ProxyCheck:
    expected = {
        "HTTP_PROXY": EXPECTED_PROXY,
        "HTTPS_PROXY": EXPECTED_PROXY,
        "NO_PROXY": EXPECTED_NO_PROXY,
    }
    wrong = [name for name, value in expected.items() if os.environ.get(name) != value]
    if wrong:
        return ProxyCheck(False, "Missing or incorrect process proxy variables: " + ", ".join(wrong))
    try:
        with socket.create_connection(("127.0.0.1", 7897), timeout=timeout_sec):
            pass
    except OSError as exception:
        return ProxyCheck(False, f"Clash proxy 127.0.0.1:7897 is unreachable: {exception}")
    return ProxyCheck(True, "Hermes network proxy is available at 127.0.0.1:7897.")


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
    raise AgentRunnerError("Infrastructure Error", "Hermes Python runtime was not found.")


def locate_hermes_home() -> Path:
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise AgentRunnerError("Infrastructure Error", "LOCALAPPDATA is unavailable.")
    return (Path(local_app_data) / "hermes").resolve()


def build_hermes_command(
    hermes_python: Path,
    prompt_path: Path,
    source: str,
    run_budget_sec: int,
) -> list[str]:
    return [
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


def run_agent_task(
    task_text: str,
    *,
    submission_mode: str,
    on_status: Callable[[StatusUpdate], None] | None = None,
    run_budget_sec: int = 900,
    task_kind: str = "focus",
) -> dict[str, Any]:
    """Submit unparsed natural language to Hermes and return its observable result."""

    if not isinstance(task_text, str) or not task_text.strip():
        raise AgentRunnerError("Agent Error", "Agent task text cannot be empty.")
    proxy = check_proxy()
    if not proxy.available:
        raise AgentRunnerError(
            "Infrastructure Error",
            "Hermes network proxy is unavailable. Please start Clash and run .\\proxy-on.ps1. "
            + proxy.message,
        )
    hermes_python = locate_hermes_python()
    hermes_home = locate_hermes_home()
    database_path = hermes_home / "state.db"
    if not database_path.is_file():
        raise AgentRunnerError("Infrastructure Error", f"Hermes state database is missing: {database_path}")

    source = f"streamlit_ui_{uuid.uuid4().hex}"
    before = set(_source_session_ids(database_path, source))
    _emit(on_status, "initializing", "Initializing Hermes Agent...")
    prompt_path = _write_prompt_file(task_text)
    environment = os.environ.copy()
    environment["HERMES_ENABLE_PROJECT_PLUGINS"] = "true"
    command = build_hermes_command(hermes_python, prompt_path, source, run_budget_sec)
    started = time.perf_counter()
    process: subprocess.Popen[str] | None = None
    session_id: str | None = None
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
        _emit(on_status, "planning", "Planning task from the submitted natural language...")
        deadline = time.monotonic() + run_budget_sec + 120
        while process.poll() is None:
            if time.monotonic() > deadline:
                process.kill()
                process.wait(timeout=10)
                raise AgentRunnerError("Infrastructure Error", "Hermes Agent exceeded its run budget.")
            if session_id is None:
                session_id = _new_source_session(database_path, source, before)
            if session_id:
                live_session = _read_live_session(database_path, session_id)
                _emit_observed_progress(
                    parse_session_tool_calls(live_session),
                    observed_calls,
                    observed_results,
                    on_status,
                )
            time.sleep(0.5)
        stdout, stderr = process.communicate(timeout=10)
    except OSError as exception:
        raise AgentRunnerError("Infrastructure Error", f"Could not start Hermes: {exception}") from exception
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
            f"Hermes did not persist a session. stderr={stderr.strip()}",
        )
    session = _export_session(hermes_python, session_id)
    if task_kind == "array_design":
        view = build_array_design_view(session, PROJECT_ROOT)
    elif task_kind == "focus":
        view = build_task_view(session, PROJECT_ROOT)
    else:
        raise AgentRunnerError("Agent Error", f"Unsupported task_kind: {task_kind}")
    view.update(
        {
            "submitted_task": task_text,
            "submission_mode": submission_mode,
            "end_to_end_runtime_sec": elapsed,
            "process_returncode": process.returncode if process else None,
            "process_stderr": stderr,
            "task_kind": task_kind,
        }
    )
    if process and process.returncode != 0:
        view["status"] = "FAILED"
        view["error_category"] = "Infrastructure Error"
        view["error_message"] = f"Hermes exited with code {process.returncode}: {stderr.strip()}"
    _write_ui_metadata(view, source)
    _emit(on_status, "completed", "Completed." if view["status"] == "SUCCESS" else "Run finished with a reported failure.")
    return view


def _emit_observed_progress(
    trajectory: list[dict[str, Any]],
    observed_calls: set[str],
    observed_results: set[str],
    callback: Callable[[StatusUpdate], None] | None,
) -> None:
    call_messages = {
        "create_focus_task": ("creating", "Creating focusing task..."),
        "run_focus_simulation": ("matlab", "Running MATLAB simulation..."),
        "evaluate_focus": ("evaluating", "Evaluating structured MATLAB result..."),
        "refine_focus": ("replanning", "Hermes selected workflow-level replanning..."),
        "create_array_design_task": ("creating", "Creating constrained array-design task..."),
        "evaluate_array_geometry": ("matlab", "Evaluating geometry with real MATLAB..."),
        "search_array_geometry": ("searching", "Running deterministic candidates in one MATLAB batch..."),
        "save_array_design": ("saving", "Persisting the selected geometry and evidence..."),
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
        if observation.get("error") is True:
            _emit(callback, "tool_error", f"{tool_name} returned an error.")
        elif tool_name == "run_focus_simulation":
            _emit(callback, "matlab_complete", "MATLAB simulation completed.")
        elif tool_name == "evaluate_focus":
            if observation.get("success") is True:
                _emit(callback, "evaluated", "Evaluation passed; waiting for Hermes final response...")
            elif observation.get("remaining_refinements", 0) > 0:
                _emit(callback, "evaluated", "Evaluation failed; Hermes is deciding whether to replan...")
            else:
                _emit(callback, "evaluated", "Evaluation failed and refinement budget is exhausted.")


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
        raise AgentRunnerError("Infrastructure Error", f"Could not export Hermes session: {completed.stderr.strip()}")
    try:
        session = json.loads(completed.stdout)
    except json.JSONDecodeError as exception:
        raise AgentRunnerError("Infrastructure Error", f"Hermes session export is malformed: {exception}") from exception
    if session.get("id") != session_id:
        raise AgentRunnerError("Infrastructure Error", "Hermes exported the wrong session.")
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
        "schema_version": 1,
        "original_task": view.get("submitted_task"),
        "submission_mode": view.get("submission_mode"),
        "hermes_session_id": view.get("session_id"),
        "hermes_source": source,
        "end_to_end_runtime_sec": view.get("end_to_end_runtime_sec"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _emit(callback: Callable[[StatusUpdate], None] | None, stage: str, message: str) -> None:
    if callback:
        callback(StatusUpdate(stage, message))
