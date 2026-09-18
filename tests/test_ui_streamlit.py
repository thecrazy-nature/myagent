from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app.job_store import create_job


class StreamlitPageTests(unittest.TestCase):
    def setUp(self) -> None:
        script = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
        self.app = AppTest.from_file(script, default_timeout=10).run()

    def test_chat_is_default_primary_entry(self) -> None:
        self.assertFalse(self.app.exception)
        self.assertEqual(
            [tab.label for tab in self.app.tabs],
            ["智能体对话", "任务中心", "结果可视化", "系统设置"],
        )
        self.assertIn("描述任务或继续追问", self.app.chat_input[0].placeholder)
        self.assertFalse(self.app.chat_input[0].disabled)
        labels = [item.label for item in self.app.selectbox]
        self.assertIn("本轮 Hermes 模型", labels)
        self.assertIn("载波频率单位", labels)
        self.assertIn("调制频率单位", labels)
        self.assertIn("阵元数量", labels)
        self.assertIn("极化", labels)
        number_labels = [item.label for item in self.app.number_input]
        self.assertIn("载波频率数值", number_labels)
        self.assertIn("调制频率数值", number_labels)

    def test_prompt_guide_exposes_model_boundary_and_examples(self) -> None:
        self.assertFalse(self.app.exception)
        rendered = "\n".join(code.value for code in self.app.code)
        guide = self.app.dataframe[0].value.to_string()
        self.assertIn("28 GHz", guide)
        self.assertIn("256", guide)
        self.assertIn("用户自由输入", guide)
        self.assertIn("GHz / MHz", guide)
        self.assertIn("MHz / kHz", guide)
        self.assertIn("20×20", guide)
        self.assertIn("axial-null", guide)
        self.assertIn("标量模型不计算极化差异", guide)
        self.assertIn("YOZ", guide)
        self.assertIn("XOY", guide)
        self.assertIn("x=0 mm", rendered)
        self.assertIn("5 mm", rendered)
        self.assertIn("spherical-cap", rendered)

    def test_frontend_does_not_require_or_advertise_proxy(self) -> None:
        messages = "\n".join(
            item.value
            for collection in (self.app.error, self.app.success, self.app.info)
            for item in collection
        )
        self.assertNotIn("Clash", messages)
        self.assertNotIn("proxy-on.ps1", messages)

    def test_out_of_range_target_is_blocked_before_agent_submission(self) -> None:
        carrier = next(
            item for item in self.app.number_input
            if item.label == "载波频率数值"
        )
        carrier.set_value(5.0)
        self.app.run()
        errors = "\n".join(item.value for item in self.app.error)
        self.assertIn("Z=100 mm", errors)
        self.assertIn("[300, 840] mm", errors)
        self.assertTrue(self.app.chat_input[0].disabled)

    def test_governance_notice_and_versions_are_prominent(self) -> None:
        warnings = "\n".join(item.value for item in self.app.warning)
        metrics = {item.label: item.value for item in self.app.metric}
        expanders = [item.label for item in self.app.expander]
        self.assertIn("自然语言、实验配置和紧凑 Tool 结果会发送到外部模型服务", warnings)
        self.assertIn("运行治理与版本（提交时会固化到任务记录）", expanders)
        self.assertEqual(metrics.get("Prompt 版本"), "1.3.0")
        self.assertEqual(metrics.get("Tool schema 版本"), "1.2.0")
        self.assertNotIn("Git commit", metrics)

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
            ["智能体对话", "任务中心", "结果可视化", "系统设置"],
        )
        self.assertGreaterEqual(len(self.app.dataframe), 1)
        self.assertGreaterEqual(len(self.app.get("vega_lite_chart")), 5)

    def test_metasurface_result_renders_three_way_baseline_and_phase_codes(self) -> None:
        def metrics(power: float, error: float) -> dict:
            return {
                "focus_error_mm": error,
                "target_power": power,
                "target_to_global_db": 0.0,
                "fwhm_x_mm": 8.0,
                "dof_z_mm": 18.0,
                "energy_concentration_ratio": 0.3,
                "peak_to_sidelobe_ratio_db": 5.0,
                "actual_peak_mm": [0, 0, 100],
                "command_target_mm": [0, 0, 110],
                "x_mm": [-1, 1],
                "z_mm": [99, 101],
                "normalized_power_xz": [[0.2, 0.3], [0.4, 1.0]],
                "element_positions_mm": [[-1, -1, 0], [1, -1, 0], [-1, 1, 0], [1, 1, 0]],
                "phase_codes": [0, 1, 0, 1],
                "phase_deg": [0, 180, 0, 180],
            }

        baseline = metrics(2.0, 6.0)
        reference = metrics(10.0, 0.0)
        designed = metrics(8.0, 1.0)
        self.app.session_state["latest_agent_result"] = {
            "status": "SAVED", "error_category": None,
            "task_kind": "metasurface_design", "trajectory": [],
            "agent_final_response": "Saved finite-bit phase controls.",
            "metasurface_state": {
                "focus_target_mm": [0, 0, 100], "frequency_ghz": 28,
                "element_count": 4, "array_size": [2, 2],
                "incident_wave": "plane_wave", "evaluated_candidates": {},
                "binary_state_characteristics": {
                    "state_0_power_transmission": 1.0,
                    "state_1_power_transmission": 1.0,
                    "relative_phase_deg_state_1_minus_0": 180.0,
                },
            },
            "unprogrammed_reference": {"metrics": baseline},
            "continuous_reference": {"metrics": reference},
            "geometrical_optics_baseline": {"metrics": designed},
            "selected_design": {
                "configuration": {"optimizer": "binary_coordinate_descent_v1"},
                "metrics": designed,
                "assessment": {"criteria_status": "pending_researcher_decision", "overall_pass": None},
            },
            "matlab_processes": 2,
        }
        self.app.run()
        self.assertFalse(self.app.exception)
        rendered = "\n".join(item.value for item in self.app.subheader)
        self.assertIn("未编程参考、几何光学基线、连续相位参考与优化结果", rendered)
        self.assertIn("最终平面超表面 0/1 控制码", rendered)

    def test_result_viewer_is_read_only_and_lists_persisted_focus_tasks(self) -> None:
        self.assertFalse(self.app.exception)
        headers = "\n".join(item.value for item in self.app.header)
        captions = "\n".join(item.value for item in self.app.caption)
        self.assertIn("历史结果可视化", headers)
        self.assertIn("不会重新运行 Hermes 或 MATLAB", captions)
        self.assertTrue(any(item.label == "选择聚焦任务" for item in self.app.selectbox))

    def test_conversation_embeds_its_durable_task_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            jobs_root = Path(temporary) / "ui_jobs"
            with patch("app.job_store.JOBS_ROOT", jobs_root):
                job = create_job(
                    "测试对话内任务卡",
                    conversation_id="conversation_embedded",
                    model_override="deepseek-v4-flash",
                    provider_override="deepseek",
                )
                self.app.session_state["conversation_id"] = "conversation_embedded"
                self.app.run()
        self.assertFalse(self.app.exception)
        rendered = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("本对话的任务", "\n".join(item.value for item in self.app.subheader))
        self.assertIn(job["job_id"], rendered)
        self.assertIn("展开任务详情", [item.label for item in self.app.toggle])


if __name__ == "__main__":
    unittest.main()
