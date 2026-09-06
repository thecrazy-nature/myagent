"""Persistent state machine for Hermes-managed MATLAB focusing experiments."""

from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from typing import Any

from bridge import run_simulation

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_TASKS_ROOT = PROJECT_ROOT / "runs" / "agent_tasks"
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Deterministic workflow-level compensation gain; this is not a field optimizer.
FEEDBACK_ALPHA = 0.7


class AgentTaskError(Exception):
    """Expected agent-layer validation or state-transition failure."""

    def __init__(self, message: str, *, agent_task_id: str | None = None) -> None:
        super().__init__(message)
        self.agent_task_id = agent_task_id


def create_task(
    target_mm: Sequence[Real],
    tolerance_mm: Real = 5.0,
    max_refinements: int = 2,
) -> dict[str, Any]:
    """Create and persist one configured focusing task."""

    desired = _validate_vector(target_mm, "target_mm")
    tolerance = _validate_positive_number(tolerance_mm, "tolerance_mm")
    if isinstance(max_refinements, bool) or not isinstance(max_refinements, int):
        raise AgentTaskError("max_refinements must be a nonnegative integer.")
    if max_refinements < 0:
        raise AgentTaskError("max_refinements must be a nonnegative integer.")

    AGENT_TASKS_ROOT.mkdir(parents=True, exist_ok=True)
    while True:
        agent_task_id = f"agent_{uuid.uuid4().hex}"
        task_dir = AGENT_TASKS_ROOT / agent_task_id
        try:
            task_dir.mkdir(exist_ok=False)
            break
        except FileExistsError:
            continue

    state: dict[str, Any] = {
        "schema_version": 1,
        "agent_task_id": agent_task_id,
        "desired_target_mm": desired,
        "current_command_target_mm": desired.copy(),
        "tolerance_mm": tolerance,
        "max_refinements": max_refinements,
        "refinement_count": 0,
        "status": "configured",
        "history": [],
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }
    _save_state(state)
    return state


def load_task(agent_task_id: str) -> dict[str, Any]:
    """Load and minimally validate one persisted task."""

    state_path = _state_path(agent_task_id)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exception:
        raise AgentTaskError(
            "Agent task does not exist.", agent_task_id=agent_task_id
        ) from exception
    except (OSError, UnicodeError, json.JSONDecodeError) as exception:
        raise AgentTaskError(
            f"Could not read agent task state: {exception}",
            agent_task_id=agent_task_id,
        ) from exception
    if not isinstance(state, dict) or state.get("agent_task_id") != agent_task_id:
        raise AgentTaskError(
            "Agent task state is invalid or has a mismatched ID.",
            agent_task_id=agent_task_id,
        )
    _validate_vector(state.get("desired_target_mm"), "desired_target_mm")
    _validate_vector(
        state.get("current_command_target_mm"), "current_command_target_mm"
    )
    if not isinstance(state.get("history"), list):
        raise AgentTaskError(
            "Agent task history must be a list.", agent_task_id=agent_task_id
        )
    return state


def run_task_simulation(agent_task_id: str) -> dict[str, Any]:
    """Run MATLAB once for the task's current commanded target."""

    state = load_task(agent_task_id)
    if state.get("status") not in {"configured", "refined"}:
        raise AgentTaskError(
            "Simulation is only allowed after task creation or refinement; "
            "evaluate the pending simulation or refine a failed evaluation first.",
            agent_task_id=agent_task_id,
        )

    commanded = _validate_vector(
        state["current_command_target_mm"], "current_command_target_mm"
    )
    simulation_run_id = f"sim_{uuid.uuid4().hex}"
    started = time.perf_counter()
    matlab_result = run_simulation(
        target_mm=commanded,
        task_id=simulation_run_id,
    )
    bridge_wall_time_sec = time.perf_counter() - started

    actual = _validate_vector(matlab_result.get("actual_peak_mm"), "actual_peak_mm")
    requested = _validate_vector(
        matlab_result.get("requested_focus_mm"), "requested_focus_mm"
    )
    if any(abs(a - b) > 1e-9 for a, b in zip(requested, commanded)):
        raise AgentTaskError(
            "MATLAB result requested_focus_mm does not match the commanded target.",
            agent_task_id=agent_task_id,
        )

    event = {
        "event": "simulation",
        "timestamp": _utc_now(),
        "simulation_run_id": simulation_run_id,
        "desired_target_mm": state["desired_target_mm"].copy(),
        "commanded_target_mm": commanded,
        "actual_peak_mm": actual,
        "peak_power": float(matlab_result["peak_power"]),
        "requested_power": float(matlab_result["requested_power"]),
        "peak_power_definition": matlab_result["peak_power_definition"],
        "matlab_runtime_sec": float(matlab_result["runtime_sec"]),
        "bridge_wall_time_sec": bridge_wall_time_sec,
    }
    state["history"].append(event)
    state["status"] = "simulated"
    state["updated_at"] = _utc_now()
    _save_state(state)
    return {
        "success": True,
        "agent_task_id": agent_task_id,
        **{key: value for key, value in event.items() if key not in {"event", "timestamp"}},
    }


