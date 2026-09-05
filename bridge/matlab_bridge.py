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


def run_simulation(
    target_mm: Sequence[Real],
    task_id: str | None = None,
    timeout_sec: Real = 300,
) -> dict[str, Any]:
    """Run one real MATLAB focus simulation and return validated JSON data."""

    target = _validate_target(target_mm)
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
        {"task_id": resolved_task_id, "target_mm": target},
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

    try:
        payload = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exception:
        raise MatlabResultError(
            f"Could not parse MATLAB result.json: {exception}",
            task_id=resolved_task_id,
            run_dir=run_dir,
        ) from exception
    return validate_success_result(
        payload,
        expected_task_id=resolved_task_id,
        run_dir=run_dir,
    )


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
            return _prefer_direct_windows_binary(candidate)
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return _prefer_direct_windows_binary(str(configured_path.resolve()))
        raise MatlabExecutableNotFound(
            f"MATLAB_EXECUTABLE does not resolve to a file: {configured}",
        )
    candidate = shutil.which("matlab")
    if candidate:
        return _prefer_direct_windows_binary(candidate)
    raise MatlabExecutableNotFound(
        "MATLAB executable was not found on PATH; set MATLAB_EXECUTABLE.",
    )


def _prefer_direct_windows_binary(executable: str) -> str:
    """Avoid the Windows launcher, whose detached child defeats run timeouts."""

    resolved = Path(executable).resolve()
    if os.name == "nt" and resolved.parent.name.lower() == "bin":
        direct = resolved.parent / "win64" / "MATLAB.exe"
        if direct.is_file():
            return str(direct)
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
    return [matlab_executable, "-batch", expression]


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
