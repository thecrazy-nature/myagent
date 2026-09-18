from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from metasurface_design import state as metasurface_state
from metasurface_design.models import InvalidMetasurfaceInput, validate_target


def fake_result(candidate: dict, target_power: float) -> dict:
    mode = candidate["mode"]
    binary = mode not in {"continuous_phase_conjugate"}
    codes = [0, 1, 0, 1] if binary else []
    focused = mode != "unprogrammed"
    return {
        "status": "success", "candidate_id": candidate["candidate_id"],
        "mode": mode, "optimizer": candidate.get("optimizer") or "none",
        "guard_weight": candidate["guard_weight"],
        "max_iterations": candidate["max_iterations"],
        "iterations_used": candidate["max_iterations"], "seed": candidate["seed"],
        "phase_codes": codes, "phase_deg": [0.0, 180.0, 0.0, 180.0] if binary else [0.0] * 4,
        "transmission_amplitudes": [1.0] * 4,
        "element_positions_mm": [[-1, -1, 0], [1, -1, 0], [-1, 1, 0], [1, 1, 0]],
        "input_power_proxy": 4.0, "transmitted_power_proxy": 4.0,
        "transmission_efficiency_proxy": 1.0, "command_target_mm": [0, 0, 100],
        "actual_peak_mm": [0, 0, 100 if focused else 94],
        "focus_error_mm": 0.0 if focused else 6.0,
        "focus_constraint_satisfied": focused, "target_power": target_power,
        "peak_power": target_power, "global_peak_power": max(target_power, 10.0),
        "global_peak_mm": [0, 0, 100], "target_to_global_db": 0.0,
        "fwhm_x_mm": 8.0, "dof_z_mm": 18.0,
        "fwhm_bounded": True, "dof_bounded": True,
        "energy_concentration_ratio": 0.35 if mode == "optimized_binary" else (0.3 if focused else 0.1),
        "peak_to_sidelobe_ratio_db": 6.0 if mode == "optimized_binary" else (5.0 if focused else -2.0),
        "phase_error_rms_deg": 20.0, "field_plane_y_mm": 0.0,
        "x_mm": [-1, 1], "z_mm": [99, 101],
        "normalized_power_xz": [[0.2, 0.3], [0.4, 1.0]],
        "lateral_profile": {}, "axial_profile": {}, "matlab_runtime_sec": 0.1,
        "error_type": "", "message": "",
    }


class MetasurfaceDesignTests(unittest.TestCase):
    def test_target_and_binary_surface_validation(self) -> None:
        with self.assertRaisesRegex(InvalidMetasurfaceInput, "above"):
            validate_target([0, 0, -1], 28)
        target, frequency = validate_target([20, -10, 100], 28)
        self.assertEqual(target, [20.0, -10.0, 100.0])
        self.assertEqual(frequency, 28.0)

    def test_persists_go_baseline_assessment_codes_and_cst_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def fake_batch(run_dir, task_id, state, candidates, timeout_sec=600):
                powers = {
                    "unprogrammed": 5.0,
                    "continuous_phase_conjugate": 20.0,
                    "geometrical_optics_binary": 14.0,
                    "optimized_binary": 16.0,
                }
                return {
                    "status": "success", "batch_id": "meta_batch_test",
                    "matlab_version": "24.2", "matlab_release": "R2024b",
                    "matlab_arch": "win64", "rng_algorithm": "twister",
                    "runtime_sec": 0.2, "end_to_end_runtime_sec": 0.3,
                    "results": [fake_result(c, powers[c["mode"]]) for c in candidates],
                }

            fake_cst = {
                "status": "success", "build_id": "cst_build_test",
                "model_kind": "layout_scaffold", "cst_launched": False,
                "cst_launch_requested": True,
                "launch_error_type": "MATLAB:COM:servercreationfailed",
                "launch_error_message": "server execution failed",
                "cst_project_path": "metasurface_layout.cst",
                "vba_history_path": "metasurface_layout_history.vba", "cell_count": 4,
            }
            with patch.object(metasurface_state, "RUNS_ROOT", root), patch.object(
                metasurface_state, "evaluate_batch", side_effect=fake_batch
            ), patch.object(metasurface_state, "build_cst_project", return_value=fake_cst):
                task = metasurface_state.create_design_task(
                    [0, 0, 100], 5, 28, [5, 5, 1], [4, 4], "plane_wave",
                    [0, 0, -100], {
                        "state_0": {"amplitude": 1, "phase_deg": 0},
                        "state_1": {"amplitude": 1, "phase_deg": 180},
                    }, 2, {"focus_accuracy": 0.4, "energy_concentration": 0.35, "sidelobe_suppression": 0.25},
                )
                baseline = metasurface_state.evaluate_baseline(task["metasurface_task_id"])
                candidate = metasurface_state.optimize_candidate(
                    task["metasurface_task_id"], "binary_coordinate_descent_v1", 3, 0.4, 7
                )
                assessment = metasurface_state.evaluate_design(
                    task["metasurface_task_id"], candidate["candidate_id"]
                )
                saved = metasurface_state.save_design(
                    task["metasurface_task_id"], candidate["candidate_id"],
                    "Best observed binary candidate; acceptance criteria remain pending.",
                )
                cst = metasurface_state.build_cst_model(task["metasurface_task_id"], False)

            run_dir = root / task["metasurface_task_id"]
            state = json.loads((run_dir / "design_state.json").read_text(encoding="utf-8"))
            self.assertIn("geometrical_optics_baseline", baseline)
            self.assertEqual(state["selected_design"]["configuration"]["optimizer"], "binary_coordinate_descent_v1")
            self.assertEqual(candidate["phase_code_histogram"], {"0": 2, "1": 2})
            self.assertIsNone(assessment["overall_pass"])
            self.assertEqual(assessment["criteria_status"], "pending_researcher_decision")
            self.assertTrue((run_dir / "metasurface_control_codes.csv").is_file())
            self.assertTrue((run_dir / "design_summary.json").is_file())
            self.assertEqual(saved["validation_status"], "pending_researcher_decision")
            self.assertEqual(cst["scientific_status"], "layout_only_not_full_wave_validated")
            self.assertTrue(cst["workflow_continues_after_launch_failure"])


if __name__ == "__main__":
    unittest.main()
