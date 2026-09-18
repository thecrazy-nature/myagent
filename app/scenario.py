"""Validated UI scenario context rendered into one Hermes conversation turn."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any


POLARIZATION_LABELS = {
    "scalar": "标量（不区分极化）",
    "x_linear": "X 线极化（场景标签）",
    "y_linear": "Y 线极化（场景标签）",
    "rhcp": "右旋圆极化 RHCP（场景标签）",
    "lhcp": "左旋圆极化 LHCP（场景标签）",
}
FREQUENCY_UNIT_HZ = {"GHz": 1e9, "MHz": 1e6, "kHz": 1e3}


def convert_frequency(value: Real, source_unit: str, target_unit: str) -> float:
    frequency = _finite_number(value, "frequency")
    if frequency <= 0:
        raise ValueError("frequency must be positive")
    if source_unit not in FREQUENCY_UNIT_HZ or target_unit not in FREQUENCY_UNIT_HZ:
        raise ValueError("unsupported frequency unit")
    return frequency * FREQUENCY_UNIT_HZ[source_unit] / FREQUENCY_UNIT_HZ[target_unit]


def build_scenario_prompt(
    message: str,
    *,
    targets_mm: Sequence[Sequence[Real]],
    frequency_ghz: Real,
    modulation_frequency_mhz: Real = 200.0,
    polarization: str = "scalar",
    element_count: int = 256,
    tolerance_mm: Real,
    max_refinements: int,
    workflow_preset: str = "array",
    metasurface_config: Mapping[str, Any] | None = None,
) -> str:
    """Attach exact UI choices while leaving workflow decisions to Hermes."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message cannot be empty")
    if workflow_preset == "metasurface":
        return _build_metasurface_prompt(message, frequency_ghz, tolerance_mm, metasurface_config)
    if workflow_preset != "array":
        raise ValueError("workflow_preset must be array or metasurface")

    targets = [_vector(target, f"targets_mm[{index}]") for index, target in enumerate(targets_mm)]
    if not 1 <= len(targets) <= 8:
        raise ValueError("targets_mm must contain one to eight users")
    frequency = _finite_number(frequency_ghz, "frequency_ghz")
    modulation = _finite_number(modulation_frequency_mhz, "modulation_frequency_mhz")
    tolerance = _finite_number(tolerance_mm, "tolerance_mm")
    if not 1 <= frequency <= 100 or not 1 <= modulation <= 1000:
        raise ValueError("frequency is out of range")
    if modulation >= frequency * 1000 or tolerance <= 0:
        raise ValueError("frequency or tolerance is out of range")
    target_errors = target_validation_errors(targets, frequency)
    if target_errors:
        raise ValueError("；".join(target_errors))
    if polarization not in POLARIZATION_LABELS:
        raise ValueError("unsupported polarization")
    side = math.isqrt(element_count) if isinstance(element_count, int) else 0
    if side * side != element_count:
        raise ValueError("element_count must be a perfect square")
    if isinstance(max_refinements, bool) or not isinstance(max_refinements, int) or max_refinements < 0:
        raise ValueError("max_refinements must be a nonnegative integer")

    harmonic_orders = assign_user_harmonics(len(targets))
    harmonic_lines = "\n".join(
        f"- 用户 {index + 1} 谐波: q={order}, f={frequency + order * modulation / 1000:g} GHz"
        for index, order in enumerate(harmonic_orders)
    )
    target_lines = "\n".join(
        f"- 用户 {index + 1}: [{_numbers(target)}] mm"
        for index, target in enumerate(targets)
    )
    return (
        f"用户当前消息：\n{message.strip()}\n\n"
        "界面快捷配置模式：阵列聚焦。以下数值是本轮上下文，不代表只要填写参数就必须运行仿真；"
        "先理解用户消息。若用户要求数值实验，使用 focus workflow；若要求改变阵元位置，使用独立 array-geometry workflow。"
        "若只是讨论，直接回答，不调用 MATLAB。\n"
        f"- frequency_ghz: {frequency:g}\n"
        f"- modulation_frequency_mhz: {modulation:g}\n"
        f"- element_count: {element_count}（{side}×{side} 平面阵列）\n"
        f"- polarization: {polarization}（{POLARIZATION_LABELS[polarization]}）\n"
        "- 极化说明：当前 MATLAB 为标量点源模型，极化只作为场景元数据保存，不改变数值场结果；总结时必须如实说明。\n"
        "- 多用户映射：每位用户对应不同谐波阶次 q，频率为 fc+q·fm。\n"
        f"{harmonic_lines}\n{target_lines}\n"
        f"- create_focus_task.target_mm: [{_numbers(targets[0])}]\n"
        f"- create_focus_task.additional_targets_mm: {_matrix(targets[1:])}\n"
        f"- tolerance_mm: {tolerance:g}\n"
        f"- max_refinements: {max_refinements}\n"
        "- 工作流语义：‘运行一次聚焦任务’表示完成一个任务，不是只允许一次 MATLAB 调用。"
        "若评估失败且 remaining_refinements > 0，继续 refine → run → evaluate，除非用户明确禁止修正。\n"
    )


