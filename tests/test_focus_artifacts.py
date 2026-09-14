from __future__ import annotations

import json
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.focus_artifacts import export_bundle, field_frame, load_field_data


class FocusArtifactTests(unittest.TestCase):
    def test_field_artifact_loads_and_exports_without_matlab(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "runs" / "sim_one"
            run_dir.mkdir(parents=True)
            payload = {
                "x_mm": [-1, 1], "z_mm": [90, 100],
                "harmonic_orders": [-1, 0, 1],
                "harmonic_frequencies_hz": [27.8e9, 28e9, 28.2e9],
                "user_harmonic_orders": [-1, 1],
                "requested_focus_points_mm": [[0, 0, 100], [1, 0, 90]],
                "actual_peak_points_mm": [[1, 0, 100], [1, 0, 90]],
                "normalized_power_by_user": [
                    [[0.1, 0.2], [0.2, 1.0]],
                    [[0.3, 0.4], [1.0, 0.5]],
                ],
            }
            (run_dir / "field_data.json").write_text(json.dumps(payload), encoding="utf-8")
            (run_dir / "field_data.mat").write_bytes(b"MAT")
            simulation = {
                "simulation_run_id": "sim_one",
                "artifacts": {"field_json": "field_data.json", "field_mat": "field_data.mat"},
                "user_harmonic_orders": [-1, 1],
                "commanded_targets_mm": payload["requested_focus_points_mm"],
            }
            data = load_field_data(root, simulation)
            self.assertIsNotNone(data)
            self.assertEqual(len(field_frame(data, 0)), 4)
            governance = {"llm": {"model": "test-model"}, "versions": {}, "git": {}, "matlab": {}, "external_data": {}}
            archive = export_bundle(root, {
                "agent_task_id": "agent_one", "user_count": 2,
                "frequency_hz": 28e9, "modulation_frequency_hz": 200e6,
                "element_count": 64, "desired_targets_mm": payload["requested_focus_points_mm"],
            }, simulation, governance)
            self.assertGreater(len(archive), 100)
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                self.assertIn("governance.json", bundle.namelist())
                self.assertIn("test-model", bundle.read("report.md").decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
