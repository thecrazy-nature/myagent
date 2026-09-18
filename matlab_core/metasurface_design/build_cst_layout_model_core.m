function result = build_cst_layout_model_core(config)
%BUILD_CST_LAYOUT_MODEL_CORE Generate and optionally execute a CST layout macro.
% This deliberately creates a control-code layout scaffold, not a validated
% electromagnetic unit-cell model. Materials, stack-up, ports and boundaries
% require researcher-supplied physical definitions before full-wave solving.

validateattributes(config.launch_cst, {'logical', 'numeric'}, {'scalar'});
assert(string(config.model_kind) == "layout_scaffold", ...
    'hermes:CstUnsupportedModelKind', ...
    'Only layout_scaffold is currently supported.');
positions = double(config.element_positions_mm);
codes = double(config.phase_codes(:));
unit_size = double(config.unit_size_mm(:).');
array_size = double(config.array_size(:).');
target = double(config.focus_target_mm(:).');
validateattributes(positions, {'numeric'}, {'2d', 'ncols', 3, 'finite', 'real'});
validateattributes(codes, {'numeric'}, {'vector', 'finite', 'real', 'integer', '>=', 0, '<=', 1});
validateattributes(unit_size, {'numeric'}, {'vector', 'numel', 3, 'positive', 'finite'});
validateattributes(array_size, {'numeric'}, {'vector', 'numel', 2, 'integer', 'positive'});
validateattributes(target, {'numeric'}, {'vector', 'numel', 3, 'finite', 'real'});
assert(size(positions, 1) == numel(codes), 'hermes:CstCodeCountMismatch', ...
    'phase_codes must contain one binary state for every element position.');
assert(prod(array_size) == numel(codes), 'hermes:CstArraySizeMismatch', ...
    'array_size does not match the number of control codes.');

history = compose_history(config, positions, codes, unit_size, target);
vba_path = string(config.output_vba_path);
vba_parent = string(fileparts(vba_path));
if strlength(vba_parent) > 0 && ~isfolder(vba_parent), mkdir(vba_parent); end
write_text_file(vba_path, history);

launch_requested = logical(config.launch_cst);
launched = false;
project_saved = false;
launch_error_type = "";
launch_error_message = "";
cst_path = string(config.output_cst_path);
if launch_requested
    if ~ispc
        launch_error_type = "hermes:CstRequiresWindows";
        launch_error_message = ...
            "CST OLE automation is supported only on Windows.";
    else
        try
            prog_id = char(string(config.cst_prog_id));
            try
                app = actxGetRunningServer(prog_id);
            catch
                app = actxserver(prog_id);
            end
            cleanup_app = onCleanup(@()release_com(app)); %#ok<NASGU>
            project = invoke(app, 'NewMWS');
            cleanup_project = onCleanup(@()release_com(project)); %#ok<NASGU>
            invoke(project, 'AddToHistory', ...
                'Hermes binary transmitarray layout scaffold', char(history));
            invoke(project, 'SaveAs', char(cst_path), false);
            launched = true;
            project_saved = isfile(cst_path);
        catch exception
            launch_error_type = string(exception.identifier);
            if strlength(launch_error_type) == 0
                launch_error_type = string(class(exception));
            end
            launch_error_message = string(exception.message);
            % The VBA artifact is already durable. CST availability is an
            % optional downstream integration and must not invalidate the
            % completed MATLAB design/control-code workflow.
        end
    end
end

result = struct( ...
    'model_kind', "layout_scaffold", ...
    'scientific_status', "layout_only_not_full_wave_validated", ...
    'cst_prog_id', string(config.cst_prog_id), ...
    'cst_launch_requested', launch_requested, ...
    'cst_launched', launched, ...
    'cst_project_saved', project_saved, ...
    'launch_error_type', launch_error_type, ...
    'launch_error_message', launch_error_message, ...
    'launch_retry_supported', ~launched, ...
    'cst_project_path', cst_path, ...
    'vba_history_path', vba_path, ...
    'cell_count', numel(codes), ...
    'state_0_count', sum(codes == 0), ...
    'state_1_count', sum(codes == 1), ...
    'message', result_message(launch_requested, launched));
end

function message = result_message(launch_requested, launched)
if launched
    message = "Created the binary control-code layout and submitted it to CST.";
elseif launch_requested
    message = "Created the durable VBA layout, but CST OLE startup failed. The design remains valid and CST launch may be retried.";
else
    message = "Created the durable binary control-code VBA layout without launching CST.";
end
message = message+" A physical unit-cell stack-up, materials, ports and solver settings are still required for full-wave validation.";
end

function history = compose_history(config, positions, codes, unit_size, target)
lines = strings(0, 1);
lines(end+1) = "' HERMES CONTROL-CODE LAYOUT ONLY - DO NOT USE AS A FULL-WAVE UNIT MODEL";
lines(end+1) = "' Binary state geometry is represented by labeled vacuum bricks.";
lines(end+1) = "With Units";
lines(end+1) = "    .Geometry ""mm""";
lines(end+1) = "    .Frequency ""GHz""";
lines(end+1) = "    .Time ""ns""";
lines(end+1) = "End With";
lines(end+1) = sprintf('StoreParameter "Hermes_frequency_GHz", "%.15g"', double(config.frequency_ghz));
lines(end+1) = sprintf('StoreParameter "Hermes_focus_x_mm", "%.15g"', target(1));
lines(end+1) = sprintf('StoreParameter "Hermes_focus_y_mm", "%.15g"', target(2));
lines(end+1) = sprintf('StoreParameter "Hermes_focus_z_mm", "%.15g"', target(3));
lines(end+1) = sprintf('StoreParameter "Hermes_cell_dx_mm", "%.15g"', unit_size(1));
lines(end+1) = sprintf('StoreParameter "Hermes_cell_dy_mm", "%.15g"', unit_size(2));
lines(end+1) = sprintf('StoreParameter "Hermes_cell_dz_mm", "%.15g"', unit_size(3));
lines(end+1) = "Component.New ""binary_state_0""";
lines(end+1) = "Component.New ""binary_state_1""";
half_x = 0.45 * unit_size(1);
half_y = 0.45 * unit_size(2);
half_z = 0.5 * unit_size(3);
for index = 1:numel(codes)
    code = codes(index);
    component = sprintf('binary_state_%d', code);
    name = sprintf('cell_%04d_s%d', index, code);
    lines(end+1) = "With Brick";
    lines(end+1) = "    .Reset";
    lines(end+1) = sprintf('    .Name "%s"', name);
    lines(end+1) = sprintf('    .Component "%s"', component);
    lines(end+1) = "    .Material ""Vacuum""";
    lines(end+1) = sprintf('    .Xrange "%.15g", "%.15g"', positions(index, 1)-half_x, positions(index, 1)+half_x);
    lines(end+1) = sprintf('    .Yrange "%.15g", "%.15g"', positions(index, 2)-half_y, positions(index, 2)+half_y);
    lines(end+1) = sprintf('    .Zrange "%.15g", "%.15g"', positions(index, 3)-half_z, positions(index, 3)+half_z);
    lines(end+1) = "    .Create";
    lines(end+1) = "End With";
end
history = join(lines, newline);
end

function write_text_file(path, content)
[file_id, message] = fopen(path, 'w', 'n', 'UTF-8');
assert(file_id >= 0, 'hermes:CstVbaWriteError', ...
    'Could not open CST VBA output: %s', message);
cleanup_file = onCleanup(@()fclose(file_id)); %#ok<NASGU>
bytes_written = fprintf(file_id, '%s\n', content);
assert(bytes_written > 0, 'hermes:CstVbaWriteError', ...
    'Could not write CST VBA output: %s', path);
end

function release_com(handle)
try
    delete(handle);
catch
end
end
