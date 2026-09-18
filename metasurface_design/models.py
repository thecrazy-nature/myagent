"""Validation and transparent assumptions for a binary transmissive metasurface."""

from __future__ import annotations

import math
from collections.abc import Sequence
from numbers import Real
from typing import Any


MODEL_LIGHT_SPEED_MM_GHZ = 300.0
SUPPORTED_INCIDENT_WAVES = ("plane_wave", "horn_spherical_wave")
SUPPORTED_OPTIMIZERS = ("binary_coordinate_descent_v1",)
SUPPORTED_EVALUATION_POLICIES = ("record_only",)
DEFAULT_OBJECTIVE_WEIGHTS = {
    "focus_accuracy": 0.40,
    "energy_concentration": 0.35,
    "sidelobe_suppression": 0.25,
}
DEFAULT_BINARY_STATES = {
    "state_0": {"amplitude": 1.0, "phase_deg": 0.0},
    "state_1": {"amplitude": 1.0, "phase_deg": 180.0},
}


class MetasurfaceDesignError(RuntimeError):
    """Base error returned through a metasurface Tool observation."""


class InvalidMetasurfaceInput(MetasurfaceDesignError):
    """The requested programmable-surface configuration is invalid."""


class MetasurfaceStateError(MetasurfaceDesignError):
    """Persisted metasurface state is missing or in an invalid phase."""


class MetasurfaceMatlabError(MetasurfaceDesignError):
    """The real MATLAB metasurface evaluator failed."""


def validate_target(value: Any, frequency_ghz: Any) -> tuple[list[float], float]:
    frequency = positive_number(frequency_ghz, "frequency_ghz")
    if not 1 <= frequency <= 100:
        raise InvalidMetasurfaceInput("frequency_ghz must be in [1, 100].")
    target = vector(value, "focus_target_mm", length=3)
    if target[2] <= 0:
        raise InvalidMetasurfaceInput("focus_target_mm.z must be above the z=0 surface.")
    if any(abs(item) > 10000 for item in target):
        raise InvalidMetasurfaceInput("focus_target_mm values must stay within ±10000 mm.")
    return target, frequency


def validate_array_size(value: Any) -> list[int]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) != 2
    ):
        raise InvalidMetasurfaceInput("array_size must contain [elements_x, elements_y].")
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 4 <= item <= 40:
            raise InvalidMetasurfaceInput("Each array_size value must be an integer in [4, 40].")
        result.append(item)
    if result[0] * result[1] > 1600:
        raise InvalidMetasurfaceInput("array_size may contain at most 1600 cells.")
    return result


def validate_unit_size(value: Any, frequency_ghz: float) -> list[float]:
    result = vector(value, "unit_size_mm", length=3)
    if any(item <= 0 for item in result):
        raise InvalidMetasurfaceInput("unit_size_mm values must be positive.")
    wavelength_mm = MODEL_LIGHT_SPEED_MM_GHZ / frequency_ghz
    if result[0] > 2 * wavelength_mm or result[1] > 2 * wavelength_mm:
        raise InvalidMetasurfaceInput(
            "The in-plane unit dimensions must not exceed 2 wavelengths in this model."
        )
    return result


def validate_incident_wave(value: Any) -> str:
    if value not in SUPPORTED_INCIDENT_WAVES:
        raise InvalidMetasurfaceInput(
            f"incident_wave must be one of {list(SUPPORTED_INCIDENT_WAVES)}."
        )
    return str(value)


def validate_feed_position(value: Any, incident_wave: str) -> list[float] | None:
    if incident_wave == "plane_wave":
        return None
    position = vector(value, "horn_feed_position_mm", length=3)
    if position[2] >= 0:
        raise InvalidMetasurfaceInput(
            "horn_feed_position_mm.z must be below the z=0 surface."
        )
    return position


