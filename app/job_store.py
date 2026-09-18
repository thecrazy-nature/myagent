"""Small durable file-backed queue for long-running local Agent jobs."""

from __future__ import annotations

import json
import os
import re
import statistics
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from app.run_governance import build_submission_snapshot, compare_job_reproducibility


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOBS_ROOT = PROJECT_ROOT / "runs" / "ui_jobs"
JOB_ID_PATTERN = re.compile(r"^job_[0-9a-f]{32}$")
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {
    "running", "pause_requested", "paused", "resume_requested", "cancel_requested"
}


class JobStoreError(RuntimeError):
    pass


def create_job(
    task_text: str,
    *,
    conversation_id: str,
    submission_mode: str = "natural_language",
    resume_session_id: str | None = None,
    recovery_of: str | None = None,
    repeat_of: str | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
) -> dict[str, Any]:
    if not isinstance(task_text, str) or not task_text.strip():
        raise JobStoreError("任务文本不能为空。")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise JobStoreError("conversation_id 不能为空。")
    model_override = _optional_override(model_override, "model_override")
    provider_override = _optional_override(provider_override, "provider_override")
    now = _utc_now()
    submission_governance = build_submission_snapshot(PROJECT_ROOT)
    submission_governance["llm_request"] = {
        "model_override": model_override,
        "provider_override": provider_override,
    }
    job = {
        "schema_version": 3,
        "job_id": f"job_{uuid.uuid4().hex}",
        "conversation_id": conversation_id,
        "task_text": task_text,
        "submission_mode": submission_mode,
        "resume_session_id": resume_session_id,
        "recovery_of": recovery_of,
        "repeat_of": repeat_of,
        "model_override": model_override,
        "provider_override": provider_override,
        "submission_governance": submission_governance,
        "reproducibility_comparison": None,
        "status": "queued",
        "stage": "queued",
        "message": "任务已进入后台队列。",
        "control": None,
        "attempt": 0,
        "worker_pid": None,
        "agent_process_pid": None,
        "matlab_processes": [],
        "elapsed_sec": 0.0,
        "estimated_remaining_sec": _estimated_total_seconds(),
        "hermes_session_id": resume_session_id,
        "agent_task_id": None,
        "result": None,
        "error": None,
        "notification_pending": False,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "updated_at": now,
        "history": [{"timestamp": now, "status": "queued", "message": "任务已进入后台队列。"}],
    }
    with _store_lock():
        _write_job_unlocked(job)
    return job


