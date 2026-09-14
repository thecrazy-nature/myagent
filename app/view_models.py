"""Pure view-model builders for Hermes sessions and persisted Agent task state."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


DOMAIN_TOOLS = {
    "create_focus_task",
    "run_focus_simulation",
    "evaluate_focus",
    "refine_focus",
    "get_focus_task_state",
    "create_array_design_task",
    "evaluate_array_geometry",
    "search_array_geometry",
    "save_array_design",
}
INFRASTRUCTURE_ERROR_TYPES = {
    "MatlabExecutableNotFound",
    "MatlabProcessError",
    "MatlabResultError",
    "MatlabTimeoutError",
}


def render_structured_task(
    target_mm: list[float], tolerance_mm: float, max_refinements: int
) -> str:
    """Render manual parameters as the natural-language request sent to Hermes."""

    x, y, z = (_format_number(value) for value in target_mm)
    tolerance = _format_number(tolerance_mm)
    refinements = int(max_refinements)
    return (
        f"请在 x={x} mm、y={y} mm、z={z} mm 处进行近场聚焦。"
        f"最终焦点定位误差必须不超过 {tolerance} mm。"
        f"如果第一次未达到要求，最多允许 {refinements} 次工作流级修正。"
        "请使用真实 Hermes Tools 和 MATLAB，根据每次结构化评估自主决定下一步，"
        "满足容差或修正预算耗尽后停止并如实总结。"
    )


def parse_session_tool_calls(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract domain calls without exposing assistant reasoning content."""

    observations = {
        message.get("tool_call_id"): _parse_json(message.get("content"))
        for message in session.get("messages", [])
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    calls: list[dict[str, Any]] = []
    for message in session.get("messages", []):
        if message.get("role") != "assistant":
            continue
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            raw_name = function.get("name")
            raw_arguments = _parse_json(function.get("arguments"))
            if raw_name == "tool_call" and isinstance(raw_arguments, dict):
                tool_name = raw_arguments.get("name")
                arguments = raw_arguments.get("arguments")
            else:
                tool_name = raw_name
                arguments = raw_arguments
            if tool_name not in DOMAIN_TOOLS:
                continue
            call_id = raw_call.get("id") or raw_call.get("call_id")
            calls.append(
                {
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "observation": observations.get(call_id),
                }
            )
    return calls


def build_task_view(
    session: dict[str, Any], project_root: Path
) -> dict[str, Any]:
    """Combine an exported Hermes session with its persisted task state."""

    trajectory = parse_session_tool_calls(session)
    agent_task_id = _agent_task_id(trajectory)
    state = _load_agent_state(project_root, agent_task_id)
    iterations = build_iteration_history(state)
    final_evaluation = _last_observation(trajectory, "evaluate_focus")
    if final_evaluation is None and state:
        final_evaluation = _latest_state_event(state, "evaluation")
    successful_run = _last_successful_run(trajectory)
    final_response = _final_response(session)
    error_category, error_message = classify_outcome(
        trajectory, state, final_evaluation
    )
    infrastructure_warnings = [
        str(observation.get("message", "MATLAB process warning."))
        for call in trajectory
        if isinstance((observation := call.get("observation")), dict)
        and observation.get("error") is True
        and str(observation.get("error_type", "")).startswith("Matlab")
    ]
    if state:
        infrastructure_warnings.extend(
            str(event["process_warning"].get("message", "MATLAB process warning."))
            for event in state.get("history", [])
            if isinstance(event, dict) and isinstance(event.get("process_warning"), dict)
        )

    desired = _state_or_observation_value(
        state, "desired_target_mm", trajectory, "desired_target_mm"
    )
    actual = (
        final_evaluation.get("actual_peak_mm")
        if isinstance(final_evaluation, dict)
        else None
    )
    if actual is None and isinstance(successful_run, dict):
        actual = successful_run.get("actual_peak_mm")
    final_error = (
        final_evaluation.get("focus_error_mm")
        if isinstance(final_evaluation, dict)
        else None
    )
    return {
        "session_id": session.get("id"),
        "agent_task_id": agent_task_id,
        "status": _status_label(error_category),
        "error_category": error_category,
        "error_message": error_message,
        "infrastructure_warnings": infrastructure_warnings,
        "desired_target_mm": desired,
        "desired_targets_mm": (
            state.get("desired_targets_mm") if state else [desired] if desired else None
        ),
        "actual_peak_mm": actual,
        "actual_peak_points_mm": (
            final_evaluation.get("actual_peak_points_mm")
            if isinstance(final_evaluation, dict)
            else successful_run.get("actual_peak_points_mm")
            if isinstance(successful_run, dict)
            else None
        ),
        "final_error_mm": final_error,
        "constraint_satisfied": (
            final_evaluation.get("success")
            if isinstance(final_evaluation, dict)
            else None
        ),
        "matlab_calls": sum(
            call["tool_name"] == "run_focus_simulation" for call in trajectory
        ),
        "replanning_count": sum(
            call["tool_name"] == "refine_focus"
            and isinstance(call.get("observation"), dict)
            and call["observation"].get("success") is True
            for call in trajectory
        ),
        "trajectory": trajectory,
        "iterations": iterations,
        "simulation_details": [
            call["observation"]
            for call in trajectory
            if call["tool_name"] == "run_focus_simulation"
            and isinstance(call.get("observation"), dict)
            and call["observation"].get("success") is True
        ],
        "agent_final_response": final_response,
        "agent_state": state,
    }


def build_conversation_view(session: dict[str, Any]) -> dict[str, Any]:
    """Build a successful chat turn when Hermes correctly needs no domain tool."""

    response = _final_response(session)
    if not response:
        return {
            "session_id": session.get("id"),
            "status": "FAILED",
            "error_category": "Agent Error",
            "error_message": "Hermes 没有返回可显示的回复。",
            "trajectory": [],
            "agent_final_response": "",
        }
    return {
        "session_id": session.get("id"),
        "status": "SUCCESS",
        "error_category": None,
        "error_message": None,
        "trajectory": [],
        "agent_final_response": response,
    }


def build_array_design_view(session: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Combine a Hermes array-design trajectory with its full persisted evidence."""
    trajectory = parse_session_tool_calls(session)
    design_task_id = None
    for call in trajectory:
        observation = call.get("observation")
        if isinstance(observation, dict) and isinstance(observation.get("design_task_id"), str):
            design_task_id = observation["design_task_id"]
            break
    state = _load_design_state(project_root, design_task_id)
    final_response = _final_response(session)
    selected = state.get("selected_design") if state else None
    baseline = state.get("baseline") if state else None
    errors = [
        call["observation"] for call in trajectory
        if isinstance(call.get("observation"), dict) and call["observation"].get("error") is True
    ]
    if errors:
        category = "Infrastructure Error" if str(errors[-1].get("error_type", "")).startswith("Matlab") or "Matlab" in str(errors[-1].get("error_type", "")) else "Agent Error"
        message = str(errors[-1].get("message", "Array-design Tool failed."))
        status = "FAILED"
    elif selected:
        category = None
        message = None
        status = "SAVED"
    else:
        category = "Agent Error"
        message = "Hermes stopped before save_array_design persisted a final selection."
        status = "FAILED"
    return {
        "session_id": session.get("id"),
        "design_task_id": design_task_id,
        "status": status,
        "error_category": category,
        "error_message": message,
        "trajectory": trajectory,
        "agent_final_response": final_response,
        "design_state": state,
        "baseline": baseline,
        "selected_design": selected,
        "baseline_metrics": baseline.get("metrics") if isinstance(baseline, dict) else None,
        "designed_metrics": selected.get("metrics") if isinstance(selected, dict) else None,
        "improvement_percentages": selected.get("improvement_percentages") if isinstance(selected, dict) else None,
        "search_rounds": sum(call["tool_name"] == "search_array_geometry" for call in trajectory),
        "matlab_processes": sum(call["tool_name"] in {"evaluate_array_geometry", "search_array_geometry"} for call in trajectory),
    }


def load_recent_designs(project_root: Path, limit: int = 12) -> list[dict[str, Any]]:
    root = project_root / "runs" / "array_designs"
    if not root.is_dir():
        return []
    paths = sorted(root.glob("design_*/design_state.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    designs = []
    for path in paths[:limit]:
        try:
            state = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        selected = state.get("selected_design") or {}
        designs.append({
            "design_task_id": state.get("design_task_id"),
            "natural_language_request": state.get("natural_language_request"),
            "focus_target_mm": state.get("focus_target_mm"),
            "status": state.get("status"),
            "selected_family": (selected.get("geometry") or {}).get("family"),
            "objective_score": selected.get("objective_score"),
            "timestamp": state.get("updated_at"),
        })
    return designs


def load_focus_task_record(project_root: Path, agent_task_id: str) -> dict[str, Any] | None:
    """Load one persisted focus task for the read-only result viewer."""
    state = _load_agent_state(project_root, agent_task_id)
    if not state:
        return None
    metadata_path = project_root / "runs" / "agent_tasks" / agent_task_id / "ui_metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        metadata = {}
    return {
        "agent_task_id": agent_task_id,
        "state": state,
        "metadata": metadata,
        "iterations": build_iteration_history(state),
    }


def load_array_design_record(project_root: Path, design_task_id: str) -> dict[str, Any] | None:
    """Load one complete persisted array design for the read-only result viewer."""
    return _load_design_state(project_root, design_task_id)


def build_iteration_history(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not state:
        return []
    history = state.get("history")
    if not isinstance(history, list):
        return []
    evaluations = {
        event.get("simulation_run_id"): event
        for event in history
        if isinstance(event, dict) and event.get("event") == "evaluation"
    }
    rows: list[dict[str, Any]] = []
    for event in history:
        if not isinstance(event, dict) or event.get("event") != "simulation":
            continue
        evaluation = evaluations.get(event.get("simulation_run_id"), {})
        rows.append(
            {
                "Iteration": len(rows) + 1,
                "Desired Target": event.get("desired_target_mm"),
                "Commanded Target": event.get("commanded_target_mm"),
                "Actual Peak": event.get("actual_peak_mm"),
                "Desired Targets": event.get("desired_targets_mm"),
                "Commanded Targets": event.get("commanded_targets_mm"),
                "Actual Peaks": event.get("actual_peak_points_mm"),
                "Per-user Errors / mm": evaluation.get("focus_errors_mm"),
                "User Harmonic Orders": event.get("user_harmonic_orders"),
                "FWHM X / mm": event.get("fwhm_x_mm_by_user"),
                "DOF Z / mm": event.get("dof_z_mm_by_user"),
                "Local Peak / Max Sidelobe / dB": event.get(
                    "peak_to_sidelobe_ratio_db_by_user"
                ),
                "Error / mm": evaluation.get("focus_error_mm"),
                "Result": (
                    "SUCCESS" if evaluation.get("success") is True
                    else "FAILED" if evaluation.get("success") is False
                    else "NOT EVALUATED"
                ),
                "Simulation Run ID": event.get("simulation_run_id"),
            }
        )
    return rows


def classify_outcome(
    trajectory: list[dict[str, Any]],
    state: dict[str, Any] | None,
    final_evaluation: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Return None for success, otherwise a stable UI error category/message."""

    if not trajectory:
        return "Agent Error", "Hermes produced no electromagnetic domain Tool Call."
    if state and state.get("status") == "focus_achieved":
        sequence_error = _trajectory_error(trajectory)
        return ("Agent Error", sequence_error) if sequence_error else (None, None)
    if state and state.get("status") == "refinement_exhausted":
        sequence_error = _trajectory_error(trajectory)
        if sequence_error:
            return "Agent Error", sequence_error
        return "Scientific Failure", "Refinement budget exhausted and requested tolerance was not satisfied."
    if isinstance(final_evaluation, dict) and final_evaluation.get("success") is True:
        sequence_error = _trajectory_error(trajectory)
        if sequence_error:
            return "Agent Error", sequence_error
        return None, None
    for call in trajectory:
        observation = call.get("observation")
        if not isinstance(observation, dict) or observation.get("error") is not True:
            continue
        error_type = str(observation.get("error_type", ""))
        message = str(observation.get("message", "Tool execution failed."))
        if error_type in INFRASTRUCTURE_ERROR_TYPES or error_type.startswith("Matlab"):
            return "Infrastructure Error", message
        return "Agent Error", message
    sequence_error = _trajectory_error(trajectory)
    if sequence_error:
        return "Agent Error", sequence_error
    if isinstance(final_evaluation, dict):
        if final_evaluation.get("success") is False:
            remaining = final_evaluation.get("remaining_refinements")
            if remaining == 0 or (state and state.get("status") == "refinement_exhausted"):
                return (
                    "Scientific Failure",
                    "Refinement budget exhausted and requested tolerance was not satisfied.",
                )
            return "Agent Error", "Hermes stopped while refinement budget remained."
    return "Agent Error", "Hermes stopped before a terminal focus evaluation."


def load_recent_tasks(project_root: Path, limit: int = 12) -> list[dict[str, Any]]:
    tasks_root = project_root / "runs" / "agent_tasks"
    if not tasks_root.is_dir():
        return []
    candidates = sorted(
        tasks_root.glob("agent_*/agent_state.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    recent: list[dict[str, Any]] = []
    for state_path in candidates[:limit]:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        metadata_path = state_path.with_name("ui_metadata.json")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            metadata = {}
        evaluations = [
            event for event in state.get("history", [])
            if isinstance(event, dict) and event.get("event") == "evaluation"
        ]
        final_evaluation = evaluations[-1] if evaluations else {}
        recent.append(
            {
                "agent_task_id": state.get("agent_task_id"),
                "original_task": metadata.get("original_task"),
                "submission_mode": metadata.get("submission_mode"),
                "desired_target_mm": state.get("desired_target_mm"),
                "user_count": state.get("user_count", 1),
                "frequency_hz": state.get("frequency_hz", 28.0e9),
                "modulation_frequency_hz": state.get("modulation_frequency_hz", 200.0e6),
                "element_count": state.get("element_count", 256),
                "polarization": state.get("polarization", "scalar"),
                "status": state.get("status"),
                "final_error_mm": final_evaluation.get("focus_error_mm"),
                "replanning_count": state.get("refinement_count", 0),
                "timestamp": state.get("updated_at") or state.get("created_at"),
                "iterations": build_iteration_history(state),
            }
        )
    return recent


def _trajectory_error(trajectory: list[dict[str, Any]]) -> str | None:
    names = [call.get("tool_name") for call in trajectory]
    if not names:
        return "Focus workflow has no Tool Call."
    if names[0] == "get_focus_task_state":
        if names.count("create_focus_task"):
            return "A recovery turn must not create a replacement focus task."
        checkpoint = trajectory[0].get("observation")
        status = checkpoint.get("status") if isinstance(checkpoint, dict) else None
        phase = {
            "configured": "configured", "refined": "refined",
            "simulated": "simulated", "evaluation_failed": "evaluation_failed",
            "focus_achieved": "terminal", "refinement_exhausted": "terminal",
        }.get(status, "invalid_checkpoint")
        remaining_calls = trajectory[1:]
    else:
        if names[0] != "create_focus_task" or names.count("create_focus_task") != 1:
            return "Tool sequence must begin with exactly one create_focus_task call."
        create_arguments = trajectory[0].get("arguments", {})
        required = {"target_mm", "tolerance_mm", "max_refinements"}
        if not required.issubset(create_arguments):
            return "create_focus_task omitted target, tolerance, or refinement budget."
        phase = "configured"
        remaining_calls = trajectory[1:]
    for call in remaining_calls:
        name = call.get("tool_name")
        observation = call.get("observation")
        if name == "get_focus_task_state":
            return "get_focus_task_state may only be called once at recovery start."
        if name == "run_focus_simulation":
            if phase not in {"configured", "refined"}:
                return f"run_focus_simulation is invalid after phase {phase}."
            if _tool_succeeded(observation):
                phase = "simulated"
        elif name == "evaluate_focus":
            if phase != "simulated":
                return f"evaluate_focus is invalid after phase {phase}."
            if isinstance(observation, dict) and observation.get("success") is True:
                phase = "terminal"
            elif isinstance(observation, dict) and observation.get("remaining_refinements") == 0:
                phase = "terminal"
            else:
                phase = "evaluation_failed"
        elif name == "refine_focus":
            if phase != "evaluation_failed":
                return f"refine_focus is invalid after phase {phase}."
            phase = "refined" if _tool_succeeded(observation) else "refine_failed"
        if phase == "terminal" and call is not trajectory[-1]:
            return "A Tool Call occurred after terminal evaluation."
    return None


def _tool_succeeded(observation: Any) -> bool:
    return isinstance(observation, dict) and observation.get("error") is not True


def _agent_task_id(trajectory: list[dict[str, Any]]) -> str | None:
    for call in trajectory:
        observation = call.get("observation")
        if isinstance(observation, dict) and isinstance(observation.get("agent_task_id"), str):
            return observation["agent_task_id"]
    return None


def _load_agent_state(project_root: Path, agent_task_id: str | None) -> dict[str, Any] | None:
    if not agent_task_id:
        return None
    state_path = project_root / "runs" / "agent_tasks" / agent_task_id / "agent_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return state if isinstance(state, dict) else None


def _load_design_state(project_root: Path, design_task_id: str | None) -> dict[str, Any] | None:
    if not design_task_id:
        return None
    path = project_root / "runs" / "array_designs" / design_task_id / "design_state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return state if isinstance(state, dict) else None


def _last_observation(trajectory: list[dict[str, Any]], tool_name: str) -> dict[str, Any] | None:
    for call in reversed(trajectory):
        if call.get("tool_name") == tool_name and isinstance(call.get("observation"), dict):
            return call["observation"]
    return None


def _latest_state_event(state: dict[str, Any], event_name: str) -> dict[str, Any] | None:
    for event in reversed(state.get("history", [])):
        if isinstance(event, dict) and event.get("event") == event_name:
            return event
    return None


def _last_successful_run(trajectory: list[dict[str, Any]]) -> dict[str, Any] | None:
    for call in reversed(trajectory):
        observation = call.get("observation")
        if (
            call.get("tool_name") == "run_focus_simulation"
            and isinstance(observation, dict)
            and observation.get("success") is True
        ):
            return observation
    return None


def _state_or_observation_value(
    state: dict[str, Any] | None,
    key: str,
    trajectory: list[dict[str, Any]],
    observation_key: str,
) -> Any:
    if state and key in state:
        return state[key]
    for call in trajectory:
        observation = call.get("observation")
        if isinstance(observation, dict) and observation_key in observation:
            return observation[observation_key]
    return None


def _final_response(session: dict[str, Any]) -> str:
    for message in reversed(session.get("messages", [])):
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            content = message.get("content")
            return content if isinstance(content, str) else ""
    return ""


def _status_label(error_category: str | None) -> str:
    if error_category is None:
        return "SUCCESS"
    if error_category == "Scientific Failure":
        return "CONSTRAINT NOT SATISFIED"
    return "FAILED"


def _parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _format_number(value: float) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Structured task values must be finite.")
    return f"{number:g}"
