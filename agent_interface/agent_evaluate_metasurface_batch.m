function agent_evaluate_metasurface_batch(config_path, result_path)
%AGENT_EVALUATE_METASURFACE_BATCH JSON interface for programmable surfaces.

started = tic;
batch_id = "";
try
    validateattributes(config_path, {'char', 'string'}, {'scalartext'});
    validateattributes(result_path, {'char', 'string'}, {'scalartext'});
    config_path = string(config_path);
    result_path = string(result_path);
    if ~isfile(config_path)
        error('hermes:MetasurfaceConfigNotFound', ...
            'Configuration file does not exist: %s', config_path);
    end
    try
        config = jsondecode(fileread(config_path));
    catch exception
        error('hermes:MetasurfaceJsonParseError', ...
            'Could not parse configuration JSON: %s', exception.message);
    end
    required = ["batch_id", "metasurface_task_id", "focus_target_mm", ...
        "focus_tolerance_mm", "frequency_hz", ...
        "surface_geometry", "unit_size_mm", "array_size", ...
        "incident_wave", "horn_feed_position_mm", "binary_states", ...
        "roi_radius_mm", "roi_half_depth_mm", "candidates"];
    for field = required
        if ~isfield(config, field)
            error('hermes:MetasurfaceMissingField', ...
                'Required field %s is missing.', field);
        end
    end
    batch_id = string(config.batch_id);
    interface_root = fileparts(mfilename('fullpath'));
    project_root = fileparts(interface_root);
    addpath(fullfile(project_root, 'matlab_core'), '-begin');
    addpath(fullfile(project_root, 'matlab_core', 'metasurface_design'), '-begin');
    results = design_planar_metasurface_batch_core(config);
    payload = struct('status', "success", 'batch_id', batch_id, ...
        'metasurface_task_id', string(config.metasurface_task_id), ...
        'results', results, 'matlab_version', string(version), ...
        'matlab_release', string(version('-release')), ...
        'matlab_arch', string(computer('arch')), ...
        'rng_algorithm', "twister", 'runtime_sec', toc(started));
catch exception
    error_type = string(exception.identifier);
    if strlength(error_type) == 0, error_type = string(class(exception)); end
    payload = struct('status', "error", 'batch_id', batch_id, ...
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
