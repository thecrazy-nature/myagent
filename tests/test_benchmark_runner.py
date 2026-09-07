from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmark.runner import aggregate_results, parse_session_calls, score_task


def assistant_call(call_id: str, name: str, arguments: dict) -> dict:
    wrapped = {"name": name, "arguments": arguments}
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "function": {"name": "tool_call", "arguments": json.dumps(wrapped)},
        }],
    }


def tool_observation(call_id: str, value: dict) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(value),
    }


class BenchmarkRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = {
            "id": "D01",
            "type": "D",
            "name": "test",
            "prompt": "test prompt",
            "task_config": {
                "desired_target_mm": [0, 0, 100],
                "tolerance_mm": 0.1,
                "max_refinements": 1,
            },
            "expected_workflow": {
                "required_sequence": [
                    "create_focus_task", "run_focus_simulation", "evaluate_focus"
                ],
                "allow_refine": True,
            },
        }

    def test_parser_unwraps_tool_call_gateway(self) -> None:
        session = {
            "messages": [
                assistant_call("one", "create_focus_task", {"target_mm": [0, 0, 100]}),
                tool_observation("one", {"agent_task_id": "agent_x"}),
            ]
        }
        hermes_calls, domain_calls = parse_session_calls(session)
        self.assertEqual(len(hermes_calls), 1)
        self.assertEqual(domain_calls[0]["tool_name"], "create_focus_task")
        self.assertEqual(domain_calls[0]["observation"]["agent_task_id"], "agent_x")

    def test_type_d_truthful_exhaustion_is_task_success(self) -> None:
        agent_id = "agent_test"
        messages = [{"role": "user", "content": self.task["prompt"]}]
        calls = [
            ("c", "create_focus_task", {
                "target_mm": [0, 0, 100], "tolerance_mm": 0.1, "max_refinements": 1
            }, {
                "agent_task_id": agent_id,
                "desired_target_mm": [0.0, 0.0, 100.0],
            }),
            ("r1", "run_focus_simulation", {"agent_task_id": agent_id}, {
                "success": True, "agent_task_id": agent_id, "simulation_run_id": "sim_one",
                "actual_peak_mm": [0, 0, 94.2], "peak_power": 10.0,
                "matlab_runtime_sec": 1.0, "bridge_wall_time_sec": 60.0,
            }),
            ("e1", "evaluate_focus", {"agent_task_id": agent_id}, {
                "success": False, "focus_error_mm": 5.8, "remaining_refinements": 1,
            }),
            ("f", "refine_focus", {"agent_task_id": agent_id}, {
                "success": True, "remaining_refinements": 0,
            }),
            ("r2", "run_focus_simulation", {"agent_task_id": agent_id}, {
                "success": True, "agent_task_id": agent_id, "simulation_run_id": "sim_two",
                "actual_peak_mm": [0, 0, 97.7], "peak_power": 9.0,
                "matlab_runtime_sec": 1.0, "bridge_wall_time_sec": 60.0,
            }),
            ("e2", "evaluate_focus", {"agent_task_id": agent_id}, {
                "success": False, "focus_error_mm": 2.3, "remaining_refinements": 0,
            }),
        ]
        for call_id, name, arguments, observation in calls:
            messages.extend([
                assistant_call(call_id, name, arguments),
                tool_observation(call_id, observation),
            ])
        messages.append({"role": "assistant", "content": "修正预算已耗尽，最终未达到 0.1 mm 约束。"})
        session = {
            "id": "session_test", "messages": messages, "model": "test",
            "billing_provider": "test",
        }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs = root / "runs"
            runs.mkdir()
            for sim_id, actual, power in (
                ("sim_one", [0, 0, 94.2], 10.0),
                ("sim_two", [0, 0, 97.7], 9.0),
            ):
                directory = runs / sim_id
                directory.mkdir()
                (directory / "result.json").write_text(json.dumps({
                    "status": "success", "task_id": sim_id,
                    "actual_peak_mm": actual, "peak_power": power,
                }), encoding="utf-8")
            with patch("benchmark.runner.PROJECT_ROOT", root):
                result = score_task(self.task, session, 125.0)

        self.assertTrue(result["task_success"])
        self.assertTrue(result["workflow_success"])
        self.assertTrue(result["stop_correctness"])
        self.assertFalse(result["constraint_satisfied"])
        self.assertTrue(result["replanning_correctness"])
        self.assertEqual(result["actual_peak_mm"], [0, 0, 97.7])
        self.assertEqual(result["peak_power"], 9.0)

    def test_aggregate_keeps_task_and_constraint_rates_separate(self) -> None:
        results = [{
            "task_type": "D", "task_success": True, "constraint_satisfied": False,
            "workflow_success": True, "stop_correctness": True,
            "correct_tool_calls": 6, "tool_calls": 6,
            "replanning_opportunities": 1, "correct_replanning_decisions": 1,
            "final_focus_error_mm": 2.3, "initial_focus_error_mm": 5.8,
            "matlab_calls": 2, "replanning_count": 1,
            "end_to_end_runtime_sec": 120.0, "matlab_runtime_sec": 2.0,
        }]
        summary = aggregate_results(results)
        self.assertEqual(summary["task_success_rate"], 1.0)
        self.assertEqual(summary["constraint_satisfaction_rate"], 0.0)

    def test_type_a_does_not_require_an_explicit_success_word(self) -> None:
        task = {
            **self.task,
            "id": "A01",
            "type": "A",
            "task_config": {
                "desired_target_mm": [0, 0, 80],
                "tolerance_mm": 5.0,
                "max_refinements": 0,
            },
            "expected_workflow": {
                "required_sequence": [
                    "create_focus_task", "run_focus_simulation", "evaluate_focus"
                ],
                "allow_refine": False,
            },
        }
        messages = [{"role": "user", "content": task["prompt"]}]
        calls = [
            ("c", "create_focus_task", {
                "target_mm": [0, 0, 80], "tolerance_mm": 5.0, "max_refinements": 0
            }, {"agent_task_id": "agent_a"}),
            ("r", "run_focus_simulation", {"agent_task_id": "agent_a"}, {
                "success": True, "simulation_run_id": "sim_a",
                "actual_peak_mm": [0, 0, 76], "peak_power": 12.0,
                "matlab_runtime_sec": 1.0, "bridge_wall_time_sec": 60.0,
            }),
            ("e", "evaluate_focus", {"agent_task_id": "agent_a"}, {
                "success": True, "focus_error_mm": 4.0, "remaining_refinements": 0,
            }),
        ]
        for call_id, name, arguments, observation in calls:
            messages.extend([
                assistant_call(call_id, name, arguments),
                tool_observation(call_id, observation),
            ])
        messages.append({
            "role": "assistant",
            "content": "实际峰值 [0, 0, 76] mm，峰值功率 12，聚焦误差 4.0 mm。",
        })
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "runs" / "sim_a"
            directory.mkdir(parents=True)
            (directory / "result.json").write_text(json.dumps({
                "status": "success", "task_id": "sim_a",
                "actual_peak_mm": [0, 0, 76], "peak_power": 12.0,
            }), encoding="utf-8")
            with patch("benchmark.runner.PROJECT_ROOT", root):
                result = score_task(task, {"id": "a", "messages": messages}, 61.0)
        self.assertTrue(result["terminal_report_correct"])
        self.assertTrue(result["task_success"])

    def test_constraint_uses_frozen_tolerance_not_agent_default(self) -> None:
        task = self.task
        messages = [{"role": "user", "content": task["prompt"]}]
        calls = [
            ("c", "create_focus_task", {"target_mm": [0, 0, 100]}, {
                "agent_task_id": "agent_wrong", "tolerance_mm": 5.0,
            }),
            ("r", "run_focus_simulation", {"agent_task_id": "agent_wrong"}, {
                "success": True, "simulation_run_id": "sim_wrong",
                "actual_peak_mm": [0, 0, 97.7], "peak_power": 10.0,
                "matlab_runtime_sec": 1.0, "bridge_wall_time_sec": 60.0,
            }),
            ("e", "evaluate_focus", {"agent_task_id": "agent_wrong"}, {
                "success": True, "focus_error_mm": 2.3, "remaining_refinements": 2,
            }),
        ]
        for call_id, name, arguments, observation in calls:
            messages.extend([
                assistant_call(call_id, name, arguments),
                tool_observation(call_id, observation),
            ])
        messages.append({"role": "assistant", "content": "未达到 0.1 mm 约束。"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "runs" / "sim_wrong"
            directory.mkdir(parents=True)
            (directory / "result.json").write_text(json.dumps({
                "status": "success", "task_id": "sim_wrong",
                "actual_peak_mm": [0, 0, 97.7], "peak_power": 10.0,
            }), encoding="utf-8")
            with patch("benchmark.runner.PROJECT_ROOT", root):
                result = score_task(task, {"id": "wrong", "messages": messages}, 61.0)
        self.assertFalse(result["constraint_satisfied"])
        self.assertLess(result["tool_call_correctness"], 1.0)


if __name__ == "__main__":
    unittest.main()
