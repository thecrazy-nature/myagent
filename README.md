# LLM-driven Electromagnetic Simulation and Focusing Optimization Workflow Agent

中文名称：**基于 Hermes Agent 的电磁仿真与聚焦优化工作流智能体**。

This project uses Hermes to turn a researcher's natural-language task into a
planned, tool-driven MATLAB experiment workflow. Hermes understands the task,
orchestrates tools, evaluates structured observations, decides whether another
experiment is needed, and reports the outcome. MATLAB remains responsible for
all electromagnetic numerical computation and focusing algorithms.

The stable MATLAB capability is an independent extraction of the deterministic
single-target, carrier-frequency near-field focusing path from
`F:\matlabcode\multiuser`. It does not require the source project on the
MATLAB path and does not use mock data.

## Architecture and responsibility boundary

```text
Researcher
    ↓
Natural Language
    ↓
Hermes Agent
    ↓
Planning / Tool Selection
    ↓
MATLAB Solver / Optimizer
    ↓
Structured Observation
    ↓
Evaluation
    ↓
Replanning / Report
```

Hermes is the research workflow agent. It interprets the requested experiment,
stores goals and constraints, selects tools, evaluates MATLAB observations,
makes task-level stop/replanning decisions, and prepares the final report.

MATLAB is the numerical system. It computes electromagnetic fields, generates
excitations, runs the existing focusing method, and measures the actual peak
and power. The LLM neither calculates electromagnetic results nor replaces a
MATLAB optimizer.

The project-level Hermes instruction is defined in [.hermes.md](.hermes.md).

## Why Agent?

### Traditional MATLAB workflow

```text
Researcher
→ manually modify parameters
→ run MATLAB
→ inspect result
→ manually decide next experiment
→ rerun
→ summarize results
```

### Agent workflow

```text
Researcher
→ natural-language task
→ Hermes planning
→ MATLAB tool execution
→ structured result analysis
→ task-level decision/replanning
→ final summary
```

The Agent automates task understanding, tool orchestration, result evaluation,
and experiment decisions in the research workflow. It does not replace the
underlying numerical algorithm.

## Hermes workflow tools

- `create_focus_task` structures a scientific focusing task and preserves the
  researcher's desired target and constraints.
- `run_focus_simulation` invokes the real MATLAB electromagnetic solver and
  focusing algorithm for one experiment.
- `evaluate_focus` performs task-level evaluation of the MATLAB result against
  the original desired target.
- `refine_focus` applies deterministic workflow-level lightweight parameter
  compensation after a failed experiment when budget remains.

`refine_focus` retains the verified formula
`new_command = old_command + 0.7 * (desired - actual)`. It exists to demonstrate
the Agent's Observation → Replanning loop; it is not a replacement for, or a
competitor to, MATLAB's numerical optimizers.

## Gate 4 Replanning demo

The retained real Hermes + MATLAB case is:

```text
Desired target:
[0, 0, 100] mm

Iteration 1:
Commanded target = [0, 0, 100]
Actual peak      = [0, 0, 94.2]
Focus error      = 5.8 mm
Result           = FAIL

Hermes observes failure
→ refine_focus

Iteration 2:
Commanded target = [0, 0, 104.06]
Actual peak      = [0, 0, 97.7]
Focus error      = 2.3 mm
Result           = SUCCESS
```

The LLM did not calculate the electromagnetic field. After MATLAB returned the
first 5.8 mm positioning error, Hermes selected the feedback-correction tool
and triggered a second real experiment. The final MATLAB observation reduced
the task-level error to 2.3 mm.

## Python bridge

The standard-library bridge hides MATLAB commands and task paths:

```python
from bridge import run_simulation

result = run_simulation([0.0, 0.0, 100.0])
```

Each invocation creates an isolated `runs/<task_id>/` record. See
[bridge/README.md](bridge/README.md) and
[bridge/GATE3_REPORT.md](bridge/GATE3_REPORT.md).

## Hermes development proxy

开发 Hermes Agent 前需先运行：

```powershell
.\proxy-on.ps1
```

开发结束或关闭 Clash 后可运行：

```powershell
.\proxy-off.ps1
```

如果当前 PowerShell 的执行策略阻止本地脚本，可仅对当前会话临时执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## Performance characteristics

The stable bridge currently uses:

```text
Python subprocess
→ matlab -batch
```

This provides isolated runs, clean MATLAB environments, straightforward
debugging, and reliable process-failure capture. Its cost is a new MATLAB cold
start for every experiment. Current observations show approximately one second
of numerical computation while process startup takes tens of seconds, so
end-to-end latency is dominated by MATLAB startup rather than LLM reasoning.

