"""Exception types and lightweight result validation for the bridge."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any


class MatlabBridgeError(Exception):
    """Base class for all expected bridge failures."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str | None = None,
        run_dir: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.run_dir = run_dir


class InvalidSimulationInput(MatlabBridgeError):
    """Python input is invalid and MATLAB was not started."""


class MatlabProcessError(MatlabBridgeError):
    """MATLAB could not be started or returned a nonzero exit code."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str | None = None,
        run_dir: Path | None = None,
        returncode: int | None = None,
    ) -> None:
        super().__init__(message, task_id=task_id, run_dir=run_dir)
        self.returncode = returncode


class MatlabExecutableNotFound(MatlabProcessError):
    """No MATLAB executable can be resolved."""


class MatlabTimeoutError(MatlabBridgeError):
    """MATLAB exceeded the caller's timeout."""


class MatlabResultError(MatlabBridgeError):
    """MATLAB output is absent, malformed, or violates the result contract."""


class MatlabSimulationError(MatlabResultError):
    """MATLAB returned a structured result with status=error."""

    def __init__(
        self,
        message: str,
        *,
        result: dict[str, Any],
        task_id: str | None = None,
        run_dir: Path | None = None,
    ) -> None:
        super().__init__(message, task_id=task_id, run_dir=run_dir)
        self.result = result
        self.error_type = result.get("error_type")


REQUIRED_SUCCESS_FIELDS = {
    "status",
    "task_id",
    "requested_focus_mm",
    "actual_peak_mm",
    "peak_power",
    "peak_power_definition",
    "requested_power",
    "runtime_sec",
}


def validate_success_result(
    payload: Any,
    *,
    expected_task_id: str,
    run_dir: Path,
) -> dict[str, Any]:
    """Validate MATLAB JSON and return the original dictionary."""

    if not isinstance(payload, dict):
        raise MatlabResultError(
            "MATLAB result JSON must contain one object.",
            task_id=expected_task_id,
            run_dir=run_dir,
        )

    status = payload.get("status")
    if status == "error":
        error_type = payload.get("error_type", "unknown")
        message = payload.get("message", "MATLAB reported an unspecified error.")
        raise MatlabSimulationError(
            f"MATLAB simulation failed ({error_type}): {message}",
            result=payload,
            task_id=expected_task_id,
            run_dir=run_dir,
        )
    if status != "success":
        raise MatlabResultError(
            f"Unexpected MATLAB result status: {status!r}.",
            task_id=expected_task_id,
            run_dir=run_dir,
        )

    missing = sorted(REQUIRED_SUCCESS_FIELDS.difference(payload))
    if missing:
        raise MatlabResultError(
            f"MATLAB success result is missing fields: {', '.join(missing)}.",
            task_id=expected_task_id,
            run_dir=run_dir,
        )
    if payload["task_id"] != expected_task_id:
        raise MatlabResultError(
            "MATLAB result task_id does not match the requested task.",
            task_id=expected_task_id,
            run_dir=run_dir,
        )

    _validate_vector(
        payload["requested_focus_mm"],
        "requested_focus_mm",
        task_id=expected_task_id,
        run_dir=run_dir,
    )
    _validate_vector(
        payload["actual_peak_mm"],
        "actual_peak_mm",
        task_id=expected_task_id,
        run_dir=run_dir,
    )
    for name in ("peak_power", "requested_power", "runtime_sec"):
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MatlabResultError(
                f"MATLAB result field {name} must be numeric.",
                task_id=expected_task_id,
                run_dir=run_dir,
            )
        if not math.isfinite(float(value)):
            raise MatlabResultError(
                f"MATLAB result field {name} must be finite.",
                task_id=expected_task_id,
                run_dir=run_dir,
            )
    if not isinstance(payload["peak_power_definition"], str):
        raise MatlabResultError(
            "MATLAB result field peak_power_definition must be a string.",
            task_id=expected_task_id,
            run_dir=run_dir,
        )
    return payload


def _validate_vector(
    value: Any,
    name: str,
    *,
    task_id: str,
    run_dir: Path,
) -> None:
    if not isinstance(value, list) or len(value) != 3:
        raise MatlabResultError(
            f"MATLAB result field {name} must have length 3.",
            task_id=task_id,
            run_dir=run_dir,
        )
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(float(item))
        for item in value
    ):
        raise MatlabResultError(
            f"MATLAB result field {name} must be finite numeric.",
            task_id=task_id,
            run_dir=run_dir,
        )
