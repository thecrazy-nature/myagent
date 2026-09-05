# Hermes EM Agent MATLAB core

This project is an independent extraction of the deterministic single-target,
carrier-frequency near-field focusing path from
`F:\matlabcode\multiuser`. It does not require the source project on the
MATLAB path and does not use mock data.

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
matlab -batch "addpath('F:/hermes-em-agent/agent_interface','-begin'); agent_run_simulation('F:/hermes-em-agent/tests/fixtures/config_valid.json','F:/hermes-em-agent/runs/result_task_001.json');"
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

See [AUDIT_REPORT.md](AUDIT_REPORT.md) for the source audit, numerical
comparison, exclusions, and gate status. See
[matlab_core/DEPENDENCIES.md](matlab_core/DEPENDENCIES.md) for the exact runtime
closure.
