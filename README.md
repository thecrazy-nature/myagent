# LLM-driven Electromagnetic Simulation and Focusing Optimization Workflow Agent

中文名称：**基于 Hermes Agent 的电磁仿真与聚焦优化工作流智能体**。

This project uses Hermes to turn a researcher's natural-language task into a
planned, tool-driven MATLAB experiment workflow. Hermes understands the task,
orchestrates tools, evaluates structured observations, decides whether another
experiment is needed, and reports the outcome. MATLAB remains responsible for
all electromagnetic numerical computation and focusing algorithms.

The MATLAB capability is an independent extraction of the deterministic
multi-harmonic near-field focusing path from
`F:\matlabcode\multiuser`. It does not require the source project on the
MATLAB path and does not use mock data.

The focus workflow now accepts one to eight simultaneous target points, a
1–100 GHz carrier (the UI offers 24/28/39 GHz), and a square 4×4–32×32 planar
array (the UI offers 64/144/256/400 elements). MATLAB uses the selected
frequency and element count in its real field calculation. Every user is
assigned a distinct harmonic order around q=0 and evaluated at `fc + q*fm`;
multiple users are never combined on q=0. MATLAB reports a peak, power,
FWHM, DOF, and target-local-peak/maximum-sidelobe ratio for every user. A task
passes only when every user satisfies the tolerance; the aggregate error is
the worst user error.

The extracted core currently synthesizes an independent ideal complex weight
vector for each active harmonic. This is a useful per-harmonic upper-bound
model, not a claim that the weights have already been projected into one
coupled rectangular-pulse TMA switching sequence. The UI method card states
this boundary on every result.

Polarization can be selected and is persisted as experiment metadata, but the
current isotropic scalar point-source solver is polarization independent. The
application states this explicitly and does not claim polarization-dependent
numerical results. A vector/dyadic element model would be required for that.

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

## Autonomous Array Geometry Design

The independent **Array Geometry Designer** tab adds a second natural-language
workflow without changing `run_focus_simulation` or the existing focus Agent:

```text
Natural language design goal
→ Hermes chooses a bounded geometry-family search
→ deterministic coordinate generator and constraint checks
→ one real MATLAB process evaluates a candidate batch
→ baseline-normalized metrics and objective score
→ Hermes decides whether to search again or stop
→ full selected design is persisted
```

This is **array geometry optimization, not individual antenna element
electromagnetic redesign**. The radiator model, frequency, and polarization
representation remain frozen. The first supported family is a spherical cap
parameterized only by surface depth (0–20 mm); identical family, parameters,
and integer seed always produce identical coordinates. A curved family is used
because the fully packed 16×16 planar baseline has no honest in-plane movement
under its unchanged aperture and actual 7.5 mm minimum spacing.

Every comparison fixes 256 elements, the 112.5×112.5 mm XY envelope, the
baseline 7.5 mm nearest-neighbour spacing, 28 GHz, and the scalar isotropic
point-source model. Candidate carrier weights are rescaled so
`sum(abs(w).^2)` equals the real baseline value. Array geometry changes the
effective aperture and spatial spectrum and can therefore change lateral
resolution, axial focusing, and energy distribution, but these metrics may
trade off; no result is described as improving all objectives unless the real
MATLAB data supports that claim.

The current field model is an XZ slice, so it reports `fwhm_x_mm` only and does
not invent a Y-direction FWHM. FWHM and Z depth of focus use the contiguous
region around the measured local peak where normalized `|E|² >= 0.5`, with
linear interpolation at both threshold crossings. Energy concentration is the
2D XZ integral inside a fixed desired-target ROI (`|x-x_target| <= 5 mm`,
`|z-z_target| <= 10 mm` by default) divided by the integral over the common
201×201 XZ evaluation area.

The deterministic score (lower is better) is:

```text
w_spot * candidate_fwhm_x / baseline_fwhm_x
+ w_dof * candidate_dof_z / baseline_dof_z
- w_energy * candidate_energy_ratio / baseline_energy_ratio
```

Weights are normalized to sum to one. Focus error beyond the task tolerance,
spacing/aperture/count violations, overlap, and non-finite MATLAB metrics make
a candidate invalid with score `1e9`. The full task state, real coordinates,
profiles, MATLAB logs, comparison, and final selection are stored under
`runs/array_designs/<design_task_id>/`.

Array-design tools are `create_array_design_task`,
`evaluate_array_geometry`, `search_array_geometry`, and `save_array_design`.
The search tool batches all candidates from one deterministic coarse grid into
one dedicated MATLAB `-batch` process; it does not ask the LLM to guess element
coordinates.

Array-design evidence and exploratory reports are generated locally and are not
published with the source repository.

## Running the Interactive App

The local Streamlit application uses a multi-turn chat as the primary entry
point. It sends the user's message plus the visible experiment configuration
to the real Hermes Agent:

```powershell
.\proxy-on.ps1
.\run-app.ps1
```

Then open `http://localhost:8501`. Prerequisites are:

- Clash is running on `127.0.0.1:7897` and the current PowerShell session has
  the project proxy variables from `proxy-on.ps1`.
- MATLAB is installed and available through `MATLAB_EXECUTABLE` or `PATH`.
- Hermes OAuth is already configured in the existing Hermes runtime.
- UI dependencies have been installed in that same runtime with
  `uv pip install --python "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" -r requirements-ui.txt`.

The app deliberately launches Hermes through the verified project-compatible
module path:

```text
Browser
→ Streamlit
→ existing Hermes Python -m hermes_cli.main
→ project em_focus Plugin
→ Python Bridge
→ matlab.exe -wait -batch
→ structured observation
→ Hermes evaluation / replanning / final response
```

