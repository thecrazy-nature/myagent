"""Standard-library Python bridge for the MATLAB batch interface."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Sequence
from numbers import Real
from pathlib import Path
from typing import Any

from .models import (
    InvalidSimulationInput,
    MatlabExecutableNotFound,
    MatlabProcessError,
    MatlabResultError,
    MatlabTimeoutError,
    validate_success_result,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = PROJECT_ROOT / "runs"
INTERFACE_DIR = PROJECT_ROOT / "agent_interface"
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
DEFAULT_FREQUENCY_HZ = 28.0e9
DEFAULT_MODULATION_FREQUENCY_HZ = 200.0e6
DEFAULT_ELEMENT_COUNT = 256
SUPPORTED_POLARIZATIONS = {"scalar", "x_linear", "y_linear", "rhcp", "lhcp"}


def run_simulation(
    target_mm: Sequence[Real],
    task_id: str | None = None,
    timeout_sec: Real = 300,
    *,
    additional_targets_mm: Sequence[Sequence[Real]] | None = None,
    frequency_hz: Real = DEFAULT_FREQUENCY_HZ,
    modulation_frequency_hz: Real = DEFAULT_MODULATION_FREQUENCY_HZ,
    element_count: int = DEFAULT_ELEMENT_COUNT,
    polarization: str = "scalar",
) -> dict[str, Any]:
    """Run one real MATLAB single- or multi-target focus simulation."""

    target = _validate_target(target_mm)
    targets = [target, *_validate_additional_targets(additional_targets_mm)]
    frequency = _validate_frequency(frequency_hz)
    modulation_frequency = _validate_modulation_frequency(
        modulation_frequency_hz, frequency
    )
    elements = _validate_element_count(element_count)
    polarization_mode = _validate_polarization(polarization)
    resolved_task_id = _validate_or_create_task_id(task_id)
    timeout = _validate_timeout(timeout_sec)

    run_dir = RUNS_ROOT / resolved_task_id
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exception:
        raise InvalidSimulationInput(
            f"Task directory already exists: {run_dir}",
            task_id=resolved_task_id,
            run_dir=run_dir,
        ) from exception
    except OSError as exception:
        raise InvalidSimulationInput(
            f"Could not create task directory {run_dir}: {exception}",
            task_id=resolved_task_id,
            run_dir=run_dir,
        ) from exception

    config_path = run_dir / "config.json"
    result_path = run_dir / "result.json"
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    _write_json_atomic(
        config_path,
        {
            "task_id": resolved_task_id,
            "target_mm": target,
            "targets_mm": targets,
            "frequency_hz": frequency,
            "modulation_frequency_hz": modulation_frequency,
            "element_count": elements,
            "polarization": polarization_mode,
            "random_seed": 0,
        },
    )

    try:
        matlab_executable = _resolve_matlab_executable()
    except MatlabExecutableNotFound as exception:
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text(f"{exception}\n", encoding="utf-8")
        exception.task_id = resolved_task_id
        exception.run_dir = run_dir
        raise

    command = _build_matlab_command(matlab_executable, config_path, result_path)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=PROJECT_ROOT,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exception:
        stdout_path.write_text(_stream_text(exception.stdout), encoding="utf-8")
        stderr_path.write_text(_stream_text(exception.stderr), encoding="utf-8")
        raise MatlabTimeoutError(
            f"MATLAB exceeded timeout_sec={timeout:g}.",
            task_id=resolved_task_id,
            run_dir=run_dir,
        ) from exception
    except OSError as exception:
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text(f"{exception}\n", encoding="utf-8")
        raise MatlabProcessError(
            f"Could not start MATLAB: {exception}",
            task_id=resolved_task_id,
            run_dir=run_dir,
        ) from exception

    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        recovered = _recover_completed_shutdown_result(
            result_path,
            completed.stderr,
            completed.returncode,
            expected_task_id=resolved_task_id,
            run_dir=run_dir,
        )
        if recovered is not None:
            return recovered
        raise MatlabProcessError(
            f"MATLAB returned nonzero exit code {completed.returncode}.",
            task_id=resolved_task_id,
            run_dir=run_dir,
            returncode=completed.returncode,
        )
    if not result_path.is_file():
        raise MatlabResultError(
            "MATLAB exited successfully but did not create result.json.",
            task_id=resolved_task_id,
            run_dir=run_dir,
        )

    return _load_validated_result(
        result_path,
        expected_task_id=resolved_task_id,
        run_dir=run_dir,
    )


def _recover_completed_shutdown_result(
    result_path: Path,
    stderr: str,
    returncode: int,
    *,
    expected_task_id: str,
    run_dir: Path,
) -> dict[str, Any] | None:
    """Accept a complete result only for the observed post-result shutdown crash."""

    signatures = ("std::terminate() detected", "MATLAB is exiting because of fatal error")
    if not result_path.is_file() or not all(signature in stderr for signature in signatures):
        return None
    payload = dict(
        _load_validated_result(
            result_path,
            expected_task_id=expected_task_id,
            run_dir=run_dir,
        )
    )
    payload["process_warning"] = {
        "warning_type": "MatlabShutdownError",
        "message": (
            "MATLAB produced and atomically persisted a complete validated result, "
            "then reported std::terminate during process shutdown."
        ),
        "returncode": returncode,
    }
    return payload


def _load_validated_result(
    result_path: Path,
    *,
    expected_task_id: str,
    run_dir: Path,
) -> dict[str, Any]:
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exception:
        raise MatlabResultError(
            f"Could not parse MATLAB result.json: {exception}",
            task_id=expected_task_id,
            run_dir=run_dir,
        ) from exception
    return validate_success_result(payload, expected_task_id=expected_task_id, run_dir=run_dir)


def _validate_target(target_mm: Sequence[Real]) -> list[float]:
    if isinstance(target_mm, (str, bytes)) or not isinstance(target_mm, Sequence):
        raise InvalidSimulationInput("target_mm must be a length-3 numeric sequence.")
    if len(target_mm) != 3:
        raise InvalidSimulationInput("target_mm must contain exactly three values.")
    target: list[float] = []
    for value in target_mm:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise InvalidSimulationInput("target_mm values must be real numbers.")
        converted = float(value)
        if not math.isfinite(converted):
            raise InvalidSimulationInput("target_mm values must be finite.")
        target.append(converted)
    return target


def _validate_additional_targets(
    targets_mm: Sequence[Sequence[Real]] | None,
) -> list[list[float]]:
    if targets_mm is None:
        return []
    if isinstance(targets_mm, (str, bytes)) or not isinstance(targets_mm, Sequence):
        raise InvalidSimulationInput("additional_targets_mm must be a sequence of [x,y,z] targets.")
    if len(targets_mm) > 7:
        raise InvalidSimulationInput("At most 8 simultaneous focus targets are supported.")
    return [_validate_target(target) for target in targets_mm]


def _validate_frequency(frequency_hz: Real) -> float:
    if isinstance(frequency_hz, bool) or not isinstance(frequency_hz, Real):
        raise InvalidSimulationInput("frequency_hz must be a finite number from 1 to 100 GHz.")
    frequency = float(frequency_hz)
    if not math.isfinite(frequency) or not 1.0e9 <= frequency <= 100.0e9:
        raise InvalidSimulationInput("frequency_hz must be between 1 and 100 GHz.")
    return frequency


def _validate_modulation_frequency(value: Real, carrier_hz: float) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise InvalidSimulationInput(
            "modulation_frequency_hz must be a positive finite number."
        )
    frequency = float(value)
    if not math.isfinite(frequency) or frequency <= 0 or frequency >= carrier_hz:
        raise InvalidSimulationInput(
            "modulation_frequency_hz must be positive and below the carrier frequency."
        )
    return frequency


def _validate_element_count(element_count: int) -> int:
    if isinstance(element_count, bool) or not isinstance(element_count, int):
        raise InvalidSimulationInput("element_count must be a perfect-square integer.")
    side = math.isqrt(element_count)
    if side * side != element_count or not 4 <= side <= 32:
        raise InvalidSimulationInput(
            "element_count must describe a square 4x4 to 32x32 planar array."
        )
    return element_count


def _validate_polarization(polarization: str) -> str:
    if not isinstance(polarization, str):
        raise InvalidSimulationInput("polarization must be a string.")
    normalized = polarization.strip().lower()
    if normalized not in SUPPORTED_POLARIZATIONS:
        raise InvalidSimulationInput(
            "polarization must be scalar, x_linear, y_linear, rhcp, or lhcp."
        )
    return normalized


def _validate_or_create_task_id(task_id: str | None) -> str:
    if task_id is None:
        return f"task_{uuid.uuid4().hex}"
    if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
        raise InvalidSimulationInput(
            "task_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}.",
        )
    if task_id in {".", ".."}:
        raise InvalidSimulationInput("task_id cannot be '.' or '..'.")
    if task_id.endswith(".") or task_id.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        raise InvalidSimulationInput("task_id is not a valid Windows directory name.")
    return task_id


def _validate_timeout(timeout_sec: Real) -> float:
    if isinstance(timeout_sec, bool) or not isinstance(timeout_sec, Real):
        raise InvalidSimulationInput("timeout_sec must be a positive finite number.")
    timeout = float(timeout_sec)
    if not math.isfinite(timeout) or timeout <= 0:
        raise InvalidSimulationInput("timeout_sec must be a positive finite number.")
    return timeout


def _resolve_matlab_executable() -> str:
    configured = os.environ.get("MATLAB_EXECUTABLE")
    if configured:
        candidate = shutil.which(configured)
        if candidate:
            return _prefer_windows_launcher(candidate)
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return _prefer_windows_launcher(str(configured_path.resolve()))
        raise MatlabExecutableNotFound(
            f"MATLAB_EXECUTABLE does not resolve to a file: {configured}",
        )
    candidate = shutil.which("matlab")
    if candidate:
        return _prefer_windows_launcher(candidate)
    raise MatlabExecutableNotFound(
        "MATLAB executable was not found on PATH; set MATLAB_EXECUTABLE.",
    )


def _prefer_windows_launcher(executable: str) -> str:
    """Use MATLAB's supported Windows launcher with the synchronous -wait flag."""

    resolved = Path(executable).resolve()
    if os.name == "nt" and resolved.parent.name.lower() == "win64":
        launcher = resolved.parent.parent / "matlab.exe"
        if launcher.is_file():
            return str(launcher.resolve())
    return str(resolved)


def _build_matlab_command(
    matlab_executable: str,
    config_path: Path,
    result_path: Path,
) -> list[str]:
    interface = _matlab_quote(INTERFACE_DIR.resolve())
    config = _matlab_quote(config_path.resolve())
    result = _matlab_quote(result_path.resolve())
    expression = (
        f"addpath('{interface}','-begin'); "
        f"agent_run_simulation('{config}','{result}');"
    )
    wait_arguments = ["-wait"] if os.name == "nt" else []
    return [matlab_executable, *wait_arguments, "-batch", expression]


def _matlab_quote(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _stream_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
