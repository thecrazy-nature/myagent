"""Read-only field artifacts, plots, exports, and transparent method cards."""

from __future__ import annotations

import io
import json
import math
import zipfile
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
        value = json.loads(path.read_text(encoding="utf-8-sig"))
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
            "本次职责": "在每个用户对应的独立谐波通道上进行 axial-null 近场复权重综合，并计算真实 XZ 场矩阵。",
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


def profile_frames(field_data: dict[str, Any], user_index: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    x, z, power = _field_arrays(field_data, user_index)
    actual = np.asarray(field_data["actual_peak_points_mm"][user_index], dtype=float)
    ix = int(np.argmin(np.abs(x - actual[0])))
    iz = int(np.argmin(np.abs(z - actual[2])))
    lateral = pd.DataFrame({"X／mm": x, "归一化功率": power[iz, :], "半功率": 0.5})
    axial = pd.DataFrame({"Z／mm": z, "归一化功率": power[:, ix], "半功率": 0.5})
    return lateral, axial


def field_png(field_data: dict[str, Any], user_index: int) -> bytes:
    x, z, power = _field_arrays(field_data, user_index)
    target = field_data["requested_focus_points_mm"][user_index]
    actual = field_data["actual_peak_points_mm"][user_index]
    db = np.maximum(10.0 * np.log10(np.maximum(power, np.finfo(float).tiny)), -30.0)
    figure = Figure(figsize=(8, 5), dpi=150, constrained_layout=True)
    axis = figure.subplots()
    image = axis.imshow(
        db, origin="lower", aspect="auto",
        extent=[float(x[0]), float(x[-1]), float(z[0]), float(z[-1])],
        cmap="turbo", vmin=-30, vmax=0,
    )
    axis.scatter([target[0]], [target[2]], marker="x", color="white", s=60, label="Target")
    axis.scatter([actual[0]], [actual[2]], marker="+", color="black", s=70, label="Peak")
    axis.set(xlabel="X / mm", ylabel="Z / mm", title=f"User {user_index + 1} normalized field")
    axis.legend(loc="upper right")
    figure.colorbar(image, ax=axis, label="Relative power / dB")
    buffer = io.BytesIO()
    FigureCanvasAgg(figure).print_png(buffer)
    return buffer.getvalue()


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
        "",
        "## 方法边界",
        "",
    ]
    lines.extend(f"- **{row['部分']}**：{row['本次职责']}" for row in build_method_card(state, simulation))
    if isinstance(governance, dict):
        llm = governance.get("llm") or {}
        versions = governance.get("versions") or {}
        git = governance.get("git") or {}
        matlab = governance.get("matlab") or {}
        external = governance.get("external_data") or {}
        lines.extend([
            "",
            "## 运行治理与可重复性",
            "",
            f"- 模型 ID：{llm.get('model') or '提供方未返回'}",
            f"- 模型 revision：{llm.get('model_revision') or '提供方未返回'}",
            f"- Prompt / Tool schema 版本：{versions.get('prompt_contract_version')} / {versions.get('tool_schema_version')}",
            f"- Git commit：{git.get('commit') or '未记录'}；dirty={git.get('dirty')}",
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
            archive.writestr(
                "governance.json",
                json.dumps(governance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
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
    except (TypeError, ValueError, IndexError):
        return False
    return True
