# Hermes 电磁科研智能体

基于 Hermes Agent、Python Bridge 与 MATLAB 的自然语言电磁仿真及近场聚焦实验系统。

研究者可以用中文描述实验目标，由 Hermes 理解任务、选择工具、调用真实 MATLAB 仿真、检查约束，并决定是否需要继续修正。所有电磁场、焦点和功率相关数值均由 MATLAB 计算；大语言模型不代替电磁求解器，也不冒充新的优化算法。

## 主要能力

- 使用自然语言创建、运行、评估和修正近场聚焦任务。
- 支持 1～8 个目标点，每个用户分配不同的 TMA 谐波阶数。
- 支持 1～100 GHz 载频；用户可自由输入数值并选择 GHz/MHz 单位。
- 支持 4×4～32×32 方形平面阵列；界面提供 64、144、256、400 阵元选项。
- 支持自由输入调制频率并选择 MHz/kHz 单位，以及极化标签、阵元数、目标点和容差配置。
- 提供后台任务队列、暂停、恢复、取消、进程状态、预计剩余时间和完成通知。
- 持久化真实场数据并显示 XOZ/YOZ 主切面、实际焦点深度处的 XOY 热力图、局部放大、主瓣/旁瓣、FWHM、DOF、迭代前后对比和阵元位置。
- 支持 PNG、CSV、MAT、Markdown 报告和 ZIP 实验包导出。
- 记录模型、Prompt/Tool 版本、MATLAB 版本、随机种子、Token 和运行环境等可重复性信息。
- 提供独立的阵列几何设计工作流，用真实 MATLAB 比较平面基线与参数化球冠阵列。
- 提供独立的 0/1 透射平面超表面工作流：几何光学基线、二进制优化、指标记录、逐单元控制码和 MATLAB→CST 布局自动化。

## 系统分工

```text
研究者自然语言
    ↓
Hermes Agent：理解目标、选择工具、判断是否继续
    ↓
Python Bridge：参数校验、任务隔离、进程调用、结构化读写
    ↓
MATLAB：电磁场计算、激励生成、焦点测量、指标计算
    ↓
Hermes Agent：评估约束、必要时重新规划、生成结论
```

Hermes 负责科研工作流决策；MATLAB 负责数值计算。项目级 Agent 指令位于 [.hermes.md](.hermes.md)。

## 快速开始

### 推荐：使用 Windows 发布包

发布包不依赖开发者电脑上的绝对路径，也不会携带 API Key、Hermes 认证、实验记录或研究文档。解压发布 ZIP 后双击 `setup.cmd`，安装器会把程序安装到当前用户的 `%LOCALAPPDATA%\HermesEMAgent`，并安装或复用 Hermes 的 Python 环境。

MATLAB 当前仍是数值求解后端，因此目标电脑需要安装兼容 MATLAB，或在安装时指定其可执行文件：

```powershell
.\install.ps1 -InstallHermes -MatlabExecutable "D:\MATLAB\bin\matlab.exe"
```

若 MATLAB 已在 `PATH` 中，直接运行 `setup.cmd` 即可。CST 仅在调用超表面 CST 建模工具时需要，不影响其他功能。

开发者可从源码构建干净发布包：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\packaging\windows\build-release.ps1"
```

产物位于本地 `dist/`；更详细的依赖边界和卸载方式见 [packaging/README.md](packaging/README.md)。

### 1. 环境要求

- Windows PowerShell
- MATLAB，可通过 `MATLAB_EXECUTABLE` 或系统 `PATH` 找到
- 已完成 OAuth 登录的 Hermes 运行环境
- Python UI 依赖安装在 Hermes 自带 Python 环境中

安装 UI 依赖：

```powershell
$hermesPython = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe"
uv pip install --python $hermesPython -r requirements-ui.txt
```

### 2. 启动应用

在项目根目录打开 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\run-app.ps1
```

浏览器打开：

```text
http://localhost:8501
```

应用不强制要求 VPN 或代理。若所选模型服务在当前网络中可以直连（例如能够直接访问 DeepSeek API），直接运行 `run-app.ps1` 即可。

