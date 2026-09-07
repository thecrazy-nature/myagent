"""Low-dimensional deterministic geometry generation and physical checks."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .models import (
    BASELINE_APERTURE_X_MM,
    BASELINE_APERTURE_Y_MM,
    BASELINE_SPACING_MM,
    ELEMENTS_X,
    ELEMENTS_Y,
    ELEMENT_COUNT,
    MAX_SURFACE_DEPTH_MM,
    InvalidArrayDesignInput,
    baseline_constraints,
)


def generate_geometry(family: str, parameters: dict[str, Any] | None = None, seed: int = 0) -> dict[str, Any]:
    """Generate the same coordinates for the same family, parameters, and seed."""

    if not isinstance(seed, int) or isinstance(seed, bool):
        raise InvalidArrayDesignInput("seed must be an integer.")
    parameters = dict(parameters or {})
    if family == "baseline":
        if parameters:
            raise InvalidArrayDesignInput("baseline geometry takes no parameters.")
        positions = _planar_grid()
        normalized = {}
        resolved_family = "planar_baseline"
    elif family == "spherical_cap":
        unknown = set(parameters) - {"depth_mm"}
        if unknown:
            raise InvalidArrayDesignInput(f"Unsupported spherical_cap parameters: {sorted(unknown)}")
        depth = float(parameters.get("depth_mm", 0.0))
        if not math.isfinite(depth) or depth < 0 or depth > MAX_SURFACE_DEPTH_MM:
            raise InvalidArrayDesignInput(f"depth_mm must be in [0, {MAX_SURFACE_DEPTH_MM:g}].")
        positions = _spherical_cap(depth)
        normalized = {"depth_mm": depth}
        resolved_family = family
    else:
        raise InvalidArrayDesignInput(f"Unsupported geometry family: {family}.")

    canonical = json.dumps(
        {"family": resolved_family, "parameters": normalized, "seed": seed},
        sort_keys=True,
        separators=(",", ":"),
    )
    geometry = {
        "geometry_id": "geo_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
        "family": resolved_family,
        "parameters": normalized,
        "seed": seed,
        "element_positions_mm": positions,
        "element_normals": [[0.0, 0.0, 1.0] for _ in positions],
        "constraints": baseline_constraints(),
    }
    geometry["constraint_check"] = check_geometry_constraints(geometry)
    return geometry


def check_geometry_constraints(geometry: dict[str, Any]) -> dict[str, Any]:
    positions = geometry.get("element_positions_mm")
    if not isinstance(positions, list) or any(not isinstance(row, list) or len(row) != 3 for row in positions):
        raise InvalidArrayDesignInput("element_positions_mm must be an N-by-3 list.")
    if any(not math.isfinite(float(value)) for row in positions for value in row):
        raise InvalidArrayDesignInput("Element positions must be finite.")
    count = len(positions)
    xs = [float(row[0]) for row in positions]
    ys = [float(row[1]) for row in positions]
    zs = [float(row[2]) for row in positions]
    aperture_x = max(xs) - min(xs) if xs else math.inf
    aperture_y = max(ys) - min(ys) if ys else math.inf
    depth = max(zs) - min(zs) if zs else math.inf
    minimum = math.inf
    for index, first in enumerate(positions):
        for second in positions[index + 1 :]:
            distance = math.dist(first, second)
            if distance < minimum:
                minimum = distance
    checks = {
        "element_count": count == ELEMENT_COUNT,
        "aperture_x": aperture_x <= BASELINE_APERTURE_X_MM + 1e-8,
        "aperture_y": aperture_y <= BASELINE_APERTURE_Y_MM + 1e-8,
        "minimum_spacing": minimum >= BASELINE_SPACING_MM - 1e-8,
        "surface_depth": depth <= MAX_SURFACE_DEPTH_MM + 1e-8,
    }
    return {
        "valid": all(checks.values()),
        "checks": checks,
        "measured": {
            "element_count": count,
            "aperture_x_mm": aperture_x,
            "aperture_y_mm": aperture_y,
            "minimum_spacing_mm": minimum,
            "surface_depth_mm": depth,
        },
    }


def candidate_parameters(family: str, bounds: dict[str, Any], budget: int) -> list[dict[str, float]]:
    if family != "spherical_cap":
        raise InvalidArrayDesignInput(f"Unsupported searchable family: {family}.")
    if not isinstance(bounds, dict) or set(bounds) - {"depth_mm"}:
        raise InvalidArrayDesignInput("spherical_cap parameter_bounds may contain only depth_mm.")
    depth_bounds = bounds.get("depth_mm", [2.0, MAX_SURFACE_DEPTH_MM])
    if not isinstance(depth_bounds, list) or len(depth_bounds) != 2:
        raise InvalidArrayDesignInput("depth_mm bounds must be [minimum, maximum].")
    low, high = map(float, depth_bounds)
    if not (math.isfinite(low) and math.isfinite(high) and 0 <= low <= high <= MAX_SURFACE_DEPTH_MM):
        raise InvalidArrayDesignInput(f"depth_mm bounds must satisfy 0 <= low <= high <= {MAX_SURFACE_DEPTH_MM:g}.")
    if budget == 1:
        values = [(low + high) / 2.0]
    else:
        values = [low + index * (high - low) / (budget - 1) for index in range(budget)]
    return [{"depth_mm": value} for value in values]


def _planar_grid() -> list[list[float]]:
    xs = [(index - (ELEMENTS_X - 1) / 2.0) * BASELINE_SPACING_MM for index in range(ELEMENTS_X)]
    ys = [(index - (ELEMENTS_Y - 1) / 2.0) * BASELINE_SPACING_MM for index in range(ELEMENTS_Y)]
    # MATLAB meshgrid(...); (:), so x varies fastest.
    return [[x, y, 0.0] for y in ys for x in xs]


def _spherical_cap(depth_mm: float) -> list[list[float]]:
    planar = _planar_grid()
    if depth_mm == 0:
        return planar
    radius_at_corner = math.hypot(BASELINE_APERTURE_X_MM / 2.0, BASELINE_APERTURE_Y_MM / 2.0)
    sphere_radius = (radius_at_corner**2 + depth_mm**2) / (2.0 * depth_mm)
    positions: list[list[float]] = []
    for x, y, _ in planar:
        radial = math.hypot(x, y)
        sag_from_center = sphere_radius - math.sqrt(max(sphere_radius**2 - radial**2, 0.0))
        positions.append([x, y, depth_mm - sag_from_center])
    return positions
