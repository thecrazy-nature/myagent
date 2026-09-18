"""Read-only field artifacts, plots, exports, and transparent method cards."""

from __future__ import annotations

import io
import json
import math
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


def artifact_paths(project_root: Path, simulation: dict[str, Any]) -> dict[str, Path]:
    run_id = simulation.get("simulation_run_id")
    artifacts = simulation.get("artifacts")
    if not isinstance(run_id, str) or not isinstance(artifacts, dict):
        return {}
    run_dir = (project_root / "runs" / run_id).resolve()
    runs_root = (project_root / "runs").resolve()
    if run_dir.parent != runs_root:
        return {}
    resolved: dict[str, Path] = {}
    for key, filename in artifacts.items():
        if not isinstance(filename, str) or Path(filename).name != filename:
            continue
        path = (run_dir / filename).resolve()
        if path.parent == run_dir and path.is_file():
            resolved[str(key)] = path
    return resolved


def load_field_data(project_root: Path, simulation: dict[str, Any]) -> dict[str, Any] | None:
    path = artifact_paths(project_root, simulation).get("field_json")
    if path is None:
        return None
    try:
        stat = path.stat()
        return _load_field_json_cached(
            str(path), stat.st_mtime_ns, stat.st_size
        )
    except OSError:
        return None