只有在当前网络无法直连所选服务时，才需要按需启用本地 Clash：

```powershell
.\proxy-on.ps1 -ProxyUrl "http://127.0.0.1:7897"
```

开发结束或关闭 Clash 后，可清除当前 PowerShell 会话中的代理变量：

```powershell
.\proxy-off.ps1
```

两个代理脚本是可选工具，只修改当前 PowerShell 进程环境，不使用 `setx`，不修改注册表、系统全局代理或 Clash 配置。

## 如何使用界面

### 智能体对话

在同一个连续会话中描述实验，例如：

```text
在 28 GHz、256 阵元条件下，把 q=0 聚焦到 [0,0,100] mm，
容差 3 mm；如果第一次不满足，请在两次修正预算内继续实验并如实报告。
```

多用户示例：

```text
使用三个不同阶谐波，同时聚焦到 [0,0,90]、[15,0,105]、[-15,0,120] mm，
比较各用户焦点误差、FWHM、DOF 和最大旁瓣比。
```

对话页中的频率、极化、阵元数量、用户数量、目标坐标、容差和修正预算会转换为明确的任务配置交给 Hermes。载波频率可自由输入并选择 GHz/MHz，调制频率可自由输入并选择 MHz/kHz；提交前统一换算为 MATLAB Bridge 使用的 GHz 和 MHz。界面本身不会绕过 Agent 直接选择工具。

独立的“系统设置”页支持跟随 Hermes 默认配置、指定 DeepSeek V4 Flash，或输入自定义 Hermes 模型与供应方 ID。该选择对之后提交的任务生效，不占用科研对话区域。

对话页会按所属对话自动显示每个后台任务的实时阶段、预计剩余时间、控制按钮、Tool Call 轨迹、智能体总结和真实结果可视化。任务中心作为跨对话总览，可按对话与状态筛选历史任务，并查看队列顺序、运行状态和完整持久化记录。

### 任务中心

任务首先写入本地文件队列，再由独立 Worker 运行 Hermes 和 MATLAB。因此关闭浏览器不会终止已开始的后台任务。

- 排队任务可以暂停、恢复或取消。
- 运行中任务会显示 Hermes/MATLAB 阶段、子进程和估计剩余时间。
- 中断任务可以读取持久化状态，从最后一次有效实验继续。
- 失败会区分为 Agent 错误、科学结果未达约束和基础设施错误。

### 结果可视化

结果页只读取已持久化的真实实验数据，可查看：

- 每个谐波用户的 XOZ 场热力图、目标附近局部图、严格 X=0 的 YOZ 主切面和实际峰值深度处的 XOY 焦平面；
- 轴向剖面，以及带 −3 dB 轮廓的焦平面热力图；
- FWHM、DOF、目标局部峰值与最大旁瓣比；
- 第一次与最后一次迭代对比；
- 多任务叠加和真实阵元坐标；
- 可重复性信息及方法职责卡片。

## 聚焦工作流工具

- `create_focus_task`：创建任务并保存用户真正期望的目标及约束。
- `get_focus_task_state`：仅用于中断恢复，读取持久化检查点和下一步有效动作。
- `run_focus_simulation`：通过 Bridge 调用真实 MATLAB 聚焦仿真。
- `evaluate_focus`：将 MATLAB 结果与最初目标进行任务级比较。
- `refine_focus`：在预算允许时应用轻量反馈补偿，再交给 MATLAB 重新计算。

`desired_target_mm` 始终表示研究者目标，`current_command_target_mm` 表示当前交给 MATLAB 的命令点，两者不会混为一谈。

当前补偿公式为：

```text
new_command = old_command + 0.7 × (desired - actual)
```

它只用于展示 Agent 的“观察—重新规划”闭环，不是新的电磁优化器，也不替代 MATLAB 内部算法。

## 多用户与 TMA 谐波边界

每个用户使用不同谐波阶数，并在 `fc + q×fm` 对应频率上分别计算。当前 MATLAB 核心为每个活跃谐波合成独立的理想复权向量，可用于估计各谐波的性能上界。

