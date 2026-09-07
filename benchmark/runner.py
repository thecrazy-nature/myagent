"""Run and score real Hermes + MATLAB workflow benchmarks.

This module only orchestrates frozen benchmark prompts and parses Hermes session
exports. It does not call the project handlers directly and does not synthesize
scientific observations.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark"
TASKS_PATH = BENCHMARK_ROOT / "tasks.json"
DOMAIN_TOOLS = {
    "create_focus_task",
    "run_focus_simulation",
    "evaluate_focus",
    "refine_focus",
}
HELPER_TOOLS = {"tool_search", "tool_describe"}


class BenchmarkInfrastructureError(RuntimeError):
    """The real Hermes session could not be executed, exported, or verified."""


def load_tasks(path: Path = TASKS_PATH) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    tasks = document.get("tasks")
    if document.get("status") != "designed_not_executed":
        raise BenchmarkInfrastructureError("Frozen task suite has an unexpected status.")
    if not isinstance(tasks, list) or len(tasks) != 12:
        raise BenchmarkInfrastructureError("Frozen task suite must contain 12 tasks.")
    return document, hashlib.sha256(raw).hexdigest()


def locate_hermes_python() -> Path:
    configured = os.environ.get("HERMES_PYTHON")
    candidates = []
    if configured:
        candidates.append(Path(configured))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data)
            / "hermes"
            / "hermes-agent"
            / "venv"
            / "Scripts"
            / "python.exe"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise BenchmarkInfrastructureError(
        "Hermes Python was not found; set HERMES_PYTHON for this process."
    )


def locate_hermes_home() -> Path:
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise BenchmarkInfrastructureError("LOCALAPPDATA is unavailable.")
    return (Path(local_app_data) / "hermes").resolve()


def locate_hermes_executable() -> str:
    executable = shutil.which("hermes")
    if not executable:
        raise BenchmarkInfrastructureError("hermes executable is not on PATH.")
    return executable


def require_network_environment() -> None:
    missing = [name for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY") if not os.environ.get(name)]
    if missing:
        raise BenchmarkInfrastructureError(
            "Missing process-level proxy variables: " + ", ".join(missing)
        )


def list_source_sessions(database_path: Path, source: str) -> list[tuple[str, float]]:
    if not database_path.is_file():
        raise BenchmarkInfrastructureError(f"Hermes state database not found: {database_path}")
    with sqlite3.connect(database_path, timeout=10) as connection:
        rows = connection.execute(
            "SELECT id, started_at FROM sessions WHERE source = ? ORDER BY started_at DESC",
            (source,),
        ).fetchall()
    return [(str(row[0]), float(row[1] or 0.0)) for row in rows]


def export_session(hermes_executable: str, session_id: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            hermes_executable,
            "sessions",
            "export",
            "-",
            "--session-id",
            session_id,
            "--yes",
            "--redact",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise BenchmarkInfrastructureError(
            f"Hermes session export failed: {completed.stderr.strip()}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exception:
        raise BenchmarkInfrastructureError(
            f"Hermes session export was not JSON: {exception}"
        ) from exception
    if payload.get("id") != session_id or not isinstance(payload.get("messages"), list):
        raise BenchmarkInfrastructureError("Hermes session export is incomplete.")
    return payload


def parse_session_calls(session: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return full Hermes calls and unwrapped project-domain calls."""

    observations = {
        message.get("tool_call_id"): message.get("content")
        for message in session.get("messages", [])
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    hermes_calls: list[dict[str, Any]] = []
    domain_calls: list[dict[str, Any]] = []
    for message in session.get("messages", []):
        if message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") or {}
            raw_name = function.get("name")
            raw_arguments = _parse_json_object(function.get("arguments"))
            call_id = tool_call.get("id") or tool_call.get("call_id")
            observation_text = observations.get(call_id)
            observation = _parse_json_value(observation_text)
            record = {
                "call_id": call_id,
                "raw_tool_name": raw_name,
                "raw_arguments": raw_arguments,
                "observation": observation,
            }
            hermes_calls.append(record)

            if raw_name == "tool_call" and isinstance(raw_arguments, dict):
                effective_name = raw_arguments.get("name")
                effective_arguments = raw_arguments.get("arguments")
            else:
                effective_name = raw_name
                effective_arguments = raw_arguments
            if effective_name in DOMAIN_TOOLS or raw_name == "tool_call":
                domain_calls.append(
                    {
                        "call_id": call_id,
                        "tool_name": effective_name,
                        "arguments": effective_arguments,
                        "observation": observation,
                    }
                )
    return hermes_calls, domain_calls


def score_task(
    task: dict[str, Any],
    session: dict[str, Any],
    end_to_end_runtime_sec: float,
) -> dict[str, Any]:
    hermes_calls, domain_calls = parse_session_calls(session)
    config = task["task_config"]
    allow_refine = task["expected_workflow"]["allow_refine"]
    phase = "start"
    agent_task_id: str | None = None
    valid_calls = 0
    invalid_reasons: list[str] = []
    terminal_seen = False

    for index, call in enumerate(domain_calls):
        name = call.get("tool_name")
        arguments = call.get("arguments")
        observation = call.get("observation")
        reasons: list[str] = []
        if terminal_seen:
            reasons.append("domain call occurred after a terminal evaluation")
        if name not in DOMAIN_TOOLS:
            reasons.append(f"unknown domain tool {name!r}")
        if not isinstance(arguments, dict):
            reasons.append("arguments are not an object")
            arguments = {}
        if not isinstance(observation, dict):
            reasons.append("observation is not a JSON object")
            observation = {}
        elif observation.get("error") is True:
            reasons.append(f"tool returned error: {observation.get('message')}")

        if name == "create_focus_task":
            if phase != "start":
                reasons.append("create_focus_task was not the first domain call")
            if not _same_vector(arguments.get("target_mm"), config["desired_target_mm"]):
                reasons.append("created target does not match frozen benchmark target")
            if not _same_number(
                arguments.get("tolerance_mm", 5.0), config["tolerance_mm"]
            ):
                reasons.append("created tolerance does not match frozen benchmark tolerance")
            if arguments.get("max_refinements", 2) != config["max_refinements"]:
                reasons.append("created refinement budget does not match frozen benchmark")
            candidate = observation.get("agent_task_id")
            if not isinstance(candidate, str):
                reasons.append("create observation has no agent_task_id")
            else:
                agent_task_id = candidate
            phase = "configured"
        elif name == "run_focus_simulation":
            if phase not in {"configured", "refined"}:
                reasons.append(f"run is invalid from phase {phase}")
            if arguments.get("agent_task_id") != agent_task_id:
                reasons.append("run uses a different agent_task_id")
            if observation.get("success") is not True:
                reasons.append("run observation is not successful")
            phase = "simulated"
        elif name == "evaluate_focus":
            if phase != "simulated":
                reasons.append(f"evaluate is invalid from phase {phase}")
            if arguments.get("agent_task_id") != agent_task_id:
                reasons.append("evaluate uses a different agent_task_id")
            if not isinstance(observation.get("success"), bool):
                reasons.append("evaluation observation has no boolean success")
            if observation.get("success") is True:
                phase = "terminal_success"
                terminal_seen = True
            elif observation.get("remaining_refinements") == 0:
                phase = "terminal_exhausted"
                terminal_seen = True
            else:
                phase = "evaluated_failed"
        elif name == "refine_focus":
            if not allow_refine:
                reasons.append("refinement is forbidden for this task")
            if phase != "evaluated_failed":
                reasons.append(f"refine is invalid from phase {phase}")
            if arguments.get("agent_task_id") != agent_task_id:
                reasons.append("refine uses a different agent_task_id")
            if observation.get("success") is not True:
                reasons.append("refine observation is not successful")
            phase = "refined"

        call["correct"] = not reasons
        call["correctness_issues"] = reasons
        if reasons:
            invalid_reasons.extend(f"call {index + 1}: {reason}" for reason in reasons)
        else:
            valid_calls += 1

    names = [call.get("tool_name") for call in domain_calls]
    run_calls = [call for call in domain_calls if call.get("tool_name") == "run_focus_simulation"]
    evaluations = [call for call in domain_calls if call.get("tool_name") == "evaluate_focus"]
    refinements = [call for call in domain_calls if call.get("tool_name") == "refine_focus"]
    create_count = names.count("create_focus_task")
    prefix_ok = names[:3] == task["expected_workflow"]["required_sequence"]
    paired_runs = len(run_calls) == len(evaluations)
    terminal_state = phase if phase.startswith("terminal_") else phase
    workflow_success = bool(
        domain_calls
        and not invalid_reasons
        and create_count == 1
        and prefix_ok
        and paired_runs
        and phase in {"terminal_success", "terminal_exhausted"}
    )

    replanning_opportunities = 0
    correct_replanning_decisions = 0
    for index, call in enumerate(domain_calls):
        if call.get("tool_name") != "evaluate_focus":
            continue
        observation = call.get("observation") or {}
        if (
            allow_refine
            and observation.get("success") is False
            and isinstance(observation.get("remaining_refinements"), int)
            and observation["remaining_refinements"] > 0
        ):
            replanning_opportunities += 1
            following = [item.get("tool_name") for item in domain_calls[index + 1 : index + 4]]
            if following == ["refine_focus", "run_focus_simulation", "evaluate_focus"]:
                correct_replanning_decisions += 1
    replanning_correctness = (
        correct_replanning_decisions == replanning_opportunities
        if replanning_opportunities
        else None
    )

    final_evaluation = evaluations[-1].get("observation") if evaluations else None
    first_evaluation = evaluations[0].get("observation") if evaluations else None
    successful_run_observations = [
        call.get("observation")
        for call in run_calls
        if isinstance(call.get("observation"), dict)
        and call["observation"].get("success") is True
    ]
    final_run = successful_run_observations[-1] if successful_run_observations else None
    final_focus_error = _number_or_none(final_evaluation, "focus_error_mm")
    constraint_satisfied = (
        final_focus_error <= float(config["tolerance_mm"])
        if final_focus_error is not None
        else None
    )
    stop_correctness = bool(
        domain_calls
        and names[-1] == "evaluate_focus"
        and phase in {"terminal_success", "terminal_exhausted"}
    )
    final_response = _final_assistant_response(session)
    terminal_report_correct = _report_matches_outcome(
        final_response,
        constraint_satisfied,
        require_explicit_conclusion=task["type"] != "A",
    )
    real_matlab_verified, matlab_verification_issues = _verify_real_matlab_runs(run_calls)

    tool_call_total = len(domain_calls)
    tool_call_correctness = valid_calls / tool_call_total if tool_call_total else 0.0
    matlab_runtime_values = [
        float(call["observation"]["matlab_runtime_sec"])
        for call in run_calls
        if isinstance(call.get("observation"), dict)
        and _finite_number(call["observation"].get("matlab_runtime_sec"))
    ]
    bridge_runtime_values = [
        float(call["observation"]["bridge_wall_time_sec"])
        for call in run_calls
        if isinstance(call.get("observation"), dict)
        and _finite_number(call["observation"].get("bridge_wall_time_sec"))
    ]
    matlab_infrastructure_error = _matlab_tool_failure(domain_calls)
    task_success = bool(
        workflow_success
        and stop_correctness
        and math.isclose(tool_call_correctness, 1.0)
        and terminal_report_correct
        and real_matlab_verified
        and matlab_infrastructure_error is None
    )

    return {
        "benchmark_id": task["id"],
        "task_type": task["type"],
        "task_name": task["name"],
        "prompt": task["prompt"],
        "hermes_session_id": session.get("id"),
        "hermes_model": session.get("model"),
        "hermes_provider": session.get("billing_provider"),
        "desired_target_mm": config["desired_target_mm"],
        "tolerance_mm": config["tolerance_mm"],
        "max_refinements": config["max_refinements"],
        "hermes_tool_call_trajectory": [
            _effective_tool_name(call) for call in hermes_calls
        ],
        "tool_call_trajectory": domain_calls,
        "tool_call_correctness": tool_call_correctness,
        "correct_tool_calls": valid_calls,
        "invalid_tool_calls": tool_call_total - valid_calls,
        "tool_call_correctness_issues": invalid_reasons,
        "workflow_success": workflow_success,
        "replanning_applicable": replanning_opportunities > 0,
        "replanning_correctness": replanning_correctness,
        "replanning_opportunities": replanning_opportunities,
        "correct_replanning_decisions": correct_replanning_decisions,
        "stop_correctness": stop_correctness,
        "terminal_report_correct": terminal_report_correct,
        "task_success": task_success,
        "constraint_satisfied": constraint_satisfied,
        "initial_focus_error_mm": _number_or_none(first_evaluation, "focus_error_mm"),
        "final_focus_error_mm": final_focus_error,
        "actual_peak_mm": final_run.get("actual_peak_mm") if isinstance(final_run, dict) else None,
        "peak_power": _number_or_none(final_run, "peak_power"),
        "matlab_calls": sum(_matlab_was_launched(call) for call in run_calls),
        "tool_calls": tool_call_total,
        "helper_tool_calls": sum(
            1 for call in hermes_calls if call.get("raw_tool_name") in HELPER_TOOLS
        ),
        "hermes_tool_calls_total": len(hermes_calls),
        "replanning_count": len(refinements),
        "matlab_runtime_sec": sum(matlab_runtime_values),
        "bridge_wall_time_sec": sum(bridge_runtime_values),
        "end_to_end_runtime_sec": end_to_end_runtime_sec,
        "terminal_state": terminal_state,
        "real_matlab_verified": real_matlab_verified,
        "matlab_verification_issues": matlab_verification_issues,
        "agent_final_response": final_response,
        "session_started_at": session.get("started_at"),
        "session_ended_at": session.get("ended_at"),
        "session_end_reason": session.get("end_reason"),
        "infrastructure_error": matlab_infrastructure_error,
    }


def run_real_task(
    task: dict[str, Any],
    *,
    source: str,
    hermes_python: Path,
    hermes_executable: str,
    hermes_home: Path,
    run_budget_sec: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    require_network_environment()
    database_path = hermes_home / "state.db"
    before = {session_id for session_id, _ in list_source_sessions(database_path, source)}
    prompt_path = BENCHMARK_ROOT / ".current_prompt.txt"
    prompt_path.write_text(task["prompt"], encoding="utf-8")
    environment = os.environ.copy()
    environment["HERMES_ENABLE_PROJECT_PLUGINS"] = "true"
    command = [
        str(hermes_python),
        "-m",
        "hermes_cli.main",
        "chat",
        "--query-file",
        str(prompt_path),
        "--oneshot",
        "--quiet",
        "--toolsets",
        "em_focus",
        "--reasoning",
        "minimal",
        "--source",
        source,
        "--max-turns",
        "30",
        "--run-budget",
        str(run_budget_sec),
    ]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=run_budget_sec + 120,
            check=False,
        )
    finally:
        prompt_path.unlink(missing_ok=True)
    elapsed = time.perf_counter() - started

    new_sessions: list[tuple[str, float]] = []
    for _ in range(20):
        after = list_source_sessions(database_path, source)
        new_sessions = [item for item in after if item[0] not in before]
        if new_sessions:
            break
        time.sleep(0.25)
    if not new_sessions:
        raise BenchmarkInfrastructureError(
            f"No new Hermes session was persisted. Exit={completed.returncode}; "
            f"stderr={completed.stderr.strip()}"
        )
    session_id = max(new_sessions, key=lambda item: item[1])[0]
    session = export_session(hermes_executable, session_id)
    result = score_task(task, session, elapsed)
    if completed.returncode != 0:
        result["infrastructure_error"] = (
            f"Hermes exited with {completed.returncode}: {completed.stderr.strip()}"
        )
        result["task_success"] = False
    trace = _sanitized_trace(task, session, result, completed.stdout, completed.stderr)
    return result, trace


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    valid_domain_calls = sum(item.get("correct_tool_calls", 0) for item in results)
    domain_calls = sum(item.get("tool_calls", 0) for item in results)
    replan_opportunities = sum(item.get("replanning_opportunities", 0) for item in results)
    correct_replans = sum(item.get("correct_replanning_decisions", 0) for item in results)
    final_errors = _numeric_values(results, "final_focus_error_mm")
    initial_errors = _numeric_values(results, "initial_focus_error_mm")
    summary = {
        "task_count": total,
        "task_success_rate": _boolean_rate(results, "task_success"),
        "constraint_satisfaction_rate": _boolean_rate(results, "constraint_satisfied"),
        "tool_call_correctness": valid_domain_calls / domain_calls if domain_calls else 0.0,
        "workflow_success_rate": _boolean_rate(results, "workflow_success"),
        "replanning_correctness": (
            correct_replans / replan_opportunities if replan_opportunities else None
        ),
        "stop_correctness": _boolean_rate(results, "stop_correctness"),
        "mean_final_focus_error_mm": statistics.fmean(final_errors) if final_errors else None,
        "median_final_focus_error_mm": statistics.median(final_errors) if final_errors else None,
        "mean_initial_focus_error_mm": statistics.fmean(initial_errors) if initial_errors else None,
        "average_matlab_calls_per_task": _mean_field(results, "matlab_calls"),
        "average_tool_calls_per_task": _mean_field(results, "tool_calls"),
        "average_replanning_count": _mean_field(results, "replanning_count"),
        "average_end_to_end_runtime_sec": _mean_field(results, "end_to_end_runtime_sec"),
        "total_matlab_calls": sum(item.get("matlab_calls", 0) for item in results),
        "total_matlab_runtime_sec": sum(item.get("matlab_runtime_sec", 0.0) for item in results),
        "total_end_to_end_runtime_sec": sum(item.get("end_to_end_runtime_sec", 0.0) for item in results),
        "infrastructure_failure_count": sum(bool(item.get("infrastructure_error")) for item in results),
    }
    summary["by_type"] = {
        task_type: _aggregate_type([item for item in results if item["task_type"] == task_type])
        for task_type in ("A", "B", "C", "D")
        if any(item["task_type"] == task_type for item in results)
    }
    return summary


def write_outputs(
    results: list[dict[str, Any]],
    *,
    document: dict[str, Any],
    tasks_sha256: str,
    output_stem: str,
) -> None:
    ordered = sorted(results, key=lambda item: item["benchmark_id"])
    summary = aggregate_results(ordered)
    payload = {
        "schema_version": 1,
        "suite_id": document["suite_id"],
        "tasks_sha256": tasks_sha256,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution": "real_hermes_agent_plus_real_matlab",
        "results": ordered,
        "summary": summary,
    }
    json_path = BENCHMARK_ROOT / f"{output_stem}.json"
    csv_path = BENCHMARK_ROOT / f"{output_stem}.csv"
    if output_stem == "results":
        markdown_path = BENCHMARK_ROOT / "summary.md"
    elif output_stem == "smoke_results":
        markdown_path = BENCHMARK_ROOT / "smoke_summary.md"
    else:
        markdown_path = BENCHMARK_ROOT / f"{output_stem}.md"
    _write_json_atomic(json_path, payload)
    _write_csv_atomic(csv_path, ordered)
    _write_text_atomic(markdown_path, _summary_markdown(payload))


def load_reusable_results(path: Path | None, tasks_sha256: str) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("tasks_sha256") != tasks_sha256:
        raise BenchmarkInfrastructureError("Reusable results use a different frozen task hash.")
    results = payload.get("results")
    if not isinstance(results, list):
        raise BenchmarkInfrastructureError("Reusable results file has no result list.")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-ids", nargs="*", help="Frozen task IDs; default is all 12.")
    parser.add_argument("--output-stem", default="results")
    parser.add_argument("--reuse-results", type=Path)
    parser.add_argument(
        "--rescore-only",
        action="store_true",
        help="Re-export and rescore --reuse-results sessions without new model or MATLAB calls.",
    )
    parser.add_argument("--run-budget-sec", type=int, default=900)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    document, tasks_sha256 = load_tasks()
    tasks_by_id = {task["id"]: task for task in document["tasks"]}
    selected_ids = args.task_ids or list(tasks_by_id)
    unknown = [task_id for task_id in selected_ids if task_id not in tasks_by_id]
    if unknown:
        raise BenchmarkInfrastructureError("Unknown benchmark IDs: " + ", ".join(unknown))
    if len(selected_ids) != len(set(selected_ids)):
        raise BenchmarkInfrastructureError("Duplicate benchmark IDs were requested.")
    if args.validate_only:
        print(f"Validated {len(selected_ids)} frozen tasks; sha256={tasks_sha256}")
        return 0

    reusable = load_reusable_results(args.reuse_results, tasks_sha256)
    if args.rescore_only:
        if not reusable:
            raise BenchmarkInfrastructureError("--rescore-only requires --reuse-results.")
        hermes_executable = locate_hermes_executable()
        reusable_by_id = {item.get("benchmark_id"): item for item in reusable}
        rescored: list[dict[str, Any]] = []
        for task_id in selected_ids:
            previous = reusable_by_id.get(task_id)
            if not previous or not previous.get("hermes_session_id"):
                raise BenchmarkInfrastructureError(f"No reusable Hermes session for {task_id}.")
            session = export_session(hermes_executable, previous["hermes_session_id"])
            rescored.append(score_task(
                tasks_by_id[task_id],
                session,
                float(previous.get("end_to_end_runtime_sec", 0.0)),
            ))
            print(f"RESCORE {task_id}", flush=True)
        write_outputs(
            rescored,
            document=document,
            tasks_sha256=tasks_sha256,
            output_stem=args.output_stem,
        )
        return 0
    results_by_id = {
        item["benchmark_id"]: item
        for item in reusable
        if item.get("benchmark_id") in selected_ids
    }
    hermes_python = locate_hermes_python()
    hermes_home = locate_hermes_home()
    hermes_executable = locate_hermes_executable()
    source = f"benchmark_gate6_{args.output_stem}"
    traces_root = BENCHMARK_ROOT / "traces" / args.output_stem
    traces_root.mkdir(parents=True, exist_ok=True)

    for task_id in selected_ids:
        if task_id in results_by_id:
            print(f"REUSE {task_id}", flush=True)
            continue
        task = tasks_by_id[task_id]
        print(f"START {task_id} ({task['type']})", flush=True)
        try:
            result, trace = run_real_task(
                task,
                source=source,
                hermes_python=hermes_python,
                hermes_executable=hermes_executable,
                hermes_home=hermes_home,
                run_budget_sec=args.run_budget_sec,
            )
            results_by_id[task_id] = result
            _write_json_atomic(traces_root / f"{task_id}.json", trace)
            print(
                f"DONE {task_id} task_success={result['task_success']} "
                f"constraint_satisfied={result['constraint_satisfied']} "
                f"matlab_calls={result['matlab_calls']}",
                flush=True,
            )
        except Exception as exception:
            results_by_id[task_id] = _infrastructure_failure_result(task, exception)
            print(f"FAILED {task_id}: {type(exception).__name__}: {exception}", flush=True)
        write_outputs(
            list(results_by_id.values()),
            document=document,
            tasks_sha256=tasks_sha256,
            output_stem=args.output_stem,
        )

    return 0


def _verify_real_matlab_runs(run_calls: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    seen: set[str] = set()
    for call in run_calls:
        observation = call.get("observation")
        if not isinstance(observation, dict):
            issues.append("run has no structured observation")
            continue
        simulation_id = observation.get("simulation_run_id")
        if not isinstance(simulation_id, str) or not simulation_id.startswith("sim_"):
            issues.append("run has no valid simulation_run_id")
            continue
        if simulation_id in seen:
            issues.append(f"duplicate simulation_run_id {simulation_id}")
        seen.add(simulation_id)
        result_path = PROJECT_ROOT / "runs" / simulation_id / "result.json"
        try:
            result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exception:
            issues.append(f"cannot read real MATLAB result for {simulation_id}: {exception}")
            continue
        if result.get("status") != "success" or result.get("task_id") != simulation_id:
            issues.append(f"MATLAB result identity/status mismatch for {simulation_id}")
        if not _same_vector(result.get("actual_peak_mm"), observation.get("actual_peak_mm")):
            issues.append(f"actual peak mismatch for {simulation_id}")
        if not _same_number(result.get("peak_power"), observation.get("peak_power")):
            issues.append(f"peak power mismatch for {simulation_id}")
    return bool(run_calls) and not issues, issues


def _sanitized_trace(
    task: dict[str, Any],
    session: dict[str, Any],
    result: dict[str, Any],
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    return {
        "benchmark_id": task["id"],
        "session_id": session.get("id"),
        "source": session.get("source"),
        "model": session.get("model"),
        "provider": session.get("billing_provider"),
        "started_at": session.get("started_at"),
        "ended_at": session.get("ended_at"),
        "end_reason": session.get("end_reason"),
        "prompt": task["prompt"],
        "hermes_tool_call_trajectory": result["hermes_tool_call_trajectory"],
        "tool_call_trajectory": result["tool_call_trajectory"],
        "agent_final_response": result["agent_final_response"],
        "process_stdout": stdout,
        "process_stderr": stderr,
    }


def _infrastructure_failure_result(task: dict[str, Any], exception: Exception) -> dict[str, Any]:
    config = task["task_config"]
    return {
        "benchmark_id": task["id"],
        "task_type": task["type"],
        "task_name": task["name"],
        "prompt": task["prompt"],
        "desired_target_mm": config["desired_target_mm"],
        "tolerance_mm": config["tolerance_mm"],
        "max_refinements": config["max_refinements"],
        "hermes_tool_call_trajectory": [],
        "tool_call_trajectory": [],
        "tool_call_correctness": 0.0,
        "correct_tool_calls": 0,
        "invalid_tool_calls": 0,
        "tool_call_correctness_issues": [],
        "workflow_success": False,
        "replanning_applicable": False,
        "replanning_correctness": None,
        "replanning_opportunities": 0,
        "correct_replanning_decisions": 0,
        "stop_correctness": False,
        "terminal_report_correct": False,
        "task_success": False,
        "constraint_satisfied": None,
        "initial_focus_error_mm": None,
        "final_focus_error_mm": None,
        "actual_peak_mm": None,
        "peak_power": None,
        "matlab_calls": 0,
        "tool_calls": 0,
        "helper_tool_calls": 0,
        "hermes_tool_calls_total": 0,
        "replanning_count": 0,
        "matlab_runtime_sec": 0.0,
        "bridge_wall_time_sec": 0.0,
        "end_to_end_runtime_sec": 0.0,
        "terminal_state": "infrastructure_failure",
        "real_matlab_verified": False,
        "matlab_verification_issues": [],
        "agent_final_response": "",
        "infrastructure_error": f"{type(exception).__name__}: {exception}",
    }


def _summary_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    results = payload["results"]
    lines = [
        "# Gate 6 benchmark summary",
        "",
        f"- Suite: `{payload['suite_id']}`",
        f"- Execution: `{payload['execution']}`",
        f"- Frozen tasks SHA-256: `{payload['tasks_sha256']}`",
        f"- Tasks recorded: {summary['task_count']}",
        "",
        "## Overall metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Task Success Rate | {_percent(summary['task_success_rate'])} |",
        f"| Constraint Satisfaction Rate | {_percent(summary['constraint_satisfaction_rate'])} |",
        f"| Tool Call Correctness | {_percent(summary['tool_call_correctness'])} |",
        f"| Workflow Success Rate | {_percent(summary['workflow_success_rate'])} |",
        f"| Replanning Correctness | {_percent(summary['replanning_correctness'])} |",
        f"| Stop Correctness | {_percent(summary['stop_correctness'])} |",
        f"| Mean Final Focus Error | {_decimal(summary['mean_final_focus_error_mm'])} mm |",
        f"| Median Final Focus Error | {_decimal(summary['median_final_focus_error_mm'])} mm |",
        f"| Mean Initial Focus Error | {_decimal(summary['mean_initial_focus_error_mm'])} mm |",
        f"| Average MATLAB Calls / Task | {_decimal(summary['average_matlab_calls_per_task'])} |",
        f"| Average Tool Calls / Task | {_decimal(summary['average_tool_calls_per_task'])} |",
        f"| Average Replanning Count | {_decimal(summary['average_replanning_count'])} |",
        f"| Average End-to-End Runtime | {_decimal(summary['average_end_to_end_runtime_sec'])} s |",
        "",
        "## Results by task",
        "",
        "| ID | Type | Task success | Constraint | Workflow | Stop | Initial error | Final error | MATLAB calls | Tool calls | Replans | E2E s |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        lines.append(
            f"| {result['benchmark_id']} | {result['task_type']} | "
            f"{_bool_text(result.get('task_success'))} | "
            f"{_bool_text(result.get('constraint_satisfied'))} | "
            f"{_bool_text(result.get('workflow_success'))} | "
            f"{_bool_text(result.get('stop_correctness'))} | "
            f"{_decimal(result.get('initial_focus_error_mm'))} | "
            f"{_decimal(result.get('final_focus_error_mm'))} | "
            f"{result.get('matlab_calls', 0)} | {result.get('tool_calls', 0)} | "
            f"{result.get('replanning_count', 0)} | "
            f"{_decimal(result.get('end_to_end_runtime_sec'))} |"
        )
    lines.extend(["", "## Results by type", "", "| Type | Tasks | Task success | Constraint | Workflow | Stop | Mean final error |", "|---|---:|---:|---:|---:|---:|---:|"])
    for task_type, item in summary.get("by_type", {}).items():
        lines.append(
            f"| {task_type} | {item['task_count']} | {_percent(item['task_success_rate'])} | "
            f"{_percent(item['constraint_satisfaction_rate'])} | "
            f"{_percent(item['workflow_success_rate'])} | {_percent(item['stop_correctness'])} | "
            f"{_decimal(item['mean_final_focus_error_mm'])} mm |"
        )
    failures = [
        result for result in results
        if not result.get("task_success") or result.get("constraint_satisfied") is False
    ]
    lines.extend(["", "## Failed or constraint-unsatisfied cases", ""])
    if not failures:
        lines.append("None.")
    else:
        for result in failures:
            lines.append(
                f"- **{result['benchmark_id']}**: task_success={result.get('task_success')}, "
                f"constraint_satisfied={result.get('constraint_satisfied')}, "
                f"terminal_state={result.get('terminal_state')}, "
                f"infrastructure_error={result.get('infrastructure_error')}"
            )
    lines.extend([
        "",
        "## Timing interpretation",
        "",
        "End-to-end time includes Hermes orchestration and fresh `matlab -batch` process startup. "
        "MATLAB `runtime_sec` is reported separately; their difference is not labelled as LLM reasoning time.",
        "",
    ])
    return "\n".join(lines)


def _write_csv_atomic(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "benchmark_id", "task_type", "desired_target_mm", "tolerance_mm",
        "hermes_session_id", "hermes_tool_call_trajectory", "tool_call_correctness",
        "workflow_success", "replanning_correctness", "stop_correctness", "task_success",
        "constraint_satisfied", "initial_focus_error_mm", "final_focus_error_mm",
        "actual_peak_mm", "peak_power", "matlab_calls", "tool_calls",
        "hermes_tool_calls_total", "replanning_count", "matlab_runtime_sec",
        "bridge_wall_time_sec", "end_to_end_runtime_sec", "terminal_state",
        "real_matlab_verified", "agent_final_response", "infrastructure_error",
    ]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for result in results:
            row = result.copy()
            for field in ("desired_target_mm", "hermes_tool_call_trajectory", "actual_peak_mm"):
                row[field] = json.dumps(row.get(field), ensure_ascii=False)
            writer.writerow(row)
    os.replace(temporary, path)


def _write_json_atomic(path: Path, value: Any) -> None:
    _write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _effective_tool_name(call: dict[str, Any]) -> str | None:
    if call.get("raw_tool_name") == "tool_call":
        arguments = call.get("raw_arguments")
        return arguments.get("name") if isinstance(arguments, dict) else "tool_call"
    return call.get("raw_tool_name")


def _parse_json_object(value: Any) -> dict[str, Any] | None:
    parsed = _parse_json_value(value)
    return parsed if isinstance(parsed, dict) else None


def _parse_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _final_assistant_response(session: dict[str, Any]) -> str:
    for message in reversed(session.get("messages", [])):
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            content = message.get("content")
            return content if isinstance(content, str) else ""
    return ""


def _report_matches_outcome(
    response: str,
    constraint_satisfied: Any,
    *,
    require_explicit_conclusion: bool,
) -> bool:
    if not response.strip() or not isinstance(constraint_satisfied, bool):
        return False
    lowered = response.lower()
    negative = any(
        token in lowered
        for token in ("未达到", "未达标", "不满足", "失败", "failed", "not meet", "not satisfied", "exhaust")
    )
    positive = any(
        token in lowered
        for token in ("达到要求", "满足", "达标", "成功", "satisfied", "success", "within tolerance")
    )
    if constraint_satisfied:
        return positive or (not require_explicit_conclusion and not negative)
    return negative


def _matlab_was_launched(call: dict[str, Any]) -> bool:
    observation = call.get("observation")
    if not isinstance(observation, dict):
        return False
    return observation.get("success") is True or observation.get("error_type") == "MatlabProcessError"


def _matlab_tool_failure(domain_calls: list[dict[str, Any]]) -> str | None:
    messages = [
        str(observation.get("message", "MATLAB process failed"))
        for call in domain_calls
        if call.get("tool_name") == "run_focus_simulation"
        and isinstance((observation := call.get("observation")), dict)
        and observation.get("error_type") == "MatlabProcessError"
    ]
    return "MATLAB tool failure: " + "; ".join(messages) if messages else None


def _same_vector(first: Any, second: Any) -> bool:
    return (
        isinstance(first, list)
        and isinstance(second, list)
        and len(first) == len(second) == 3
        and all(_same_number(a, b) for a, b in zip(first, second))
    )


def _same_number(first: Any, second: Any) -> bool:
    return _finite_number(first) and _finite_number(second) and math.isclose(
        float(first), float(second), rel_tol=1e-9, abs_tol=1e-9
    )


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _number_or_none(mapping: Any, key: str) -> float | None:
    if isinstance(mapping, dict) and _finite_number(mapping.get(key)):
        return float(mapping[key])
    return None


def _numeric_values(results: list[dict[str, Any]], key: str) -> list[float]:
    return [float(item[key]) for item in results if _finite_number(item.get(key))]


def _boolean_rate(results: list[dict[str, Any]], key: str) -> float | None:
    values = [item.get(key) for item in results if isinstance(item.get(key), bool)]
    return sum(values) / len(values) if values else None


def _mean_field(results: list[dict[str, Any]], key: str) -> float | None:
    values = _numeric_values(results, key)
    return statistics.fmean(values) if values else None


def _aggregate_type(results: list[dict[str, Any]]) -> dict[str, Any]:
    final_errors = _numeric_values(results, "final_focus_error_mm")
    return {
        "task_count": len(results),
        "task_success_rate": _boolean_rate(results, "task_success"),
        "constraint_satisfaction_rate": _boolean_rate(results, "constraint_satisfied"),
        "workflow_success_rate": _boolean_rate(results, "workflow_success"),
        "stop_correctness": _boolean_rate(results, "stop_correctness"),
        "mean_final_focus_error_mm": statistics.fmean(final_errors) if final_errors else None,
        "average_matlab_calls_per_task": _mean_field(results, "matlab_calls"),
        "average_tool_calls_per_task": _mean_field(results, "tool_calls"),
        "average_replanning_count": _mean_field(results, "replanning_count"),
        "average_end_to_end_runtime_sec": _mean_field(results, "end_to_end_runtime_sec"),
    }


def _percent(value: Any) -> str:
    return "N/A" if value is None else f"{100.0 * float(value):.1f}%"


def _decimal(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.3f}"


def _bool_text(value: Any) -> str:
    return "PASS" if value is True else "FAIL" if value is False else "N/A"


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkInfrastructureError as exception:
        print(f"ERROR: {exception}", file=sys.stderr)
        raise SystemExit(2)
