"""Sequential background worker for durable Hermes/MATLAB jobs."""

from __future__ import annotations

import os
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psutil

from app.agent_runner import AgentRunnerError, StatusUpdate, run_agent_task
from app.job_store import (
    JOBS_ROOT,
    claim_next_job,
    latest_conversation_session,
    load_job,
    list_jobs,
    mutate_job,
    update_reproducibility_comparison,
)


class JobCancelled(AgentRunnerError):
    def __init__(self, session_id: str | None) -> None:
        super().__init__("Cancelled", "任务已由用户取消。", session_id=session_id)


def run_worker(poll_interval_sec: float = 1.0) -> None:
    """Run one queue consumer; a lifetime lock prevents duplicate workers."""
    with _worker_lock() as acquired:
        if not acquired:
            return
        _recover_stale_jobs()
        while True:
            job = claim_next_job(os.getpid())
            if job is None:
                time.sleep(poll_interval_sec)
                continue
            _run_one(job)


def _run_one(job: dict[str, Any]) -> None:
    job_id = str(job["job_id"])
    resume_session_id = job.get("resume_session_id")
    latest_session = latest_conversation_session(
        str(job.get("conversation_id")), job_id
    )
    if job.get("submission_mode") != "recovery" and latest_session:
        resume_session_id = latest_session
    elif not isinstance(resume_session_id, str):
        resume_session_id = latest_session
    expected_total = float(job.get("estimated_remaining_sec") or 300.0)
    last_heartbeat = 0.0

    def on_status(update: StatusUpdate) -> None:
        def mutate(current: dict[str, Any]) -> None:
            if current.get("status") not in {
                "running", "pause_requested", "resume_requested"
            }:
                return
            current["stage"] = update.stage
            current["message"] = update.message
            if "session_id" in update.details:
                current["hermes_session_id"] = update.details["session_id"]
            if "agent_process_pid" in update.details:
                current["agent_process_pid"] = update.details["agent_process_pid"]
            if "agent_task_id" in update.details:
                current["agent_task_id"] = update.details["agent_task_id"]
            current.setdefault("history", []).append({
                "timestamp": _utc_now(), "status": current["status"],
                "stage": update.stage, "message": update.message,
            })

        mutate_job(job_id, mutate)

    def control(process: Any, session_id: str | None, elapsed: float) -> None:
        nonlocal last_heartbeat
        now = time.monotonic()
        current = load_job(job_id)
        action = current.get("control")
        if action == "cancel" or current.get("status") == "cancel_requested":
            _terminate_tree(process.pid)
            raise JobCancelled(session_id)
        if action == "pause" or current.get("status") == "pause_requested":
            suspended = _suspend_tree(process.pid)

            def paused(value: dict[str, Any]) -> None:
                value.update(
                    status="paused", stage="paused", control=None,
                    message="Hermes/MATLAB 进程树已挂起，可安全恢复。",
                    elapsed_sec=float(elapsed),
                    hermes_session_id=session_id or value.get("hermes_session_id"),
                    matlab_processes=_matlab_children(process.pid),
                )
                value.setdefault("history", []).append({
                    "timestamp": _utc_now(), "status": "paused",
                    "message": value["message"],
                })

            mutate_job(job_id, paused)
            while True:
                time.sleep(0.5)
                current = load_job(job_id)
                action = current.get("control")
                if action == "cancel" or current.get("status") == "cancel_requested":
                    _resume_processes(suspended)
                    _terminate_tree(process.pid)
                    raise JobCancelled(session_id)
                if action == "resume" or current.get("status") == "resume_requested":
                    _resume_processes(suspended)

                    def resumed(value: dict[str, Any]) -> None:
                        value.update(
                            status="running", stage="resuming", control=None,
                            message="进程树已恢复，任务继续执行。",
                        )
                        value.setdefault("history", []).append({
                            "timestamp": _utc_now(), "status": "running",
                            "message": value["message"],
                        })

                    mutate_job(job_id, resumed)
                    break
        if now - last_heartbeat >= 2.0:
            last_heartbeat = now

            def heartbeat(value: dict[str, Any]) -> None:
                if value.get("status") != "running":
                    return
                value["elapsed_sec"] = float(elapsed)
                value["estimated_remaining_sec"] = max(0.0, expected_total - elapsed)
                value["hermes_session_id"] = session_id or value.get("hermes_session_id")
                value["agent_process_pid"] = process.pid
                value["matlab_processes"] = _matlab_children(process.pid)

            mutate_job(job_id, heartbeat)

    try:
        result = run_agent_task(
            str(job["task_text"]),
            submission_mode=str(job.get("submission_mode", "natural_language")),
            on_status=on_status,
            task_kind="auto",
            resume_session_id=resume_session_id,
            control=control,
            submission_governance=(
                job.get("submission_governance")
                if isinstance(job.get("submission_governance"), dict) else None
            ),
        )

        def completed(current: dict[str, Any]) -> None:
            status = (
                "completed"
                if result.get("status") in {"SUCCESS", "SAVED", "CONSTRAINT NOT SATISFIED"}
                else "failed"
            )
            current.update(
                status=status,
                stage=status,
                message=(
                    "工作流已完成；科学约束未满足。"
                    if result.get("status") == "CONSTRAINT NOT SATISFIED"
                    else "任务已完成。" if status == "completed"
                    else "任务执行失败。"
                ),
                result=result,
                error=None if status == "completed" else {
                    "category": result.get("error_category"),
                    "message": result.get("error_message"),
                },
                elapsed_sec=float(result.get("end_to_end_runtime_sec") or current.get("elapsed_sec") or 0),
                estimated_remaining_sec=0.0,
                hermes_session_id=result.get("session_id") or current.get("hermes_session_id"),
                agent_task_id=result.get("agent_task_id") or current.get("agent_task_id"),
                control=None,
                worker_pid=None,
                agent_process_pid=None,
                matlab_processes=[],
                completed_at=_utc_now(),
                notification_pending=True,
            )
            current.setdefault("history", []).append({
                "timestamp": _utc_now(), "status": status, "message": current["message"]
            })

        mutate_job(job_id, completed)
        if isinstance(job.get("repeat_of"), str):
            try:
                update_reproducibility_comparison(job_id)
            except Exception as exception:
                mutate_job(job_id, lambda value: value.setdefault("history", []).append({
                    "timestamp": _utc_now(), "status": value.get("status"),
                    "message": f"重复运行已完成，但一致性比较无法保存：{exception}",
                }))
    except JobCancelled as exception:
        _finish_error(job_id, "cancelled", exception, exception.session_id)
    except AgentRunnerError as exception:
        _finish_error(job_id, "failed", exception, exception.session_id)
    except Exception as exception:  # worker boundary: persist instead of dying
        _finish_error(job_id, "failed", exception, None, traceback.format_exc(limit=12))