Potential future work includes MATLAB Engine for Python, a persistent MATLAB
worker, or a persistent MATLAB process with IPC. None is implemented in the
current stable Bridge.

## Workflow benchmark design

The benchmark evaluates whether Hermes correctly completes a scientific
workflow; it does not compare “Agent optimization accuracy” with MATLAB
optimizer accuracy. The initial suite contains 12 designed tasks:

- Type A: one-shot simulation and truthful result reporting.
- Type B: constrained task and correct task-level decision.
- Type C: failed first experiment followed by Observation → Replanning.
- Type D: strict constraint, budget exhaustion, and truthful failure exit.

See [benchmark/tasks.json](benchmark/tasks.json) for the 3 × 4 task definitions
and [benchmark/METRICS.md](benchmark/METRICS.md) for workflow, MATLAB-result,
and efficiency metrics. Schema, state-machine, invalid-call, evaluation,
budget, stop-logic, and trajectory-parser checks should remain deterministic
unit/workflow tests. Only explicitly labelled end-to-end runs use real Hermes
and MATLAB; mock output must never be reported as an end-to-end result.

The 12-task suite is designed but has not been executed.

## Scope and physical model

- 28 GHz carrier (`lambda_c = 10.7142857143 mm`)
- 16 x 16 planar array, 0.7 carrier-wavelength spacing
- isotropic scalar point-source elements
- deterministic `axial_null` near-field weight synthesis
- one target on the x-z plane (`y = 0`)
- accepted target domain: `x = [-75, 75] mm`, `z = [53.5714, 150] mm`
- field grid: paper evaluation region `x = [-100, 100] mm`,
  `z = [20, 160] mm`, 201 x 201 samples

`peak_power` is the raw scalar-model `|E|^2` at the local focal peak. It is a
power proxy in model units, not calibrated watts or W/m^2. `actual_peak_mm`
uses the existing `measure_focal_spots` local-search contract (radius 2.5
carrier wavelengths), because the scalar Green-function model can have a
larger unrelated near-array global maximum. The core result also exposes that
global maximum separately.

## MATLAB core usage

```matlab
restoredefaultpath;
addpath('F:\hermes-em-agent\matlab_core', '-begin');
result = run_focus_core([0, 0, 100]);
disp(result.actual_peak_mm);
disp(result.peak_power);
```

The result also contains the complex excitation vector and the computed field
maps. `run_focus_core` explicitly adds only its own `core`, `solver`,
`evaluation`, and `config` subfolders on every invocation.

## JSON interface

Input:

```json
{
  "task_id": "task_001",
  "target_mm": [0.0, 0.0, 100.0]
}
```

Command-line invocation:

```powershell
matlab -batch "addpath('F:/hermes-em-agent/agent_interface','-begin'); agent_run_simulation('F:/hermes-em-agent/runs/task_001/config.json','F:/hermes-em-agent/runs/task_001/result.json');"
```

The interface writes a complete temporary UTF-8 JSON file and then atomically
replaces the requested result path. Both successful and expected failure paths
produce JSON.

## Tests

Generate the source-project baseline once (audit-only; reads but does not alter
the source project):

```powershell
matlab -batch "addpath('F:/hermes-em-agent/tests'); generate_original_baseline('F:/matlabcode/multiuser','F:/hermes-em-agent/runs/original_baseline.mat');"
```

Run every extracted-project test:

```powershell
matlab -batch "addpath('F:/hermes-em-agent/tests'); run_all_tests();"
```

Run the three external cold-start `matlab -batch` cases on Windows:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\hermes-em-agent\tests\run_batch_end_to_end.ps1" -MatlabExecutable "F:\matlab\bin\matlab.exe" -ProjectRoot "F:\hermes-em-agent"
```

See [AUDIT_REPORT.md](AUDIT_REPORT.md) for the source audit, numerical
comparison, exclusions, and gate status. See
[matlab_core/DEPENDENCIES.md](matlab_core/DEPENDENCIES.md) for the exact runtime
closure. The command-line-interface evidence is in
[agent_interface/VALIDATION_REPORT.md](agent_interface/VALIDATION_REPORT.md).

## Future work

If multiple stable MATLAB methods are added later, Hermes may select a method
and orchestrate structured comparisons. The current project deliberately keeps
the one verified focusing method and does not reintroduce unvalidated legacy
methods merely to broaden the Agent interface.

For a concise résumé/project-description version, see
[docs/PROJECT_DESCRIPTION.md](docs/PROJECT_DESCRIPTION.md).
