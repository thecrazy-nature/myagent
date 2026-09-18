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
DEFAULT_FREQUENCY_HZ = 28.0e9
DEFAULT_MODULATION_FREQUENCY_HZ = 200.0e6
DEFAULT_ELEMENT_COUNT = 256
SUPPORTED_POLARIZATIONS = {"scalar", "x_linear", "y_linear", "rhcp", "lhcp"}


class AgentTaskError(Exception):
    """Expected agent-layer validation or state-transition failure."""

    def __init__(self, message: str, *, agent_task_id: str | None = None) -> None:
        super().__init__(message)
        self.agent_task_id = agent_task_id


def create_task(
    target_mm: Sequence[Real],
    tolerance_mm: Real = 5.0,
    max_refinements: int = 2,
    additional_targets_mm: Sequence[Sequence[Real]] | None = None,
    frequency_ghz: Real = 28.0,
    modulation_frequency_mhz: Real = 200.0,
    element_count: int = DEFAULT_ELEMENT_COUNT,
    polarization: str = "scalar",
) -> dict[str, Any]:
    """Create and persist one configured single- or multi-user focus task."""

    desired = _validate_vector(target_mm, "target_mm")
    additional = _validate_targets(additional_targets_mm or [], "additional_targets_mm")
    desired_targets = [desired, *additional]
    if len(desired_targets) > 8:
        raise AgentTaskError("At most eight simultaneous users are supported.")
    tolerance = _validate_positive_number(tolerance_mm, "tolerance_mm")
    frequency = _validate_positive_number(frequency_ghz, "frequency_ghz") * 1.0e9
    if not 1.0e9 <= frequency <= 100.0e9:
        raise AgentTaskError("frequency_ghz must be between 1 and 100.")
    modulation_frequency = _validate_positive_number(
        modulation_frequency_mhz, "modulation_frequency_mhz"
    ) * 1.0e6
    if modulation_frequency >= frequency:
        raise AgentTaskError("modulation_frequency_mhz must be below the carrier frequency.")
    elements = _validate_element_count(element_count)
    polarization_mode = _validate_polarization(polarization)
    user_harmonic_orders = _assign_user_harmonics(len(desired_targets))
    harmonic_orders = list(range(min(user_harmonic_orders), max(user_harmonic_orders) + 1))
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
        "schema_version": 4,
        "agent_task_id": agent_task_id,
        "desired_target_mm": desired,
        "current_command_target_mm": desired.copy(),
        "desired_targets_mm": desired_targets,
        "current_command_targets_mm": [target.copy() for target in desired_targets],
        "user_count": len(desired_targets),
        "frequency_hz": frequency,
        "modulation_frequency_hz": modulation_frequency,
        "harmonic_orders": harmonic_orders,
        "harmonic_frequencies_hz": [
            frequency + order * modulation_frequency for order in harmonic_orders
        ],
        "user_harmonic_orders": user_harmonic_orders,
        "element_count": elements,
        "polarization": polarization_mode,
        "polarization_model": (
            "scenario metadata only; scalar point-source fields are polarization independent"
        ),
        "random_seed": 0,
        "rng_algorithm": "twister",
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
    desired_targets = state.get("desired_targets_mm", [state.get("desired_target_mm")])
    commanded_targets = state.get(
        "current_command_targets_mm", [state.get("current_command_target_mm")]
    )
    _validate_targets(desired_targets, "desired_targets_mm")
    _validate_targets(commanded_targets, "current_command_targets_mm")
    if len(desired_targets) != len(commanded_targets):
        raise AgentTaskError("Desired and commanded target counts do not match.")
    if int(state.get("schema_version", 1)) >= 3:
        user_orders = _optional_numeric_list(
            state.get("user_harmonic_orders"), "user_harmonic_orders",
            len(desired_targets),
        )
        plan_orders = _optional_numeric_list(
            state.get("harmonic_orders"), "harmonic_orders"
        )
        frequencies = _optional_numeric_list(
            state.get("harmonic_frequencies_hz"), "harmonic_frequencies_hz"
        )
        if (
            user_orders is None or plan_orders is None or frequencies is None
            or len(set(user_orders)) != len(user_orders)
            or not set(user_orders).issubset(set(plan_orders))
            or len(plan_orders) != len(frequencies)
        ):
            raise AgentTaskError("Persisted harmonic plan is invalid.")
    if not isinstance(state.get("history"), list):
        raise AgentTaskError(
            "Agent task history must be a list.", agent_task_id=agent_task_id
        )
    return state


