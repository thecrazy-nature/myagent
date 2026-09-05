function report = test_error_inputs()
%TEST_ERROR_INPUTS Verify invalid target and missing JSON field errors.

project_root = string(fileparts(fileparts(mfilename('fullpath'))));
restoredefaultpath;
addpath(fullfile(project_root, 'matlab_core'), '-begin');
invalid_target_passed = false;
try
    run_focus_core([0, 1, 100]);
catch exception
    invalid_target_passed = exception.identifier == "hermes:TargetOutOfPlane";
end
assert(invalid_target_passed, 'Invalid out-of-plane target was not rejected.');

addpath(fullfile(project_root, 'agent_interface'), '-begin');
config_path = fullfile(project_root, 'tests', 'fixtures', ...
    'config_missing_target.json');
result_path = fullfile(project_root, 'runs', 'error_missing_target.json');
agent_run_simulation(config_path, result_path);
payload = jsondecode(fileread(result_path));
missing_field_passed = string(payload.status) == "error" && ...
    string(payload.error_type) == "hermes:MissingTarget";
assert(missing_field_passed, 'Missing target field was not reported correctly.');

invalid_dimension_path = fullfile(project_root, 'tests', 'fixtures', ...
    'config_invalid_dimension.json');
invalid_dimension_result = fullfile(project_root, 'runs', ...
    'error_invalid_dimension.json');
agent_run_simulation(invalid_dimension_path, invalid_dimension_result);
payload = jsondecode(fileread(invalid_dimension_result));
invalid_dimension_passed = string(payload.status) == "error" && ...
    string(payload.error_type) == "hermes:InvalidTarget";
assert(invalid_dimension_passed, ...
    'Invalid target dimension was not reported correctly.');

missing_config_path = fullfile(project_root, 'tests', 'fixtures', ...
    'does_not_exist.json');
missing_config_result = fullfile(project_root, 'runs', ...
    'error_missing_config.json');
agent_run_simulation(missing_config_path, missing_config_result);
payload = jsondecode(fileread(missing_config_result));
missing_config_passed = string(payload.status) == "error" && ...
    string(payload.error_type) == "hermes:ConfigNotFound";
assert(missing_config_passed, ...
    'Missing configuration file was not reported correctly.');

malformed_path = fullfile(project_root, 'tests', 'fixtures', ...
    'config_malformed.json');
malformed_result = fullfile(project_root, 'runs', 'error_malformed_json.json');
agent_run_simulation(malformed_path, malformed_result);
payload = jsondecode(fileread(malformed_result));
malformed_passed = string(payload.status) == "error" && ...
    string(payload.error_type) == "hermes:JsonParseError";
assert(malformed_passed, 'Malformed JSON was not reported correctly.');

out_of_range_path = fullfile(project_root, 'tests', 'fixtures', ...
    'config_out_of_range.json');
out_of_range_result = fullfile(project_root, 'runs', ...
    'error_out_of_range.json');
agent_run_simulation(out_of_range_path, out_of_range_result);
payload = jsondecode(fileread(out_of_range_result));
out_of_range_passed = string(payload.status) == "error" && ...
    string(payload.error_type) == "hermes:TargetOutOfRange";
assert(out_of_range_passed, 'Out-of-range target was not reported correctly.');

report = struct('passed', true, ...
    'invalid_target_rejected', invalid_target_passed, ...
    'missing_target_json_rejected', missing_field_passed, ...
    'invalid_target_dimension_rejected', invalid_dimension_passed, ...
    'missing_config_rejected', missing_config_passed, ...
    'malformed_json_rejected', malformed_passed, ...
    'out_of_range_target_rejected', out_of_range_passed);
fprintf('TEST_ERROR_INPUTS_PASS\n');
end
