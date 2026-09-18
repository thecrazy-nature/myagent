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
        "orthogonal_plane_resolution": 101,
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
        self.assertEqual(loaded["user_harmonic_orders"], [0])
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
            simulated = run_task_simulation(agent_task_id)
        self.assertEqual(simulated["orthogonal_plane_resolution"], 101)
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

    def test_multi_user_evaluation_uses_worst_error_and_preserves_scenario(self) -> None:
        created = create_task(
            [0, 0, 100],
            tolerance_mm=5,
            additional_targets_mm=[[20, 0, 90]],
            frequency_ghz=39,
            element_count=144,
            polarization="rhcp",
        )
        agent_task_id = created["agent_task_id"]

        def fake_run(**kwargs: object) -> dict[str, object]:
            return {
                "status": "success",
                "task_id": kwargs["task_id"],
                "requested_focus_mm": [0, 0, 100],
                "actual_peak_mm": [0, 0, 98],
                "requested_focus_points_mm": [[0, 0, 100], [20, 0, 90]],
                "actual_peak_points_mm": [[0, 0, 98], [14, 0, 90]],
                "peak_power": 12.5,
                "peak_power_by_user": [12.5, 8.0],
                "peak_power_definition": "test power",
                "requested_power": 11.0,
                "requested_power_by_user": [11.0, 7.5],
                "runtime_sec": 0.75,
            }

        with patch("em_focus_agent.task_state.run_simulation", side_effect=fake_run) as run:
            result = run_task_simulation(agent_task_id)
        evaluated = evaluate_task(agent_task_id)
        self.assertEqual(run.call_args.kwargs["frequency_hz"], 39e9)
        self.assertEqual(result["user_count"], 2)
        self.assertEqual(evaluated["focus_errors_mm"], [2.0, 6.0])
        self.assertEqual(evaluated["focus_error_mm"], 6.0)
        self.assertFalse(evaluated["success"])
        refined = refine_task(agent_task_id)
        self.assertAlmostEqual(refined["new_command_targets_mm"][0][2], 101.4)
        self.assertAlmostEqual(refined["new_command_targets_mm"][1][0], 24.2)
        persisted = load_task(agent_task_id)
        self.assertEqual(persisted["desired_targets_mm"], [[0.0, 0.0, 100.0], [20.0, 0.0, 90.0]])
        self.assertEqual(persisted["polarization"], "rhcp")
        self.assertEqual(persisted["user_harmonic_orders"], [-1, 1])
        self.assertEqual(persisted["harmonic_frequencies_hz"], [38.8e9, 39e9, 39.2e9])

    def test_matlab_cannot_silently_collapse_multi_user_targets_to_q0(self) -> None:
        created = create_task([0, 0, 100], additional_targets_mm=[[20, 0, 90]])

        def fake_run(**kwargs: object) -> dict[str, object]:
            result = matlab_result(str(kwargs["task_id"]), [0.0, 0.0, 100.0])
            result.update({
                "requested_focus_points_mm": [[0, 0, 100], [20, 0, 90]],
                "actual_peak_points_mm": [[0, 0, 94.2], [20, 0, 90]],
                "peak_power_by_user": [12.5, 10.0],
                "requested_power_by_user": [11.0, 9.0],
                "user_harmonic_orders": [0, 0],
            })
            return result

        with patch("em_focus_agent.task_state.run_simulation", side_effect=fake_run):
            with self.assertRaisesRegex(AgentTaskError, "harmonic mapping"):
                run_task_simulation(created["agent_task_id"])


if __name__ == "__main__":
    unittest.main()
