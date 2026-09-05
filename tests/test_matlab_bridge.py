from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge import InvalidSimulationInput, run_simulation
from bridge.matlab_bridge import _matlab_quote, _prefer_direct_windows_binary


class MatlabBridgeInputTests(unittest.TestCase):
    def test_rejects_wrong_length(self) -> None:
        with patch("bridge.matlab_bridge.subprocess.run") as run:
            with self.assertRaises(InvalidSimulationInput):
                run_simulation([1, 2])
            run.assert_not_called()

    def test_rejects_nan(self) -> None:
        with patch("bridge.matlab_bridge.subprocess.run") as run:
            with self.assertRaises(InvalidSimulationInput):
                run_simulation([1, float("nan"), 3])
            run.assert_not_called()

    def test_rejects_string(self) -> None:
        with patch("bridge.matlab_bridge.subprocess.run") as run:
            with self.assertRaises(InvalidSimulationInput):
                run_simulation("abc")
            run.assert_not_called()

    def test_rejects_unsafe_task_id(self) -> None:
        with self.assertRaises(InvalidSimulationInput):
            run_simulation([0, 0, 100], task_id="../escape")

    def test_rejects_windows_reserved_task_id(self) -> None:
        with self.assertRaises(InvalidSimulationInput):
            run_simulation([0, 0, 100], task_id="CON")

    def test_windows_launcher_resolution_is_stable(self) -> None:
        resolved = _prefer_direct_windows_binary("matlab")
        self.assertIsInstance(resolved, str)

    def test_matlab_path_quoting_handles_spaces_and_apostrophes(self) -> None:
        quoted = _matlab_quote(Path("F:/path with space/O'Neil/config.json"))
        self.assertEqual(quoted, "F:/path with space/O''Neil/config.json")

    def test_success_creates_complete_task_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary)
            task_id = "unit_success"

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                result_path = runs_root / task_id / "result.json"
                result_path.write_text(
                    json.dumps(
                        {
                            "status": "success",
                            "task_id": task_id,
                            "requested_focus_mm": [0, 0, 100],
                            "actual_peak_mm": [0, 0, 94.2],
                            "peak_power": 1.0,
                            "peak_power_definition": "test definition",
                            "requested_power": 0.9,
                            "runtime_sec": 1.0,
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(command[1], "-batch")
                self.assertFalse(bool(kwargs["shell"]))
                return subprocess.CompletedProcess(command, 0, "stdout", "stderr")

            with (
                patch("bridge.matlab_bridge.RUNS_ROOT", runs_root),
                patch("bridge.matlab_bridge._resolve_matlab_executable", return_value="matlab"),
                patch("bridge.matlab_bridge.subprocess.run", side_effect=fake_run),
            ):
                result = run_simulation([0, 0, 100], task_id=task_id)

            self.assertEqual(result["status"], "success")
            task_dir = runs_root / task_id
            self.assertEqual(
                {path.name for path in task_dir.iterdir()},
                {"config.json", "result.json", "stdout.log", "stderr.log"},
            )


if __name__ == "__main__":
    unittest.main()
