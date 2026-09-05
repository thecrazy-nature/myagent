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
在 Windows 上会把 PATH 中的 `matlab.exe` 启动器解析为同一安装下真实的
`bin/win64/MATLAB.exe`；这是为了让 Python timeout 能终止实际进程并避免孤儿进程。

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

Python 版本为 3.13.5，没有新增第三方依赖。完整真实验收见
`bridge/GATE3_REPORT.md`。
