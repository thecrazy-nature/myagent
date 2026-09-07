"""Frozen baseline and validation helpers for array-geometry design."""

from __future__ import annotations

import math
from collections.abc import Sequence
from numbers import Real
from typing import Any

LIGHT_SPEED_M_S = 3.0e8
FREQUENCY_HZ = 28.0e9
WAVELENGTH_MM = LIGHT_SPEED_M_S / FREQUENCY_HZ * 1000.0
ELEMENTS_X = 16
ELEMENTS_Y = 16
ELEMENT_COUNT = ELEMENTS_X * ELEMENTS_Y
BASELINE_SPACING_MM = 0.7 * WAVELENGTH_MM
BASELINE_APERTURE_X_MM = (ELEMENTS_X - 1) * BASELINE_SPACING_MM
BASELINE_APERTURE_Y_MM = (ELEMENTS_Y - 1) * BASELINE_SPACING_MM
MAX_SURFACE_DEPTH_MM = 20.0
SUPPORTED_FAMILIES = ("spherical_cap",)
DEFAULT_WEIGHTS = {
    "lateral_spot": 0.45,
    "depth_of_focus": 0.35,
    "energy_concentration": 0.20,
}


class ArrayDesignError(RuntimeError):
    """Base error returned through an array-design Tool observation."""


class InvalidArrayDesignInput(ArrayDesignError):
    """The requested task, family, or geometry is invalid."""


class ArrayDesignStateError(ArrayDesignError):
    """Persisted design state is missing or in an invalid phase."""


class ArrayDesignMatlabError(ArrayDesignError):
    """The dedicated MATLAB batch evaluator failed."""


def validate_vector3(value: Any, name: str) -> list[float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 3:
        raise InvalidArrayDesignInput(f"{name} must be a length-3 numeric sequence.")
    result: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, Real):
            raise InvalidArrayDesignInput(f"{name} values must be real numbers.")
        number = float(item)
        if not math.isfinite(number):
            raise InvalidArrayDesignInput(f"{name} values must be finite.")
        result.append(number)
    if abs(result[1]) > 1e-9:
        raise InvalidArrayDesignInput("The current XZ-plane model requires target y = 0 mm.")
    return result


def validate_positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise InvalidArrayDesignInput(f"{name} must be a positive finite number.")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise InvalidArrayDesignInput(f"{name} must be a positive finite number.")
    return number


def normalize_weights(value: Any) -> dict[str, float]:
    if value is None:
        return dict(DEFAULT_WEIGHTS)
    if not isinstance(value, dict):
        raise InvalidArrayDesignInput("objective_weights must be an object.")
    if set(value) != set(DEFAULT_WEIGHTS):
        raise InvalidArrayDesignInput(
            "objective_weights must contain lateral_spot, depth_of_focus, and energy_concentration."
        )
    converted = {name: validate_positive(weight, f"objective_weights.{name}") for name, weight in value.items()}
    total = sum(converted.values())
    return {name: weight / total for name, weight in converted.items()}


def baseline_constraints() -> dict[str, Any]:
    return {
        "element_count": ELEMENT_COUNT,
        "max_aperture_x_mm": BASELINE_APERTURE_X_MM,
        "max_aperture_y_mm": BASELINE_APERTURE_Y_MM,
        "min_element_spacing_mm": BASELINE_SPACING_MM,
        "max_surface_depth_mm": MAX_SURFACE_DEPTH_MM,
        "frequency_hz": FREQUENCY_HZ,
        "element_model": "scalar_point_source",
        "element_pattern": "isotropic",
        "polarization": "not represented by current scalar model",
        "input_power_normalization": "sum(abs(carrier_weights).^2) equals real baseline",
        "evaluation_plane": "XZ (y=0)",
    }
