from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


class StreamlitPageTests(unittest.TestCase):
    def setUp(self) -> None:
        script = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
        self.app = AppTest.from_file(script, default_timeout=10).run()

    def test_chat_is_default_primary_entry(self) -> None:
        self.assertFalse(self.app.exception)
        self.assertEqual(
            [tab.label for tab in self.app.tabs],
            ["智能体对话", "任务中心", "结果可视化"],
        )
        self.assertIn("描述任务或继续追问", self.app.chat_input[0].placeholder)
        self.assertTrue(self.app.chat_input[0].disabled)
        labels = [item.label for item in self.app.selectbox]
        self.assertIn("载波频率", labels)
        self.assertIn("调制频率", labels)
        self.assertIn("阵元数量", labels)
        self.assertIn("极化", labels)

    def test_prompt_guide_exposes_model_boundary_and_examples(self) -> None:
        self.assertFalse(self.app.exception)
        rendered = "\n".join(code.value for code in self.app.code)
        guide = self.app.dataframe[0].value.to_string()
        self.assertIn("28 GHz", guide)
        self.assertIn("256", guide)
        self.assertIn("24 / 28 / 39 GHz", guide)
        self.assertIn("20×20", guide)
        self.assertIn("axial-null", guide)
        self.assertIn("标量模型不计算极化差异", guide)
        self.assertIn("x=0 mm", rendered)
        self.assertIn("5 mm", rendered)
        self.assertIn("spherical-cap", rendered)

    def test_unavailable_proxy_message_is_actionable(self) -> None:
        messages = "\n".join(error.value for error in self.app.error)
        self.assertIn("Hermes 网络代理不可用", messages)
        self.assertIn("proxy-on.ps1", messages)

    def test_governance_notice_and_versions_are_prominent(self) -> None:
        warnings = "\n".join(item.value for item in self.app.warning)
        metrics = {item.label: item.value for item in self.app.metric}
        expanders = [item.label for item in self.app.expander]
        self.assertIn("自然语言、实验配置和紧凑 Tool 结果会发送到外部模型服务", warnings)
        self.assertIn("运行治理与版本（提交时会固化到任务记录）", expanders)
        self.assertEqual(metrics.get("Prompt 版本"), "1.0.0")
        self.assertEqual(metrics.get("Tool schema 版本"), "1.0.0")

    def test_recovered_infrastructure_warning_keeps_success_result_visible(self) -> None:
        self.app.session_state["latest_agent_result"] = {
            "status": "SUCCESS", "error_category": None, "error_message": None,
            "task_kind": "focus",
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
        self.assertIn("已满足用户要求的科学约束", successes)
        self.assertIn("真实 Hermes 最终回复", "\n".join(item.value for item in self.app.markdown))

    def test_array_design_tab_renders_comparison_and_coordinate_charts(self) -> None:
        geometry = {"family": "spherical_cap", "element_positions_mm": [[-1, -1, 0], [1, -1, 0], [-1, 1, 1], [1, 1, 1]]}
        metrics = {
            "focus_error_mm": 1.0, "fwhm_x_mm": 8.0, "dof_z_mm": 20.0,
            "energy_concentration_ratio": 0.2, "peak_power": 10.0,
            "lateral_profile": {"position_mm": [-1, 0, 1], "normalized_power": [0.4, 1, 0.4]},
            "axial_profile": {"position_mm": [99, 100, 101], "normalized_power": [0.4, 1, 0.4]},
        }
        self.app.session_state["latest_agent_result"] = {
            "status": "SAVED", "error_category": None, "trajectory": [],
            "task_kind": "array_design",
            "agent_final_response": "Saved from real MATLAB evidence.",
            "design_state": {"focus_target_mm": [0, 0, 100]},
            "selected_design": {"geometry": geometry, "metrics": metrics},
            "baseline": {"geometry": geometry, "metrics": metrics},
            "baseline_metrics": metrics, "designed_metrics": metrics,
            "search_rounds": 1, "matlab_processes": 2,
        }
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertEqual(
            [tab.label for tab in self.app.tabs],
            ["智能体对话", "任务中心", "结果可视化"],
        )
        self.assertGreaterEqual(len(self.app.dataframe), 1)
        self.assertGreaterEqual(len(self.app.get("vega_lite_chart")), 5)

    def test_result_viewer_is_read_only_and_lists_persisted_focus_tasks(self) -> None:
        self.assertFalse(self.app.exception)
        headers = "\n".join(item.value for item in self.app.header)
        captions = "\n".join(item.value for item in self.app.caption)
        self.assertIn("历史结果可视化", headers)
        self.assertIn("不会重新运行 Hermes 或 MATLAB", captions)
        self.assertTrue(any(item.label == "选择聚焦任务" for item in self.app.selectbox))


if __name__ == "__main__":
    unittest.main()
