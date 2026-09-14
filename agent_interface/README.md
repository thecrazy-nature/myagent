# MATLAB 命令行黑盒接口

## 输入格式

`config.json` 必须是一个 JSON 对象：

```json
{
  "task_id": "task_001",
  "target_mm": [0.0, 0.0, 100.0]
}
```

- `task_id`：字符串，由外部调用方提供并原样写入结果。
- `target_mm`：有限数值向量 `[x,y,z]`，单位为 mm。
- 第一版仅支持 x-z 平面，因此 `y` 必须为 0。
- 有效范围为 `x ∈ [-75,75] mm`、`z ∈ [53.5714,150] mm`。

真实示例见 `agent_interface/example_config.json`。

## MATLAB 核心入口

```matlab
result = run_focus_core(target_mm)
```

该函数使用固定的 28 GHz、16×16 平面阵列和 `axial_null` 近场合成配置，
调用提取自原科研工程的真实算法，返回激励、复场、功率图、请求位置和实测峰值。

## Agent 外部入口

```matlab
agent_run_simulation(config_path, result_path)
```

入口自行定位 `matlab_core`，不要求当前目录位于项目根目录，也不要求预先初始化
MATLAB path。它不使用 GUI、Live Script、base workspace、`input()` 或文件选择器。

## 成功输出字段

- `status`：固定为 `success`。
- `task_id`：输入任务 ID。
- `requested_focus_mm`：请求的 `[x,y,z]`，单位 mm。
- `actual_peak_mm`：现有 `measure_focal_spots` 在请求点周围 2.5λ 范围内测得的局部场峰值坐标，单位 mm。
- `peak_power`：局部场峰值处未经归一化的标量模型 `|E|^2`；它是模型单位功率代理，不是 W 或 W/m²。
- `peak_power_definition`：上述物理含义的机器可读说明。
- `requested_power`：请求点处精确计算的未经归一化标量模型 `|E|^2`。
- `runtime_sec`：接口内从读配置到获得仿真结果的墙钟时间，不包含 MATLAB 进程启动时间。
- `matlab_version`、`matlab_release`、`matlab_arch`：由执行本次数值任务的同一 MATLAB 进程返回。
- `random_seed=0`、`rng_algorithm="twister"`：可重复性治理信息；当前聚焦核心不含随机搜索。

接口不计算聚焦误差、成功阈值、Agent 决策、重试或参数修正。

失败时返回 `status=error`、尽可能保留的 `task_id`、`error_type`、
`message` 和 `runtime_sec`。结果先写入临时 UTF-8 文件，再原子替换目标文件，
以免外部进程读到未完成的 JSON。

## 命令行调用

以下命令已在本项目中真实执行：

```powershell
matlab -batch "addpath('F:/hermes-em-agent/agent_interface','-begin'); agent_run_simulation('F:/hermes-em-agent/runs/task_001/config.json','F:/hermes-em-agent/runs/task_001/result.json');"
```

命令可从任意当前工作目录执行。

三个冷启动任务可用以下测试驱动重复执行。本机 PowerShell 默认禁止脚本，
所以命令使用只对该进程生效的 `-ExecutionPolicy Bypass`；它不会修改系统策略：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\hermes-em-agent\tests\run_batch_end_to_end.ps1" -MatlabExecutable "F:\matlab\bin\matlab.exe" -ProjectRoot "F:\hermes-em-agent"
```

## 当前限制

- 仅支持单焦点和 `y=0` 的二维 x-z 场图。
- 阵列、频率、场网格和合成方法固定为已验证配置。
- 峰值位置受到 201×201 场网格分辨率限制；请求点功率另外进行精确计算。
- 该 MATLAB 层自身不包含 Python、Hermes、自动重试、评价阈值或新优化算法；
  Python 调用适配位于项目根目录的 `bridge/`，不会改变本接口职责。
