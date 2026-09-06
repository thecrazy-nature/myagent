# Workflow benchmark metrics

This benchmark measures whether Hermes correctly completes a scientific
simulation workflow. It does not compare an LLM's numerical accuracy against a
MATLAB optimizer. MATLAB observations are scientific task outputs; workflow
metrics describe Agent behavior.

## A. Agent / workflow metrics

### Task Success Rate

`tasks satisfying the requested workflow outcome / executed tasks`

A task passes when the Agent parses the target and constraints, follows a legal
workflow, reports tool-derived values, and gives the correct terminal conclusion.
For Type D, a truthful failure after budget exhaustion is a successful Agent
task even though the scientific positioning constraint was not achieved. Record
the separate `constraint_satisfied` field to avoid conflating these outcomes.

### Tool Call Correctness

`schema-valid and state-valid domain tool calls / all domain tool calls`

A call is correct only if its arguments satisfy the schema and it is legal in
the persisted state. Discovery helpers such as `tool_search` are reported
separately and excluded from the denominator.

### Workflow Success Rate

`tasks with a valid complete domain-tool trajectory / executed tasks`

A valid trajectory begins with one create call, pairs every successful MATLAB
run with exactly one evaluation, refines only after a failed evaluation with
remaining budget, and reaches a valid terminal state.

### Replanning Correctness

`correct replanning decisions / eligible failed evaluations`

For each failed evaluation, check that the Agent reads the failure, respects the
remaining budget and task intent, calls `refine_focus` when another experiment
is required, then performs a new run and evaluation. A one-shot Type A/B task is
correct when it does not replan despite failure.

### Stop Correctness

`tasks stopped at the correct terminal condition / executed tasks`

Correct terminal conditions are successful evaluation, exhausted refinement
budget, or an explicit one-shot user instruction. Extra calls after a terminal
condition fail this metric.

## B. MATLAB-result metrics

- **Final Focus Error (mm):** Euclidean distance between the final real MATLAB
  `actual_peak_mm` and immutable `desired_target_mm`.
- **Actual Peak Position (mm):** final `actual_peak_mm` returned by MATLAB.
- **Peak Power:** final MATLAB `peak_power`, retaining its existing raw
  scalar-field definition and units caveat.
- **MATLAB Runtime (s):** MATLAB's `runtime_sec` for each run, plus sum per task.
- **Constraint Satisfied:** whether final focus error is within task tolerance.

These values must come from real MATLAB for an end-to-end benchmark. Mock values
are permitted only in deterministic unit/workflow tests and cannot be entered as
scientific benchmark results.

## C. Workflow-efficiency metrics

- **MATLAB Calls / Task:** number of `run_focus_simulation` calls that actually
  launched MATLAB.
- **Tool Calls / Task:** domain tool calls; also report discovery/helper calls
  separately.
- **Replanning Count:** successful `refine_focus` calls per task.
- **End-to-End Runtime (s):** elapsed time from the natural-language request to
  the final Agent response.

The current Bridge uses a Python subprocess and a fresh `matlab -batch` process
for every run. Numerical computation is approximately one second in the
validated case, while cold-start overhead is tens of seconds. Report both
MATLAB `runtime_sec` and Bridge/end-to-end wall time; do not label their
difference as LLM reasoning time.

## Per-task record

An end-to-end result record should contain at least:

```text
task_id, task_type, agent_session_id
desired_target_mm, tolerance_mm, max_refinements
domain_tool_trajectory, invalid_tool_calls
final_actual_peak_mm, final_focus_error_mm, constraint_satisfied
matlab_calls, matlab_runtime_sec_total
tool_calls, replanning_count, end_to_end_runtime_sec
terminal_state, terminal_report_correct, task_success
```

Aggregate metrics must be reported overall and by Type A–D. A task whose
intended branch was not exercised (for example, a Type D task succeeds before
budget exhaustion) remains a valid execution but must be flagged as not
covering the intended failure-exit branch.
