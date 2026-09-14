from __future__ import annotations

import unittest

from app.scenario import assign_user_harmonics, build_scenario_prompt, target_bounds_mm


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

    def test_frequency_changes_physical_target_bounds(self) -> None:
        bounds = target_bounds_mm(28)
        self.assertAlmostEqual(bounds["x"][1], 75.0)
        self.assertAlmostEqual(bounds["z"][0], 53.57142857)

    def test_harmonic_plan_is_symmetric_and_distinct(self) -> None:
        self.assertEqual(assign_user_harmonics(1), [0])
        self.assertEqual(assign_user_harmonics(2), [-1, 1])
        self.assertEqual(assign_user_harmonics(4), [-2, -1, 1, 2])


if __name__ == "__main__":
    unittest.main()
