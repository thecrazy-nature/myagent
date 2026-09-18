from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.job_store import (
    claim_next_job,
    create_job,
    create_recovery_job,
    create_repeat_job,
    load_job,
    request_action,
)


class JobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "ui_jobs"
        self.patch = patch("app.job_store.JOBS_ROOT", self.root)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temporary.cleanup()

    def test_queue_pause_resume_cancel_are_persistent(self) -> None:
        job = create_job("运行一次聚焦", conversation_id="conversation_one")
        paused = request_action(job["job_id"], "pause")
        self.assertEqual(paused["status"], "paused")
        resumed = request_action(job["job_id"], "resume")
        self.assertEqual(resumed["status"], "queued")
        cancelled = request_action(job["job_id"], "cancel")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(load_job(job["job_id"])["notification_pending"])

    def test_worker_claims_oldest_job_and_running_controls_are_requests(self) -> None:
        first = create_job("first", conversation_id="conversation_one")
        create_job("second", conversation_id="conversation_one")
        claimed = claim_next_job(1234)
        self.assertEqual(claimed["job_id"], first["job_id"])
        self.assertEqual(claimed["worker_pid"], 1234)
        requested = request_action(first["job_id"], "pause")
        self.assertEqual(requested["status"], "pause_requested")
        self.assertEqual(requested["control"], "pause")

    def test_failed_job_creates_resume_checkpoint_request(self) -> None:
        job = create_job(
            "original",
            conversation_id="conversation_one",
            model_override="deepseek-v4-flash",
            provider_override="deepseek",
        )
        from app.job_store import mutate_job
        mutate_job(job["job_id"], lambda value: value.update(
            status="failed", hermes_session_id="session_1"
        ))
        recovery = create_recovery_job(job["job_id"])
        self.assertEqual(recovery["resume_session_id"], "session_1")
        self.assertEqual(recovery["recovery_of"], job["job_id"])
        self.assertIn("get_focus_task_state", recovery["task_text"])
        self.assertIn("remaining_refinements", recovery["task_text"])
        self.assertEqual(recovery["model_override"], "deepseek-v4-flash")
        self.assertEqual(recovery["provider_override"], "deepseek")

    def test_completed_job_can_repeat_in_a_fresh_conversation(self) -> None:
        job = create_job(
            "same scientific request",
            conversation_id="conversation_one",
            model_override="deepseek-v4-flash",
            provider_override="deepseek",
        )
        from app.job_store import mutate_job
        mutate_job(job["job_id"], lambda value: value.update(
            status="completed", result={"task_kind": "focus", "status": "SUCCESS"}
        ))
        repeated = create_repeat_job(job["job_id"])
        self.assertEqual(repeated["task_text"], job["task_text"])
        self.assertEqual(repeated["repeat_of"], job["job_id"])
        self.assertTrue(repeated["conversation_id"].startswith("repeat_"))
        self.assertIsNone(repeated["resume_session_id"])
        self.assertIn("source_hashes", repeated["submission_governance"])
        self.assertEqual(repeated["model_override"], "deepseek-v4-flash")
        self.assertEqual(
            repeated["submission_governance"]["llm_request"]["provider_override"],
            "deepseek",
        )


if __name__ == "__main__":
    unittest.main()
