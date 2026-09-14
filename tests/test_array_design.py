from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from array_design.geometry import check_geometry_constraints, generate_geometry
from array_design.metrics import INVALID_SCORE, half_power_width, score_metrics, xz_energy_concentration
from array_design.models import BASELINE_APERTURE_X_MM, BASELINE_SPACING_MM, ELEMENT_COUNT
from array_design import state as design_state


def fake_metrics(geometry_id: str, *, fwhm: float = 10.0, dof: float = 20.0, energy: float = 0.25):
    return {
        "status": "success", "geometry_id": geometry_id, "actual_peak_mm": [0, 0, 100],
        "focus_error_mm": 0.0, "fwhm_x_mm": fwhm, "dof_z_mm": dof,
        "energy_concentration_ratio": energy, "peak_power": 2.0,
        "matlab_runtime_sec": 1.0, "geometry_constraint_satisfied": True,
        "focus_constraint_satisfied": True, "constraint_satisfied": True,
        "lateral_profile": {}, "axial_profile": {},
    }


class GeometryTests(unittest.TestCase):
    def test_baseline_is_frozen_and_valid(self):
        geometry = generate_geometry("baseline")
        measured = geometry["constraint_check"]["measured"]
        self.assertEqual(len(geometry["element_positions_mm"]), ELEMENT_COUNT)
        self.assertAlmostEqual(measured["aperture_x_mm"], BASELINE_APERTURE_X_MM)
        self.assertAlmostEqual(measured["minimum_spacing_mm"], BASELINE_SPACING_MM)
        self.assertTrue(geometry["constraint_check"]["valid"])

    def test_spherical_cap_is_deterministic_and_valid(self):
        first = generate_geometry("spherical_cap", {"depth_mm": 10}, seed=7)
        second = generate_geometry("spherical_cap", {"depth_mm": 10}, seed=7)
        self.assertEqual(first, second)
        self.assertTrue(first["constraint_check"]["valid"])
        self.assertGreater(first["constraint_check"]["measured"]["surface_depth_mm"], 0)

    def test_spacing_violation_is_rejected(self):
        geometry = generate_geometry("baseline")
        geometry["element_positions_mm"][1] = list(geometry["element_positions_mm"][0])
        self.assertFalse(check_geometry_constraints(geometry)["valid"])


class MetricTests(unittest.TestCase):
    def test_fwhm_and_dof_exact_half_power_interpolation(self):
        coordinates = [-2, -1, 0, 1, 2]
        power = [0.0, 0.5, 1.0, 0.5, 0.0]
        self.assertAlmostEqual(half_power_width(coordinates, power, 2), 2.0)

    def test_energy_concentration_uses_xz_area(self):
        x = [-1, 0, 1]
        z = [-1, 0, 1]
        power = [[1.0] * 3 for _ in range(3)]
        self.assertAlmostEqual(xz_energy_concentration(power, x, z, 0, 0, 1, 1), 1.0)

    def test_score_and_hard_focus_penalty(self):
        baseline = fake_metrics("base")
        candidate = fake_metrics("candidate", fwhm=8, dof=18, energy=0.30)
        weights = {"lateral_spot": 0.45, "depth_of_focus": 0.35, "energy_concentration": 0.20}
        result = score_metrics(candidate, baseline, weights, 5)
        self.assertTrue(result["hard_constraint_satisfied"])
        self.assertLess(result["objective_score"], score_metrics(baseline, baseline, weights, 5)["objective_score"])
        candidate["focus_error_mm"] = 6
        self.assertEqual(score_metrics(candidate, baseline, weights, 5)["objective_score"], INVALID_SCORE)


class StateTests(unittest.TestCase):
    def test_persists_baseline_search_and_selected_coordinates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(design_state, "RUNS_ROOT", root):
                task = design_state.create_design_task([0, 0, 100], 5, 2, ["spherical_cap"])

                def batch(run_dir, task_id, target, tolerance, roi_radius, roi_depth, geometries, timeout_sec=600):
                    return {"status": "success", "batch_id": "batch_test", "runtime_sec": 1.0,
                            "end_to_end_runtime_sec": 1.1,
                            "matlab_version": "24.2", "matlab_release": "R2024b",
                            "matlab_arch": "win64", "random_seed": 0,
                            "rng_algorithm": "twister",
                            "results": [fake_metrics(item["geometry_id"], fwhm=9.0 if item["family"] == "spherical_cap" else 10.0)
                                        for item in geometries]}

                with patch.object(design_state, "evaluate_batch", side_effect=batch), patch("array_design.search.evaluate_batch", side_effect=batch):
                    baseline = design_state.evaluate_geometry(task["design_task_id"], "baseline", {}, 0)
                    searched = design_state.search_geometry(task["design_task_id"], "spherical_cap", {"depth_mm": [5, 10]}, 2, 0)
                    selected_id = searched["candidates"][0]["geometry"]["geometry_id"]
                    design_state.save_design(task["design_task_id"], selected_id, "Lowest valid score.")
                run_dir = root / task["design_task_id"]
                state = json.loads((run_dir / "design_state.json").read_text(encoding="utf-8"))
                self.assertEqual(state["selected_design"]["geometry_id"], selected_id)
                self.assertTrue((run_dir / "element_coordinates.csv").is_file())
                self.assertEqual(baseline["geometry"]["element_count"], ELEMENT_COUNT)


if __name__ == "__main__":
    unittest.main()
