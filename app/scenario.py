"""Validated UI scenario context rendered into a Hermes conversation turn."""

from __future__ import annotations

import math
from collections.abc import Sequence
from numbers import Real


POLARIZATION_LABELS = {
    "scalar": "标量（不区分极化）",
    "x_linear": "X 线极化（场景标签）",
    "y_linear": "Y 线极化（场景标签）",
    "rhcp": "右旋圆极化 RHCP（场景标签）",
    "lhcp": "左旋圆极化 LHCP（场景标签）",
}


def build_scenario_prompt(
    message: str,
    *,
    targets_mm: Sequence[Sequence[Real]],
    frequency_ghz: Real,
    modulation_frequency_mhz: Real = 200.0,
    polarization: str,
    element_count: int,
    tolerance_mm: Real,
    max_refinements: int,
) -> str:
    """Attach explicit UI choices without making the UI choose Agent tools."""

    if not isinstance(message, str) or not message.strip():
        raise ValueError("message cannot be empty")
    targets = [_vector(target, f"targets_mm[{index}]") for index, target in enumerate(targets_mm)]
    if not 1 <= len(targets) <= 8:
        raise ValueError("targets_mm must contain one to eight users")
    frequency = _finite_number(frequency_ghz, "frequency_ghz")
    modulation_frequency = _finite_number(
        modulation_frequency_mhz, "modulation_frequency_mhz"
    )
    tolerance = _finite_number(tolerance_mm, "tolerance_mm")
    if (
        not 1 <= frequency <= 100
        or not 1 <= modulation_frequency <= 1000
        or modulation_frequency >= frequency * 1000
        or tolerance <= 0
    ):
        raise ValueError("frequency or tolerance is out of range")
    if polarization not in POLARIZATION_LABELS:
        raise ValueError("unsupported polarization")
    side = math.isqrt(element_count) if isinstance(element_count, int) else 0
    if side * side != element_count:
        raise ValueError("element_count must be a perfect square")
    if isinstance(max_refinements, bool) or not isinstance(max_refinements, int) or max_refinements < 0:
        raise ValueError("max_refinements must be a nonnegative integer")

    target_lines = "\n".join(
        f"- 用户 {index + 1}: [{_numbers(target)}] mm"
        for index, target in enumerate(targets)
    )
    harmonic_orders = assign_user_harmonics(len(targets))
    harmonic_lines = "\n".join(
        f"- 用户 {index + 1} 谐波: q={order}, f={frequency + order * modulation_frequency / 1000:g} GHz"
        for index, order in enumerate(harmonic_orders)
    )
    additional = targets[1:]
    return (
        "用户当前消息：\n"
        f"{message.strip()}\n\n"
        "界面中已选择的实验场景如下。这些参数是本轮上下文，不代表用户仅因选择参数就要求运行仿真；"
        "请先理解当前消息。若消息要求近场聚焦实验，必须把这些值准确传给 create_focus_task；"
        "若只是询问或讨论，请直接回答，不要调用 MATLAB。阵列几何优化仍使用其冻结的 baseline 约束。\n"
        f"- 载波频率 frequency_ghz: {frequency:g}\n"
        f"- 调制频率 modulation_frequency_mhz: {modulation_frequency:g}\n"
        f"- 阵元数量 element_count: {element_count}（{side}×{side} 平面阵列）\n"
        f"- 极化 polarization: {polarization}（{POLARIZATION_LABELS[polarization]}）\n"
        "- 极化说明：当前 MATLAB 为标量点源模型，极化只作为场景元数据保存，不改变数值场结果；总结时必须如实说明。\n"
        f"- 用户数: {len(targets)}\n"
        "- 多用户映射：每个用户必须对应一个不同谐波阶次 q，频率为 fc+q·fm；不得把多个用户都放在 q=0。\n"
        f"{harmonic_lines}\n"
        f"{target_lines}\n"
        f"- create_focus_task.target_mm: [{_numbers(targets[0])}]\n"
        f"- create_focus_task.additional_targets_mm: {_matrix(additional)}\n"
        f"- tolerance_mm: {tolerance:g}\n"
        f"- max_refinements: {max_refinements}\n"
    )


def target_bounds_mm(frequency_ghz: Real) -> dict[str, tuple[float, float]]:
    """Return the current MATLAB selection region converted from wavelength units."""

    frequency = _finite_number(frequency_ghz, "frequency_ghz")
    wavelength_mm = 300.0 / frequency
    return {
        "x": (-7.0 * wavelength_mm, 7.0 * wavelength_mm),
        "y": (0.0, 0.0),
        "z": (5.0 * wavelength_mm, 14.0 * wavelength_mm),
    }


def assign_user_harmonics(user_count: int) -> list[int]:
    """Return the deterministic symmetric, distinct order plan used by MATLAB."""

    if isinstance(user_count, bool) or not isinstance(user_count, int) or not 1 <= user_count <= 8:
        raise ValueError("user_count must be an integer from one to eight")
    if user_count % 2:
        half_count = (user_count - 1) // 2
        return list(range(-half_count, half_count + 1))
    half_count = user_count // 2
    return [*range(-half_count, 0), *range(1, half_count + 1)]


def _vector(value: Sequence[Real], name: str) -> list[float]:
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
