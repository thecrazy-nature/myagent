"""Hermes handlers for persistent near-field focusing operations."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def em_focus_ping(args: dict[str, Any], **kwargs: Any) -> str:
    """Echo one diagnostic string."""
    del kwargs
    try:
        message = args.get("message")
        if not isinstance(message, str):
            raise ValueError("message must be a string.")
        return _json({"success": True, "message": message})
    except Exception as exception:
        return _error_json(exception, args)


def create_focus_task(args: dict[str, Any], **kwargs: Any) -> str:
    """Create a new persisted focus task."""
    del kwargs
    return _invoke(
        args,
        lambda: _agent_api().create_task(
            args["target_mm"],
            args["tolerance_mm"],
            args["max_refinements"],
            args.get("additional_targets_mm"),
            args.get("frequency_ghz", 28.0),
            args.get("modulation_frequency_mhz", 200.0),
            args.get("element_count", 256),
            args.get("polarization", "scalar"),
        ),
    )


def get_focus_task_state(args: dict[str, Any], **kwargs: Any) -> str:
    """Load the last durable workflow checkpoint for interrupted-session recovery."""
    del kwargs
    return _invoke(
        args, lambda: _agent_api().inspect_task(args.get("agent_task_id"))
    )


def run_focus_simulation(args: dict[str, Any], **kwargs: Any) -> str:
    """Run one real MATLAB simulation through the existing bridge."""
    del kwargs
    return _invoke(
        args,
        lambda: _agent_api().run_task_simulation(args.get("agent_task_id")),
    )


def evaluate_focus(args: dict[str, Any], **kwargs: Any) -> str:
    """Evaluate the latest real peak against the desired target."""
    del kwargs
    return _invoke(
        args, lambda: _agent_api().evaluate_task(args.get("agent_task_id"))
    )


def refine_focus(args: dict[str, Any], **kwargs: Any) -> str:
    """Apply one deterministic workflow-level parameter compensation."""
    del kwargs
    return _invoke(args, lambda: _agent_api().refine_task(args.get("agent_task_id")))


def create_array_design_task(args: dict[str, Any], **kwargs: Any) -> str:
    """Create a persisted, physically constrained geometry-design task."""
    del kwargs
    return _invoke(args, lambda: _array_api().create_design_task(
        args["focus_target_mm"], args["focus_tolerance_mm"], args["search_budget"],
        args["allowed_geometry_families"], args["objective_weights"],
        args.get("roi_radius_mm", 5.0), args.get("roi_half_depth_mm", 10.0),
    ))


def evaluate_array_geometry(args: dict[str, Any], **kwargs: Any) -> str:
    """Evaluate one deterministic geometry with real MATLAB."""
    del kwargs
    return _invoke(args, lambda: _array_api().evaluate_geometry(
        args["design_task_id"], args["geometry_family"], args["parameters"], args["seed"]
    ))


def search_array_geometry(args: dict[str, Any], **kwargs: Any) -> str:
    """Search one parameterized family in one real MATLAB batch."""
    del kwargs
    return _invoke(args, lambda: _array_api().search_geometry(
        args["design_task_id"], args["geometry_family"], args["parameter_bounds"],
        args["candidate_budget"], args["seed"],
    ))


def save_array_design(args: dict[str, Any], **kwargs: Any) -> str:
    """Persist the final selected evaluated design."""
    del kwargs
    return _invoke(args, lambda: _array_api().save_design(
        args["design_task_id"], args["geometry_id"], args["selection_reason"]
    ))


def _invoke(args: dict[str, Any], operation: Callable[[], dict[str, Any]]) -> str:
    try:
        return _json(operation())
    except Exception as exception:
        return _error_json(exception, args)


def _error_json(exception: Exception, args: Any) -> str:
    result: dict[str, Any] = {
        "success": False,
        "error": True,
        "error_type": type(exception).__name__,
        "message": str(exception),
    }
    agent_task_id = None
    candidate_from_exception = getattr(exception, "agent_task_id", None)
    if isinstance(candidate_from_exception, str):
        agent_task_id = candidate_from_exception
    if agent_task_id is None and isinstance(args, dict):
        candidate = args.get("agent_task_id") or args.get("design_task_id")
        if isinstance(candidate, str):
            agent_task_id = candidate
    if agent_task_id is not None:
        key = "design_task_id" if agent_task_id.startswith("design_") else "agent_task_id"
        result[key] = agent_task_id
    simulation_run_id = getattr(exception, "task_id", None)
    if isinstance(simulation_run_id, str) and simulation_run_id.startswith("sim_"):
        result["simulation_run_id"] = simulation_run_id
    return _json(result)


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _agent_api() -> Any:
    """Import the project API lazily so isolated Plugin Doctor can register tools."""
    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    import em_focus_agent

    return em_focus_agent


def _array_api() -> Any:
    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    import array_design

    return array_design