def load_job(job_id: str) -> dict[str, Any]:
    path = _job_path(job_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exception:
        raise JobStoreError("任务不存在。") from exception
    except (OSError, UnicodeError, json.JSONDecodeError) as exception:
        raise JobStoreError(f"任务状态不可读：{exception}") from exception
    if not isinstance(value, dict) or value.get("job_id") != job_id:
        raise JobStoreError("任务状态文件无效。")
    return value


def list_jobs(limit: int = 100) -> list[dict[str, Any]]:
    if not JOBS_ROOT.is_dir():
        return []
    jobs: list[dict[str, Any]] = []
    for path in JOBS_ROOT.glob("job_*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and JOB_ID_PATTERN.fullmatch(str(value.get("job_id", ""))):
            jobs.append(value)
    return sorted(jobs, key=lambda item: str(item.get("created_at", "")), reverse=True)[:limit]


def mutate_job(job_id: str, mutation: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    with _store_lock():
        job = load_job(job_id)
        mutation(job)
        job["updated_at"] = _utc_now()
        _write_job_unlocked(job)
        return job


def claim_next_job(worker_pid: int) -> dict[str, Any] | None:
    with _store_lock():
        candidates = [job for job in list_jobs(1000) if job.get("status") == "queued"]
        if not candidates:
            return None
        job = min(candidates, key=lambda item: str(item.get("created_at", "")))
        now = _utc_now()
        job.update({
            "status": "running",
            "stage": "initializing",
            "message": "后台工作进程已领取任务。",
            "control": None,
            "worker_pid": int(worker_pid),
            "attempt": int(job.get("attempt", 0)) + 1,
            "started_at": job.get("started_at") or now,
            "updated_at": now,
        })
        job.setdefault("history", []).append({
            "timestamp": now, "status": "running", "message": job["message"]
        })
        _write_job_unlocked(job)
        return job


def request_action(job_id: str, action: str) -> dict[str, Any]:
    if action not in {"pause", "resume", "cancel"}:
        raise JobStoreError("不支持的任务控制操作。")

    def mutate(job: dict[str, Any]) -> None:
        status = str(job.get("status"))
        if status in TERMINAL_STATUSES:
            raise JobStoreError("已结束的任务不能执行该操作。")
        if action == "pause":
            if status == "queued":
                job.update(status="paused", stage="paused", message="任务已在队列中暂停。")
            elif status in {"running", "resume_requested"}:
                job.update(status="pause_requested", control="pause", message="正在安全暂停进程树……")
            else:
                raise JobStoreError("任务当前不能暂停。")
        elif action == "resume":
            if status != "paused":
                raise JobStoreError("只有已暂停任务可以恢复。")
            if job.get("worker_pid"):
                job.update(status="resume_requested", control="resume", message="正在恢复进程树……")
            else:
                job.update(status="queued", stage="queued", control=None, message="任务已重新进入队列。")
        else:
            if status in ACTIVE_STATUSES and job.get("worker_pid"):
                job.update(status="cancel_requested", control="cancel", message="正在取消任务……")
            else:
                job.update(
                    status="cancelled", stage="cancelled", control=None,
                    completed_at=_utc_now(), notification_pending=True,
                    message="任务已取消。",
                )
        job.setdefault("history", []).append({
            "timestamp": _utc_now(), "status": job["status"], "message": job["message"]
        })

    return mutate_job(job_id, mutate)


def create_recovery_job(job_id: str) -> dict[str, Any]:
    source = load_job(job_id)
    if source.get("status") not in {"failed", "cancelled"}:
        raise JobStoreError("只有失败或已取消任务可以恢复。")
    session_id = source.get("hermes_session_id")
    agent_task_id = source.get("agent_task_id")
    if not isinstance(session_id, str) and not isinstance(agent_task_id, str):
        return create_job(
            str(source.get("task_text", "")),
            conversation_id=str(source.get("conversation_id")),
            submission_mode="retry",
            recovery_of=job_id,
            model_override=source.get("model_override"),
            provider_override=source.get("provider_override"),
        )
    checkpoint_instruction = (
        f"待恢复的 agent_task_id 是 {agent_task_id!r}。"
        if isinstance(agent_task_id, str)
        else "请从本会话先前的 Tool observation 找到 agent_task_id。"
    )
    recovery_prompt = (
        "请从上次中断后最后一个已持久化的有效状态继续，不要重复已经成功完成的 MATLAB 实验。"
        + "先调用 get_focus_task_state 检查任务状态，再根据状态选择 run、evaluate、refine 或停止；"
        + checkpoint_instruction
        + "若状态为 evaluation_failed 且 remaining_refinements 大于 0，必须直接执行 refine、run、evaluate，"
        + "无需再次向用户确认；若没有可恢复的聚焦任务状态，则如实说明。\n\n原始请求：\n"
        + str(source.get("task_text", ""))
    )
    return create_job(
        recovery_prompt,
        conversation_id=str(source.get("conversation_id")),
        submission_mode="recovery",
        resume_session_id=session_id if isinstance(session_id, str) else None,
        recovery_of=job_id,
        model_override=source.get("model_override"),
        provider_override=source.get("provider_override"),
    )


def create_repeat_job(job_id: str) -> dict[str, Any]:
    """Queue the same request in a fresh conversation for an honest repeat check."""

    source = load_job(job_id)
    if source.get("status") != "completed" or not isinstance(source.get("result"), dict):
        raise JobStoreError("只有已完成任务可以发起一致性重复运行。")
    return create_job(
        str(source.get("task_text", "")),
        conversation_id=f"repeat_{uuid.uuid4().hex}",
        submission_mode="reproducibility_repeat",
        repeat_of=job_id,
        model_override=source.get("model_override"),
        provider_override=source.get("provider_override"),
    )


def update_reproducibility_comparison(job_id: str) -> dict[str, Any] | None:
    """Persist a comparison on both jobs once a repeat run has completed."""

    repeat = load_job(job_id)
    baseline_id = repeat.get("repeat_of")
    if not isinstance(baseline_id, str):
        return None
    baseline = load_job(baseline_id)
    comparison = {
        **compare_job_reproducibility(baseline, repeat),
        "baseline_job_id": baseline_id,
        "repeat_job_id": job_id,
    }
    mutate_job(job_id, lambda value: value.update(reproducibility_comparison=comparison))
    mutate_job(baseline_id, lambda value: value.update(reproducibility_comparison={
        **comparison, "repeat_job_id": job_id,
    }))
    return comparison


def latest_conversation_session(conversation_id: str, before_job_id: str) -> str | None:
    jobs = [
        job for job in list_jobs(1000)
        if job.get("conversation_id") == conversation_id
        and job.get("job_id") != before_job_id
        and isinstance(job.get("hermes_session_id"), str)
        and str(job.get("created_at", "")) < str(load_job(before_job_id).get("created_at", ""))
    ]
    if not jobs:
        return None
    latest = max(jobs, key=lambda item: str(item.get("created_at", "")))
    return str(latest["hermes_session_id"])


def mark_notification_seen(job_id: str) -> dict[str, Any]:
    return mutate_job(job_id, lambda job: job.update(notification_pending=False))


def _optional_override(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise JobStoreError(f"{name} 必须是字符串。")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 200 or any(ord(character) < 32 for character in cleaned):
        raise JobStoreError(f"{name} 无效。")
    return cleaned


def _estimated_total_seconds() -> float:
    values = []
    for job in list_jobs(50):
        result = job.get("result")
        if job.get("status") == "completed" and isinstance(result, dict):
            value = result.get("end_to_end_runtime_sec")
            if isinstance(value, (int, float)) and value > 0:
                values.append(float(value))
    return statistics.median(values) if values else 300.0


@contextmanager
def _store_lock() -> Iterator[None]:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    path = JOBS_ROOT / ".store.lock"
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _job_path(job_id: str) -> Path:
    if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
        raise JobStoreError("job_id 格式无效。")
    return JOBS_ROOT / f"{job_id}.json"


def _write_job_unlocked(job: dict[str, Any]) -> None:
    path = _job_path(str(job.get("job_id")))
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(job, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
