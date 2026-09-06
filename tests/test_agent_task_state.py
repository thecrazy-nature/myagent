from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from em_focus_agent import (
    AgentTaskError,
    create_task,
    evaluate_task,
    load_task,
    refine_task,
    run_task_simulation,
)


def matlab_result(task_id: str, commanded: list[float]) -> dict[str, object]:
    return {
        "status": "success",
        "task_id": task_id,
        "requested_focus_mm": commanded,
        "actual_peak_mm": [0.0, 0.0, 94.2],
        "peak_power": 12.5,
        "peak_power_definition": "test power",
        "requested_power": 11.0,
        "runtime_sec": 0.75,
    }


class AgentTaskStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.tasks_root = Path(self.temporary.name) / "agent_tasks"
        self.root_patch = patch(
            "em_focus_agent.task_state.AGENT_TASKS_ROOT", self.tasks_root
        )
        self.root_patch.start()

    def tearDown(self) -> None:
        self.root_patch.stop()
        self.temporary.cleanup()

    def test_create_persists_distinct_desired_and_command_targets(self) -> None:
        created = create_task([0, 0, 100])
        loaded = load_task(created["agent_task_id"])
        self.assertEqual(loaded["desired_target_mm"], [0.0, 0.0, 100.0])
        self.assertEqual(loaded["current_command_target_mm"], [0.0, 0.0, 100.0])
        state_path = (
            self.tasks_root / created["agent_task_id"] / "agent_state.json"
        )
        self.assertEqual(json.loads(state_path.read_text())["status"], "configured")

    def test_evaluate_uses_desired_target_not_command_target(self) -> None:
        created = create_task([0, 0, 100], tolerance_mm=5.0)
        agent_task_id = created["agent_task_id"]

        def fake_run(target_mm: list[float], task_id: str) -> dict[str, object]:
            return matlab_result(task_id, list(target_mm))

        with patch("em_focus_agent.task_state.run_simulation", side_effect=fake_run):
            run_task_simulation(agent_task_id)
        evaluated = evaluate_task(agent_task_id)
        self.assertAlmostEqual(evaluated["focus_error_mm"], 5.8)
        self.assertFalse(evaluated["success"])
        self.assertEqual(evaluated["desired_target_mm"], [0.0, 0.0, 100.0])

    def test_refine_changes_command_but_never_desired_target(self) -> None:
        created = create_task([0, 0, 100], max_refinements=2)
        agent_task_id = created["agent_task_id"]

        def fake_run(target_mm: list[float], task_id: str) -> dict[str, object]:
            return matlab_result(task_id, list(target_mm))

        with patch("em_focus_agent.task_state.run_simulation", side_effect=fake_run):
            run_task_simulation(agent_task_id)
        evaluate_task(agent_task_id)
        refined = refine_task(agent_task_id)
        persisted = load_task(agent_task_id)
        self.assertEqual(persisted["desired_target_mm"], [0.0, 0.0, 100.0])
        self.assertEqual(refined["new_command_target_mm"][:2], [0.0, 0.0])
        self.assertAlmostEqual(refined["new_command_target_mm"][2], 104.06)
        self.assertEqual(
            persisted["current_command_target_mm"],
            refined["new_command_target_mm"],
        )

    def test_invalid_state_transitions_are_rejected(self) -> None:
        created = create_task([0, 0, 100])
        with self.assertRaises(AgentTaskError):
            evaluate_task(created["agent_task_id"])
        with self.assertRaises(AgentTaskError):
            refine_task(created["agent_task_id"])

    def test_each_run_uses_a_unique_simulation_id(self) -> None:
        created = create_task([0, 0, 100])
        agent_task_id = created["agent_task_id"]

        def fake_run(target_mm: list[float], task_id: str) -> dict[str, object]:
            return matlab_result(task_id, list(target_mm))

        with patch("em_focus_agent.task_state.run_simulation", side_effect=fake_run):
            first = run_task_simulation(agent_task_id)
        evaluate_task(agent_task_id)
        refine_task(agent_task_id)
        with patch("em_focus_agent.task_state.run_simulation", side_effect=fake_run):
            second = run_task_simulation(agent_task_id)
        self.assertNotEqual(first["simulation_run_id"], second["simulation_run_id"])
        self.assertNotEqual(agent_task_id, first["simulation_run_id"])


if __name__ == "__main__":
    unittest.main()
