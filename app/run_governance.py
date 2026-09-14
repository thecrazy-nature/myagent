"""Reproducibility, provenance, usage, and data-boundary evidence for UI runs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_MANIFEST = PROJECT_ROOT / "governance" / "versions.json"
HASH_GROUPS = {
    "prompt_sha256": [".hermes.md"],
    "tool_schema_sha256": [
        ".hermes/plugins/em_focus/__init__.py",
        ".hermes/plugins/em_focus/schemas.py",
        ".hermes/plugins/em_focus/tools.py",
    ],
    "matlab_contract_sha256": [
        "agent_interface/agent_run_simulation.m",
        "agent_interface/agent_evaluate_geometry_batch.m",
        "matlab_core/run_focus_core.m",
    ],
    "bridge_sha256": [
        "bridge/matlab_bridge.py",
        "bridge/models.py",
        "em_focus_agent/task_state.py",
    ],
}
TOKEN_FIELDS = (
    "api_call_count",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
GENERATED_ARGUMENT_KEYS = {
    "agent_task_id", "design_task_id", "simulation_run_id", "geometry_id", "batch_id"
}


def build_submission_snapshot(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Capture immutable-at-submission provenance without starting Hermes or MATLAB."""

    versions = _load_versions(project_root)
    return {
        "schema_version": 1,
        "captured_at": _utc_now(),
        "versions": versions,
        "source_hashes": {
            name: _hash_files(project_root, relative_paths)
            for name, relative_paths in HASH_GROUPS.items()
        } | {"runtime_source_sha256": _hash_runtime_tree(project_root)},
        "git": _git_evidence(project_root),
        "runtime": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "hermes_version": _hermes_package_version(),
        },
        "randomness": {
            "focus_seed": 0,
            "matlab_rng_algorithm": "twister",
            "focus_solver_policy": "固定随机种子；当前聚焦数值路径本身不使用随机搜索",
            "array_design_policy": "每个几何评估／搜索 Tool Call 显式记录整数 seed",
        },
        "external_data": {
            "external_llm_used": True,
            "notice": "提交后，Hermes 会把自然语言请求和紧凑工具结果发送给所配置的外部模型服务。",
            "sent_to_model": [
                "用户自然语言请求与实验配置",
                "Tool schema",
                "任务 ID、工具参数与紧凑数值结果",
                "MATLAB 峰值、误差和评价指标摘要",
            ],
            "kept_local": [
                "完整二维场矩阵 JSON/MAT",
                "MATLAB stdout/stderr 日志",
                "Hermes 认证凭据和项目密钥",
            ],
            "full_field_sent_to_model": False,
        },
    }


def read_session_usage(database_path: Path, session_id: str) -> dict[str, Any] | None:
    """Read non-message Hermes accounting metadata; never reads prompts or credentials."""

    if not database_path.is_file() or not session_id:
        return None
    columns = (
        "model, model_config, system_prompt_hash, input_tokens, output_tokens, "
        "cache_read_tokens, cache_write_tokens, reasoning_tokens, billing_provider, "
        "billing_base_url, billing_mode, estimated_cost_usd, actual_cost_usd, "
        "cost_status, cost_source, pricing_version, api_call_count, profile_name"
    )
    try:
        uri = database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                f"SELECT {columns} FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            try:
                model_rows = connection.execute(
                    "SELECT model, billing_provider, billing_base_url, billing_mode, task, "
                    "api_call_count, input_tokens, output_tokens, cache_read_tokens, "
                    "cache_write_tokens, reasoning_tokens, estimated_cost_usd, "
                    "actual_cost_usd, cost_status, cost_source "
                    "FROM session_model_usage WHERE session_id = ? ORDER BY first_seen",
                    (session_id,),
                ).fetchall()
            except sqlite3.Error:
                model_rows = []
    except (OSError, sqlite3.Error):
        return None
    value = dict(row)
    value["model_config"] = _json_or_value(value.get("model_config"))
    value["model_revision"] = None
    value["per_model_usage"] = [dict(item) for item in model_rows]
    return value


def usage_delta(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    new_session: bool,
) -> dict[str, Any] | None:
    """Return this turn's accounting delta from cumulative Hermes session counters."""

    if after is None:
        return None
    result = {
        key: max(0, _number(after.get(key)) - (0 if new_session else _number((before or {}).get(key))))
        for key in TOKEN_FIELDS
    }
    for key in (
        "model", "model_revision", "model_config", "system_prompt_hash", "profile_name",
        "billing_provider", "billing_base_url", "billing_mode", "cost_status",
        "cost_source", "pricing_version",
    ):
        result[key] = after.get(key)
    result["session_cumulative_per_model_usage"] = after.get("per_model_usage", [])
    result["actual_cost_usd"] = _cost_delta(
        before, after, "actual_cost_usd", new_session=new_session
    )
    result["estimated_cost_usd"] = _cost_delta(
        before, after, "estimated_cost_usd", new_session=new_session
    )
    result["cost_is_provider_actual"] = result["actual_cost_usd"] is not None
    return result


