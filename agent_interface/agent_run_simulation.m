function agent_run_simulation(config_path, result_path)
%AGENT_RUN_SIMULATION Execute JSON -> MATLAB focus simulation -> JSON.

started = tic;
task_id = "";
try
    validateattributes(config_path, {'char', 'string'}, {'scalartext'});
    validateattributes(result_path, {'char', 'string'}, {'scalartext'});
    config_path = string(config_path);
    result_path = string(result_path);
    if ~isfile(config_path)
        error('hermes:ConfigNotFound', ...
            'Configuration file does not exist: %s', config_path);
    end

    try
        config = jsondecode(fileread(config_path));
    catch exception
        error('hermes:JsonParseError', ...
            'Could not parse configuration JSON: %s', exception.message);
    end
    if ~isstruct(config) || ~isscalar(config)
        error('hermes:InvalidConfig', ...
            'Configuration JSON must decode to one object.');
    end
    if isfield(config, 'task_id')
        task_id = string(config.task_id);
        if ~isscalar(task_id) || ismissing(task_id)
            error('hermes:InvalidTaskId', 'task_id must be a scalar string.');
        end
    else
        error('hermes:MissingTaskId', 'Required field task_id is missing.');
    end
    if ~isfield(config, 'target_mm')
        error('hermes:MissingTarget', 'Required field target_mm is missing.');
    end
    target_mm = config.target_mm;
    if ~isnumeric(target_mm) || numel(target_mm) ~= 3 || ...
            ~isreal(target_mm) || any(~isfinite(target_mm), 'all')
        error('hermes:InvalidTarget', ...
            'target_mm must be a finite numeric [x,y,z] vector.');
    end

    interface_root = fileparts(mfilename('fullpath'));
    project_root = fileparts(interface_root);
    addpath(fullfile(project_root, 'matlab_core'), '-begin');
    core_result = run_focus_core(double(target_mm(:).'));
    payload = struct('status', "success", 'task_id', task_id, ...
        'requested_focus_mm', core_result.requested_focus_mm, ...
        'actual_peak_mm', core_result.actual_peak_mm, ...
        'peak_power', core_result.peak_power, ...
        'peak_power_definition', core_result.peak_power_definition, ...
        'requested_power', core_result.requested_power, ...
        'focus_error_mm', core_result.focus_error_mm, ...
        'runtime_sec', toc(started));
catch exception
    error_type = string(exception.identifier);
    if strlength(error_type) == 0
        error_type = string(class(exception));
    end
    payload = struct('status', "error", 'task_id', task_id, ...
        'error_type', error_type, 'message', string(exception.message), ...
        'runtime_sec', toc(started));
end

write_json_result(result_path, payload);
end

function write_json_result(result_path, payload)
result_path = string(result_path);
parent = string(fileparts(result_path));
if strlength(parent) == 0
    parent = string(pwd);
end
if ~isfolder(parent)
    mkdir(parent);
end
encoded = jsonencode(payload, 'PrettyPrint', true);
temporary_path = string(tempname(char(parent)))+".json.tmp";
[file_id, message] = fopen(temporary_path, 'w', 'n', 'UTF-8');
assert(file_id >= 0, 'hermes:ResultWriteError', ...
    'Could not open temporary result JSON: %s', message);
bytes_written = fprintf(file_id, '%s\n', encoded);
close_status = fclose(file_id);
if bytes_written <= 0 || close_status ~= 0
    if isfile(temporary_path), delete(temporary_path); end
    error('hermes:ResultWriteError', ...
        'Could not finish writing result JSON: %s', result_path);
end
[moved, move_message] = movefile(temporary_path, result_path, 'f');
if ~moved
    if isfile(temporary_path), delete(temporary_path); end
    error('hermes:ResultWriteError', ...
        'Could not publish result JSON: %s', move_message);
end
end
