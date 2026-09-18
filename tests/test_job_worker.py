from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent_runner import StatusUpdate
from app.job_store import claim_next_job, create_job, load_job
from app.job_worker import _recover_stale_jobs, _run_one


class JobWorkerTests(unittest.TestCase):
    def test_queue_paused_before_claim_is_not_treated_as_worker_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ui_jobs"
            with patch("app.job_store.JOBS_ROOT", root):
                job = create_job("hello", conversation_id="conversation_one")
                from app.job_store import request_action
                request_action(job["job_id"], "pause")
                _recover_stale_jobs()
                self.assertEqual(load_job(job["job_id"])["status"], "paused")

    def test_completed_agent_result_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ui_jobs"
            with patch("app.job_store.JOBS_ROOT", root):
                job = create_job(
                    "hello",
                    conversation_id="conversation_one",
                    model_override="deepseek-v4-flash",
                    provider_override="deepseek",
                )
                claimed = claim_next_job(4321)
                invocation = {}

                def fake_run(*args, **kwargs):
                    invocation.update(kwargs)
                    kwargs["on_status"](StatusUpdate(
                        "checkpoint", "checkpoint saved",
                        {"session_id": "session_one", "agent_task_id": "agent_one"},
                    ))
                    return {
                        "status": "CONSTRAINT NOT SATISFIED",
                        "task_kind": "focus",
                        "session_id": "session_one",
                        "agent_task_id": "agent_one",
                        "end_to_end_runtime_sec": 12.0,
                    }

                with patch("app.job_worker.run_agent_task", side_effect=fake_run):
                    _run_one(claimed)
                stored = load_job(job["job_id"])
                self.assertEqual(stored["status"], "completed")
                self.assertEqual(stored["agent_task_id"], "agent_one")
                self.assertTrue(stored["notification_pending"])
                self.assertEqual(invocation["model"], "deepseek-v4-flash")
                self.assertEqual(invocation["provider"], "deepseek")


if __name__ == "__main__":
    unittest.main()
