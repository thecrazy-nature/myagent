function report = test_core_independent()
%TEST_CORE_INDEPENDENT Verify the core runs with only the extracted path.

project_root = string(fileparts(fileparts(mfilename('fullpath'))));
restoredefaultpath;
addpath(fullfile(project_root, 'matlab_core'), '-begin');
result = run_focus_core([0, 0, 100]);
resolved = string(which('synthesize_near_field_weights'));
expected_core_root = lower(string(fullfile(project_root, 'matlab_core')));
assert(startsWith(lower(resolved), expected_core_root), ...
    'A core dependency resolved outside the extracted project: %s', resolved);
assert(all(isfinite([result.actual_peak_mm, result.peak_power])), ...
    'Core result contains a non-finite value.');
report = struct('passed', true, 'target_mm', result.requested_focus_mm, ...
    'actual_peak_mm', result.actual_peak_mm, ...
    'peak_power', result.peak_power, 'resolved_dependency', resolved);
fprintf('TEST_CORE_INDEPENDENT_PASS\n');
end
