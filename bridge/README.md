# Python–MATLAB Bridge

该 bridge 只使用 Python 标准库，将现有 MATLAB 黑盒包装成同步 Python API。

```python
from bridge import run_simulation

result = run_simulation([0.0, 0.0, 100.0])
```

真实签名：

```python
def run_simulation(
    target_mm,
    task_id=None,
    timeout_sec=300,
) -> dict:
```

未提供 `task_id` 时生成 `task_<uuid4 hex>`。每个任务写入：

```text
runs/<task_id>/
├─ config.json
├─ result.json
├─ stdout.log
└─ stderr.log
```

超时或 MATLAB 未产生结果时可能没有 `result.json`，但 config 和已有日志会保留。
显式 task ID 必须是安全的 Windows 目录名且不能与已有任务重复。

bridge 使用参数列表和 `subprocess.run(..., shell=False)`，不会使用 MATLAB Engine。
在 Windows 上使用 MathWorks 支持的 `bin/matlab.exe -wait -batch` 启动方式；
`-wait` 使 Python 同步等待 MATLAB 完成并取得退出状态，同时避免直接启动内部
`bin/win64/MATLAB.exe` 时观察到的退出阶段 lifecycle crash。

本机 R2024b 偶发在完整 `result.json` 原子写入之后、`ddux` shutdown 清理阶段
发生 `std::terminate`。bridge 仅在 stderr 同时匹配已观察到的两个 shutdown
特征，且该次新建 run 目录中的 success JSON 通过全部字段、有限数、task ID
契约校验时接收结果，并附加 `process_warning=MatlabShutdownError`。普通非零退出、
缺失或损坏结果仍抛出异常，因此该窄恢复不会伪造或放宽科学结果。

## 异常

- `InvalidSimulationInput`：Python 输入非法，MATLAB 不会启动。
- `MatlabExecutableNotFound`：找不到 MATLAB。
- `MatlabTimeoutError`：超过 `timeout_sec`。
- `MatlabProcessError`：MATLAB 无法启动或返回非零码。
- `MatlabResultError`：结果缺失、JSON 损坏或字段契约错误。
- `MatlabSimulationError`：MATLAB 返回结构化 `status=error`；异常的
  `result` 属性保留原始 dict。

## Demo 与测试

本机的 `python.exe` WindowsApps 别名不可用，实际解释器通过 `py` 启动：

```powershell
py -3.13 demo_bridge.py
py -3.13 -m unittest discover -s tests -p "test_*.py" -v
```

Python 版本为 3.13.5，没有新增第三方依赖。真实验收记录保存在本地，
不随公开源码仓库发布。