def build_completion_governance(
    submission: dict[str, Any],
    session_accounting: dict[str, Any] | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Combine the submission snapshot with actual Agent and MATLAB evidence."""

    governance = dict(submission)
    governance["completed_at"] = _utc_now()
    governance["llm"] = session_accounting or {
        "model": None,
        "model_revision": None,
        "accounting_status": "Hermes 未提供本次会话计量数据",
    }
    governance["matlab"] = extract_matlab_environment(result)
    return governance


def extract_matlab_environment(result: dict[str, Any]) -> dict[str, Any]:
    state = result.get("agent_state")
    if isinstance(state, dict):
        simulations = [
            event for event in state.get("history", [])
            if isinstance(event, dict) and event.get("event") == "simulation"
        ]
        if simulations:
            latest = simulations[-1]
            return {
                "version": latest.get("matlab_version"),
                "release": latest.get("matlab_release"),
                "architecture": latest.get("matlab_arch"),
                "random_seed": latest.get("random_seed"),
                "rng_algorithm": latest.get("rng_algorithm"),
            }
    design_state = result.get("design_state")
    if isinstance(design_state, dict) and isinstance(design_state.get("matlab_environment"), dict):
        return dict(design_state["matlab_environment"])
    return {
        "version": None,
        "release": None,
        "architecture": None,
        "random_seed": None,
        "rng_algorithm": None,
    }


def compare_job_reproducibility(
    baseline_job: dict[str, Any], repeat_job: dict[str, Any]
) -> dict[str, Any]:
    """Separate stochastic Agent consistency from deterministic MATLAB consistency."""

    first = baseline_job.get("result")
    second = repeat_job.get("result")
    if not isinstance(first, dict) or not isinstance(second, dict):
        return {"status": "pending", "message": "两次任务均结束后才能比较。"}
    same_task = baseline_job.get("task_text") == repeat_job.get("task_text")
    first_hashes = (baseline_job.get("submission_governance") or {}).get("source_hashes")
    second_hashes = (repeat_job.get("submission_governance") or {}).get("source_hashes")
    same_source = (
        isinstance(first_hashes, dict) and bool(first_hashes)
        and isinstance(second_hashes, dict) and first_hashes == second_hashes
    )
    first_trajectory = _normalized_trajectory(first.get("trajectory"))
    second_trajectory = _normalized_trajectory(second.get("trajectory"))
    first_numeric = _numerical_outcome(first)
    second_numeric = _numerical_outcome(second)
    trajectory_consistent = first_trajectory == second_trajectory
    maximum_delta = _maximum_numeric_delta(first_numeric, second_numeric)
    matlab_consistent = (
        first_numeric is not None
        and second_numeric is not None
        and maximum_delta is not None
        and maximum_delta <= 1.0e-9
    )
    outcome_consistent = (
        first.get("status") == second.get("status")
        and first.get("constraint_satisfied") == second.get("constraint_satisfied")
    )
    return {
        "status": "consistent" if same_task and same_source and trajectory_consistent and matlab_consistent and outcome_consistent else "different",
        "same_task_text": same_task,
        "same_source_snapshot": same_source,
        "agent_trajectory_consistent": trajectory_consistent,
        "matlab_numerically_consistent": matlab_consistent,
        "final_outcome_consistent": outcome_consistent,
        "maximum_matlab_numeric_delta": maximum_delta,
        "note": "LLM 决策可能有随机性；MATLAB 数值一致性与 Agent 轨迹一致性分别判定。",
    }


def _normalized_trajectory(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {
            "tool_name": item.get("tool_name"),
            "arguments": _drop_generated(item.get("arguments", {})),
            "success": (item.get("observation") or {}).get("success")
            if isinstance(item.get("observation"), dict) else None,
        }
        for item in value if isinstance(item, dict)
    ]


def _drop_generated(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_generated(item) for key, item in value.items()
            if key not in GENERATED_ARGUMENT_KEYS
        }
    if isinstance(value, list):
        return [_drop_generated(item) for item in value]
    return value


def _numerical_outcome(result: dict[str, Any]) -> Any:
    state = result.get("agent_state")
    if isinstance(state, dict):
        simulations = [
            event for event in state.get("history", [])
            if isinstance(event, dict) and event.get("event") == "simulation"
        ]
        evaluations = [
            event for event in state.get("history", [])
            if isinstance(event, dict) and event.get("event") == "evaluation"
        ]
        if simulations:
            latest = simulations[-1]
            evaluation = evaluations[-1] if evaluations else {}
            return {
                "actual_peak_points_mm": latest.get("actual_peak_points_mm"),
                "peak_power_by_user": latest.get("peak_power_by_user"),
                "focus_errors_mm": evaluation.get("focus_errors_mm"),
                "fwhm_x_mm_by_user": latest.get("fwhm_x_mm_by_user"),
                "dof_z_mm_by_user": latest.get("dof_z_mm_by_user"),
            }
    selected = result.get("selected_design")
    if isinstance(selected, dict):
        return {
            "geometry": (selected.get("geometry") or {}).get("element_positions_mm"),
            "metrics": selected.get("metrics"),
        }
    return None


def _maximum_numeric_delta(first: Any, second: Any) -> float | None:
    left = list(_numbers(first))
    right = list(_numbers(second))
    if not left or len(left) != len(right):
        return None
    return max(abs(a - b) for a, b in zip(left, right))


def _numbers(value: Any) -> Iterable[float]:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        yield float(value)
    elif isinstance(value, dict):
        for key in sorted(value):
            yield from _numbers(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _numbers(item)


def _load_versions(project_root: Path) -> dict[str, Any]:
    path = project_root / "governance" / "versions.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"schema_version": 1, "status": "version manifest unavailable"}
    return value if isinstance(value, dict) else {"schema_version": 1, "status": "invalid manifest"}


def _hash_files(project_root: Path, relative_paths: list[str]) -> str | None:
    digest = hashlib.sha256()
    try:
        for relative in relative_paths:
            path = project_root / relative
            digest.update(relative.replace("\\", "/").encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except OSError:
        return None
    return digest.hexdigest()


def _hash_runtime_tree(project_root: Path) -> str | None:
    roots = (
        ".hermes.md", ".hermes/plugins/em_focus", "agent_interface", "app",
        "array_design", "bridge", "em_focus_agent", "governance", "matlab_core",
        "run-app.ps1", "proxy-on.ps1", "proxy-off.ps1",
    )
    paths: list[Path] = []
    for relative in roots:
        path = project_root / relative
        if path.is_file():
            paths.append(path)
        elif path.is_dir():
            paths.extend(
                item for item in path.rglob("*")
                if item.is_file() and "__pycache__" not in item.parts
                and item.suffix.lower() in {".py", ".m", ".md", ".json", ".ps1"}
            )
    if not paths:
        return None
    digest = hashlib.sha256()
    try:
        for path in sorted(set(paths), key=lambda item: item.relative_to(project_root).as_posix()):
            relative = path.relative_to(project_root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except OSError:
        return None
    return digest.hexdigest()


def _git_evidence(project_root: Path) -> dict[str, Any]:
    def run(*arguments: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *arguments], cwd=project_root, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    branch = run("branch", "--show-current")
    status = run("status", "--porcelain")
    return {
        "commit": commit,
        "short_commit": commit[:12] if commit else None,
        "branch": branch,
        "dirty": bool(status) if status is not None else None,
        "working_tree_status_available": status is not None,
    }


def _hermes_package_version() -> str | None:
    configured = os.environ.get("HERMES_PYTHON")
    candidates = [Path(configured)] if configured else []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe")
    for executable in candidates:
        site_packages = executable.parent.parent / "Lib" / "site-packages"
        for metadata in sorted(site_packages.glob("*hermes*.dist-info/METADATA")):
            try:
                fields = {}
                for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith(("Name: ", "Version: ")):
                        key, value = line.split(": ", 1)
                        fields[key] = value
                if fields.get("Version"):
                    return f"{fields.get('Name', 'hermes')} {fields['Version']}"
            except OSError:
                continue
    return None


def _cost_delta(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    field: str,
    *,
    new_session: bool,
) -> float | None:
    after_value = after.get(field)
    if not isinstance(after_value, (int, float)) or isinstance(after_value, bool):
        return None
    if new_session:
        return max(0.0, float(after_value))
    before_value = (before or {}).get(field)
    if not isinstance(before_value, (int, float)) or isinstance(before_value, bool):
        return None
    return max(0.0, float(after_value) - float(before_value))


def _number(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _json_or_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