The Chinese **智能体对话** tab is one unified, continuous conversation. Follow-up
turns resume the same Hermes session rather than reconstructing an answer in
Streamlit. The user does not choose "focus" or "array design" in advance, and
the UI does not select tools. Hermes determines whether a turn needs a plain
answer, the focus workflow, or the independent array-design workflow; the UI
identifies numerical result types only from actual Tool Calls.

The experiment panel exposes carrier and modulation frequency, polarization label, element
count, user count and target coordinates, tolerance, and refinement budget.
The physical target bounds update with wavelength. These controls are rendered
into explicit natural-language context for Hermes; they never call MATLAB or
choose a workflow directly. Array-geometry comparisons remain frozen to their
28 GHz / 256-element baseline so existing fair-comparison evidence is not
silently redefined.

Submissions enter the file-backed **任务中心** under `runs/ui_jobs/`. A separate
single-consumer worker runs Hermes and MATLAB, so closing the browser does not
stop an active task. The task center auto-refreshes persisted status, elapsed
time, heuristic ETA, and MATLAB child-process information. Queued tasks can be
paused without starting; active tasks pause/resume the exact Hermes/MATLAB
process tree through `psutil`; cancellation terminates that tree. Completion
notifications remain pending until the browser is reopened. Interrupted
failures can resume the same Hermes session, call `get_focus_task_state`, and
continue from the last valid persisted state instead of repeating a completed
MATLAB run.

The Chinese **结果可视化** tab is a read-only browser for persisted focus tasks
and array designs. Every new focus run stores `field_data.json` plus a full
`field_data.mat`. The viewer plots true per-harmonic XZ heatmaps, target-local
zoom, lateral main/side-lobe and axial profiles, first/last iteration maps,
multi-task overlays, and real element coordinates. PNG, CSV, MAT, Markdown
report, and an all-in-one ZIP can be downloaded without rerunning MATLAB.

### Run governance and reproducibility

The UI shows a prominent data-boundary notice before submission and persists a
governance snapshot with every background job. The snapshot records the actual
Hermes model ID observed for that session, Hermes runtime version, versioned
Prompt/Tool/MATLAB contracts from `governance/versions.json`, SHA-256 hashes of
the effective project sources, Git commit plus dirty-working-tree status,
Python platform, and the MATLAB version/release/architecture returned by the
same numerical process. A dirty tree is reported explicitly because a commit
alone cannot reproduce uncommitted source.

Hermes token counters are read from the local session database without reading
message bodies or authentication credentials. For resumed conversations the UI
records the current turn's counter delta, not the whole conversation again.
Input, output, cache-read, cache-write, reasoning tokens and API-call count are
kept separately. Actual USD cost is shown only when the provider returns it;
subscription-included or unavailable pricing is labelled as such instead of
being presented as a fabricated zero-cost measurement.

Focus runs use MATLAB seed `0` with the `twister` RNG policy (the current focus
solver has no stochastic search). Array-design calls continue to persist their
explicit integer seed. The task center's **重复运行** button deliberately starts
a fresh Hermes conversation with the exact same task text. It warns that this
consumes model quota and MATLAB time, then reports Agent Tool-trajectory
consistency separately from deterministic MATLAB numerical consistency. No
repeat is triggered automatically.

The external-service notice states that the natural-language request, scenario,
Tool schemas, Tool arguments, IDs, and compact MATLAB metrics can be sent to the
configured model provider. Full 2D field arrays, MAT artifacts, MATLAB logs,
and authentication credentials remain local and are not placed in Tool
observations. The report and ZIP exports include the persisted governance
evidence (`governance.json`) when it is available.

MATLAB cold start can take about one minute. Replanning can start MATLAB more
than once, so the page provides stage-level updates while the run is active.
Failures are separated into Agent Error, Scientific Failure, and Infrastructure
Error. `peak_power` retains the MATLAB definition shown below and is never
labelled as watts.

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
- `get_focus_task_state` reads a durable checkpoint only for interrupted-task
  recovery and exposes the valid next action.
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

Each invocation creates an isolated local `runs/<task_id>/` record. See
[bridge/README.md](bridge/README.md).

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

The full 12-task suite has not been executed. Benchmark results and traces are
kept locally and ignored by Git; mock output must never be presented as an
end-to-end result.

## Scope and physical model

- 1–100 GHz carrier (UI presets: 24, 28, and 39 GHz)
- configurable modulation frequency (UI presets: 100, 200, and 500 MHz)
- 4 x 4 through 32 x 32 planar array, 0.7 carrier-wavelength spacing
- isotropic scalar point-source elements
- deterministic independent per-harmonic `axial_null` near-field weight synthesis
- one distinct harmonic order per user; up to eight targets on the x-z plane (`y = 0`)
- accepted target domain scales with carrier wavelength: `x = [-7λc, 7λc]`,
  `z = [5λc, 14λc]`
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

See [matlab_core/DEPENDENCIES.md](matlab_core/DEPENDENCIES.md) for the exact
runtime closure.

## Repository privacy

The public repository contains source code, tests, configuration examples, and
README-style usage documentation. Local experiment records under `runs/`,
benchmark result traces, research reports and presentations under `docs/`, and
credential files are intentionally excluded by `.gitignore`.

## Future work

If multiple stable MATLAB methods are added later, Hermes may select a method
and orchestrate structured comparisons. The current project deliberately keeps
the one verified focusing method and does not reintroduce unvalidated legacy
methods merely to broaden the Agent interface.
