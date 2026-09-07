from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


class StreamlitPageTests(unittest.TestCase):
    def setUp(self) -> None:
        script = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
        self.app = AppTest.from_file(script, default_timeout=10).run()

    def test_natural_language_is_default_primary_entry(self) -> None:
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.radio[0].value, "Natural Language Mode")
        self.assertIn("Natural Language Task", self.app.text_area[0].label)
        self.assertIn("z=100 mm", self.app.text_area[0].value)
        self.assertTrue(self.app.button[0].disabled)

    def test_structured_mode_generates_visible_natural_language_task(self) -> None:
        self.app.radio[0].set_value("Structured Mode").run()
        self.assertFalse(self.app.exception)
        rendered = "\n".join(code.value for code in self.app.code)
        self.assertIn("x=0 mm", rendered)
        self.assertIn("5 mm", rendered)
        self.assertIn("最多允许 2 次", rendered)

    def test_unavailable_proxy_message_is_actionable(self) -> None:
        messages = "\n".join(error.value for error in self.app.error)
        self.assertIn("Hermes network proxy is unavailable", messages)
        self.assertIn("proxy-on.ps1", messages)

    def test_recovered_infrastructure_warning_keeps_success_result_visible(self) -> None:
        self.app.session_state["latest_agent_result"] = {
            "status": "SUCCESS", "error_category": None, "error_message": None,
            "infrastructure_warnings": ["Recovered post-result shutdown crash."],
            "desired_target_mm": [0, 0, 100], "actual_peak_mm": [0, 0, 97.7],
            "final_error_mm": 2.3, "matlab_calls": 2, "replanning_count": 1,
            "trajectory": [], "iterations": [], "simulation_details": [],
            "agent_final_response": "真实 Hermes 最终回复。",
        }
        self.app.run()
        warnings = "\n".join(item.value for item in self.app.warning)
        successes = "\n".join(item.value for item in self.app.success)
        self.assertIn("Recovered post-result shutdown crash", warnings)
        self.assertIn("scientific constraint was satisfied", successes)
        self.assertIn("真实 Hermes 最终回复", "\n".join(item.value for item in self.app.markdown))

    def test_array_design_tab_renders_comparison_and_coordinate_charts(self) -> None:
        geometry = {"family": "spherical_cap", "element_positions_mm": [[-1, -1, 0], [1, -1, 0], [-1, 1, 1], [1, 1, 1]]}
        metrics = {
            "focus_error_mm": 1.0, "fwhm_x_mm": 8.0, "dof_z_mm": 20.0,
            "energy_concentration_ratio": 0.2, "peak_power": 10.0,
            "lateral_profile": {"position_mm": [-1, 0, 1], "normalized_power": [0.4, 1, 0.4]},
            "axial_profile": {"position_mm": [99, 100, 101], "normalized_power": [0.4, 1, 0.4]},
        }
        self.app.session_state["latest_design_result"] = {
            "status": "SAVED", "error_category": None, "trajectory": [],
            "agent_final_response": "Saved from real MATLAB evidence.",
            "design_state": {"focus_target_mm": [0, 0, 100]},
            "selected_design": {"geometry": geometry, "metrics": metrics},
            "baseline": {"geometry": geometry, "metrics": metrics},
            "baseline_metrics": metrics, "designed_metrics": metrics,
            "search_rounds": 1, "matlab_processes": 2,
        }
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertEqual([tab.label for tab in self.app.tabs], ["Focusing Agent", "Array Geometry Designer"])
        self.assertGreaterEqual(len(self.app.dataframe), 1)
        self.assertGreaterEqual(len(self.app.get("vega_lite_chart")), 5)


if __name__ == "__main__":
    unittest.main()