def inspect_task(agent_task_id: str) -> dict[str, Any]:
    """Return a compact persisted checkpoint and its valid next actions."""

    state = load_task(agent_task_id)
    status = str(state.get("status"))
    actions = {
        "configured": ["run_focus_simulation"],
        "refined": ["run_focus_simulation"],
        "simulated": ["evaluate_focus"],
        "evaluation_failed": ["refine_focus"],
        "focus_achieved": ["stop"],
        "refinement_exhausted": ["stop"],
    }.get(status, [])
    history = state.get("history", [])
    return {
        "success": True,
        "agent_task_id": agent_task_id,
        "status": status,
        "desired_targets_mm": state.get("desired_targets_mm"),
        "current_command_targets_mm": state.get("current_command_targets_mm"),
        "tolerance_mm": state.get("tolerance_mm"),
        "refinement_count": state.get("refinement_count"),
        "remaining_refinements": int(state.get("max_refinements", 0))
        - int(state.get("refinement_count", 0)),
        "last_valid_event": history[-1] if history else None,
        "valid_next_actions": actions,
    }


def run_task_simulation(agent_task_id: str) -> dict[str, Any]:
    """Run MATLAB once for the task's current commanded target."""

    state = load_task(agent_task_id)
    if state.get("status") not in {"configured", "refined"}:
        raise AgentTaskError(
            "Simulation is only allowed after task creation or refinement; "
            "evaluate the pending simulation or refine a failed evaluation first.",
            agent_task_id=agent_task_id,
        )

    commanded_targets = _validate_targets(
        state.get("current_command_targets_mm", [state["current_command_target_mm"]]),
        "current_command_targets_mm",
    )
    commanded = commanded_targets[0]
    simulation_run_id = f"sim_{uuid.uuid4().hex}"
    started = time.perf_counter()
    is_legacy_default = (
        len(commanded_targets) == 1
        and float(state.get("frequency_hz", DEFAULT_FREQUENCY_HZ)) == DEFAULT_FREQUENCY_HZ
        and float(state.get("modulation_frequency_hz", DEFAULT_MODULATION_FREQUENCY_HZ))
        == DEFAULT_MODULATION_FREQUENCY_HZ
        and int(state.get("element_count", DEFAULT_ELEMENT_COUNT)) == DEFAULT_ELEMENT_COUNT
        and state.get("polarization", "scalar") == "scalar"
    )
    if is_legacy_default:
        matlab_result = run_simulation(target_mm=commanded, task_id=simulation_run_id)
    else:
        matlab_result = run_simulation(
            target_mm=commanded,
            additional_targets_mm=commanded_targets[1:],
            frequency_hz=state.get("frequency_hz", DEFAULT_FREQUENCY_HZ),
            modulation_frequency_hz=state.get(
                "modulation_frequency_hz", DEFAULT_MODULATION_FREQUENCY_HZ
            ),
            element_count=state.get("element_count", DEFAULT_ELEMENT_COUNT),
            polarization=state.get("polarization", "scalar"),
            task_id=simulation_run_id,
        )
    bridge_wall_time_sec = time.perf_counter() - started

    actual = _validate_vector(matlab_result.get("actual_peak_mm"), "actual_peak_mm")
    requested = _validate_vector(
        matlab_result.get("requested_focus_mm"), "requested_focus_mm"
    )
    requested_targets = _result_targets(
        matlab_result, "requested_focus_points_mm", requested, len(commanded_targets)
    )
    actual_targets = _result_targets(
        matlab_result, "actual_peak_points_mm", actual, len(commanded_targets)
    )
    if not _same_targets(requested_targets, commanded_targets):
        raise AgentTaskError(
            "MATLAB requested focus points do not match the commanded targets.",
            agent_task_id=agent_task_id,
        )
    matlab_harmonic_orders = _optional_numeric_list(
        matlab_result.get("harmonic_orders"), "harmonic_orders"
    )
    matlab_harmonic_frequencies = _optional_numeric_list(
        matlab_result.get("harmonic_frequencies_hz"), "harmonic_frequencies_hz"
    )
    matlab_user_orders = _optional_numeric_list(
        matlab_result.get("user_harmonic_orders"), "user_harmonic_orders",
        len(commanded_targets),
    )
    matlab_user_indices = _optional_numeric_list(
        matlab_result.get("user_harmonic_indices"), "user_harmonic_indices",
        len(commanded_targets),
    )
    configured_user_orders = [
        float(value) for value in state.get("user_harmonic_orders", [])
    ]
    if (
        configured_user_orders
        and matlab_user_orders is not None
        and matlab_user_orders != configured_user_orders
    ):
        raise AgentTaskError(
            "MATLAB user harmonic mapping does not match the configured task.",
            agent_task_id=agent_task_id,
        )
    configured_orders = [float(value) for value in state.get("harmonic_orders", [])]
    configured_frequencies = [
        float(value) for value in state.get("harmonic_frequencies_hz", [])
    ]
    if (
        configured_orders
        and matlab_harmonic_orders is not None
        and matlab_harmonic_orders != configured_orders
    ):
        raise AgentTaskError(
            "MATLAB harmonic plan does not match the configured task.",
            agent_task_id=agent_task_id,
        )
    if configured_frequencies and matlab_harmonic_frequencies is not None and (
        len(matlab_harmonic_frequencies) != len(configured_frequencies)
        or any(
            abs(actual - expected) > 1.0
            for actual, expected in zip(matlab_harmonic_frequencies, configured_frequencies)
        )
    ):
        raise AgentTaskError(
            "MATLAB harmonic frequencies do not match fc+q*fm.",
            agent_task_id=agent_task_id,
        )

    event = {
        "event": "simulation",
        "timestamp": _utc_now(),
        "simulation_run_id": simulation_run_id,
        "desired_target_mm": state["desired_target_mm"].copy(),
        "commanded_target_mm": commanded,
        "actual_peak_mm": actual,
        "desired_targets_mm": [target.copy() for target in state.get("desired_targets_mm", [state["desired_target_mm"]])],
        "commanded_targets_mm": commanded_targets,
        "actual_peak_points_mm": actual_targets,
        "user_count": len(commanded_targets),
        "frequency_hz": float(state.get("frequency_hz", DEFAULT_FREQUENCY_HZ)),
        "modulation_frequency_hz": float(
            state.get("modulation_frequency_hz", DEFAULT_MODULATION_FREQUENCY_HZ)
        ),
        "element_count": int(state.get("element_count", DEFAULT_ELEMENT_COUNT)),
        "element_positions_mm": matlab_result.get("element_positions_mm"),
        "polarization": state.get("polarization", "scalar"),
        "polarization_model": state.get("polarization_model"),
        "peak_power": float(matlab_result["peak_power"]),
        "requested_power": float(matlab_result["requested_power"]),
        "peak_power_by_user": _numeric_list(
            matlab_result.get("peak_power_by_user", matlab_result["peak_power"]),
            len(commanded_targets),
            "peak_power_by_user",
        ),
        "requested_power_by_user": _numeric_list(
            matlab_result.get("requested_power_by_user", matlab_result["requested_power"]),
            len(commanded_targets),
            "requested_power_by_user",
        ),
        "peak_power_definition": matlab_result["peak_power_definition"],
        "focus_error_mm_by_user": _optional_numeric_list(
            matlab_result.get("focus_error_mm_by_user"),
            "focus_error_mm_by_user", len(commanded_targets),
        ),
        "fwhm_x_mm_by_user": _optional_numeric_list(
            matlab_result.get("fwhm_x_mm_by_user"),
            "fwhm_x_mm_by_user", len(commanded_targets),
        ),
        "dof_z_mm_by_user": _optional_numeric_list(
            matlab_result.get("dof_z_mm_by_user"),
            "dof_z_mm_by_user", len(commanded_targets),
        ),
        "peak_to_sidelobe_ratio_db_by_user": _optional_numeric_list(
            matlab_result.get("peak_to_sidelobe_ratio_db_by_user"),
            "peak_to_sidelobe_ratio_db_by_user", len(commanded_targets),
        ),
        "peak_to_sidelobe_definition": matlab_result.get(
            "peak_to_sidelobe_definition"
        ),
        "harmonic_orders": matlab_harmonic_orders,
        "harmonic_frequencies_hz": matlab_harmonic_frequencies,
        "user_harmonic_orders": matlab_user_orders,
        "user_harmonic_indices": matlab_user_indices,
        "orthogonal_plane_resolution": matlab_result.get(
            "orthogonal_plane_resolution"
        ),
        "method": matlab_result.get("method"),
        "hardware_realization": matlab_result.get("hardware_realization"),
        "artifacts": matlab_result.get("artifacts"),
        "matlab_version": matlab_result.get("matlab_version"),
        "matlab_release": matlab_result.get("matlab_release"),
        "matlab_arch": matlab_result.get("matlab_arch"),
        "random_seed": matlab_result.get("random_seed", state.get("random_seed", 0)),
        "rng_algorithm": matlab_result.get(
            "rng_algorithm", state.get("rng_algorithm", "twister")
        ),
        "matlab_runtime_sec": float(matlab_result["runtime_sec"]),
        "bridge_wall_time_sec": bridge_wall_time_sec,
    }
    if isinstance(matlab_result.get("process_warning"), dict):
        event["process_warning"] = matlab_result["process_warning"]
    state["history"].append(event)
    state["status"] = "simulated"
    state["updated_at"] = _utc_now()
    _save_state(state)
    return {
        "success": True,
        "agent_task_id": agent_task_id,
        **{
            key: value for key, value in event.items()
            if key not in {"event", "timestamp", "element_positions_mm"}
        },
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
    desired_targets = _validate_targets(
        state.get("desired_targets_mm", [desired]), "desired_targets_mm"
    )
    actual_targets = _validate_targets(
        simulation.get("actual_peak_points_mm", [actual]), "actual_peak_points_mm"
    )
    if len(actual_targets) != len(desired_targets):
        raise AgentTaskError("MATLAB returned the wrong number of user peaks.")
    focus_errors_mm = [
        math.dist(actual_target, desired_target)
        for actual_target, desired_target in zip(actual_targets, desired_targets)
    ]
    focus_error_mm = max(focus_errors_mm)
    tolerance_mm = _validate_positive_number(state["tolerance_mm"], "tolerance_mm")
    achieved = focus_error_mm <= tolerance_mm
    remaining = state["max_refinements"] - state["refinement_count"]
    status = "focus_achieved" if achieved else (
        "evaluation_failed" if remaining > 0 else "refinement_exhausted"
    )
    user_harmonic_orders = simulation.get("user_harmonic_orders")
    if not isinstance(user_harmonic_orders, list):
        user_harmonic_orders = (
            [user_harmonic_orders]
            if isinstance(user_harmonic_orders, (int, float))
            else [None] * len(desired_targets)
        )
    event = {
        "event": "evaluation",
        "timestamp": _utc_now(),
        "simulation_run_id": simulation["simulation_run_id"],
        "desired_target_mm": desired.copy(),
        "commanded_target_mm": simulation["commanded_target_mm"].copy(),
        "actual_peak_mm": actual.copy(),
        "focus_error_mm": focus_error_mm,
        "desired_targets_mm": desired_targets,
        "commanded_targets_mm": simulation.get("commanded_targets_mm", [simulation["commanded_target_mm"]]),
        "actual_peak_points_mm": actual_targets,
        "focus_errors_mm": focus_errors_mm,
        "user_results": [
            {
                "user": index + 1,
                "desired_target_mm": desired_target,
                "actual_peak_mm": actual_target,
                "focus_error_mm": error,
                "harmonic_order": user_harmonic_orders[index],
                "frequency_hz": (
                    _user_harmonic_frequency(simulation, index)
                ),
                "success": error <= tolerance_mm,
            }
            for index, (desired_target, actual_target, error) in enumerate(
                zip(desired_targets, actual_targets, focus_errors_mm)
            )
        ],
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
    desired_targets = _validate_targets(
        state.get("desired_targets_mm", [desired]), "desired_targets_mm"
    )
    actual_targets = _validate_targets(
        evaluation.get("actual_peak_points_mm", [actual]), "actual_peak_points_mm"
    )
    old_commands = _validate_targets(
        state.get("current_command_targets_mm", [state["current_command_target_mm"]]),
        "current_command_targets_mm",
    )
    corrections = [
        [desired_point[index] - actual_point[index] for index in range(3)]
        for desired_point, actual_point in zip(desired_targets, actual_targets)
    ]
    new_commands = [
        [old[index] + FEEDBACK_ALPHA * correction[index] for index in range(3)]
        for old, correction in zip(old_commands, corrections)
    ]
    old_command = old_commands[0]
    correction = corrections[0]
    new_command = new_commands[0]
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
        "old_command_targets_mm": old_commands,
        "corrections_mm": corrections,
        "new_command_targets_mm": new_commands,
        "refinement_count": state["refinement_count"],
        "remaining_refinements": (
            state["max_refinements"] - state["refinement_count"]
        ),
    }
    state["current_command_target_mm"] = new_command
    state["current_command_targets_mm"] = new_commands
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


def _validate_targets(value: Any, name: str) -> list[list[float]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AgentTaskError(f"{name} must be a sequence of [x,y,z] targets.")
    return [_validate_vector(target, f"{name}[{index}]") for index, target in enumerate(value)]


def _result_targets(
    result: dict[str, Any], key: str, primary: list[float], expected_count: int
) -> list[list[float]]:
    value = result.get(key)
    if value is None:
        if expected_count == 1:
            return [primary]
        raise AgentTaskError(f"MATLAB result is missing {key}.")
    if expected_count == 1 and isinstance(value, Sequence) and len(value) == 3 and all(
        isinstance(item, Real) and not isinstance(item, bool) for item in value
    ):
        return [_validate_vector(value, key)]
    targets = _validate_targets(value, key)
    if len(targets) != expected_count:
        raise AgentTaskError(f"MATLAB result field {key} has the wrong user count.")
    return targets


def _same_targets(first: list[list[float]], second: list[list[float]]) -> bool:
    return len(first) == len(second) and all(
        abs(a - b) <= 1e-9
        for first_target, second_target in zip(first, second)
        for a, b in zip(first_target, second_target)
    )


def _numeric_list(value: Any, expected_count: int, name: str) -> list[float]:
    values = value if isinstance(value, list) else [value]
    if len(values) != expected_count:
        raise AgentTaskError(f"{name} has the wrong user count.")
    return [_validate_positive_number(item, name) for item in values]


def _optional_numeric_list(
    value: Any, name: str, expected_count: int | None = None
) -> list[float] | None:
    if value is None:
        return None
    values = value if isinstance(value, list) else [value]
    if expected_count is not None and len(values) != expected_count:
        raise AgentTaskError(f"{name} has the wrong item count.")
    result: list[float] = []
    for item in values:
        if isinstance(item, bool) or not isinstance(item, Real):
            raise AgentTaskError(f"{name} must contain finite numbers.")
        converted = float(item)
        if not math.isfinite(converted):
            raise AgentTaskError(f"{name} must contain finite numbers.")
        result.append(converted)
    return result


def _user_harmonic_frequency(simulation: dict[str, Any], user_index: int) -> float | None:
    orders = simulation.get("harmonic_orders")
    frequencies = simulation.get("harmonic_frequencies_hz")
    user_orders = simulation.get("user_harmonic_orders")
    if not all(isinstance(value, list) for value in (orders, frequencies, user_orders)):
        return None
    try:
        order = user_orders[user_index]
        return float(frequencies[orders.index(order)])
    except (IndexError, ValueError, TypeError):
        return None


def _assign_user_harmonics(user_count: int) -> list[int]:
    if user_count % 2:
        half_count = (user_count - 1) // 2
        return list(range(-half_count, half_count + 1))
    half_count = user_count // 2
    return [*range(-half_count, 0), *range(1, half_count + 1)]


def _validate_element_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgentTaskError("element_count must be a perfect-square integer.")
    side = math.isqrt(value)
    if side * side != value or not 4 <= side <= 32:
        raise AgentTaskError("element_count must describe a square 4x4 to 32x32 array.")
    return value


def _validate_polarization(value: Any) -> str:
    if not isinstance(value, str) or value.strip().lower() not in SUPPORTED_POLARIZATIONS:
        raise AgentTaskError(
            "polarization must be scalar, x_linear, y_linear, rhcp, or lhcp."
        )
    return value.strip().lower()


def _validate_positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise AgentTaskError(f"{name} must be a positive finite number.")
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0:
        raise AgentTaskError(f"{name} must be a positive finite number.")
    return converted


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