def _build_metasurface_prompt(
    message: str,
    frequency_ghz: Real,
    tolerance_mm: Real,
    config: Mapping[str, Any] | None,
) -> str:
    if not isinstance(config, Mapping):
        raise ValueError("metasurface_config is required")
    frequency = _finite_number(frequency_ghz, "frequency_ghz")
    tolerance = _finite_number(tolerance_mm, "tolerance_mm")
    target = _vector(config.get("focus_target_mm"), "focus_target_mm")
    unit = _vector(config.get("unit_size_mm"), "unit_size_mm")
    array_size = config.get("array_size")
    if not isinstance(array_size, Sequence) or len(array_size) != 2:
        raise ValueError("array_size must contain two values")
    nx, ny = int(array_size[0]), int(array_size[1])
    if not 4 <= nx <= 40 or not 4 <= ny <= 40 or nx * ny > 1600:
        raise ValueError("array_size is out of range")
    incident = str(config.get("incident_wave"))
    if incident not in {"plane_wave", "horn_spherical_wave"}:
        raise ValueError("unsupported incident wave")
    feed = _vector(config.get("horn_feed_position_mm", [0, 0, -100]), "horn_feed_position_mm")
    states = config.get("binary_states")
    if not isinstance(states, Mapping):
        raise ValueError("binary_states is required")
    candidate_budget = int(config.get("candidate_budget", 2))
    objective = config.get("objective_weights") or {
        "focus_accuracy": 0.40,
        "energy_concentration": 0.35,
        "sidelobe_suppression": 0.25,
    }
    return (
        f"用户当前消息：\n{message.strip()}\n\n"
        "界面快捷配置模式：0/1 透射可编程超表面（与阵列任务互斥）。若用户要求执行该实验，必须严格使用独立 metasurface workflow，"
        "不得调用 array-geometry 或普通 focus 工具，也不得把超表面单元称为有源阵元。\n"
        "执行顺序：create_metasurface_design_task → evaluate_metasurface_baseline（未编程、连续相位参考、几何光学 0/1 基线）"
        "→ optimize_metasurface_candidate → evaluate_metasurface_design → save_metasurface_design → build_metasurface_cst_model。\n"
        "当前 evaluation_policy=record_only，整体通过标准尚未由研究者决定；只能报告指标并把保存结果标为研究草案，不能宣称自动验收通过。"
        "CST 工具只生成控制码布局骨架；缺少单元结构、材料、端口和 S 参数时不得称为全波模型。\n"
        f"- focus_target_mm: [{_numbers(target)}]\n"
        f"- focus_tolerance_mm: {tolerance:g}\n"
        f"- frequency_ghz: {frequency:g}\n"
        f"- unit_size_mm: [{_numbers(unit)}]\n"
        f"- array_size: [{nx}, {ny}]\n"
        f"- incident_wave: {incident}\n"
        f"- horn_feed_position_mm: [{_numbers(feed)}]（plane_wave 时忽略）\n"
        f"- binary_states: {states!r}\n"
        f"- candidate_budget: {candidate_budget}\n"
        f"- objective_weights: {objective!r}\n"
        "- evaluation_policy: record_only\n"
        "- optimizer: binary_coordinate_descent_v1；max_iterations 与 guard_weight 由 Hermes 基于 MATLAB 证据选择。\n"
        "- build_metasurface_cst_model.launch_cst: true。若 CST OLE 启动失败，VBA 布局仍视为有效产物；如实报告警告并结束任务，不得重复调用或把整个超表面任务判为失败。\n"
    )


def target_bounds_mm(frequency_ghz: Real) -> dict[str, tuple[float, float]]:
    frequency = _finite_number(frequency_ghz, "frequency_ghz")
    wavelength_mm = 300.0 / frequency
    return {"x": (-7.0 * wavelength_mm, 7.0 * wavelength_mm), "y": (0.0, 0.0), "z": (5.0 * wavelength_mm, 14.0 * wavelength_mm)}


def target_validation_errors(targets_mm: Sequence[Sequence[Real]], frequency_ghz: Real) -> list[str]:
    frequency = _finite_number(frequency_ghz, "frequency_ghz")
    bounds = target_bounds_mm(frequency)
    targets = [_vector(target, f"targets_mm[{index}]") for index, target in enumerate(targets_mm)]
    errors: list[str] = []
    for index, (x, y, z) in enumerate(targets, start=1):
        if not bounds["x"][0] <= x <= bounds["x"][1]:
            errors.append(f"用户 {index} 的 X={x:g} mm 超出当前 {frequency:g} GHz 计算范围 [{bounds['x'][0]:g}, {bounds['x'][1]:g}] mm")
        if y != 0:
            errors.append(f"用户 {index} 的 Y={y:g} mm 不受当前求解器支持；Y 必须为 0 mm")
        if not bounds["z"][0] <= z <= bounds["z"][1]:
            errors.append(f"用户 {index} 的 Z={z:g} mm 超出当前 {frequency:g} GHz 计算范围 [{bounds['z'][0]:g}, {bounds['z'][1]:g}] mm")
    return errors


def assign_user_harmonics(user_count: int) -> list[int]:
    if isinstance(user_count, bool) or not isinstance(user_count, int) or not 1 <= user_count <= 8:
        raise ValueError("user_count must be an integer from one to eight")
    if user_count % 2:
        half_count = (user_count - 1) // 2
        return list(range(-half_count, half_count + 1))
    half_count = user_count // 2
    return [*range(-half_count, 0), *range(1, half_count + 1)]


def _vector(value: Any, name: str) -> list[float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 3:
        raise ValueError(f"{name} must contain three values")
    return [_finite_number(item, name) for item in value]


def _finite_number(value: Real, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _numbers(vector: Sequence[float]) -> str:
    return ", ".join(f"{value:g}" for value in vector)


def _matrix(vectors: Sequence[Sequence[float]]) -> str:
    return "[" + ", ".join(f"[{_numbers(vector)}]" for vector in vectors) + "]"
