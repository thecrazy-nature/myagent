"""Deterministic coarse geometry-family search."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .geometry import candidate_parameters, generate_geometry
from .matlab_batch import evaluate_batch
from .metrics import score_metrics


def run_search(
    run_dir: Path,
    state: dict[str, Any],
    family: str,
    parameter_bounds: dict[str, Any],
    candidate_budget: int,
    seed: int,
) -> dict[str, Any]:
    parameters = candidate_parameters(family, parameter_bounds, candidate_budget)
    geometries = [generate_geometry(family, item, seed) for item in parameters]
    valid = [geometry for geometry in geometries if geometry["constraint_check"]["valid"]]
    batch = evaluate_batch(
        run_dir,
        state["design_task_id"],
        state["focus_target_mm"],
        state["focus_tolerance_mm"],
        state["roi_radius_mm"],
        state["roi_half_depth_mm"],
        valid,
    )
    by_id = {result["geometry_id"]: result for result in batch["results"]}
    candidates: list[dict[str, Any]] = []
    for geometry in geometries:
        if not geometry["constraint_check"]["valid"]:
            candidates.append({
                "geometry": geometry,
                "status": "invalid_geometry",
                "objective_score": 1.0e9,
            })
            continue
        metrics = by_id[geometry["geometry_id"]]
        scoring = score_metrics(
            metrics,
            state["baseline"]["metrics"],
            state["objective_weights"],
            state["focus_tolerance_mm"],
        )
        candidates.append({"geometry": geometry, "metrics": metrics, "status": "evaluated", **scoring})
    candidates.sort(key=lambda item: (item["objective_score"], item["geometry"]["geometry_id"]))
    return {
        "family": family,
        "parameter_bounds": parameter_bounds,
        "candidate_budget": candidate_budget,
        "seed": seed,
        "batch_id": batch["batch_id"],
        "matlab_process_count": 1,
        "batch_runtime_sec": batch["runtime_sec"],
        "end_to_end_runtime_sec": batch["end_to_end_runtime_sec"],
        "candidates": candidates,
    }
