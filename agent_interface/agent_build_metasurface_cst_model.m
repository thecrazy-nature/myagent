function agent_build_metasurface_cst_model(config_path, result_path)
%AGENT_BUILD_METASURFACE_CST_MODEL JSON interface for a CST layout scaffold.

started = tic;
build_id = "";
try
    validateattributes(config_path, {'char', 'string'}, {'scalartext'});
    validateattributes(result_path, {'char', 'string'}, {'scalartext'});
    config_path = string(config_path);
    result_path = string(result_path);
    if ~isfile(config_path)
        error('hermes:CstConfigNotFound', ...
            'Configuration file does not exist: %s', config_path);
    end
    try
        config = jsondecode(fileread(config_path));
    catch exception
        error('hermes:CstJsonParseError', ...
            'Could not parse CST configuration JSON: %s', exception.message);
    end
    required = ["build_id", "metasurface_task_id", "launch_cst", ...
        "cst_prog_id", "model_kind", "output_cst_path", "output_vba_path", ...
        "frequency_ghz", "focus_target_mm", "unit_size_mm", "array_size", ...
        "incident_wave", "horn_feed_position_mm", "binary_states", ...
        "element_positions_mm", "phase_codes"];
    for field = required
        if ~isfield(config, field)
            error('hermes:CstMissingField', ...
                'Required field %s is missing.', field);
        end
    end
    build_id = string(config.build_id);
    interface_root = fileparts(mfilename('fullpath'));
    project_root = fileparts(interface_root);
    addpath(fullfile(project_root, 'matlab_core', 'metasurface_design'), '-begin');
    payload = build_cst_layout_model_core(config);
    payload.status = "success";
    payload.build_id = build_id;
    payload.metasurface_task_id = string(config.metasurface_task_id);
    payload.matlab_version = string(version);
    payload.matlab_release = string(version('-release'));
    payload.matlab_arch = string(computer('arch'));
    payload.runtime_sec = toc(started);
catch exception
    error_type = string(exception.identifier);
    if strlength(error_type) == 0, error_type = string(class(exception)); end
    payload = struct('status', "error", 'build_id', build_id, ...
        'error_type', error_type, 'message', string(exception.message), ...
        'runtime_sec', toc(started));
end

write_json_result(result_path, payload);
end

function write_json_result(result_path, payload)
result_path = string(result_path);
parent = string(fileparts(result_path));
if strlength(parent) == 0, parent = string(pwd); end
if ~isfolder(parent), mkdir(parent); end
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