该模型尚未把所有理想权重投影到同一套受耦合约束的矩形脉冲、PWM 或真实 RF 开关序列，因此不能将当前结果直接描述为已完成硬件可实现的 TMA 时序综合。

## 阵列几何设计工作流

独立的“阵列几何设计”能力包含：

```text
自然语言设计目标
→ Hermes 选择受限的几何族搜索
→ 确定性坐标生成与约束检查
→ 单个真实 MATLAB 进程批量评价候选
→ 相对平面基线计算指标和分数
→ Hermes 决定继续搜索或停止
→ 保存完整设计证据
```

当前支持平面基线和以表面深度参数化的球冠阵列。比较固定 256 阵元、112.5×112.5 mm XY 包络、7.5 mm 最小间距、28 GHz 和相同总激励功率。

几何设计工具包括：

- `create_array_design_task`
- `evaluate_array_geometry`
- `search_array_geometry`
- `save_array_design`

当前几何评价场是 XZ 二维切片，只报告 `fwhm_x_mm` 和 `dof_z_mm`，不会虚构 Y 向宽度或三维焦体积。

## 平面可编程超表面设计工作流

界面顶部可在 `Array 阵列聚焦` 与 `0/1 透射超表面` 两种快捷配置之间二选一。超表面表单包含工作频率、单元尺寸、平面阵列大小、平面波/喇叭球面波、0/1 单元幅相响应和单焦点位置。

```text
自然语言焦点与 0/1 单元参数
→ Hermes 创建独立超表面设计任务
→ MATLAB 在同一批次评价未编程参考、连续相位参考和几何光学 0/1 基线
→ Hermes 根据观测选择已注册的二进制优化器、旁瓣权重、迭代上限和随机种子
→ MATLAB 以几何光学码为初值执行二进制坐标下降并计算完整 XZ 场
→ Agent 记录定位、能量集中度、旁瓣和透射能量代理（当前不自动判定整体通过）
→ 保存逐单元 0/1 码、幅相、基线对比和选择理由
→ MATLAB 生成 CST VBA 历史并可通过 CST 2025 OLE 建立控制码布局工程
```

超表面工具包括：

- `create_metasurface_design_task`
- `evaluate_metasurface_baseline`
- `optimize_metasurface_candidate`
- `evaluate_metasurface_design`
- `save_metasurface_design`
- `build_metasurface_cst_model`

结果页会并列展示未编程参考、不可直接制造的连续相位参考、AI 优化前的几何光学 0/1 基线和最终二进制设计。Hermes 只选择有界工作流参数并比较证据；控制码与全部聚焦数值均由 MATLAB 产生。

0/1 状态允许分别输入线性透射幅度和相位，程序会计算两态功率透射率、相对幅度和相对相位，并记录传输能量代理。当前验收策略固定为 `record_only`，`overall_pass` 保持空值，等待研究者确定并版本化“通过”标准。

CST 产物目前是带 `binary_state_0/1` 标签的控制码布局骨架。因为单元尺寸不足以唯一确定真实电磁结构，程序不会自动虚构材料、金属图案、介质层、端口、边界或 S 参数，也不会把该布局称为已经通过全波验证的模型。

## Python 桥接层

可以绕过界面直接调用稳定 Bridge：

```python
from bridge import run_simulation

result = run_simulation([0.0, 0.0, 100.0])
print(result["actual_peak_mm"])
```

每次调用会在本地 `runs/<task_id>/` 下创建隔离记录。详细接口见 [bridge/README.md](bridge/README.md)。

## MATLAB 核心调用

在 MATLAB 中将项目根目录保存为变量后运行：

```matlab
projectRoot = '你的项目根目录';
addpath(fullfile(projectRoot, 'matlab_core'), '-begin');
result = run_focus_core([0, 0, 100]);
disp(result.actual_peak_mm);
disp(result.peak_power);
```

JSON 黑盒接口示例：

```json
{
  "task_id": "task_001",
  "target_mm": [0.0, 0.0, 100.0]
}
```

```powershell
$projectRoot = (Resolve-Path .).Path.Replace('\', '/')
matlab -batch "addpath('$projectRoot/agent_interface','-begin'); agent_run_simulation('$projectRoot/runs/task_001/config.json','$projectRoot/runs/task_001/result.json');"
```

