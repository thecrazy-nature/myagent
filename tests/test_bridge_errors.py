from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge import (
    MatlabExecutableNotFound,
    MatlabProcessError,
    MatlabResultError,
    MatlabSimulationError,
    MatlabTimeoutError,
    run_simulation,
)


class MatlabBridgeErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.runs_root = Path(self.temporary.name)
        self.runs_patch = patch("bridge.matlab_bridge.RUNS_ROOT", self.runs_root)
        self.runs_patch.start()

    def tearDown(self) -> None:
        self.runs_patch.stop()
        self.temporary.cleanup()

    def test_executable_not_found(self) -> None:
        error = MatlabExecutableNotFound("MATLAB is unavailable.")
        with patch("bridge.matlab_bridge._resolve_matlab_executable", side_effect=error):
            with self.assertRaises(MatlabExecutableNotFound) as caught:
                run_simulation([0, 0, 100], task_id="unit_no_matlab")
        self.assertTrue((caught.exception.run_dir / "stderr.log").is_file())

    def test_timeout(self) -> None:
        timeout = subprocess.TimeoutExpired(["matlab"], 0.01, output="partial")
        with (
            patch("bridge.matlab_bridge._resolve_matlab_executable", return_value="matlab"),
            patch("bridge.matlab_bridge.subprocess.run", side_effect=timeout),
        ):
            with self.assertRaises(MatlabTimeoutError):
                run_simulation([0, 0, 100], task_id="unit_timeout", timeout_sec=0.01)
        self.assertEqual(
            (self.runs_root / "unit_timeout" / "stdout.log").read_text(encoding="utf-8"),
            "partial",
        )

    def test_nonzero_process(self) -> None:
        completed = subprocess.CompletedProcess(["matlab"], 7, "out", "failure")
        with (
            patch("bridge.matlab_bridge._resolve_matlab_executable", return_value="matlab"),
            patch("bridge.matlab_bridge.subprocess.run", return_value=completed),
        ):
            with self.assertRaises(MatlabProcessError) as caught:
                run_simulation([0, 0, 100], task_id="unit_process_error")
        self.assertEqual(caught.exception.returncode, 7)

    def test_missing_result(self) -> None:
        completed = subprocess.CompletedProcess(["matlab"], 0, "", "")
        with (
            patch("bridge.matlab_bridge._resolve_matlab_executable", return_value="matlab"),
            patch("bridge.matlab_bridge.subprocess.run", return_value=completed),
        ):
            with self.assertRaises(MatlabResultError):
                run_simulation([0, 0, 100], task_id="unit_missing_result")

    def test_malformed_result(self) -> None:
        task_id = "unit_malformed_result"

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            del args, kwargs
            (self.runs_root / task_id / "result.json").write_text("{", encoding="utf-8")
            return subprocess.CompletedProcess(["matlab"], 0, "", "")

        with (
            patch("bridge.matlab_bridge._resolve_matlab_executable", return_value="matlab"),
            patch("bridge.matlab_bridge.subprocess.run", side_effect=fake_run),
        ):
            with self.assertRaises(MatlabResultError):
                run_simulation([0, 0, 100], task_id=task_id)

    def test_success_result_missing_required_field(self) -> None:
        task_id = "unit_missing_field"

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            del args, kwargs
            result = {
                "status": "success",
                "task_id": task_id,
                "requested_focus_mm": [0, 0, 100],
            }
            (self.runs_root / task_id / "result.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            return subprocess.CompletedProcess(["matlab"], 0, "", "")

        with (
            patch("bridge.matlab_bridge._resolve_matlab_executable", return_value="matlab"),
            patch("bridge.matlab_bridge.subprocess.run", side_effect=fake_run),
        ):
            with self.assertRaises(MatlabResultError):
                run_simulation([0, 0, 100], task_id=task_id)

    def test_matlab_error_result(self) -> None:
        task_id = "unit_matlab_error"

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            del args, kwargs
            result = {
                "status": "error",
                "task_id": task_id,
                "error_type": "hermes:TargetOutOfRange",
                "message": "Target is outside the allowed region.",
                "runtime_sec": 0.1,
            }
            (self.runs_root / task_id / "result.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            return subprocess.CompletedProcess(["matlab"], 0, "", "")

        with (
            patch("bridge.matlab_bridge._resolve_matlab_executable", return_value="matlab"),
            patch("bridge.matlab_bridge.subprocess.run", side_effect=fake_run),
        ):
            with self.assertRaises(MatlabSimulationError) as caught:
                run_simulation([100, 0, 100], task_id=task_id)
        self.assertEqual(caught.exception.error_type, "hermes:TargetOutOfRange")


if __name__ == "__main__":
    unittest.main()
