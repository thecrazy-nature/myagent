"""Run the one authorized real Browser -> Streamlit -> Hermes -> MATLAB test."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from websockets.sync.client import connect

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent_runner import _export_session, locate_hermes_python
from app.view_models import build_task_view


EVIDENCE_PATH = PROJECT_ROOT / "runs" / "ui_e2e_result.json"
PORT = 8512
DEVTOOLS_PORT = 9223


def main() -> int:
    if "--recover" in sys.argv:
        return recover_existing_evidence()
    if EVIDENCE_PATH.exists():
        raise RuntimeError(
            f"Refusing to repeat the one-shot UI E2E test; evidence already exists: {EVIDENCE_PATH}"
        )
    edge = _edge_path()
    hermes_python = locate_hermes_python()
    started_epoch = time.time()
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "chain": "Browser -> Streamlit -> Hermes -> em_focus Tools -> Python Bridge -> MATLAB",
        "browser": str(edge),
        "streamlit_url": f"http://localhost:{PORT}",
        "browser_clicked_run_agent": False,
        "observed_status_messages": [],
    }
    server: subprocess.Popen[Any] | None = None
    browser: subprocess.Popen[Any] | None = None
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        server = subprocess.Popen(
            [
                str(hermes_python), "-m", "streamlit", "run", "app/streamlit_app.py",
                "--server.address", "localhost", "--server.port", str(PORT),
                "--server.headless", "true", "--browser.gatherUsageStats", "false",
            ],
            cwd=PROJECT_ROOT,
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        _wait_http(f"http://localhost:{PORT}/_stcore/health", 30)
        evidence["streamlit_health"] = "ok"
        with tempfile.TemporaryDirectory(
            prefix="hermes_ui_edge_", ignore_cleanup_errors=True
        ) as profile:
            browser = subprocess.Popen(
                [
                    str(edge), "--headless=new", f"--remote-debugging-port={DEVTOOLS_PORT}",
                    "--remote-allow-origins=*", f"--user-data-dir={profile}",
                    "--no-first-run", "--disable-default-apps", "--disable-extensions",
                    "--no-proxy-server", f"http://localhost:{PORT}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            websocket_url = _wait_devtools_page(30)
            with connect(websocket_url, open_timeout=10) as websocket:
                cdp = CDP(websocket)
                cdp.call("Runtime.enable")
                _wait_expression(
                    cdp,
                    "document.body && document.body.innerText.includes('运行智能体')",
                    timeout_sec=30,
                )
                initial_text = cdp.evaluate("document.body.innerText")
                evidence["natural_language_default_visible"] = (
                    "告诉智能体你想完成什么" in initial_text
                    and "当前模型参数与提示词写法" in initial_text
                )
                clicked = cdp.evaluate(
                    "(() => { const b=[...document.querySelectorAll('button')]"
                    ".find(x => x.innerText.includes('运行智能体'));"
                    "if (!b || b.disabled) return false; b.click(); return true; })()"
                )
                if clicked is not True:
                    raise RuntimeError("Browser could not click the enabled 运行智能体 button.")
                evidence["browser_clicked_run_agent"] = True
                _wait_for_ui_completion(cdp, evidence, timeout_sec=1020)
            _stop_process_tree(browser)
            browser = None

        metadata = _newest_ui_metadata(started_epoch)
        evidence["ui_metadata_path"] = str(metadata)
        metadata_payload = json.loads(metadata.read_text(encoding="utf-8-sig"))
        session_id = metadata_payload["hermes_session_id"]
        session = _export_session(hermes_python, session_id)
        result = build_task_view(session, PROJECT_ROOT)
        result["submitted_task"] = metadata_payload.get("original_task")
        result["submission_mode"] = metadata_payload.get("submission_mode")
        result["end_to_end_runtime_sec"] = metadata_payload.get("end_to_end_runtime_sec")
        evidence["result"] = result
        evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
        evidence["passed"] = bool(
            evidence["streamlit_health"] == "ok"
            and evidence["browser_clicked_run_agent"]
            and result.get("trajectory")
            and result.get("agent_final_response")
        )
        _write_evidence(evidence)
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0 if evidence["passed"] else 1
    except Exception as exception:
        evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
        evidence["passed"] = False
        evidence["error"] = f"{type(exception).__name__}: {exception}"
        _write_evidence(evidence)
        raise
    finally:
        _stop_process_tree(browser)
        _stop_process_tree(server)


def recover_existing_evidence() -> int:
    """Finish evidence collection after a post-run browser cleanup failure."""

    if not EVIDENCE_PATH.is_file():
        raise RuntimeError("No UI E2E evidence exists to recover.")
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8-sig"))
    metadata = max(
        (PROJECT_ROOT / "runs" / "agent_tasks").glob("agent_*/ui_metadata.json"),
        key=lambda path: path.stat().st_mtime,
    )
    metadata_payload = json.loads(metadata.read_text(encoding="utf-8-sig"))
    hermes_python = locate_hermes_python()
    session = _export_session(hermes_python, metadata_payload["hermes_session_id"])
    result = build_task_view(session, PROJECT_ROOT)
    result["submitted_task"] = metadata_payload.get("original_task")
    result["submission_mode"] = metadata_payload.get("submission_mode")
    result["end_to_end_runtime_sec"] = metadata_payload.get("end_to_end_runtime_sec")
    evidence["ui_metadata_path"] = str(metadata)
    evidence["result"] = result
    evidence["collector_cleanup_warning"] = evidence.pop("error", None)
    evidence["passed"] = bool(
        evidence.get("streamlit_health") == "ok"
        and evidence.get("browser_clicked_run_agent")
        and evidence.get("natural_language_default_visible")
        and result.get("trajectory")
        and result.get("agent_final_response")
    )
    _write_evidence(evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["passed"] else 1


class CDP:
    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket
        self.next_id = 1

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self.websocket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.websocket.recv(timeout=30))
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"CDP {method} failed: {message['error']}")
                return message.get("result", {})

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        remote = result.get("result", {})
        if "exceptionDetails" in result:
            raise RuntimeError(f"Browser expression failed: {result['exceptionDetails']}")
        return remote.get("value")


def _wait_for_ui_completion(cdp: CDP, evidence: dict[str, Any], timeout_sec: int) -> None:
    statuses = [
        "正在初始化 Hermes 智能体", "正在理解自然语言任务", "正在创建聚焦任务",
        "正在运行 MATLAB 聚焦仿真", "MATLAB 仿真已完成",
        "正在评估 MATLAB 结构化结果", "Hermes 已选择进行工作流级修正",
        "智能体总结",
    ]
    observed: set[str] = set()
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        body = cdp.evaluate("document.body.innerText") or ""
        for status in statuses:
            if status in body:
                observed.add(status)
        evidence["observed_status_messages"] = sorted(observed)
        if "智能体总结" in body and (
            "成功" in body or "未满足科学约束" in body or "基础设施错误" in body
        ):
            evidence["browser_final_page_excerpt"] = body[-4000:]
            return
        time.sleep(2)
    raise TimeoutError("The browser did not observe a terminal Agent Summary before timeout.")


def _wait_expression(cdp: CDP, expression: str, timeout_sec: int) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if cdp.evaluate(expression):
            return
        time.sleep(0.5)
    raise TimeoutError(f"Browser condition timed out: {expression}")


def _wait_http(url: str, timeout_sec: int) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"Service did not become healthy: {url}")


def _wait_devtools_page(timeout_sec: int) -> str:
    deadline = time.monotonic() + timeout_sec
    url = f"http://127.0.0.1:{DEVTOOLS_PORT}/json"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                targets = json.load(response)
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return str(target["webSocketDebuggerUrl"])
        except OSError:
            pass
        time.sleep(0.5)
    raise TimeoutError("Edge DevTools endpoint did not expose a page target.")


def _newest_ui_metadata(started_epoch: float) -> Path:
    candidates = [
        path for path in (PROJECT_ROOT / "runs" / "agent_tasks").glob("agent_*/ui_metadata.json")
        if path.stat().st_mtime >= started_epoch
    ]
    if not candidates:
        raise RuntimeError("The UI run did not persist ui_metadata.json.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _edge_path() -> Path:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("Microsoft Edge was not found for the browser E2E test.")


def _stop_process_tree(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        process.terminate()


def _write_evidence(evidence: dict[str, Any]) -> None:
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = EVIDENCE_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, EVIDENCE_PATH)


if __name__ == "__main__":
    raise SystemExit(main())
