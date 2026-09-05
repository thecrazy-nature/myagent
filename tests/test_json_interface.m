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
fprintf('TEST_JSON_INTERFACE_PASS %s\n', result_path);
end