def normalize_binary_states(value: Any) -> dict[str, dict[str, float]]:
    source = DEFAULT_BINARY_STATES if value is None else value
    if not isinstance(source, dict) or set(source) != {"state_0", "state_1"}:
        raise InvalidMetasurfaceInput("binary_states must contain state_0 and state_1.")
    result: dict[str, dict[str, float]] = {}
    for name in ("state_0", "state_1"):
        state = source[name]
        if not isinstance(state, dict) or set(state) != {"amplitude", "phase_deg"}:
            raise InvalidMetasurfaceInput(
                f"binary_states.{name} must contain amplitude and phase_deg."
            )
        amplitude = finite_number(state["amplitude"], f"binary_states.{name}.amplitude")
        phase = finite_number(state["phase_deg"], f"binary_states.{name}.phase_deg")
        if not 0 <= amplitude <= 1:
            raise InvalidMetasurfaceInput(
                f"binary_states.{name}.amplitude must be in [0, 1]."
            )
        result[name] = {"amplitude": amplitude, "phase_deg": phase % 360.0}
    if result["state_0"]["amplitude"] == 0 and result["state_1"]["amplitude"] == 0:
        raise InvalidMetasurfaceInput("At least one binary state must transmit nonzero amplitude.")
    return result


def binary_state_characteristics(
    states: dict[str, dict[str, float]],
) -> dict[str, float | bool | None]:
    amplitude_0 = states["state_0"]["amplitude"]
    amplitude_1 = states["state_1"]["amplitude"]
    relative_phase = (
        states["state_1"]["phase_deg"] - states["state_0"]["phase_deg"]
    ) % 360.0
    if relative_phase > 180:
        relative_phase -= 360
    amplitude_ratio_db = (
        20 * math.log10(amplitude_1 / amplitude_0)
        if amplitude_0 > 0 and amplitude_1 > 0
        else None
    )
    return {
        "state_0_power_transmission": amplitude_0**2,
        "state_1_power_transmission": amplitude_1**2,
        "relative_amplitude_db_state_1_over_0": amplitude_ratio_db,
        "relative_phase_deg_state_1_minus_0": relative_phase,
        "ideal_lossless_binary_assumption": (
            abs(amplitude_0 - 1) <= 1e-12 and abs(amplitude_1 - 1) <= 1e-12
        ),
    }


def normalize_objective_weights(value: Any) -> dict[str, float]:
    if value is None:
        return dict(DEFAULT_OBJECTIVE_WEIGHTS)
    if not isinstance(value, dict) or set(value) != set(DEFAULT_OBJECTIVE_WEIGHTS):
        raise InvalidMetasurfaceInput(
            "objective_weights must contain focus_accuracy, energy_concentration, "
            "and sidelobe_suppression."
        )
    converted = {
        name: positive_number(weight, f"objective_weights.{name}")
        for name, weight in value.items()
    }
    total = sum(converted.values())
    return {name: weight / total for name, weight in converted.items()}


def validate_optimizer(value: Any) -> str:
    if value not in SUPPORTED_OPTIMIZERS:
        raise InvalidMetasurfaceInput(
            f"optimizer must be one of {list(SUPPORTED_OPTIMIZERS)}."
        )
    return str(value)


def validate_evaluation_policy(value: Any) -> str:
    if value not in SUPPORTED_EVALUATION_POLICIES:
        raise InvalidMetasurfaceInput(
            f"evaluation_policy must be one of {list(SUPPORTED_EVALUATION_POLICIES)}."
        )
    return str(value)


def positive_number(value: Any, name: str) -> float:
    number = finite_number(value, name)
    if number <= 0:
        raise InvalidMetasurfaceInput(f"{name} must be positive.")
    return number


def finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise InvalidMetasurfaceInput(f"{name} must be a finite real number.")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidMetasurfaceInput(f"{name} must be a finite real number.")
    return number


def vector(value: Any, name: str, *, length: int) -> list[float]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) != length
    ):
        raise InvalidMetasurfaceInput(f"{name} must contain {length} real values.")
    return [finite_number(item, name) for item in value]


def constraints(
    frequency_ghz: float,
    array_size: list[int],
    unit_size_mm: list[float],
    incident_wave: str,
    states: dict[str, dict[str, float]],
) -> dict[str, Any]:
    return {
        "surface": "fixed rectangular planar transmissive aperture at z=0",
        "array_size": array_size,
        "element_count": array_size[0] * array_size[1],
        "unit_size_mm": unit_size_mm,
        "frequency_hz": frequency_ghz * 1e9,
        "incident_wave": incident_wave,
        "element_model": "scalar point transmission coefficient",
        "binary_states": states,
        "control": "one binary state per cell",
        "baseline": "geometrical-optics path compensation quantized to states 0/1",
        "evaluation_plane": "XZ plane through the requested target y coordinate",
        "energy_accounting": "transmitted/input proxy from supplied state amplitudes",
    }