def evaluate_task(agent_task_id: str) -> dict[str, Any]:
    """Evaluate the latest MATLAB peak against the immutable desired target."""

    state = load_task(agent_task_id)
    if state.get("status") != "simulated":
        raise AgentTaskError(
            "Evaluation requires one unevaluated simulation result.",
            agent_task_id=agent_task_id,
        )
    simulation = _latest_event(state, "simulation")
    desired = _validate_vector(state["desired_target_mm"], "desired_target_mm")
    actual = _validate_vector(simulation["actual_peak_mm"], "actual_peak_mm")
    focus_error_mm = math.dist(actual, desired)
    tolerance_mm = _validate_positive_number(state["tolerance_mm"], "tolerance_mm")
    achieved = focus_error_mm <= tolerance_mm
    remaining = state["max_refinements"] - state["refinement_count"]
    status = "focus_achieved" if achieved else (
        "evaluation_failed" if remaining > 0 else "refinement_exhausted"
    )
    event = {
        "event": "evaluation",
        "timestamp": _utc_now(),
        "simulation_run_id": simulation["simulation_run_id"],
        "desired_target_mm": desired.copy(),
        "commanded_target_mm": simulation["commanded_target_mm"].copy(),
        "actual_peak_mm": actual.copy(),
        "focus_error_mm": focus_error_mm,
        "tolerance_mm": tolerance_mm,
        "success": achieved,
        "refinement_count": state["refinement_count"],
        "remaining_refinements": remaining,
    }
    state["history"].append(event)
    state["status"] = status
    state["updated_at"] = _utc_now()
    _save_state(state)
    return {
        "agent_task_id": agent_task_id,
        **{key: value for key, value in event.items() if key not in {"event", "timestamp"}},
        "status": status,
    }


def refine_task(agent_task_id: str) -> dict[str, Any]:
    """Apply one workflow-level fixed-gain compensation after a failed experiment."""

    state = load_task(agent_task_id)
    if state.get("status") != "evaluation_failed":
        raise AgentTaskError(
            "Refinement requires a failed evaluation with remaining budget.",
            agent_task_id=agent_task_id,
        )
    if state["refinement_count"] >= state["max_refinements"]:
        raise AgentTaskError(
            "No refinement budget remains.", agent_task_id=agent_task_id
        )

    evaluation = _latest_event(state, "evaluation")
    if evaluation.get("success") is not False:
        raise AgentTaskError(
            "The latest evaluation did not fail.", agent_task_id=agent_task_id
        )
    desired = _validate_vector(state["desired_target_mm"], "desired_target_mm")
    actual = _validate_vector(evaluation["actual_peak_mm"], "actual_peak_mm")
    old_command = _validate_vector(
        state["current_command_target_mm"], "current_command_target_mm"
    )
    correction = [desired[index] - actual[index] for index in range(3)]
    new_command = [
        old_command[index] + FEEDBACK_ALPHA * correction[index]
        for index in range(3)
    ]
    state["refinement_count"] += 1
    event = {
        "event": "refinement",
        "timestamp": _utc_now(),
        "based_on_simulation_run_id": evaluation["simulation_run_id"],
        "desired_target_mm": desired.copy(),
        "actual_peak_mm": actual.copy(),
        "old_command_target_mm": old_command,
        "correction_mm": correction,
        "alpha": FEEDBACK_ALPHA,
        "new_command_target_mm": new_command,
        "refinement_count": state["refinement_count"],
        "remaining_refinements": (
            state["max_refinements"] - state["refinement_count"]
        ),
    }
    state["current_command_target_mm"] = new_command
    state["history"].append(event)
    state["status"] = "refined"
    state["updated_at"] = _utc_now()
    _save_state(state)
    return {
        "success": True,
        "agent_task_id": agent_task_id,
        **{key: value for key, value in event.items() if key not in {"event", "timestamp"}},
        "status": "refined",
    }


def _latest_event(state: dict[str, Any], event_name: str) -> dict[str, Any]:
    for event in reversed(state["history"]):
        if isinstance(event, dict) and event.get("event") == event_name:
            return event
    raise AgentTaskError(
        f"Task has no {event_name} event.", agent_task_id=state.get("agent_task_id")
    )


def _state_path(agent_task_id: str) -> Path:
    if not isinstance(agent_task_id, str) or not TASK_ID_PATTERN.fullmatch(agent_task_id):
        raise AgentTaskError("agent_task_id has an invalid format.")
    path = AGENT_TASKS_ROOT / agent_task_id / "agent_state.json"
    if path.parent.parent.resolve() != AGENT_TASKS_ROOT.resolve():
        raise AgentTaskError("agent_task_id escapes the task root.")
    return path


def _save_state(state: dict[str, Any]) -> None:
    state_path = _state_path(state["agent_task_id"])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_name(f".{state_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, state_path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_vector(value: Any, name: str) -> list[float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AgentTaskError(f"{name} must be a length-3 numeric sequence.")
    if len(value) != 3:
        raise AgentTaskError(f"{name} must contain exactly three values.")
    result: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, Real):
            raise AgentTaskError(f"{name} values must be real numbers.")
        converted = float(item)
        if not math.isfinite(converted):
            raise AgentTaskError(f"{name} values must be finite.")
        result.append(converted)
    return result


def _validate_positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise AgentTaskError(f"{name} must be a positive finite number.")
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0:
        raise AgentTaskError(f"{name} must be a positive finite number.")
    return converted


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
