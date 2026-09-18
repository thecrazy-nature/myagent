# MATLAB JSON 黑盒接口

本目录提供无 GUI、无交互输入的 MATLAB 入口，供 Python Bridge 或命令行进程调用。

## 输入

最小配置为：

```json
{
  "task_id": "task_001",
  "target_mm": [0.0, 0.0, 100.0]
}
```

- `task_id`：调用方生成的任务标识。
- `target_mm`：焦点坐标 `[x,y,z]`，单位为 mm。
- 完整可选参数与当前物理边界以 `agent_run_simulation.m` 和主项目 README 为准。

## 调用

在项目根目录运行：

```powershell
$projectRoot = (Resolve-Path .).Path.Replace('\', '/')
$config = "$projectRoot/runs/task_001/config.json"
$result = "$projectRoot/runs/task_001/result.json"
matlab -batch "addpath('$projectRoot/agent_interface','-begin'); agent_run_simulation('$config','$result');"
```

也可以执行仓库内的冷启动批处理测试：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\tests\run_batch_end_to_end.ps1" `
  -MatlabExecutable "matlab"
```

接口会自行定位同一项目中的 `matlab_core`。结果先写入临时 UTF-8 JSON，再原子替换目标文件，避免调用方读取到半写入内容。

## 输出职责

成功结果包含任务 ID、请求焦点、实际峰值、功率代理、运行时间、MATLAB 版本和可重复性信息。失败结果包含 `status=error`、错误类型、错误消息和已知任务 ID。

MATLAB 层只负责数值求解和测量，不负责 Agent 决策、阈值判定、重试或自然语言总结。