def _recover_stale_jobs() -> None:
    """Turn jobs orphaned by a worker crash into explicit recoverable failures."""

    active = {
        "running", "pause_requested", "paused", "resume_requested", "cancel_requested"
    }
    for job in list_jobs(1000):
        if job.get("status") not in active:
            continue
        if job.get("status") == "paused" and not job.get("worker_pid"):
            # A task paused before being claimed is a valid durable queue state.
            continue
        agent_pid = job.get("agent_process_pid")
        process_note = "没有记录仍在运行的 Hermes 进程。"
        if isinstance(agent_pid, int) and _is_hermes_process(agent_pid):
            _terminate_tree(agent_pid)
            process_note = f"已终止上次 worker 遗留的 Hermes/MATLAB 进程树 PID {agent_pid}。"

        def stale(current: dict[str, Any], note: str = process_note) -> None:
            current.update(
                status="failed", stage="worker_recovery", control=None,
                worker_pid=None, agent_process_pid=None, matlab_processes=[],
                completed_at=_utc_now(), notification_pending=True,
                estimated_remaining_sec=0.0,
                message="检测到上次 worker 中断，可从最后有效检查点恢复。",
                error={"category": "WorkerInterrupted", "message": note},
            )
            current.setdefault("history", []).append({
                "timestamp": _utc_now(), "status": "failed",
                "message": current["message"] + note,
            })

        mutate_job(str(job["job_id"]), stale)


def _finish_error(
    job_id: str,
    status: str,
    exception: Exception,
    session_id: str | None,
    details: str | None = None,
) -> None:
    def mutate(current: dict[str, Any]) -> None:
        current.update(
            status=status,
            stage=status,
            message="任务已取消。" if status == "cancelled" else "任务执行失败，可从最后有效状态恢复。",
            error={
                "category": getattr(exception, "category", type(exception).__name__),
                "message": str(exception),
                "details": details,
            },
            hermes_session_id=session_id or current.get("hermes_session_id"),
            estimated_remaining_sec=0.0,
            control=None,
            worker_pid=None,
            agent_process_pid=None,
            matlab_processes=[],
            completed_at=_utc_now(),
            notification_pending=True,
        )
        current.setdefault("history", []).append({
            "timestamp": _utc_now(), "status": status, "message": current["message"]
        })

    mutate_job(job_id, mutate)


def _process_tree(root_pid: int) -> list[psutil.Process]:
    try:
        root = psutil.Process(root_pid)
        return [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def _suspend_tree(root_pid: int) -> list[psutil.Process]:
    processes = _process_tree(root_pid)
    if not processes:
        raise AgentRunnerError("Infrastructure Error", "无法定位可暂停的 Hermes 进程树。")
    suspended: list[psutil.Process] = []
    try:
        for process in reversed(processes):
            process.suspend()
            suspended.append(process)
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exception:
        _resume_processes(suspended)
        raise AgentRunnerError(
            "Infrastructure Error", f"进程树暂停失败：{exception}"
        ) from exception
    return processes


def _resume_processes(processes: list[psutil.Process]) -> None:
    for process in processes:
        try:
            process.resume()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _terminate_tree(root_pid: int) -> None:
    processes = _process_tree(root_pid)
    for process in reversed(processes):
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(processes, timeout=5)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _matlab_children(root_pid: int) -> list[dict[str, Any]]:
    result = []
    for process in _process_tree(root_pid)[1:]:
        try:
            name = process.name()
            if "matlab" in name.lower():
                result.append({"pid": process.pid, "name": name, "status": process.status()})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def _is_hermes_process(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        command = " ".join(process.cmdline()).lower()
        return "hermes_cli.main" in command and "chat" in command
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


@contextmanager
def _worker_lock() -> Iterator[bool]:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    path = JOBS_ROOT / ".worker.lock"
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    pass
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    pass
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    try:
        run_worker()
    except KeyboardInterrupt:
        sys.exit(0)
