from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.view_models import (
    build_task_view,
    classify_outcome,
    load_array_design_record,
    load_focus_task_record,
    load_metasurface_design_record,
    load_recent_metasurface_designs,
    load_recent_tasks,
    parse_session_tool_calls,
    render_structured_task,
)


def call(call_id: str, name: str, arguments: dict, observation: dict) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "function": {
                    "name": "tool_call",
                    "arguments": json.dumps({"name": name, "arguments": arguments}),
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(observation),
        },
    ]


class UIViewModelTests(unittest.TestCase):
    def test_structured_mode_renders_explicit_constraints_for_hermes(self) -> None:
        rendered = render_structured_task([1, -2, 100], 0.25, 1)
        self.assertIn("x=1 mm", rendered)
        self.assertIn("y=-2 mm", rendered)
        self.assertIn("z=100 mm", rendered)
        self.assertIn("0.25 mm", rendered)
        self.assertIn("最多允许 1 次", rendered)

    def test_parser_exposes_tools_but_not_reasoning(self) -> None:
        session = {
            "messages": [{"role": "assistant", "reasoning": "hidden plan"}]
            + call("one", "create_focus_task", {
                "target_mm": [0, 0, 100],
                "tolerance_mm": 5,
                "max_refinements": 2,
            }, {"agent_task_id": "agent_one"})
        }
        trajectory = parse_session_tool_calls(session)
        self.assertEqual(len(trajectory), 1)
        self.assertEqual(trajectory[0]["tool_name"], "create_focus_task")
        self.assertNotIn("reasoning", trajectory[0])

    def test_success_view_combines_session_and_real_state_shape(self) -> None:
        messages: list[dict] = []
        messages += call("c", "create_focus_task", {
            "target_mm": [0, 0, 100], "tolerance_mm": 5, "max_refinements": 2,
        }, {"agent_task_id": "agent_success", "desired_target_mm": [0, 0, 100]})
        messages += call("r", "run_focus_simulation", {"agent_task_id": "agent_success"}, {
            "success": True, "agent_task_id": "agent_success", "simulation_run_id": "sim_one",
            "desired_target_mm": [0, 0, 100], "commanded_target_mm": [0, 0, 100],
            "actual_peak_mm": [0, 0, 97.7], "peak_power": 10,
        })
        messages += call("e", "evaluate_focus", {"agent_task_id": "agent_success"}, {
            "success": True, "agent_task_id": "agent_success", "simulation_run_id": "sim_one",
            "desired_target_mm": [0, 0, 100], "commanded_target_mm": [0, 0, 100],
            "actual_peak_mm": [0, 0, 97.7], "focus_error_mm": 2.3,
            "remaining_refinements": 2,
        })
        messages.append({"role": "assistant", "content": "真实结果满足 5 mm 要求。"})
        state = {
            "agent_task_id": "agent_success", "desired_target_mm": [0, 0, 100],
            "status": "focus_achieved", "refinement_count": 0,
            "history": [
                {"event": "simulation", "simulation_run_id": "sim_one",
                 "desired_target_mm": [0, 0, 100], "commanded_target_mm": [0, 0, 100],
                 "actual_peak_mm": [0, 0, 97.7]},
                {"event": "evaluation", "simulation_run_id": "sim_one",
                 "focus_error_mm": 2.3, "success": True},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_dir = root / "runs" / "agent_tasks" / "agent_success"
            task_dir.mkdir(parents=True)
            (task_dir / "agent_state.json").write_text(json.dumps(state), encoding="utf-8")
            view = build_task_view({"id": "session", "messages": messages}, root)
        self.assertEqual(view["status"], "SUCCESS")
        self.assertEqual(view["actual_peak_mm"], [0, 0, 97.7])
        self.assertEqual(view["iterations"][0]["Commanded Target"], [0, 0, 100])
        self.assertEqual(view["agent_final_response"], "真实结果满足 5 mm 要求。")

    def test_error_categories_are_distinct(self) -> None:
        infrastructure = [{
            "tool_name": "run_focus_simulation", "arguments": {},
            "observation": {"error": True, "error_type": "MatlabProcessError", "message": "crash"},
        }]
        self.assertEqual(classify_outcome(infrastructure, None, None)[0], "Infrastructure Error")
        invalid_target = [{
            "tool_name": "run_focus_simulation", "arguments": {},
            "observation": {
                "error": True,
                "error_type": "MatlabSimulationError",
                "message": (
                    "MATLAB simulation failed (hermes:TargetOutOfRange): "
                    "Every target must satisfy z in [300, 840] mm."
                ),
            },
        }]
        self.assertEqual(classify_outcome(invalid_target, None, None)[0], "Input Error")
        scientific_trajectory = [{
            "tool_name": "create_focus_task",
            "arguments": {"target_mm": [0, 0, 100], "tolerance_mm": 0.1, "max_refinements": 1},
            "observation": {"agent_task_id": "agent_x"},
        }, {
            "tool_name": "run_focus_simulation", "arguments": {"agent_task_id": "agent_x"},
            "observation": {"success": True},
        }, {
            "tool_name": "evaluate_focus", "arguments": {"agent_task_id": "agent_x"},
            "observation": {"success": False, "remaining_refinements": 0},
        }]
        category, _ = classify_outcome(
            scientific_trajectory, {"status": "refinement_exhausted"},
            scientific_trajectory[-1]["observation"],
        )
        self.assertEqual(category, "Scientific Failure")
        agent = [{
            "tool_name": "create_focus_task", "arguments": {"target_mm": [0, 0, 100]},
            "observation": {"agent_task_id": "agent_x"},
        }]
        self.assertEqual(classify_outcome(agent, None, None)[0], "Agent Error")

    def test_recovery_trajectory_continues_from_persisted_phase(self) -> None:
        trajectory = [{
            "tool_name": "get_focus_task_state", "arguments": {"agent_task_id": "agent_x"},
            "observation": {"agent_task_id": "agent_x", "status": "simulated"},
        }, {
            "tool_name": "evaluate_focus", "arguments": {"agent_task_id": "agent_x"},
            "observation": {"success": True, "remaining_refinements": 1},
        }]
        self.assertEqual(
            classify_outcome(trajectory, {"status": "focus_achieved"}, trajectory[-1]["observation"]),
            (None, None),
        )

    def test_early_stop_with_budget_has_actionable_chinese_message(self) -> None:
        trajectory = [{
            "tool_name": "create_focus_task",
            "arguments": {"target_mm": [0, 0, 100], "tolerance_mm": 5, "max_refinements": 2},
            "observation": {"agent_task_id": "agent_x"},
        }, {
            "tool_name": "run_focus_simulation",
            "arguments": {"agent_task_id": "agent_x"},
            "observation": {"success": True},
        }, {
            "tool_name": "evaluate_focus",
            "arguments": {"agent_task_id": "agent_x"},
            "observation": {"success": False, "remaining_refinements": 2},
        }]
        category, message = classify_outcome(
            trajectory,
            {"status": "evaluation_failed"},
            trajectory[-1]["observation"],
        )
        self.assertEqual(category, "Agent Error")
        self.assertIn("仍有修正预算时提前停止", message)
        self.assertIn("任务中心", message)

    def test_recovered_matlab_error_does_not_override_later_success(self) -> None:
        trajectory = [{
            "tool_name": "create_focus_task",
            "arguments": {"target_mm": [0, 0, 100], "tolerance_mm": 5, "max_refinements": 2},
            "observation": {"agent_task_id": "agent_x"},
        }, {
            "tool_name": "run_focus_simulation", "arguments": {"agent_task_id": "agent_x"},
            "observation": {"error": True, "error_type": "MatlabProcessError", "message": "shutdown"},
        }, {
            "tool_name": "run_focus_simulation", "arguments": {"agent_task_id": "agent_x"},
            "observation": {"success": True},
        }, {
            "tool_name": "evaluate_focus", "arguments": {"agent_task_id": "agent_x"},
            "observation": {"success": True, "remaining_refinements": 2},
        }]
        self.assertEqual(classify_outcome(trajectory, None, trajectory[-1]["observation"]), (None, None))

    def test_recent_tasks_reads_files_without_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "runs" / "agent_tasks" / "agent_recent"
            directory.mkdir(parents=True)
            state = {
                "agent_task_id": "agent_recent", "desired_target_mm": [0, 0, 80],
                "status": "focus_achieved", "refinement_count": 0,
                "created_at": "2026-01-01T00:00:00+00:00", "history": [],
            }
            (directory / "agent_state.json").write_text(json.dumps(state), encoding="utf-8")
            (directory / "ui_metadata.json").write_text(json.dumps({
                "original_task": "真实自然语言任务", "submission_mode": "natural_language",
            }), encoding="utf-8")
            recent = load_recent_tasks(root)
        self.assertEqual(recent[0]["original_task"], "真实自然语言任务")
        self.assertEqual(recent[0]["agent_task_id"], "agent_recent")

    def test_result_viewer_loaders_read_only_persisted_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            focus_dir = root / "runs" / "agent_tasks" / "agent_view"
            focus_dir.mkdir(parents=True)
            focus_state = {"agent_task_id": "agent_view", "history": []}
            (focus_dir / "agent_state.json").write_text(json.dumps(focus_state), encoding="utf-8")
            design_dir = root / "runs" / "array_designs" / "design_view"
            design_dir.mkdir(parents=True)
            design_state = {"design_task_id": "design_view", "selected_design": {}}
            (design_dir / "design_state.json").write_text(json.dumps(design_state), encoding="utf-8")
            metasurface_dir = root / "runs" / "metasurface_designs" / "metasurface_view"
            metasurface_dir.mkdir(parents=True)
            metasurface_state = {
                "metasurface_task_id": "metasurface_view",
                "selected_design": {"configuration": {"optimizer": "binary_coordinate_descent_v1"}},
            }
            (metasurface_dir / "design_state.json").write_text(
                json.dumps(metasurface_state), encoding="utf-8"
            )
            (metasurface_dir / "design_summary.json").write_text(
                json.dumps({
                    "metasurface_task_id": "metasurface_view",
                    "focus_target_mm": [0, 0, 100],
                    "array_size": [16, 16],
                    "optimizer": "binary_coordinate_descent_v1",
                    "status": "saved",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }),
                encoding="utf-8",
            )
            focus = load_focus_task_record(root, "agent_view")
            design = load_array_design_record(root, "design_view")
            metasurface = load_metasurface_design_record(root, "metasurface_view")
            recent_metasurfaces = load_recent_metasurface_designs(root)
        self.assertEqual(focus["state"]["agent_task_id"], "agent_view")
        self.assertEqual(design["design_task_id"], "design_view")
        self.assertEqual(metasurface["metasurface_task_id"], "metasurface_view")
        self.assertEqual(recent_metasurfaces[0]["array_size"], [16, 16])


if __name__ == "__main__":
    unittest.main()
