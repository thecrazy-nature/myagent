"""Dedicated one-process MATLAB batch bridge for geometry candidates."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from bridge.matlab_bridge import _matlab_quote, _resolve_matlab_executable

from .models import ArrayDesignMatlabError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INTERFACE_DIR = PROJECT_ROOT / "agent_interface"


def evaluate_batch(
    run_dir: Path,
    design_task_id: str,
    target_mm: list[float],
    focus_tolerance_mm: float,
    roi_radius_mm: float,
    roi_half_depth_mm: float,
    geometries: list[dict[str, Any]],
    timeout_sec: float = 600.0,
) -> dict[str, Any]:
    """Evaluate several geometries during one real MATLAB cold start."""

    batch_id = "batch_" + uuid.uuid4().hex
    batch_dir = run_dir / "matlab_batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
    config_path = batch_dir / "config.json"
    result_path = batch_dir / "result.json"
    stdout_path = batch_dir / "stdout.log"
    stderr_path = batch_dir / "stderr.log"
    config = {
        "schema_version": 2,
        "batch_id": batch_id,
        "design_task_id": design_task_id,
        "target_mm": target_mm,
        "focus_tolerance_mm": focus_tolerance_mm,
        "roi_radius_mm": roi_radius_mm,
        "roi_half_depth_mm": roi_half_depth_mm,
        "geometries": geometries,
        "random_seed": int(geometries[0].get("seed", 0)) if geometries else 0,
    }
    _write_json_atomic(config_path, config)
    executable = _resolve_matlab_executable()
    expression = (
        f"addpath('{_matlab_quote(INTERFACE_DIR.resolve())}','-begin'); "
        f"agent_evaluate_geometry_batch('{_matlab_quote(config_path.resolve())}',"
        f"'{_matlab_quote(result_path.resolve())}');"
    )
    command = [executable, *( ["-wait"] if os.name == "nt" else []), "-batch", expression]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exception:
        stdout_path.write_text(getattr(exception, "stdout", "") or "", encoding="utf-8")
        stderr_path.write_text(str(exception) + "\n", encoding="utf-8")
        raise ArrayDesignMatlabError(f"Could not complete MATLAB geometry batch: {exception}") from exception
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if not result_path.is_file():
        raise ArrayDesignMatlabError(
            f"MATLAB geometry batch returned {completed.returncode} without an atomic result."
        )
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exception:
        raise ArrayDesignMatlabError(f"Could not parse MATLAB geometry batch result: {exception}") from exception
    if isinstance(payload, dict) and isinstance(payload.get("results"), dict):
        payload["results"] = [payload["results"]]
    _validate_batch_result(payload, batch_id, [geometry["geometry_id"] for geometry in geometries])
    payload["end_to_end_runtime_sec"] = time.perf_counter() - started
    if completed.returncode != 0:
        signatures = ("std::terminate() detected", "MATLAB is exiting because of fatal error")
        if all(signature in completed.stderr for signature in signatures):
            payload["process_warning"] = {
                "warning_type": "MatlabShutdownError",
                "message": "MATLAB persisted a complete validated batch, then failed during shutdown.",
                "returncode": completed.returncode,
            }
        else:
            raise ArrayDesignMatlabError(f"MATLAB geometry batch returned nonzero exit code {completed.returncode}.")
    return payload


def _validate_batch_result(payload: Any, batch_id: str, geometry_ids: list[str]) -> None:
    if not isinstance(payload, dict) or payload.get("status") != "success" or payload.get("batch_id") != batch_id:
        message = payload.get("message") if isinstance(payload, dict) else "malformed payload"
        raise ArrayDesignMatlabError(f"MATLAB geometry batch failed: {message}")
    if not all(isinstance(payload.get(name), str) and payload.get(name) for name in (
        "matlab_version", "matlab_release", "matlab_arch", "rng_algorithm"
    )) or not isinstance(payload.get("random_seed"), int):
        raise ArrayDesignMatlabError("MATLAB geometry batch runtime metadata is missing.")
    results = payload.get("results")
    if not isinstance(results, list) or [item.get("geometry_id") for item in results if isinstance(item, dict)] != geometry_ids:
        raise ArrayDesignMatlabError("MATLAB geometry result list does not match the submitted batch.")
    required = {
        "geometry_id", "focus_error_mm", "actual_peak_mm", "fwhm_x_mm", "dof_z_mm",
        "energy_concentration_ratio", "peak_power", "matlab_runtime_sec",
        "geometry_constraint_satisfied", "focus_constraint_satisfied", "lateral_profile", "axial_profile",
    }
    for result in results:
        if not isinstance(result, dict) or result.get("status") != "success" or not required.issubset(result):
            raise ArrayDesignMatlabError(f"Incomplete MATLAB geometry result: {result}")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
