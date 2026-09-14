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
    if "user_count" in payload:
        user_count = payload["user_count"]
        if isinstance(user_count, bool) or not isinstance(user_count, int) or not 1 <= user_count <= 8:
            raise MatlabResultError(
                "MATLAB result field user_count must be an integer from 1 to 8.",
                task_id=expected_task_id,
                run_dir=run_dir,
            )
        for name in ("requested_focus_points_mm", "actual_peak_points_mm"):
            _validate_vector_collection(
                payload.get(name), name, user_count,
                task_id=expected_task_id, run_dir=run_dir,
            )
        for name in ("peak_power_by_user", "requested_power_by_user"):
            _validate_numeric_collection(
                payload.get(name), name, user_count,
                task_id=expected_task_id, run_dir=run_dir,
            )
        for name in ("frequency_hz", "wavelength_mm"):
            value = payload.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise MatlabResultError(
                    f"MATLAB result field {name} must be finite numeric.",
                    task_id=expected_task_id,
                    run_dir=run_dir,
                )
        for name in ("modulation_frequency_hz",):
            value = payload.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise MatlabResultError(
                    f"MATLAB result field {name} must be finite numeric.",
                    task_id=expected_task_id,
                    run_dir=run_dir,
                )
        for name in ("harmonic_orders", "harmonic_frequencies_hz"):
            value = payload.get(name)
            values = value if isinstance(value, list) else [value]
            if values == [None]:
                raise MatlabResultError(
                    f"MATLAB result field {name} must be a nonempty numeric list.",
                    task_id=expected_task_id,
                    run_dir=run_dir,
                )
            _validate_numeric_collection(
                value, name, len(values), task_id=expected_task_id, run_dir=run_dir
            )
        _validate_numeric_collection(
            payload.get("user_harmonic_orders"), "user_harmonic_orders", user_count,
            task_id=expected_task_id, run_dir=run_dir,
        )
        _validate_numeric_collection(
            payload.get("user_harmonic_indices"), "user_harmonic_indices", user_count,
            task_id=expected_task_id, run_dir=run_dir,
        )
        for name in (
            "focus_error_mm_by_user", "fwhm_x_mm_by_user", "dof_z_mm_by_user",
            "peak_to_sidelobe_ratio_db_by_user",
        ):
            _validate_numeric_collection(
                payload.get(name), name, user_count,
                task_id=expected_task_id, run_dir=run_dir,
            )
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, dict) or not all(
            isinstance(artifacts.get(name), str)
            for name in ("field_json", "field_mat")
        ):
            raise MatlabResultError(
                "MATLAB result artifact manifest is missing or invalid.",
                task_id=expected_task_id,
                run_dir=run_dir,
            )
        for filename in artifacts.values():
            if Path(filename).name != filename or not (run_dir / filename).is_file():
                raise MatlabResultError(
                    f"MATLAB result artifact is missing or unsafe: {filename!r}.",
                    task_id=expected_task_id,
                    run_dir=run_dir,
                )
        if not isinstance(payload.get("element_count"), int):
            raise MatlabResultError(
                "MATLAB result field element_count must be an integer.",
                task_id=expected_task_id,
                run_dir=run_dir,
            )
        if not isinstance(payload.get("polarization"), str) or not isinstance(
            payload.get("polarization_model"), str
        ):
            raise MatlabResultError(
                "MATLAB polarization metadata is missing or invalid.",
                task_id=expected_task_id,
                run_dir=run_dir,
            )
        if not isinstance(payload.get("method"), str) or not isinstance(
            payload.get("hardware_realization"), str
        ):
            raise MatlabResultError(
                "MATLAB method-boundary metadata is missing or invalid.",
                task_id=expected_task_id,
                run_dir=run_dir,
            )
        if not isinstance(payload.get("peak_to_sidelobe_definition"), str):
            raise MatlabResultError(
                "MATLAB sidelobe metric definition is missing or invalid.",
                task_id=expected_task_id,
                run_dir=run_dir,
            )
        for name in ("matlab_version", "matlab_release", "matlab_arch", "rng_algorithm"):
            if not isinstance(payload.get(name), str) or not payload[name]:
                raise MatlabResultError(
                    f"MATLAB runtime metadata field {name} is missing or invalid.",
                    task_id=expected_task_id,
                    run_dir=run_dir,
                )
        random_seed = payload.get("random_seed")
        if isinstance(random_seed, bool) or not isinstance(random_seed, int):
            raise MatlabResultError(
                "MATLAB runtime metadata field random_seed must be an integer.",
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


def _validate_vector_collection(
    value: Any,
    name: str,
    count: int,
    *,
    task_id: str,
    run_dir: Path,
) -> None:
    values = [value] if count == 1 and isinstance(value, list) and len(value) == 3 else value
    if not isinstance(values, list) or len(values) != count:
        raise MatlabResultError(
            f"MATLAB result field {name} must contain {count} vectors.",
            task_id=task_id,
            run_dir=run_dir,
        )
    for vector in values:
        _validate_vector(vector, name, task_id=task_id, run_dir=run_dir)


def _validate_numeric_collection(
    value: Any,
    name: str,
    count: int,
    *,
    task_id: str,
    run_dir: Path,
) -> None:
    values = value if isinstance(value, list) else [value]
    if len(values) != count or any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(float(item))
        for item in values
    ):
        raise MatlabResultError(
            f"MATLAB result field {name} must contain {count} finite numbers.",
            task_id=task_id,
            run_dir=run_dir,
        )
