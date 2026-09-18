function payload = test_json_interface()
%TEST_JSON_INTERFACE Run the real JSON -> MATLAB -> JSON path.

project_root = string(fileparts(fileparts(mfilename('fullpath'))));
restoredefaultpath;
addpath(fullfile(project_root, 'agent_interface'), '-begin');
config_path = fullfile(project_root, 'tests', 'fixtures', 'config_valid.json');
result_path = fullfile(project_root, 'runs', 'result_task_001.json');
agent_run_simulation(config_path, result_path);
payload = jsondecode(fileread(result_path));
if string(payload.status) ~= "success"
    error('JSON simulation did not succeed: %s', payload.message);
end
assert(all(isfinite([payload.actual_peak_mm(:); payload.peak_power])), ...
    'JSON result contains a non-finite simulation value.');
assert(~isfield(payload, 'focus_error_mm'), ...
    'MATLAB interface must not emit Agent-side focus-error evaluation.');
field_path = fullfile(fileparts(result_path), 'field_data.json');
field_payload = jsondecode(fileread(field_path));
assert(field_payload.schema_version == 2 && ...
    isfield(field_payload, 'normalized_power_yz_by_user') && ...
    isfield(field_payload, 'normalized_power_xy_by_user'), ...
    'JSON field artifact must contain the true YZ and XY focal cuts.');
fprintf('TEST_JSON_INTERFACE_PASS %s\n', result_path);
end
