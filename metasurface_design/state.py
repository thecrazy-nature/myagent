"""Durable Agent workflow for a binary transmissive programmable metasurface."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .matlab_batch import build_cst_project, evaluate_batch
from .models import (
    InvalidMetasurfaceInput,
    MetasurfaceStateError,
    binary_state_characteristics,
    constraints,
    finite_number,
    normalize_binary_states,
    normalize_objective_weights,
    positive_number,
    validate_array_size,
    validate_evaluation_policy,
    validate_feed_position,
    validate_incident_wave,
    validate_optimizer,
    validate_target,
    validate_unit_size,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = PROJECT_ROOT / "runs" / "metasurface_designs"
INVALID_SCORE = 1.0e6


def create_design_task(
    focus_target_mm: Any,
    focus_tolerance_mm: Any,
    frequency_ghz: Any,
    unit_size_mm: Any,
    array_size: Any,
    incident_wave: Any,
    horn_feed_position_mm: Any,
    binary_states: Any,
    candidate_budget: Any,
    objective_weights: Any = None,
    evaluation_policy: Any = "record_only",
    roi_radius_mm: Any = 15.0,
    roi_half_depth_mm: Any = 20.0,
) -> dict[str, Any]:
    target, frequency = validate_target(focus_target_mm, frequency_ghz)
    tolerance = positive_number(focus_tolerance_mm, "focus_tolerance_mm")
    size = validate_array_size(array_size)
    unit_size = validate_unit_size(unit_size_mm, frequency)
    wave = validate_incident_wave(incident_wave)
    feed_position = validate_feed_position(horn_feed_position_mm, wave)
    states = normalize_binary_states(binary_states)
    if (
        isinstance(candidate_budget, bool)
        or not isinstance(candidate_budget, int)
        or not 1 <= candidate_budget <= 8
    ):
        raise InvalidMetasurfaceInput("candidate_budget must be an integer from 1 to 8.")
    policy = validate_evaluation_policy(evaluation_policy)
    metasurface_task_id = "metasurface_" + uuid.uuid4().hex
    run_dir = RUNS_ROOT / metasurface_task_id
    run_dir.mkdir(parents=True, exist_ok=False)
    now = _now()
    state = {
        "schema_version": 2,
        "metasurface_task_id": metasurface_task_id,
        "status": "created",
        "focus_target_mm": target,
        "focus_tolerance_mm": tolerance,
        "frequency_ghz": frequency,
        "surface_geometry": "planar",
        "unit_size_mm": unit_size,
        "array_size": size,
        "element_count": size[0] * size[1],
        "incident_wave": wave,
        "horn_feed_position_mm": feed_position,
        "binary_states": states,
        "binary_state_characteristics": binary_state_characteristics(states),
        "candidate_budget": candidate_budget,
        "remaining_candidate_budget": candidate_budget,
        "objective_weights": normalize_objective_weights(objective_weights),
        "evaluation_policy": policy,
        "roi_radius_mm": positive_number(roi_radius_mm, "roi_radius_mm"),
        "roi_half_depth_mm": positive_number(
            roi_half_depth_mm, "roi_half_depth_mm"
        ),
        "constraints": constraints(frequency, size, unit_size, wave, states),
        "unprogrammed_reference": None,
        "continuous_reference": None,
        "geometrical_optics_baseline": None,
        "evaluated_candidates": {},
        "candidate_assessments": {},
        "optimization_trajectory": [],
        "selected_design": None,
        "cst_model": None,
        "matlab_environment": None,
        "natural_language_request": None,
        "hermes_session_id": None,
        "hermes_final_response": None,
        "hermes_tool_trajectory": [],
        "end_to_end_runtime_sec": None,
        "governance": None,
        "created_at": now,
        "updated_at": now,
    }
    _write_state(run_dir, state)
    return _public_task(state)


def evaluate_baseline(metasurface_task_id: str) -> dict[str, Any]:
    run_dir, state = _load_state(metasurface_task_id)
    if state.get("geometrical_optics_baseline") is not None:
        raise MetasurfaceStateError("Metasurface baselines have already been evaluated.")
    candidates = [
        _candidate_spec("unprogrammed", "reference_unprogrammed", 0, 0.0, 0),
        _candidate_spec(
            "continuous_phase_conjugate", "reference_continuous", 0, 0.0, 0
        ),
        _candidate_spec(
            "geometrical_optics_binary", "baseline_geometrical_optics", 0, 0.0, 0
        ),
    ]
    batch = evaluate_batch(run_dir, metasurface_task_id, state, candidates)
    unprogrammed, continuous, geometrical_optics = batch["results"]
    state["unprogrammed_reference"] = {
        "configuration": candidates[0], "metrics": unprogrammed
    }
    state["continuous_reference"] = {
        "configuration": candidates[1], "metrics": continuous
    }
    state["geometrical_optics_baseline"] = {
        "configuration": candidates[2], "metrics": geometrical_optics
    }
    state["matlab_environment"] = _matlab_environment(batch)
    state["optimization_trajectory"].append({
        "event": "evaluate_geometrical_optics_baseline",
        "batch_id": batch["batch_id"],
        "candidate_ids": [item["candidate_id"] for item in candidates],
        "timestamp": _now(),
    })
    state["status"] = "baseline_evaluated"
    _write_state(run_dir, state)
    return {
        "success": True,
        "metasurface_task_id": metasurface_task_id,
        "unprogrammed_reference": _compact_metrics(unprogrammed),
        "continuous_phase_reference": _compact_metrics(continuous),
        "geometrical_optics_baseline": _compact_metrics(geometrical_optics),
        "binary_state_characteristics": state["binary_state_characteristics"],
        "interpretation": (
            "The geometrical-optics 0/1 code is the before-optimization baseline. "
            "The continuous-phase result only bounds target-point power. Supplied unit "
            "amplitudes determine the transmitted/input energy proxy."
        ),
        "remaining_candidate_budget": state["remaining_candidate_budget"],
        "matlab_process_count": 1,
        "batch_id": batch["batch_id"],
    }


def optimize_candidate(
    metasurface_task_id: str,
    optimizer: Any,
    max_iterations: Any,
    guard_weight: Any,
    seed: Any = 0,
) -> dict[str, Any]:
    run_dir, state = _load_state(metasurface_task_id)
    if state.get("geometrical_optics_baseline") is None:
        raise MetasurfaceStateError("Evaluate the geometrical-optics baseline first.")
    optimizer_name = validate_optimizer(optimizer)
    if (
        isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or not 1 <= max_iterations <= 8
    ):
        raise InvalidMetasurfaceInput("max_iterations must be an integer from 1 to 8.")
    guard = finite_number(guard_weight, "guard_weight")
    if not 0 <= guard <= 2:
        raise InvalidMetasurfaceInput("guard_weight must be in [0, 2].")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise InvalidMetasurfaceInput("seed must be a nonnegative integer.")
    specification = _candidate_spec(
        "optimized_binary", None, max_iterations, guard, seed, optimizer_name
    )
    candidate_id = specification["candidate_id"]
    existing = state["evaluated_candidates"].get(candidate_id)
    if isinstance(existing, dict):
        return _compact_candidate(existing, state)
    if state["remaining_candidate_budget"] <= 0:
        raise MetasurfaceStateError("The metasurface candidate budget is exhausted.")
    batch = evaluate_batch(run_dir, metasurface_task_id, state, [specification])
    metrics = batch["results"][0]
    scoring = _score(metrics, state)
    record = {"configuration": specification, "metrics": metrics, **scoring}
    state["evaluated_candidates"][candidate_id] = record
    state["remaining_candidate_budget"] -= 1
    state["matlab_environment"] = _matlab_environment(batch)
    state["optimization_trajectory"].append({
        "event": "optimize_binary_candidate",
        "candidate_id": candidate_id,
        "configuration": specification,
        "batch_id": batch["batch_id"],
        "timestamp": _now(),
    })
    state["status"] = "candidate_evaluated"
    _write_state(run_dir, state)
    return {
        **_compact_candidate(record, state),
        "success": True,
        "matlab_process_count": 1,
        "batch_id": batch["batch_id"],
    }


def evaluate_design(
    metasurface_task_id: str,
    candidate_id: str,
    evaluation_policy: Any = "record_only",
) -> dict[str, Any]:
    run_dir, state = _load_state(metasurface_task_id)
    policy = validate_evaluation_policy(evaluation_policy)
    candidate = state.get("evaluated_candidates", {}).get(candidate_id)
    if not isinstance(candidate, dict):
        raise InvalidMetasurfaceInput("candidate_id is not an evaluated binary design.")
    metrics = candidate["metrics"]
    baseline = state["geometrical_optics_baseline"]["metrics"]
    indicators = {
        "focus_tolerance_satisfied": (
            float(metrics["focus_error_mm"]) <= state["focus_tolerance_mm"]
        ),
        "energy_concentration_not_worse_than_go_baseline": (
            float(metrics["energy_concentration_ratio"])
            >= float(baseline["energy_concentration_ratio"])
        ),
        "target_power_not_worse_than_go_baseline": (
            float(metrics["target_power"]) >= float(baseline["target_power"])
        ),
        "lossless_energy_proxy_satisfied": (
            abs(float(metrics["transmission_efficiency_proxy"]) - 1.0) <= 1e-9
            if state["binary_state_characteristics"][
                "ideal_lossless_binary_assumption"
            ]
            else None
        ),
    }
    assessment = {
        "candidate_id": candidate_id,
        "evaluation_policy": policy,
        "criteria_status": "pending_researcher_decision",
        "overall_pass": None,
        "indicators": indicators,
        "metrics": _compact_metrics(metrics),
        "note": (
            "record_only intentionally does not turn provisional metrics into an acceptance "
            "decision. Define and version a research policy before using automatic pass/fail."
        ),
        "evaluated_at": _now(),
    }
    state["candidate_assessments"][candidate_id] = assessment
    state["status"] = "candidate_assessed"
    _write_state(run_dir, state)
    return {"success": True, "metasurface_task_id": metasurface_task_id, **assessment}


def save_design(
    metasurface_task_id: str, candidate_id: str, selection_reason: str
) -> dict[str, Any]:
    run_dir, state = _load_state(metasurface_task_id)
    if not isinstance(selection_reason, str) or not selection_reason.strip():
        raise InvalidMetasurfaceInput("selection_reason must be a non-empty string.")
    selected = state.get("evaluated_candidates", {}).get(candidate_id)
    assessment = state.get("candidate_assessments", {}).get(candidate_id)
    if not isinstance(selected, dict):
        raise InvalidMetasurfaceInput("candidate_id is not an evaluated binary design.")
    if not isinstance(assessment, dict):
        raise MetasurfaceStateError("Call evaluate_metasurface_design before saving.")
    saved = {
        **selected,
        "assessment": assessment,
        "selection_reason": selection_reason.strip(),
        "saved_at": _now(),
    }
    state["selected_design"] = saved
    state["status"] = "saved_pending_research_criteria"
    _write_state(run_dir, state)
    _write_json_atomic(run_dir / "selected_metasurface.json", saved)
    _write_control_csv(
        run_dir / "metasurface_control_codes.csv",
        selected["metrics"],
        state["binary_states"],
    )
    comparison = {
        "unprogrammed_reference": state["unprogrammed_reference"]["metrics"],
        "continuous_phase_reference": state["continuous_reference"]["metrics"],
        "geometrical_optics_baseline": state["geometrical_optics_baseline"]["metrics"],
        "selected_programmable_design": selected["metrics"],
        "improvements": selected["improvements"],
        "assessment": assessment,
    }
    _write_json_atomic(run_dir / "baseline_comparison.json", comparison)
    return {
        "success": True,
        "metasurface_task_id": metasurface_task_id,
        "selected_design": _compact_candidate(saved, state),
        "validation_status": assessment["criteria_status"],
        "geometrical_optics_baseline": _compact_metrics(
            state["geometrical_optics_baseline"]["metrics"]
        ),
        "artifacts": {
            "design": "selected_metasurface.json",
            "control_codes": "metasurface_control_codes.csv",
            "comparison": "baseline_comparison.json",
        },
    }


def build_cst_model(
    metasurface_task_id: str,
    launch_cst: bool = True,
    model_kind: str = "layout_scaffold",
) -> dict[str, Any]:
    run_dir, state = _load_state(metasurface_task_id)
    if not isinstance(launch_cst, bool):
        raise InvalidMetasurfaceInput("launch_cst must be a boolean.")
    if model_kind != "layout_scaffold":
        raise InvalidMetasurfaceInput("Only model_kind='layout_scaffold' is currently supported.")
    if not isinstance(state.get("selected_design"), dict):
        raise MetasurfaceStateError("Save an evaluated design before building its CST model.")
    result = build_cst_project(run_dir, metasurface_task_id, state, launch_cst)
    state["cst_model"] = result
    state["status"] = "cst_layout_built" if result.get("cst_launched") else "cst_layout_generated"
    _write_state(run_dir, state)
    return {
        "success": True,
        "metasurface_task_id": metasurface_task_id,
        **result,
        "scientific_status": "layout_only_not_full_wave_validated",
        "workflow_continues_after_launch_failure": True,
    }


def attach_agent_metadata(
    metasurface_task_id: str,
    request: str,
    session_id: str,
    final_response: str,
    tool_trajectory: list[dict[str, Any]] | None = None,
    end_to_end_runtime_sec: float | None = None,
    governance: dict[str, Any] | None = None,
) -> None:
    run_dir, state = _load_state(metasurface_task_id)
    state["natural_language_request"] = request
    state["hermes_session_id"] = session_id
    state["hermes_final_response"] = final_response
    state["hermes_tool_trajectory"] = tool_trajectory or []
    state["end_to_end_runtime_sec"] = end_to_end_runtime_sec
    state["governance"] = governance
    _write_state(run_dir, state)


def _score(metrics: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    reference = state["continuous_reference"]["metrics"]
    baseline = state["geometrical_optics_baseline"]["metrics"]
    tolerance = state["focus_tolerance_mm"]
    error = float(metrics["focus_error_mm"])
    pslr = float(metrics["peak_to_sidelobe_ratio_db"])
    accuracy_penalty = min(error / tolerance, 10.0)
    concentration_penalty = 1.0 - min(
        max(float(metrics["energy_concentration_ratio"]), 0.0), 1.0
    )
    sidelobe_penalty = 1.0 / (1.0 + 10.0 ** (pslr / 10.0))
    weights = state["objective_weights"]
    raw_score = (
        weights["focus_accuracy"] * accuracy_penalty
        + weights["energy_concentration"] * concentration_penalty
        + weights["sidelobe_suppression"] * sidelobe_penalty
    )
    hard = error <= tolerance
    baseline_power = max(float(baseline["target_power"]), float.fromhex("0x1.0p-1022"))
    reference_power = max(float(reference["target_power"]), float.fromhex("0x1.0p-1022"))
    improvements = {
        "focus_error_reduction_mm_vs_go": float(baseline["focus_error_mm"]) - error,
        "target_power_gain_db_vs_go": 10.0 * math.log10(
            max(float(metrics["target_power"]), float.fromhex("0x1.0p-1022"))
            / baseline_power
        ),
        "energy_concentration_change_vs_go": (
            float(metrics["energy_concentration_ratio"])
            - float(baseline["energy_concentration_ratio"])
        ),
        "target_power_fraction_of_continuous_reference": min(
            max(float(metrics["target_power"]) / reference_power, 0.0), 1.0
        ),
    }
    return {
        "objective_score": raw_score if hard else INVALID_SCORE + raw_score,
        "unpenalized_score": raw_score,
        "hard_focus_constraint_satisfied": hard,
        "improvements": improvements,
    }


def _candidate_spec(
    mode: str,
    fixed_id: str | None,
    max_iterations: int,
    guard_weight: float,
    seed: int,
    optimizer: str | None = None,
) -> dict[str, Any]:
    parameters = {
        "mode": mode,
        "optimizer": optimizer,
        "max_iterations": max_iterations,
        "guard_weight": guard_weight,
        "seed": seed,
    }
    if fixed_id is None:
        canonical = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
        fixed_id = "meta_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return {"candidate_id": fixed_id, **parameters}


def _compact_candidate(record: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    metrics = record["metrics"]
    histogram = Counter(int(value) for value in metrics.get("phase_codes", []))
    return {
        "metasurface_task_id": state["metasurface_task_id"],
        "candidate_id": record["configuration"]["candidate_id"],
        "configuration": record["configuration"],
        "metrics": _compact_metrics(metrics),
        "objective_score": record.get("objective_score"),
        "hard_focus_constraint_satisfied": record.get(
            "hard_focus_constraint_satisfied"
        ),
        "improvements": record.get("improvements"),
        "phase_code_histogram": {
            str(key): value for key, value in sorted(histogram.items())
        },
        "remaining_candidate_budget": state["remaining_candidate_budget"],
    }


def _compact_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    omitted = {
        "phase_codes", "phase_deg", "transmission_amplitudes",
        "element_positions_mm", "x_mm", "z_mm", "normalized_power_xz",
        "lateral_profile", "axial_profile",
    }
    return {key: value for key, value in metrics.items() if key not in omitted}


def _public_task(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "metasurface_task_id": state["metasurface_task_id"],
        "focus_target_mm": state["focus_target_mm"],
        "focus_tolerance_mm": state["focus_tolerance_mm"],
        "frequency_ghz": state["frequency_ghz"],
        "unit_size_mm": state["unit_size_mm"],
        "array_size": state["array_size"],
        "incident_wave": state["incident_wave"],
        "horn_feed_position_mm": state["horn_feed_position_mm"],
        "binary_states": state["binary_states"],
        "binary_state_characteristics": state["binary_state_characteristics"],
        "candidate_budget": state["candidate_budget"],
        "objective_weights": state["objective_weights"],
        "evaluation_policy": state["evaluation_policy"],
        "constraints": state["constraints"],
        "next_required_action": "evaluate_metasurface_baseline",
    }


def _matlab_environment(batch: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": batch["matlab_version"],
        "release": batch["matlab_release"],
        "architecture": batch["matlab_arch"],
        "rng_algorithm": batch["rng_algorithm"],
    }


def _load_state(metasurface_task_id: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(metasurface_task_id, str) or not metasurface_task_id.startswith(
        "metasurface_"
    ):
        raise InvalidMetasurfaceInput("Invalid metasurface_task_id.")
    run_dir = RUNS_ROOT / metasurface_task_id
    path = run_dir / "design_state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exception:
        raise MetasurfaceStateError(
            f"Could not load metasurface state: {exception}"
        ) from exception
    return run_dir, state


def _write_state(run_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _now()
    _write_json_atomic(run_dir / "design_state.json", state)
    selected = state.get("selected_design") or {}
    configuration = selected.get("configuration") or {}
    metrics = selected.get("metrics") or {}
    _write_json_atomic(run_dir / "design_summary.json", {
        "metasurface_task_id": state.get("metasurface_task_id"),
        "focus_target_mm": state.get("focus_target_mm"),
        "frequency_ghz": state.get("frequency_ghz"),
        "array_size": state.get("array_size"),
        "element_count": state.get("element_count"),
        "incident_wave": state.get("incident_wave"),
        "optimizer": configuration.get("optimizer"),
        "focus_error_mm": metrics.get("focus_error_mm"),
        "hard_focus_constraint_satisfied": selected.get(
            "hard_focus_constraint_satisfied"
        ),
        "criteria_status": (selected.get("assessment") or {}).get(
            "criteria_status"
        ),
        "status": state.get("status"),
        "updated_at": state.get("updated_at"),
    })


def _write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_control_csv(
    path: Path,
    metrics: dict[str, Any],
    states: dict[str, dict[str, float]],
) -> None:
    positions = metrics["element_positions_mm"]
    codes = metrics["phase_codes"]
    phases = metrics["phase_deg"]
    amplitudes = metrics["transmission_amplitudes"]
    lines = [
        "element_index,x_mm,y_mm,z_mm,binary_code,transmission_amplitude,phase_deg"
    ]
    lines.extend(
        f"{index},{position[0]:.12g},{position[1]:.12g},{position[2]:.12g},"
        f"{int(code)},{amplitude:.12g},{phase:.12g}"
        for index, (position, code, amplitude, phase) in enumerate(
            zip(positions, codes, amplitudes, phases), 1
        )
    )
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
