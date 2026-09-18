"""One-process real MATLAB bridge for programmable-metasurface candidates."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from bridge.matlab_bridge import _matlab_quote, _resolve_matlab_executable

from .models import MetasurfaceMatlabError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INTERFACE_DIR = PROJECT_ROOT / "agent_interface"


def evaluate_batch(
    run_dir: Path,
    metasurface_task_id: str,
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
    timeout_sec: float = 600.0,
) -> dict[str, Any]:
    batch_id = "meta_batch_" + uuid.uuid4().hex
    batch_dir = run_dir / "matlab_batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
    config_path = batch_dir / "config.json"
    result_path = batch_dir / "result.json"
    stdout_path = batch_dir / "stdout.log"
    stderr_path = batch_dir / "stderr.log"
    config = {
        "schema_version": 1,
        "batch_id": batch_id,
        "metasurface_task_id": metasurface_task_id,
        "focus_target_mm": state["focus_target_mm"],
        "focus_tolerance_mm": state["focus_tolerance_mm"],
        "frequency_hz": state["frequency_ghz"] * 1e9,
        "surface_geometry": state["surface_geometry"],
        "unit_size_mm": state["unit_size_mm"],
        "array_size": state["array_size"],
        "incident_wave": state["incident_wave"],
        "horn_feed_position_mm": state["horn_feed_position_mm"],
        "binary_states": state["binary_states"],
        "roi_radius_mm": state["roi_radius_mm"],
        "roi_half_depth_mm": state["roi_half_depth_mm"],
        "candidates": candidates,
    }
    _write_json_atomic(config_path, config)
    executable = _resolve_matlab_executable()
    expression = (
        f"addpath('{_matlab_quote(INTERFACE_DIR.resolve())}','-begin'); "
        f"agent_evaluate_metasurface_batch('{_matlab_quote(config_path.resolve())}',"
        f"'{_matlab_quote(result_path.resolve())}');"
    )
    command = [executable, *(["-wait"] if os.name == "nt" else []), "-batch", expression]
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
        raise MetasurfaceMatlabError(
            f"Could not complete MATLAB metasurface batch: {exception}"
        ) from exception
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if not result_path.is_file():
        raise MetasurfaceMatlabError(
            f"MATLAB metasurface batch returned {completed.returncode} without a result."
        )
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exception:
        raise MetasurfaceMatlabError(
            f"Could not parse MATLAB metasurface result: {exception}"
        ) from exception
    if isinstance(payload, dict) and isinstance(payload.get("results"), dict):
        payload["results"] = [payload["results"]]
    _validate_result(payload, batch_id, [item["candidate_id"] for item in candidates])
    payload["end_to_end_runtime_sec"] = time.perf_counter() - started
    if completed.returncode != 0:
        signatures = ("std::terminate() detected", "MATLAB is exiting because of fatal error")
        if all(signature in completed.stderr for signature in signatures):
            payload["process_warning"] = {
                "warning_type": "MatlabShutdownError",
                "message": "MATLAB persisted a validated metasurface batch before shutdown failed.",
                "returncode": completed.returncode,
            }
        else:
            raise MetasurfaceMatlabError(
                f"MATLAB metasurface batch returned nonzero exit code {completed.returncode}."
            )
    return payload


def _validate_result(payload: Any, batch_id: str, candidate_ids: list[str]) -> None:
    if not isinstance(payload, dict) or payload.get("status") != "success" or payload.get("batch_id") != batch_id:
        message = payload.get("message") if isinstance(payload, dict) else "malformed payload"
        raise MetasurfaceMatlabError(f"MATLAB metasurface batch failed: {message}")
    results = payload.get("results")
    if not isinstance(results, list) or [item.get("candidate_id") for item in results] != candidate_ids:
        raise MetasurfaceMatlabError("MATLAB metasurface result order does not match the request.")
    required = {
        "candidate_id", "mode", "actual_peak_mm", "focus_error_mm",
        "target_power", "global_peak_power", "target_to_global_db",
        "fwhm_x_mm", "dof_z_mm", "energy_concentration_ratio",
        "peak_to_sidelobe_ratio_db", "phase_codes", "phase_deg",
        "transmission_amplitudes", "transmission_efficiency_proxy",
        "command_target_mm", "element_positions_mm", "x_mm", "z_mm", "normalized_power_xz",
        "matlab_runtime_sec",
    }
    for result in results:
        if not isinstance(result, dict) or result.get("status") != "success" or not required.issubset(result):
            raise MetasurfaceMatlabError(f"Incomplete MATLAB metasurface result: {result}")
    for name in ("matlab_version", "matlab_release", "matlab_arch", "rng_algorithm"):
        if not isinstance(payload.get(name), str) or not payload[name]:
            raise MetasurfaceMatlabError("MATLAB runtime metadata is missing.")


def build_cst_project(
    run_dir: Path,
    metasurface_task_id: str,
    state: dict[str, Any],
    launch_cst: bool,
    timeout_sec: float = 75.0,
) -> dict[str, Any]:
    """Ask MATLAB to create a CST 2025 control-layout project or a dry-run VBA script."""

    build_id = "cst_build_" + uuid.uuid4().hex
    build_dir = run_dir / "cst" / build_id
    build_dir.mkdir(parents=True, exist_ok=False)
    config_path = build_dir / "config.json"
    result_path = build_dir / "result.json"
    cst_path = build_dir / "metasurface_layout.cst"
    vba_path = build_dir / "metasurface_layout_history.vba"
    selected = state["selected_design"]
    config = {
        "schema_version": 1,
        "build_id": build_id,
        "metasurface_task_id": metasurface_task_id,
        "launch_cst": launch_cst,
        "cst_prog_id": "CSTStudio.Application",
        "model_kind": "layout_scaffold",
        "output_cst_path": str(cst_path.resolve()),
        "output_vba_path": str(vba_path.resolve()),
        "frequency_ghz": state["frequency_ghz"],
        "focus_target_mm": state["focus_target_mm"],
        "unit_size_mm": state["unit_size_mm"],
        "array_size": state["array_size"],
        "incident_wave": state["incident_wave"],
        "horn_feed_position_mm": state["horn_feed_position_mm"],
        "binary_states": state["binary_states"],
        "element_positions_mm": selected["metrics"]["element_positions_mm"],
        "phase_codes": selected["metrics"]["phase_codes"],
    }
    _write_json_atomic(config_path, config)
    executable = _resolve_matlab_executable()
    expression = (
        f"addpath('{_matlab_quote(INTERFACE_DIR.resolve())}','-begin'); "
        f"agent_build_metasurface_cst_model('{_matlab_quote(config_path.resolve())}',"
        f"'{_matlab_quote(result_path.resolve())}');"
    )
    command = [executable, *(["-wait"] if os.name == "nt" else []), "-batch", expression]
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
    except subprocess.TimeoutExpired as exception:
        (build_dir / "stdout.log").write_text(
            (exception.stdout or "") if isinstance(exception.stdout, str) else "",
            encoding="utf-8",
        )
        (build_dir / "stderr.log").write_text(str(exception) + "\n", encoding="utf-8")
        if vba_path.is_file():
            return {
                "status": "success",
                "build_id": build_id,
                "metasurface_task_id": metasurface_task_id,
                "model_kind": "layout_scaffold",
                "scientific_status": "layout_only_not_full_wave_validated",
                "cst_prog_id": config["cst_prog_id"],
                "cst_launch_requested": launch_cst,
                "cst_launched": False,
                "cst_project_saved": cst_path.is_file(),
                "launch_error_type": "CstLaunchTimeout",
                "launch_error_message": (
                    f"CST OLE did not return within {timeout_sec:g} seconds. "
                    "The durable VBA layout was retained."
                ),
                "launch_retry_supported": True,
                "cst_project_path": str(cst_path.resolve()),
                "vba_history_path": str(vba_path.resolve()),
                "cell_count": len(config["phase_codes"]),
                "end_to_end_runtime_sec": time.perf_counter() - started,
            }
        raise MetasurfaceMatlabError(
            f"MATLAB-to-CST build timed out before producing a VBA layout: {exception}"
        ) from exception
    except OSError as exception:
        raise MetasurfaceMatlabError(
            f"Could not complete MATLAB-to-CST model build: {exception}"
        ) from exception
    (build_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (build_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    if not result_path.is_file():
        raise MetasurfaceMatlabError(
            f"MATLAB CST builder returned {completed.returncode} without a result."
        )
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exception:
        raise MetasurfaceMatlabError(
            f"Could not parse MATLAB CST builder result: {exception}"
        ) from exception
    if not isinstance(payload, dict) or payload.get("status") != "success":
        message = payload.get("message") if isinstance(payload, dict) else "malformed payload"
        raise MetasurfaceMatlabError(f"MATLAB CST builder failed: {message}")
    if payload.get("build_id") != build_id or completed.returncode != 0:
        raise MetasurfaceMatlabError("MATLAB CST builder returned inconsistent process evidence.")
    payload["end_to_end_runtime_sec"] = time.perf_counter() - started
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
