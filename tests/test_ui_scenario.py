from __future__ import annotations

import unittest

from app.scenario import (
    assign_user_harmonics,
    build_scenario_prompt,
    convert_frequency,
    target_bounds_mm,
    target_validation_errors,
)


class ScenarioPromptTests(unittest.TestCase):
    def test_selected_scenario_is_explicitly_rendered_for_hermes(self) -> None:
        prompt = build_scenario_prompt(
            "按当前配置运行聚焦",
            targets_mm=[[0, 0, 100], [20, 0, 90]],
            frequency_ghz=39,
            modulation_frequency_mhz=200,
            polarization="rhcp",
            element_count=144,
            tolerance_mm=3,
            max_refinements=1,
        )
        self.assertIn("frequency_ghz: 39", prompt)
        self.assertIn("modulation_frequency_mhz: 200", prompt)
        self.assertIn("不同谐波阶次", prompt)
        self.assertIn("用户 1 谐波: q=-1", prompt)
        self.assertIn("用户 2 谐波: q=1", prompt)
        self.assertIn("element_count: 144", prompt)
        self.assertIn("additional_targets_mm: [[20, 0, 90]]", prompt)
        self.assertIn("极化只作为场景元数据", prompt)
        self.assertIn("运行一次聚焦任务", prompt)
        self.assertIn("remaining_refinements > 0", prompt)

    def test_frequency_changes_physical_target_bounds(self) -> None:
        bounds = target_bounds_mm(28)
        self.assertAlmostEqual(bounds["x"][1], 75.0)
        self.assertAlmostEqual(bounds["z"][0], 53.57142857)

    def test_five_ghz_rejects_a_one_hundred_mm_focus_before_matlab(self) -> None:
        bounds = target_bounds_mm(5)
        self.assertEqual(bounds["x"], (-420.0, 420.0))
        self.assertEqual(bounds["z"], (300.0, 840.0))
        errors = target_validation_errors([[0, 0, 100]], 5)
        self.assertEqual(len(errors), 1)
        self.assertIn("Z=100 mm", errors[0])
        self.assertIn("[300, 840] mm", errors[0])
        with self.assertRaisesRegex(ValueError, "Z=100 mm"):
            build_scenario_prompt(
                "按当前配置运行聚焦",
                targets_mm=[[0, 0, 100]],
                frequency_ghz=5,
                modulation_frequency_mhz=200,
                polarization="scalar",
                element_count=256,
                tolerance_mm=5,
                max_refinements=2,
            )

    def test_user_frequency_units_convert_to_bridge_units(self) -> None:
        self.assertAlmostEqual(convert_frequency(27_350, "MHz", "GHz"), 27.35)
        self.assertAlmostEqual(convert_frequency(123_450, "kHz", "MHz"), 123.45)

    def test_harmonic_plan_is_symmetric_and_distinct(self) -> None:
        self.assertEqual(assign_user_harmonics(1), [0])
        self.assertEqual(assign_user_harmonics(2), [-1, 1])
        self.assertEqual(assign_user_harmonics(4), [-2, -1, 1, 2])

    def test_metasurface_preset_renders_exact_binary_workflow(self) -> None:
        prompt = build_scenario_prompt(
            "执行超表面设计并生成 CST 布局",
            targets_mm=[[0, 0, 100]], frequency_ghz=28,
            tolerance_mm=5, max_refinements=0,
            workflow_preset="metasurface",
            metasurface_config={
                "focus_target_mm": [10, 5, 120],
                "unit_size_mm": [5, 5, 1], "array_size": [16, 12],
                "incident_wave": "horn_spherical_wave",
                "horn_feed_position_mm": [0, 0, -150],
                "binary_states": {
                    "state_0": {"amplitude": 0.9, "phase_deg": 0},
                    "state_1": {"amplitude": 0.85, "phase_deg": 180},
                },
                "candidate_budget": 2,
            },
        )
        self.assertIn("0/1 透射可编程超表面", prompt)
        self.assertIn("array_size: [16, 12]", prompt)
        self.assertIn("incident_wave: horn_spherical_wave", prompt)
        self.assertIn("evaluate_metasurface_design", prompt)
        self.assertIn("build_metasurface_cst_model", prompt)
        self.assertIn("record_only", prompt)


if __name__ == "__main__":
    unittest.main()
