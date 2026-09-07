"""Persistent workflow state for autonomous array geometry design."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .geometry import generate_geometry
from .matlab_batch import evaluate_batch
from .metrics import compare_to_baseline, score_metrics
from .models import (
    SUPPORTED_FAMILIES,
    ArrayDesignStateError,
    InvalidArrayDesignInput,
    baseline_constraints,
    normalize_weights,
    validate_positive,
    validate_vector3,
)
from .search import run_search

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = PROJECT_ROOT / "runs" / "array_designs"


def create_design_task(
    focus_target_mm: Any,
    focus_tolerance_mm: Any,
    search_budget: Any,
    allowed_geometry_families: Any,
    objective_weights: Any = None,
    roi_radius_mm: Any = 5.0,
    roi_half_depth_mm: Any = 10.0,
) -> dict[str, Any]:
    target = validate_vector3(focus_target_mm, "focus_target_mm")
    tolerance = validate_positive(focus_tolerance_mm, "focus_tolerance_mm")
    if isinstance(search_budget, bool) or not isinstance(search_budget, int) or not 1 <= search_budget <= 12:
        raise InvalidArrayDesignInput("search_budget must be an integer from 1 to 12.")
    if not isinstance(allowed_geometry_families, list) or not allowed_geometry_families:
        raise InvalidArrayDesignInput("allowed_geometry_families must be a non-empty list.")
    families = list(dict.fromkeys(str(item) for item in allowed_geometry_families))
    unsupported = set(families) - set(SUPPORTED_FAMILIES)
    if unsupported:
        raise InvalidArrayDesignInput(f"Unsupported geometry families: {sorted(unsupported)}")
    design_task_id = "design_" + uuid.uuid4().hex
    run_dir = RUNS_ROOT / design_task_id
    run_dir.mkdir(parents=True, exist_ok=False)
    now = _now()
    state = {
        "schema_version": 1,
        "design_task_id": design_task_id,
        "status": "created",
        "natural_language_request": None,
        "hermes_session_id": None,
        "hermes_final_response": None,
        "hermes_tool_trajectory": [],
        "end_to_end_runtime_sec": None,
        "focus_target_mm": target,
        "focus_tolerance_mm": tolerance,
        "search_budget": search_budget,
        "remaining_search_budget": search_budget,
        "allowed_geometry_families": families,
        "objective_weights": normalize_weights(objective_weights),
        "roi_radius_mm": validate_positive(roi_radius_mm, "roi_radius_mm"),
        "roi_half_depth_mm": validate_positive(roi_half_depth_mm, "roi_half_depth_mm"),
        "constraints": baseline_constraints(),
        "baseline": None,
        "search_trajectory": [],
        "evaluated_geometries": {},
        "selected_design": None,
        "created_at": now,
        "updated_at": now,
    }
    _write_state(run_dir, state)
    return _public_task(state)


def evaluate_geometry(
    design_task_id: str,
    geometry_family: str,
    parameters: dict[str, Any] | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    run_dir, state = _load_state(design_task_id)
    geometry = generate_geometry(geometry_family, parameters, seed)
    if not geometry["constraint_check"]["valid"]:
        raise InvalidArrayDesignInput("Geometry violates frozen physical constraints.")
    if state["baseline"] is None and geometry_family != "baseline":
        raise ArrayDesignStateError("Evaluate the real baseline geometry before any candidate.")
    batch = evaluate_batch(
        run_dir, design_task_id, state["focus_target_mm"], state["focus_tolerance_mm"],
        state["roi_radius_mm"], state["roi_half_depth_mm"], [geometry],
    )
    metrics = batch["results"][0]
    record: dict[str, Any] = {"geometry": geometry, "metrics": metrics}
    if geometry_family == "baseline":
        scoring = score_metrics(metrics, metrics, state["objective_weights"], state["focus_tolerance_mm"])
        record.update(scoring)
        state["baseline"] = record
        state["status"] = "baseline_evaluated"
    else:
        scoring = score_metrics(metrics, state["baseline"]["metrics"], state["objective_weights"], state["focus_tolerance_mm"])
        record.update(scoring)
        state["evaluated_geometries"][geometry["geometry_id"]] = record
        state["status"] = "candidate_evaluated"
    state["search_trajectory"].append({
        "event": "evaluate_geometry", "geometry_id": geometry["geometry_id"],
        "family": geometry["family"], "batch_id": batch["batch_id"], "timestamp": _now(),
    })
    _write_state(run_dir, state)
    return {
        "success": True,
        "design_task_id": design_task_id,
        "geometry": _compact_geometry(geometry),
        "metrics": _compact_metrics(metrics),
        **scoring,
        "matlab_process_count": 1,
        "batch_id": batch["batch_id"],
    }


def search_geometry(
    design_task_id: str,
    geometry_family: str,
    parameter_bounds: dict[str, Any],
    candidate_budget: int,
    seed: int = 0,
) -> dict[str, Any]:
    run_dir, state = _load_state(design_task_id)
    if state["baseline"] is None:
        raise ArrayDesignStateError("Evaluate baseline before search_array_geometry.")
    if geometry_family not in state["allowed_geometry_families"]:
        raise InvalidArrayDesignInput("geometry_family was not allowed when the task was created.")
    if isinstance(candidate_budget, bool) or not isinstance(candidate_budget, int) or candidate_budget < 1:
        raise InvalidArrayDesignInput("candidate_budget must be a positive integer.")
    if candidate_budget > state["remaining_search_budget"]:
        raise InvalidArrayDesignInput("candidate_budget exceeds the task's remaining search budget.")
    result = run_search(run_dir, state, geometry_family, parameter_bounds, candidate_budget, seed)
    state["remaining_search_budget"] -= candidate_budget
    for candidate in result["candidates"]:
        geometry_id = candidate["geometry"]["geometry_id"]
        state["evaluated_geometries"][geometry_id] = candidate
    state["search_trajectory"].append({
        "event": "search_geometry", "family": geometry_family,
        "parameter_bounds": parameter_bounds, "candidate_budget": candidate_budget,
        "seed": seed, "batch_id": result["batch_id"], "candidate_ids": [
            item["geometry"]["geometry_id"] for item in result["candidates"]
        ], "timestamp": _now(),
    })
    state["status"] = "search_complete"
    _write_state(run_dir, state)
    return {
        "success": True,
        "design_task_id": design_task_id,
        "remaining_search_budget": state["remaining_search_budget"],
        "baseline_objective_score": state["baseline"]["objective_score"],
        "family": result["family"],
        "parameter_bounds": result["parameter_bounds"],
        "candidate_budget": result["candidate_budget"],
        "seed": result["seed"],
        "batch_id": result["batch_id"],
        "matlab_process_count": result["matlab_process_count"],
        "batch_runtime_sec": result["batch_runtime_sec"],
        "end_to_end_runtime_sec": result["end_to_end_runtime_sec"],
        "candidates": [_compact_candidate(candidate) for candidate in result["candidates"]],
    }


def save_design(design_task_id: str, geometry_id: str, selection_reason: str) -> dict[str, Any]:
    run_dir, state = _load_state(design_task_id)
    if not isinstance(selection_reason, str) or not selection_reason.strip():
        raise InvalidArrayDesignInput("selection_reason must be a non-empty string.")
    baseline = state.get("baseline")
    if not baseline:
        raise ArrayDesignStateError("Cannot save before baseline evaluation.")
    if geometry_id == baseline["geometry"]["geometry_id"]:
        selected = baseline
    else:
        selected = state["evaluated_geometries"].get(geometry_id)
    if not isinstance(selected, dict) or selected.get("status") == "invalid_geometry":
        raise InvalidArrayDesignInput("geometry_id is not a valid evaluated design.")
    improvement = compare_to_baseline(selected["metrics"], baseline["metrics"])
    saved = {
        "geometry_id": geometry_id,
        "selection_reason": selection_reason.strip(),
        "geometry": selected["geometry"],
        "metrics": selected["metrics"],
        "objective_score": selected["objective_score"],
        "hard_constraint_satisfied": selected["hard_constraint_satisfied"],
        "improvement_percentages": improvement,
        "saved_at": _now(),
    }
    state["selected_design"] = saved
    state["status"] = "saved"
    _write_state(run_dir, state)
    _write_json_atomic(run_dir / "selected_geometry.json", selected["geometry"])
    _write_json_atomic(run_dir / "selected_metrics.json", selected["metrics"])
    _write_coordinates(run_dir / "element_coordinates.csv", selected["geometry"]["element_positions_mm"])
    comparison = {
        "baseline_metrics": baseline["metrics"],
        "optimized_metrics": selected["metrics"],
        "improvement_percentages": improvement,
    }
    _write_json_atomic(run_dir / "baseline_comparison.json", comparison)
    return {
        "success": True,
        "design_task_id": design_task_id,
        "selected_design": {
            **{key: value for key, value in saved.items() if key not in {"geometry", "metrics"}},
            "geometry": _compact_geometry(saved["geometry"]),
            "metrics": _compact_metrics(saved["metrics"]),
        },
        "baseline_metrics": _compact_metrics(baseline["metrics"]),
        "optimized_metrics": _compact_metrics(selected["metrics"]),
        "improvement_percentages": improvement,
    }


def attach_agent_metadata(
    design_task_id: str,
    request: str,
    session_id: str,
    final_response: str,
    tool_trajectory: list[dict[str, Any]] | None = None,
    end_to_end_runtime_sec: float | None = None,
) -> None:
    run_dir, state = _load_state(design_task_id)
    state["natural_language_request"] = request
    state["hermes_session_id"] = session_id
    state["hermes_final_response"] = final_response
    state["hermes_tool_trajectory"] = tool_trajectory or []
    state["end_to_end_runtime_sec"] = end_to_end_runtime_sec
    _write_state(run_dir, state)


def _public_task(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "design_task_id": state["design_task_id"],
        "focus_target_mm": state["focus_target_mm"],
        "focus_tolerance_mm": state["focus_tolerance_mm"],
        "search_budget": state["search_budget"],
        "allowed_geometry_families": state["allowed_geometry_families"],
        "objective_weights": state["objective_weights"],
        "roi_radius_mm": state["roi_radius_mm"],
        "roi_half_depth_mm": state["roi_half_depth_mm"],
        "constraints": state["constraints"],
        "next_required_action": "evaluate_array_geometry with geometry_family=baseline",
    }


def _load_state(design_task_id: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(design_task_id, str) or not design_task_id.startswith("design_"):
        raise InvalidArrayDesignInput("Invalid design_task_id.")
    run_dir = RUNS_ROOT / design_task_id
    path = run_dir / "design_state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exception:
        raise ArrayDesignStateError(f"Could not load design state: {exception}") from exception
    return run_dir, state


def _write_state(run_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _now()
    _write_json_atomic(run_dir / "design_state.json", state)


def _write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_coordinates(path: Path, positions: list[list[float]]) -> None:
    lines = ["element_index,x_mm,y_mm,z_mm"]
    lines.extend(f"{index},{row[0]:.12g},{row[1]:.12g},{row[2]:.12g}" for index, row in enumerate(positions, 1))
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    return {
        "geometry_id": geometry["geometry_id"],
        "family": geometry["family"],
        "parameters": geometry["parameters"],
        "seed": geometry["seed"],
        "constraint_check": geometry["constraint_check"],
        "element_count": len(geometry["element_positions_mm"]),
    }


def _compact_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    omitted = {"lateral_profile", "axial_profile", "geometry_constraint_check"}
    return {key: value for key, value in metrics.items() if key not in omitted}


def _compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    compact = {key: value for key, value in candidate.items() if key not in {"geometry", "metrics"}}
    compact["geometry"] = _compact_geometry(candidate["geometry"])
    if isinstance(candidate.get("metrics"), dict):
        compact["metrics"] = _compact_metrics(candidate["metrics"])
    return compact