结果文件通过“写入完整临时 UTF-8 JSON，再原子替换目标文件”的方式生成，成功和预期失败路径都会留下结构化结果。

## 物理模型范围

- 1～100 GHz 载频，界面允许以 GHz 或 MHz 自由输入；
- 1～1000 MHz 调制频率，界面允许以 MHz 或 kHz 自由输入；
- 4×4～32×32 平面阵列，间距为载波波长的 0.7 倍；
- 各向同性标量点源模型；
- 确定性的独立谐波 `axial_null` 近场权重；
- 每个用户分配不同谐波，最多 8 个 XZ 平面目标，当前要求 `y=0`；
- 默认场区域为 `x=[-100,100] mm`、`z=[20,160] mm`，201×201 网格。

`peak_power` 是标量模型中局部焦点处的 `|E|²`，属于模型量纲下的功率代理，不是经过标定的 W 或 W/m²。极化目前只作为实验元数据保存；标量各向同性模型不会产生极化相关数值结论。

## 可重复性与数据边界

每次后台任务会记录：

- Hermes 实际模型 ID 与运行时版本；
- Prompt、Tool schema、MATLAB 合同和 Bridge 的版本及 SHA-256；
- Python 平台、MATLAB 版本、随机种子和 RNG 策略；
- Token 分类、API 调用次数，以及服务商实际返回时才显示的费用。

自然语言请求、场景、Tool schema、Tool 参数、任务 ID 和精简 MATLAB 指标可能发送到当前配置的模型服务商。完整二维场矩阵、MAT 文件、MATLAB 日志和认证凭据保留在本地，不放入 Tool Observation。

## 测试

运行 Python 单元测试：

```powershell
$hermesPython = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe"
& $hermesPython -m unittest discover -s tests
```

运行 MATLAB 测试：

```matlab
addpath(fullfile('你的项目根目录', 'tests'));
run_all_tests();
```

运行外部冷启动批处理测试：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\tests\run_batch_end_to_end.ps1" `
  -MatlabExecutable "你的 MATLAB 可执行文件路径" `
  -ProjectRoot (Resolve-Path .).Path
```

## 基准测试（Benchmark）

冻结的 benchmark 包含 12 个任务：

- Type A：一次仿真并如实报告；
- Type B：识别约束并做出正确任务判断；
- Type C：第一次失败后完成“观察—重新规划”；
- Type D：耗尽预算后如实停止并报告约束未满足。

任务定义见 [benchmark/tasks.json](benchmark/tasks.json)，指标定义见 [benchmark/METRICS.md](benchmark/METRICS.md)。只有明确标注为端到端的测试才允许使用真实 Hermes 与 MATLAB；mock 结果不能作为端到端成绩。

## 当前限制

- MATLAB 冷启动通常占主要运行时间；
- 标量点源模型不包含真实阵元方向图、极化、互耦和材料损耗；
- 理想谐波权重尚未映射到一套可制造的耦合 TMA 开关时序；
- 当前几何设计指标来自二维 XZ 场，不等同于三维焦体积；
- 当前仅支持参数化球冠几何，尚未加入圆柱、多面板和任意三维曲面族。
- 可编程超表面当前使用标量传播和二进制坐标下降；验收指标/阈值仍待研究者选择，尚未接入真实单元数据库、互耦和全波联合验证。

这些限制会在界面方法卡片和实验记录中明确显示，避免把工作流自动化误解为新的电磁算法。

## 仓库隐私

公开仓库只包含程序源码、测试、配置示例和 README 类使用说明。以下内容由 `.gitignore` 排除并仅保留在本地：

- `runs/` 下的实验记录；
- benchmark 结果和 Tool Call 轨迹；
- `docs/` 下的研究方案、汇报材料和内部报告；
- `.env`、认证文件、密钥和其他凭据。

提交前建议执行：

```powershell
git status --short
git grep -n -I -E "api[_-]?key|sk-[A-Za-z0-9_-]{16,}|Bearer[[:space:]]+"
```