@lru_cache(maxsize=32)
def _load_field_json_cached(
    path_text: str, modified_ns: int, size_bytes: int
) -> dict[str, Any] | None:
    """Cache immutable-by-contract field JSON using its file identity."""

    del modified_ns, size_bytes
    try:
        value = json.loads(Path(path_text).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if _valid_field_data(value) else None


def build_method_card(state: dict[str, Any], simulation: dict[str, Any]) -> list[dict[str, str]]:
    user_orders = simulation.get("user_harmonic_orders") or []
    matlab_calls = sum(
        isinstance(event, dict) and event.get("event") == "simulation"
        for event in state.get("history", [])
    )
    remaining_refinements = max(
        0,
        int(state.get("max_refinements", 0)) - int(state.get("refinement_count", 0)),
    )
    field_scope = (
        "在每个用户对应的独立谐波通道上进行 axial-null 近场复权重综合，"
        "并计算真实 XOZ/YOZ 主切面及实际焦点深度处的 XOY 切面。"
        if simulation.get("orthogonal_plane_resolution")
        else "在每个用户对应的独立谐波通道上进行 axial-null 近场复权重综合，并计算真实 XOZ 场矩阵。"
    )
    return [
        {
            "部分": "Hermes 智能体",
            "本次职责": "理解用户意图、选择 Tool、依据评估决定是否修正，以及决定何时停止；不计算电磁场。",
        },
        {
            "部分": "工作流固定补偿",
            "本次职责": "若定位失败，以 α=0.7 更新命令目标；这是反馈策略，不是新的电磁优化算法。",
        },
        {
            "部分": "MATLAB 数值核心",
            "本次职责": field_scope,
        },
        {
            "部分": "多用户频率计划",
            "本次职责": (
                f"用户依次绑定不同谐波 q={user_orders}；载波为 "
                f"{float(state.get('frequency_hz', 0))/1e9:g} GHz，调制频率为 "
                f"{float(state.get('modulation_frequency_hz', 0))/1e6:g} MHz。"
            ),
        },
        {
            "部分": "方法边界",
            "本次职责": "当前是理想的逐谐波独立复激励，不等同于已投影到同一组矩形脉冲时序的硬件可实现 TMA 控制。极化仅为元数据。",
        },
        {
            "部分": "结果含义",
            "本次职责": "“成功”仅表示所有用户满足所设定位容差，不代表已证明全局最优；未尝试候选由 Tool 轨迹和预算显示。",
        },
        {
            "部分": "搜索覆盖",
            "本次职责": (
                f"已执行 {matlab_calls} 次真实 MATLAB 实验；仍有 {remaining_refinements} 次"
                "工作流修正预算。这里不存在对连续参数空间穷举后的全局最优证明。"
            ),
        },
    ]


def field_frame(field_data: dict[str, Any], user_index: int, local_radius_mm: float | None = None) -> pd.DataFrame:
    x, z, power = _field_arrays(field_data, user_index)
    xx, zz = np.meshgrid(x, z)
    if local_radius_mm is not None:
        target = np.asarray(field_data["requested_focus_points_mm"][user_index], dtype=float)
        mask = (np.abs(xx - target[0]) <= local_radius_mm) & (
            np.abs(zz - target[2]) <= local_radius_mm
        )
    else:
        mask = np.ones_like(power, dtype=bool)
    db = 10.0 * np.log10(np.maximum(power, np.finfo(float).tiny))
    return pd.DataFrame({
        "X／mm": xx[mask], "Z／mm": zz[mask],
        "归一化功率": power[mask], "相对功率／dB": np.maximum(db[mask], -60.0),
    })


def orthogonal_field_frame(
    field_data: dict[str, Any], user_index: int, plane: str
) -> pd.DataFrame:
    """Return a true MATLAB YZ or XY focal cut as a plotting table."""

    horizontal, vertical, power = _orthogonal_field_arrays(
        field_data, user_index, plane
    )
    horizontal_grid, vertical_grid = np.meshgrid(horizontal, vertical)
    db = 10.0 * np.log10(np.maximum(power, np.finfo(float).tiny))
    if plane == "yz":
        columns = {"Y／mm": horizontal_grid.ravel(), "Z／mm": vertical_grid.ravel()}
    elif plane == "xy":
        columns = {"X／mm": horizontal_grid.ravel(), "Y／mm": vertical_grid.ravel()}
    else:
        raise ValueError("plane must be 'yz' or 'xy'")
    columns["归一化功率"] = power.ravel()
    columns["相对功率／dB"] = np.maximum(db.ravel(), -60.0)
    return pd.DataFrame(columns)


def profile_frames(field_data: dict[str, Any], user_index: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    x, z, power = _field_arrays(field_data, user_index)
    actual = np.asarray(field_data["actual_peak_points_mm"][user_index], dtype=float)
    ix = int(np.argmin(np.abs(x - actual[0])))
    iz = int(np.argmin(np.abs(z - actual[2])))
    lateral = pd.DataFrame({"X／mm": x, "归一化功率": power[iz, :], "半功率": 0.5})
    axial = pd.DataFrame({"Z／mm": z, "归一化功率": power[:, ix], "半功率": 0.5})
    return lateral, axial


def plane_png(
    field_data: dict[str, Any],
    user_index: int,
    plane: str,
    *,
    local_radius_mm: float | None = None,
    dynamic_range_db: float = 12.0,
) -> bytes:
    """Render one MATLAB field plane without sending a long table to the browser."""

    if not 3 <= float(dynamic_range_db) <= 80:
        raise ValueError("dynamic_range_db must be between 3 and 80")
    if local_radius_mm is not None and float(local_radius_mm) <= 0:
        raise ValueError("local_radius_mm must be positive")

    target = np.asarray(
        field_data["requested_focus_points_mm"][user_index], dtype=float
    )
    actual = np.asarray(
        field_data["actual_peak_points_mm"][user_index], dtype=float
    )
    if plane == "xz":
        horizontal, vertical, power = _field_arrays(field_data, user_index)
        horizontal_label, vertical_label = "X / mm", "Z / mm"
        center = actual[[0, 2]]
        markers = [(target[[0, 2]], "Target", "x", "white")]
        markers.append((actual[[0, 2]], "Measured peak", "+", "black"))
    elif plane == "yz":
        horizontal, vertical, power = _orthogonal_field_arrays(
            field_data, user_index, plane
        )
        horizontal_label, vertical_label = "Y / mm", "Z / mm"
        center = actual[[1, 2]]
        fixed_x = float(field_data["yz_plane_x_mm_by_user"][user_index])
        markers = []
        if math.isclose(float(target[0]), fixed_x, abs_tol=1e-9):
            markers.append((target[[1, 2]], "Target", "x", "white"))
        if math.isclose(float(actual[0]), fixed_x, abs_tol=1e-9):
            markers.append((actual[[1, 2]], "Measured peak", "+", "black"))
    elif plane == "xy":
        horizontal, vertical, power = _orthogonal_field_arrays(
            field_data, user_index, plane
        )
        horizontal_label, vertical_label = "X / mm", "Y / mm"
        center = actual[[0, 1]]
        fixed_z = float(field_data["xy_plane_z_mm_by_user"][user_index])
        markers = [(actual[[0, 1]], "Measured peak", "+", "black")]
        if math.isclose(float(target[2]), fixed_z, abs_tol=1e-9):
            markers.insert(0, (target[[0, 1]], "Target", "x", "white"))
    else:
        raise ValueError("plane must be 'xz', 'yz', or 'xy'")

    if local_radius_mm is not None:
        radius = float(local_radius_mm)
        horizontal_mask = _local_axis_mask(horizontal, center[0], radius)
        vertical_mask = _local_axis_mask(vertical, center[1], radius)
        if np.any(horizontal_mask) and np.any(vertical_mask):
            horizontal = horizontal[horizontal_mask]
            vertical = vertical[vertical_mask]
            power = power[np.ix_(vertical_mask, horizontal_mask)]

    raw_db = 10.0 * np.log10(np.maximum(power, np.finfo(float).tiny))
    db = np.maximum(raw_db, -float(dynamic_range_db))
    figure = Figure(figsize=(7.2, 5.2), dpi=130, constrained_layout=True)
    axis = figure.subplots()
    image = axis.imshow(
        db,
        origin="lower",
        aspect="equal",
        extent=[
            float(horizontal[0]), float(horizontal[-1]),
            float(vertical[0]), float(vertical[-1]),
        ],
        cmap="turbo",
        interpolation="bilinear",
        vmin=-float(dynamic_range_db),
        vmax=0,
    )
    if (
        raw_db.shape[0] >= 2
        and raw_db.shape[1] >= 2
        and float(np.nanmin(raw_db)) <= -3 <= float(np.nanmax(raw_db))
    ):
        axis.contour(
            horizontal,
            vertical,
            raw_db,
            levels=[-3],
            colors="white",
            linewidths=0.9,
            linestyles="solid",
        )
    horizontal_limits = (float(horizontal[0]), float(horizontal[-1]))
    vertical_limits = (float(vertical[0]), float(vertical[-1]))
    for point, label, marker, color in markers:
        if (
            horizontal_limits[0] <= point[0] <= horizontal_limits[1]
            and vertical_limits[0] <= point[1] <= vertical_limits[1]
        ):
            axis.scatter(
                [point[0]], [point[1]], marker=marker, color=color,
                s=70, linewidths=1.5, label=label,
            )
    axis.set(
        xlabel=horizontal_label,
        ylabel=vertical_label,
        title=f"User {user_index + 1} · {plane.upper()} normalized power",
    )
    if markers:
        axis.legend(loc="upper right")
    figure.colorbar(image, ax=axis, label="Relative power / dB")
    buffer = io.BytesIO()
    FigureCanvasAgg(figure).print_png(buffer)
    return buffer.getvalue()


def _local_axis_mask(
    coordinates: np.ndarray, center: float, radius: float
) -> np.ndarray:
    mask = np.abs(coordinates - center) <= radius
    required = min(2, len(coordinates))
    if int(np.count_nonzero(mask)) < required:
        nearest = np.argsort(np.abs(coordinates - center))[:required]
        mask[nearest] = True
    return mask


def field_png(field_data: dict[str, Any], user_index: int) -> bytes:
    """Backward-compatible exported XOZ image."""

    return plane_png(
        field_data, user_index, "xz", dynamic_range_db=30.0
    )


def load_plane_png(
    project_root: Path,
    simulation: dict[str, Any],
    user_index: int,
    plane: str,
    *,
    local_radius_mm: float | None = None,
    dynamic_range_db: float = 12.0,
) -> bytes:
    """Load and cache a rendered field plane by immutable artifact identity."""

    path = artifact_paths(project_root, simulation).get("field_json")
    if path is None:
        raise ValueError("该迭代没有可读取的场数据。")
    stat = path.stat()
    rendered = _load_plane_png_cached(
        str(path), stat.st_mtime_ns, stat.st_size, int(user_index), plane,
        None if local_radius_mm is None else float(local_radius_mm),
        float(dynamic_range_db),
    )
    if rendered is None:
        raise ValueError("场数据无效，无法生成热力图。")
    return rendered


@lru_cache(maxsize=128)
def _load_plane_png_cached(
    path_text: str,
    modified_ns: int,
    size_bytes: int,
    user_index: int,
    plane: str,
    local_radius_mm: float | None,
    dynamic_range_db: float,
) -> bytes | None:
    field_data = _load_field_json_cached(path_text, modified_ns, size_bytes)
    if field_data is None:
        return None
    return plane_png(
        field_data,
        user_index,
        plane,
        local_radius_mm=local_radius_mm,
        dynamic_range_db=dynamic_range_db,
    )


def report_markdown(
    state: dict[str, Any], simulation: dict[str, Any], governance: dict[str, Any] | None = None
) -> str:
    lines = [
        f"# 近场聚焦实验报告：{state.get('agent_task_id', 'unknown')}",
        "",
        "## 场景",
        "",
        f"- 用户数：{state.get('user_count', 1)}",
        f"- 载波频率：{float(state.get('frequency_hz', 0))/1e9:g} GHz",
        f"- 调制频率：{float(state.get('modulation_frequency_hz', 0))/1e6:g} MHz",
        f"- 阵元数量：{state.get('element_count')}",
        f"- 用户谐波阶次：{simulation.get('user_harmonic_orders')}",
        f"- 期望目标：{state.get('desired_targets_mm')}",
        f"- 本轮命令目标：{simulation.get('commanded_targets_mm')}",
        f"- 实际峰值：{simulation.get('actual_peak_points_mm')}",
        "",
        "## MATLAB 指标",
        "",
        f"- X 向 FWHM / mm：{simulation.get('fwhm_x_mm_by_user')}",
        f"- Z 向 DOF / mm：{simulation.get('dof_z_mm_by_user')}",
        f"- 峰旁比 / dB：{simulation.get('peak_to_sidelobe_ratio_db_by_user')}",
        "- 峰旁比定义：目标局部峰功率除以 FWHM×DOF 主瓣矩形之外的最大功率；负值表示外部存在更强峰。",
        f"- 峰值功率（模型单位）：{simulation.get('peak_power_by_user')}",
        (
            "- 场切面：XOZ 主评估面为 201×201；YOZ 主切面固定在 X=0，"
            "XOY 面固定在实际峰值 Z，额外切面均为 101×101。"
            if simulation.get("orthogonal_plane_resolution")
            else "- 场切面：该历史结果仅记录 XOZ 主评估面。"
        ),
        "",
        "## 方法边界",
        "",
    ]
    lines.extend(f"- **{row['部分']}**：{row['本次职责']}" for row in build_method_card(state, simulation))
    if isinstance(governance, dict):
        llm = governance.get("llm") or {}
        versions = governance.get("versions") or {}
        matlab = governance.get("matlab") or {}
        external = governance.get("external_data") or {}
        lines.extend([
            "",
            "## 运行治理与可重复性",
            "",
            f"- 模型 ID：{llm.get('model') or '提供方未返回'}",
            f"- 模型 revision：{llm.get('model_revision') or '提供方未返回'}",
            f"- Prompt / Tool schema 版本：{versions.get('prompt_contract_version')} / {versions.get('tool_schema_version')}",
            f"- MATLAB：{matlab.get('version') or '未记录'} ({matlab.get('release') or '未知 release'})",
            f"- Token（input/output/cache-read/cache-write/reasoning）：{llm.get('input_tokens')} / {llm.get('output_tokens')} / {llm.get('cache_read_tokens')} / {llm.get('cache_write_tokens')} / {llm.get('reasoning_tokens')}",
            f"- 费用：actual={llm.get('actual_cost_usd')} USD；estimated={llm.get('estimated_cost_usd')} USD；status={llm.get('cost_status')}",
            f"- 随机种子 / RNG：{matlab.get('random_seed')} / {matlab.get('rng_algorithm')}",
            f"- 使用外部模型服务：{external.get('external_llm_used')}；完整二维场发送：{external.get('full_field_sent_to_model')}",
        ])
    return "\n".join(lines) + "\n"


def export_bundle(
    project_root: Path,
    state: dict[str, Any],
    simulation: dict[str, Any],
    governance: dict[str, Any] | None = None,
) -> bytes:
    field_data = load_field_data(project_root, simulation)
    if field_data is None:
        raise ValueError("该迭代没有完整场数据。")
    paths = artifact_paths(project_root, simulation)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.md", report_markdown(state, simulation, governance))
        if isinstance(governance, dict):
            public_governance = {
                key: value for key, value in governance.items() if key != "git"
            }
            archive.writestr(
                "governance.json",
                json.dumps(
                    public_governance,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                ) + "\n",
            )
        for name, path in paths.items():
            archive.write(path, arcname=path.name)
        for user_index in range(int(field_data["user_count"])):
            archive.writestr(
                f"user_{user_index + 1}_field.csv",
                field_frame(field_data, user_index).to_csv(index=False).encode("utf-8-sig"),
            )
            archive.writestr(
                f"user_{user_index + 1}_field.png", field_png(field_data, user_index)
            )
            if _has_orthogonal_planes(field_data):
                for plane in ("yz", "xy"):
                    archive.writestr(
                        f"user_{user_index + 1}_{plane}_field.csv",
                        orthogonal_field_frame(field_data, user_index, plane)
                        .to_csv(index=False)
                        .encode("utf-8-sig"),
                    )
    return output.getvalue()


def _field_arrays(field_data: dict[str, Any], user_index: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = np.asarray(field_data["x_mm"], dtype=float).reshape(-1)
    z = np.asarray(field_data["z_mm"], dtype=float).reshape(-1)
    values = np.asarray(field_data["normalized_power_by_user"], dtype=float)
    if values.ndim == 2:
        values = values[:, :, np.newaxis]
    if values.ndim != 3 or not 0 <= user_index < values.shape[2]:
        raise ValueError("场矩阵的用户维度无效。")
    power = values[:, :, user_index]
    if power.shape != (len(z), len(x)):
        raise ValueError("场矩阵与坐标轴尺寸不一致。")
    return x, z, power


def _orthogonal_field_arrays(
    field_data: dict[str, Any], user_index: int, plane: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if plane == "yz":
        horizontal = np.asarray(field_data["y_mm"], dtype=float).reshape(-1)
        vertical = np.asarray(field_data["yz_z_mm"], dtype=float).reshape(-1)
        values = np.asarray(field_data["normalized_power_yz_by_user"], dtype=float)
    elif plane == "xy":
        horizontal = np.asarray(field_data["xy_x_mm"], dtype=float).reshape(-1)
        vertical = np.asarray(field_data["xy_y_mm"], dtype=float).reshape(-1)
        values = np.asarray(field_data["normalized_power_xy_by_user"], dtype=float)
    else:
        raise ValueError("plane must be 'yz' or 'xy'")
    if values.ndim == 2:
        values = values[:, :, np.newaxis]
    if values.ndim != 3 or not 0 <= user_index < values.shape[2]:
        raise ValueError("正交场矩阵的用户维度无效。")
    power = values[:, :, user_index]
    if power.shape != (len(vertical), len(horizontal)):
        raise ValueError("正交场矩阵与坐标轴尺寸不一致。")
    return horizontal, vertical, power


def _has_orthogonal_planes(value: dict[str, Any]) -> bool:
    return {
        "y_mm",
        "yz_z_mm",
        "yz_plane_x_mm_by_user",
        "normalized_power_yz_by_user",
        "xy_x_mm",
        "xy_y_mm",
        "xy_plane_z_mm_by_user",
        "normalized_power_xy_by_user",
    }.issubset(value)


def _valid_field_data(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "x_mm", "z_mm", "normalized_power_by_user", "requested_focus_points_mm",
        "actual_peak_points_mm", "user_harmonic_orders",
    }
    if not required.issubset(value):
        return False
    try:
        for key in ("harmonic_orders", "harmonic_frequencies_hz", "user_harmonic_orders"):
            if not isinstance(value.get(key), list):
                value[key] = [value.get(key)]
        for key in ("requested_focus_points_mm", "actual_peak_points_mm"):
            if (
                isinstance(value.get(key), list)
                and len(value[key]) == 3
                and all(isinstance(item, (int, float)) for item in value[key])
            ):
                value[key] = [value[key]]
        user_count = len(value["user_harmonic_orders"])
        value["user_count"] = user_count
        for index in range(user_count):
            _field_arrays(value, index)
        if int(value.get("schema_version", 1)) >= 2:
            if not _has_orthogonal_planes(value):
                return False
            for key in ("yz_plane_x_mm_by_user", "xy_plane_z_mm_by_user"):
                if not isinstance(value.get(key), list):
                    value[key] = [value.get(key)]
                if len(value[key]) != user_count or not all(
                    isinstance(item, (int, float)) and math.isfinite(float(item))
                    for item in value[key]
                ):
                    return False
            for index in range(user_count):
                _orthogonal_field_arrays(value, index, "yz")
                _orthogonal_field_arrays(value, index, "xy")
    except (TypeError, ValueError, IndexError):
        return False
    return True
