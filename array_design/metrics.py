"""Deterministic baseline-normalized multi-objective scoring."""

from __future__ import annotations

import math
from typing import Any

from .models import InvalidArrayDesignInput

INVALID_SCORE = 1.0e9


def half_power_width(coordinates: list[float], normalized_power: list[float], peak_index: int) -> float:
    """Reference implementation of the exact 0.5-power interpolated width."""
    if len(coordinates) != len(normalized_power) or not 0 <= peak_index < len(coordinates):
        raise InvalidArrayDesignInput("Profile coordinates/power or peak_index are invalid.")
    left = peak_index
    while left > 0 and normalized_power[left - 1] >= 0.5:
        left -= 1
    right = peak_index
    while right + 1 < len(normalized_power) and normalized_power[right + 1] >= 0.5:
        right += 1
    if left == 0 or right + 1 == len(normalized_power):
        raise InvalidArrayDesignInput("Half-power region reaches the evaluation boundary.")
    left_cross = _crossing(coordinates[left - 1 : left + 1], normalized_power[left - 1 : left + 1])
    right_cross = _crossing(coordinates[right : right + 2], normalized_power[right : right + 2])
    return right_cross - left_cross


def xz_energy_concentration(
    power: list[list[float]], x: list[float], z: list[float], target_x: float,
    target_z: float, radius_x: float, half_depth_z: float,
) -> float:
    """Reference 2D trapezoid integral used to unit-test the MATLAB definition."""
    if len(power) != len(z) or any(len(row) != len(x) for row in power):
        raise InvalidArrayDesignInput("Power map dimensions do not match x/z coordinates.")
    x_indices = [index for index, value in enumerate(x) if abs(value - target_x) <= radius_x]
    z_indices = [index for index, value in enumerate(z) if abs(value - target_z) <= half_depth_z]
    if len(x_indices) < 2 or len(z_indices) < 2:
        raise InvalidArrayDesignInput("ROI must contain at least two samples per axis.")
    total = _trapz(z, [_trapz(x, row) for row in power])
    roi_x = [x[index] for index in x_indices]
    roi_z = [z[index] for index in z_indices]
    roi_rows = [[power[iz][ix] for ix in x_indices] for iz in z_indices]
    roi = _trapz(roi_z, [_trapz(roi_x, row) for row in roi_rows])
    return roi / total


def score_metrics(
    candidate: dict[str, Any],
    baseline: dict[str, Any],
    weights: dict[str, float],
    focus_tolerance_mm: float,
) -> dict[str, Any]:
    names = ("fwhm_x_mm", "dof_z_mm", "energy_concentration_ratio")
    values: dict[str, tuple[float, float]] = {}
    for name in names:
        base = _metric(baseline, name)
        current = _metric(candidate, name)
        if base <= 0 or current <= 0:
            raise InvalidArrayDesignInput(f"{name} must be finite and positive for scoring.")
        values[name] = (current, base)
    focus_error = _metric(candidate, "focus_error_mm", allow_zero=True)
    geometry_valid = bool(candidate.get("geometry_constraint_satisfied", False))
    hard_valid = geometry_valid and focus_error <= focus_tolerance_mm
    spot_ratio = values["fwhm_x_mm"][0] / values["fwhm_x_mm"][1]
    dof_ratio = values["dof_z_mm"][0] / values["dof_z_mm"][1]
    energy_ratio = values["energy_concentration_ratio"][0] / values["energy_concentration_ratio"][1]
    raw_score = (
        weights["lateral_spot"] * spot_ratio
        + weights["depth_of_focus"] * dof_ratio
        - weights["energy_concentration"] * energy_ratio
    )
    return {
        "objective_score": raw_score if hard_valid else INVALID_SCORE,
        "unpenalized_score": raw_score,
        "hard_constraint_satisfied": hard_valid,
        "ratios_to_baseline": {
            "lateral_spot": spot_ratio,
            "depth_of_focus": dof_ratio,
            "energy_concentration": energy_ratio,
        },
        "improvement_percentages": compare_to_baseline(candidate, baseline),
    }


def compare_to_baseline(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float]:
    def lower(name: str) -> float:
        return 100.0 * (_metric(baseline, name) - _metric(candidate, name)) / _metric(baseline, name)

    def higher(name: str) -> float:
        return 100.0 * (_metric(candidate, name) - _metric(baseline, name)) / _metric(baseline, name)

    return {
        "focus_error_improvement_pct": lower("focus_error_mm") if _metric(baseline, "focus_error_mm", True) > 0 else 0.0,
        "lateral_fwhm_improvement_pct": lower("fwhm_x_mm"),
        "dof_improvement_pct": lower("dof_z_mm"),
        "energy_concentration_improvement_pct": higher("energy_concentration_ratio"),
        "peak_power_change_pct": higher("peak_power"),
    }


def _metric(payload: dict[str, Any], name: str, allow_zero: bool = False) -> float:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidArrayDesignInput(f"Missing numeric metric: {name}.")
    number = float(value)
    if not math.isfinite(number) or (number < 0 if allow_zero else number <= 0):
        raise InvalidArrayDesignInput(f"Invalid metric: {name}.")
    return number


def _crossing(x: list[float], y: list[float]) -> float:
    if abs(y[1] - y[0]) <= float.fromhex("0x1.0p-52"):
        return (x[0] + x[1]) / 2.0
    return x[0] + (0.5 - y[0]) * (x[1] - x[0]) / (y[1] - y[0])


def _trapz(x: list[float], y: list[float]) -> float:
    return sum((x[index + 1] - x[index]) * (y[index] + y[index + 1]) / 2.0 for index in range(len(x) - 1))
