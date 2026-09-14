from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.run_governance import (
    build_submission_snapshot,
    compare_job_reproducibility,
    read_session_usage,
    usage_delta,
)


class RunGovernanceTests(unittest.TestCase):
    def test_submission_snapshot_has_versions_hashes_git_and_data_boundary(self) -> None:
        snapshot = build_submission_snapshot()
        self.assertEqual(snapshot["versions"]["governance_policy_version"], "1.0.0")
        self.assertEqual(len(snapshot["source_hashes"]["prompt_sha256"]), 64)
        self.assertIn("commit", snapshot["git"])
        self.assertEqual(snapshot["randomness"]["focus_seed"], 0)
        self.assertFalse(snapshot["external_data"]["full_field_sent_to_model"])

    def test_reads_accounting_without_reading_message_content_and_computes_turn_delta(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT, model_config TEXT, "
                    "system_prompt_hash TEXT, input_tokens INTEGER, output_tokens INTEGER, "
                    "cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, "
                    "billing_provider TEXT, billing_base_url TEXT, billing_mode TEXT, "
                    "estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, "
                    "cost_source TEXT, pricing_version TEXT, api_call_count INTEGER, profile_name TEXT)"
                )
                connection.execute(
                    "CREATE TABLE session_model_usage (session_id TEXT, model TEXT, "
                    "billing_provider TEXT, billing_base_url TEXT, billing_mode TEXT, task TEXT, "
                    "api_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER, "
                    "cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, "
                    "estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, "
                    "first_seen TEXT, last_seen TEXT)"
                )
                connection.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("session_1", "deepseek-chat", "{}", "hash", 120, 30, 20, 0, 4,
                     "provider", "https://example.invalid", "metered", .02, .018,
                     "actual", "provider", "v1", 3, "default"),
                )
                connection.execute(
                    "INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("session_1", "deepseek-chat", "provider", "https://example.invalid",
                     "metered", "chat", 3, 120, 30, 20, 0, 4, .02, .018,
                     "actual", "provider", "now", "now"),
                )
                connection.commit()
            after = read_session_usage(database, "session_1")
            self.assertEqual(after["model"], "deepseek-chat")
            before = dict(after)
            before.update(input_tokens=100, output_tokens=25, api_call_count=2,
                          estimated_cost_usd=.015, actual_cost_usd=.014)
            delta = usage_delta(before, after, new_session=False)
            self.assertEqual(delta["input_tokens"], 20)
            self.assertEqual(delta["output_tokens"], 5)
            self.assertEqual(delta["api_call_count"], 1)
            self.assertAlmostEqual(delta["actual_cost_usd"], .004)

    def test_repeat_comparison_separates_agent_and_matlab_consistency(self) -> None:
        result = {
            "status": "SUCCESS", "constraint_satisfied": True,
            "trajectory": [{
                "tool_name": "create_focus_task",
                "arguments": {"target_mm": [0, 0, 100], "agent_task_id": "generated"},
                "observation": {"success": True},
            }],
            "agent_state": {"history": [
                {"event": "simulation", "actual_peak_points_mm": [[0, 0, 100]],
                 "peak_power_by_user": [2.0], "fwhm_x_mm_by_user": [5.0],
                 "dof_z_mm_by_user": [10.0]},
                {"event": "evaluation", "focus_errors_mm": [0.0]},
            ]},
        }
        hashes = {"prompt_sha256": "same"}
        first = {"task_text": "same", "submission_governance": {"source_hashes": hashes}, "result": result}
        second_result = {**result, "trajectory": [{
            **result["trajectory"][0],
            "arguments": {"target_mm": [0, 0, 100], "agent_task_id": "other"},
        }]}
        second = {"task_text": "same", "submission_governance": {"source_hashes": hashes}, "result": second_result}
        comparison = compare_job_reproducibility(first, second)
        self.assertEqual(comparison["status"], "consistent")
        self.assertTrue(comparison["agent_trajectory_consistent"])
        self.assertTrue(comparison["matlab_numerically_consistent"])


if __name__ == "__main__":
    unittest.main()
