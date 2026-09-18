function results = design_planar_metasurface_batch_core(config)
%DESIGN_PLANAR_METASURFACE_BATCH_CORE Evaluate and optimize a binary transmitarray.
% The forward model is a scalar point-transmission approximation. State 0/1
% amplitudes and phases are explicit inputs. Geometry, incident field,
% geometrical-optics coding, binary local search, and all metrics are MATLAB
% calculations; the LLM only selects bounded workflow parameters.

initialize_paths();
target_mm = double(config.focus_target_mm(:).');
validateattributes(target_mm, {'numeric'}, ...
    {'real', 'finite', 'numel', 3}, mfilename, 'focus_target_mm');
assert(target_mm(3) > 0, 'hermes:MetasurfaceTargetOutOfRange', ...
    'The focus target must be above the z=0 transmissive surface.');
frequency_hz = positive_scalar(config.frequency_hz, 'frequency_hz');
assert(frequency_hz >= 1e9 && frequency_hz <= 100e9, ...
    'hermes:MetasurfaceFrequencyRange', ...
    'frequency_hz must be in [1e9, 100e9].');
assert(string(config.surface_geometry) == "planar", ...
    'hermes:MetasurfaceGeometry', 'Only a planar surface is supported.');
array_size = double(config.array_size(:).');
validateattributes(array_size, {'numeric'}, ...
    {'real', 'finite', 'integer', 'numel', 2, '>=', 4, '<=', 40}, ...
    mfilename, 'array_size');
assert(prod(array_size) <= 1600, 'hermes:MetasurfaceElementCount', ...
    'The binary surface may contain at most 1600 cells.');
unit_size_mm = double(config.unit_size_mm(:).');
validateattributes(unit_size_mm, {'numeric'}, ...
    {'real', 'finite', 'positive', 'numel', 3}, mfilename, 'unit_size_mm');
tolerance_mm = positive_scalar(config.focus_tolerance_mm, 'focus_tolerance_mm');
roi_radius_mm = positive_scalar(config.roi_radius_mm, 'roi_radius_mm');
roi_half_depth_mm = positive_scalar(config.roi_half_depth_mm, 'roi_half_depth_mm');

propagation_speed = 3e8;
lambda_mm = propagation_speed/frequency_hz*1000;
array = build_planar_array(array_size, unit_size_mm);
incident = incident_field(config, array, frequency_hz, propagation_speed);
state_coefficients = binary_state_coefficients(config.binary_states);
target_operator = build_harmonic_weight_operator( ...
    target_mm/1000, array, frequency_hz, propagation_speed);
target_operator = target_operator{1};
desired_transmission = exp(-1i*angle(target_operator(:).*incident));
continuous_weights = incident.*desired_transmission;
go_codes = nearest_binary_codes(desired_transmission, state_coefficients);
go_transmission = reshape(state_coefficients(go_codes+1), [], 1);

model = struct('array', array, 'frequency_hz', frequency_hz, ...
    'propagation_speed', propagation_speed, 'lambda_mm', lambda_mm, ...
    'incident', incident, 'state_coefficients', state_coefficients, ...
    'desired_transmission', desired_transmission, ...
    'target_mm', target_mm, 'unit_size_mm', unit_size_mm);

candidates = config.candidates;
if isempty(candidates)
    results = struct([]);
    return;
end
results = repmat(empty_result(), numel(candidates), 1);
for index = 1:numel(candidates)
    candidate_started = tic;
    candidate = candidates(index);
    result = empty_result();
    result.candidate_id = string(candidate.candidate_id);
    result.mode = string(candidate.mode);
    try
        mode = string(candidate.mode);
        max_iterations = double(candidate.max_iterations);
        guard_weight = double(candidate.guard_weight);
        seed = double(candidate.seed);
        validateattributes(seed, {'numeric'}, ...
            {'scalar', 'integer', 'nonnegative'});
        rng(seed, 'twister');
        iterations_used = 0;
        optimizer = "none";
        if mode == "unprogrammed"
            codes = zeros(array.count, 1);
            transmission = repmat(state_coefficients(1), array.count, 1);
        elseif mode == "continuous_phase_conjugate"
            codes = zeros(0, 1);
            transmission = desired_transmission;
        elseif mode == "geometrical_optics_binary"
            codes = go_codes;
            transmission = go_transmission;
        elseif mode == "optimized_binary"
            validateattributes(max_iterations, {'numeric'}, ...
                {'scalar', 'integer', '>=', 1, '<=', 8});
            validateattributes(guard_weight, {'numeric'}, ...
                {'scalar', 'real', 'finite', '>=', 0, '<=', 2});
            if isfield(candidate, 'optimizer') && ~isempty(candidate.optimizer)
                optimizer = string(candidate.optimizer);
            else
                optimizer = "binary_coordinate_descent_v1";
            end
            assert(optimizer == "binary_coordinate_descent_v1", ...
                'hermes:MetasurfaceOptimizer', ...
                'Unsupported binary optimizer: %s.', optimizer);
            [transmission, codes, iterations_used] = optimize_binary_codes( ...
                model, go_codes, max_iterations, guard_weight, seed);
        else
            error('hermes:MetasurfaceMode', ...
                'Unsupported metasurface candidate mode: %s.', mode);
        end

        weights = incident.*transmission;
        metrics = evaluate_weights(weights, transmission, codes, model, ...
            tolerance_mm, roi_radius_mm, roi_half_depth_mm);
        result.status = "success";
        result.optimizer = optimizer;
        result.guard_weight = guard_weight;
        result.max_iterations = max_iterations;
        result.iterations_used = iterations_used;
        result.seed = seed;
        result.phase_codes = codes(:).';
        result.phase_deg = mod(angle(transmission(:).')*180/pi, 360);
        result.transmission_amplitudes = abs(transmission(:).');
        result.element_positions_mm = 1000*[array.x, array.y, array.z];
        result.input_power_proxy = sum(abs(incident).^2, 'all');
        result.transmitted_power_proxy = sum(abs(weights).^2, 'all');
        result.transmission_efficiency_proxy = ...
            result.transmitted_power_proxy/max(result.input_power_proxy, eps);
        result.command_target_mm = target_mm;
        metric_fields = fieldnames(metrics);
        for field_index = 1:numel(metric_fields)
            field = metric_fields{field_index};
            result.(field) = metrics.(field);
        end
        result.matlab_runtime_sec = toc(candidate_started);
    catch exception
        result.status = "error";
        result.error_type = string(exception.identifier);
        result.message = string(exception.message);
        result.matlab_runtime_sec = toc(candidate_started);
    end
    results(index) = result;
end
end

function array = build_planar_array(array_size, unit_size_mm)
nx = array_size(1);
ny = array_size(2);
x_position_mm = ((1:nx)-nx/2-0.5)*unit_size_mm(1);
y_position_mm = ((1:ny)-ny/2-0.5)*unit_size_mm(2);
[x_element_mm, y_element_mm] = meshgrid(x_position_mm, y_position_mm);
positions_m = [x_element_mm(:), y_element_mm(:), ...
    zeros(numel(x_element_mm), 1)]/1000;
normals = repmat([0, 0, 1], size(positions_m, 1), 1);
array = struct('x', positions_m(:, 1), 'y', positions_m(:, 2), ...
    'z', positions_m(:, 3), 'normal_x', normals(:, 1), ...
    'normal_y', normals(:, 2), 'normal_z', normals(:, 3), ...
    'count', size(positions_m, 1), 'geometry', "planar", ...
    'element_model', "scalar_point_transmission", ...
    'element_pattern', "isotropic", 'pattern_exponent', 1);
end

function incident = incident_field(config, array, frequency_hz, propagation_speed)
wave = string(config.incident_wave);
if wave == "plane_wave"
    incident = complex(ones(array.count, 1));
    return;
end
assert(wave == "horn_spherical_wave", 'hermes:MetasurfaceIncidentWave', ...
    'incident_wave must be plane_wave or horn_spherical_wave.');
feed_mm = double(config.horn_feed_position_mm(:).');
validateattributes(feed_mm, {'numeric'}, ...
    {'real', 'finite', 'numel', 3}, mfilename, 'horn_feed_position_mm');
assert(feed_mm(3) < 0, 'hermes:MetasurfaceFeedPosition', ...
    'The horn phase center must be below the z=0 surface.');
dx = array.x-feed_mm(1)/1000;
dy = array.y-feed_mm(2)/1000;
dz = array.z-feed_mm(3)/1000;
distance = sqrt(dx.^2+dy.^2+dz.^2);
assert(all(distance > eps), 'hermes:MetasurfaceFeedSingularity', ...
    'The horn phase center coincides with a cell.');
k = 2*pi*frequency_hz/propagation_speed;
incident = exp(-1i*k*distance)./distance;
incident = incident/sqrt(mean(abs(incident).^2));
end

function coefficients = binary_state_coefficients(binary_states)
states = {binary_states.state_0, binary_states.state_1};
coefficients = complex(zeros(1, 2));
for index = 1:2
    amplitude = double(states{index}.amplitude);
    phase_deg = double(states{index}.phase_deg);
    validateattributes(amplitude, {'numeric'}, ...
        {'scalar', 'real', 'finite', '>=', 0, '<=', 1});
    validateattributes(phase_deg, {'numeric'}, ...
        {'scalar', 'real', 'finite'});
    coefficients(index) = amplitude*exp(1i*phase_deg*pi/180);
end
assert(any(abs(coefficients) > 0), 'hermes:MetasurfaceBinaryStates', ...
    'At least one binary state must transmit nonzero amplitude.');
end

function codes = nearest_binary_codes(desired, state_coefficients)
phase_error = zeros(numel(desired), 2);
for state_index = 1:2
    phase_error(:, state_index) = abs(angle( ...
        state_coefficients(state_index).*conj(desired)));
end
[~, best] = min(phase_error, [], 2);
codes = best-1;
end

function [transmission, codes, iterations_used] = optimize_binary_codes( ...
    model, initial_codes, max_iterations, guard_weight, seed)
codes = initial_codes;
states = model.state_coefficients;
transmission = reshape(states(codes+1), [], 1);
guard_points_mm = build_guard_points(model.target_mm, model.lambda_mm);
operators = build_harmonic_weight_operator(guard_points_mm/1000, ...
    model.array, model.frequency_hz, model.propagation_speed);
response = operators{1}.*model.incident.';
scale = max(vecnorm(response(1, :), 2, 2), eps);
response = response/scale;
field = response*transmission;
[probe_operator, probe_points_mm, target_probe_index] = ...
    build_focus_probe(model);
[best_focus_error, best_target_power] = focus_probe_score( ...
    transmission, probe_operator, probe_points_mm, ...
    target_probe_index, model.target_mm);
best_transmission = transmission;
best_codes = codes;
iterations_used = 0;
rng(seed, 'twister');
for iteration = 1:max_iterations
    changed = false;
    order = randperm(model.array.count);
    for order_index = 1:numel(order)
        element = order(order_index);
        without = field-response(:, element)*transmission(element);
        scores = zeros(2, 1);
        for state_index = 1:2
            trial = without+response(:, element)*states(state_index);
            scores(state_index) = abs(trial(1)).^2 ...
                -guard_weight*mean(abs(trial(2:end)).^2);
        end
        [~, best] = max(scores);
        new_code = best-1;
        if new_code ~= codes(element)
            codes(element) = new_code;
            transmission(element) = states(best);
            field = without+response(:, element)*transmission(element);
            changed = true;
        end
    end
    iterations_used = iteration;
    [focus_error, target_power] = focus_probe_score( ...
        transmission, probe_operator, probe_points_mm, ...
        target_probe_index, model.target_mm);
    if focus_error < best_focus_error-1e-9 || ...
            (abs(focus_error-best_focus_error) <= 1e-9 && ...
            target_power > best_target_power)
        best_focus_error = focus_error;
        best_target_power = target_power;
        best_transmission = transmission;
        best_codes = codes;
    else
        transmission = best_transmission;
        codes = best_codes;
        field = response*transmission;
    end
    if ~changed, break; end
end
transmission = best_transmission;
codes = best_codes;
end

function points_mm = build_guard_points(target_mm, lambda_mm)
offsets = [ ...
    lambda_mm, 0, 0; -lambda_mm, 0, 0; ...
    0, lambda_mm, 0; 0, -lambda_mm, 0; ...
    0, 0, lambda_mm; 0, 0, -lambda_mm; ...
    lambda_mm, lambda_mm, 0; -lambda_mm, -lambda_mm, 0];
guards = target_mm+offsets;
guards = guards(guards(:, 3) > 0, :);
points_mm = [target_mm; guards];
end

function [operator, points_mm, target_index] = build_focus_probe(model)
[x_mm, z_mm] = evaluation_axes(model);
x_mm = linspace(x_mm(1), x_mm(end), 41);
z_mm = linspace(z_mm(1), z_mm(end), 41);
[x_grid_mm, z_grid_mm] = meshgrid(x_mm, z_mm);
points_mm = [x_grid_mm(:), ...
    repmat(model.target_mm(2), numel(x_grid_mm), 1), z_grid_mm(:)];
operators = build_harmonic_weight_operator(points_mm/1000, ...
    model.array, model.frequency_hz, model.propagation_speed);
operator = operators{1}.*model.incident.';
[~, target_index] = min(vecnorm(points_mm-model.target_mm, 2, 2));
end

function [focus_error_mm, target_power] = focus_probe_score( ...
    transmission, operator, points_mm, target_index, target_mm)
power = abs(operator*transmission).^2;
[~, peak_index] = max(power);
focus_error_mm = norm(points_mm(peak_index, :)-target_mm);
target_power = power(target_index);
end

function metrics = evaluate_weights(weights, transmission, codes, model, ...
    tolerance_mm, roi_radius_mm, roi_half_depth_mm)
[x_mm, z_mm] = evaluation_axes(model);
[x_grid_mm, z_grid_mm] = meshgrid(x_mm, z_mm);
points_mm = [x_grid_mm(:), ...
    repmat(model.target_mm(2), numel(x_grid_mm), 1), z_grid_mm(:)];
operators = build_harmonic_weight_operator(points_mm/1000, ...
    model.array, model.frequency_hz, model.propagation_speed);
field = operators{1}*weights;
raw_power = reshape(abs(field).^2, size(x_grid_mm));
[global_peak_power, global_index] = max(raw_power, [], 'all', 'linear');
[peak_iz, peak_ix] = ind2sub(size(raw_power), global_index);
actual_peak_mm = [x_mm(peak_ix), model.target_mm(2), z_mm(peak_iz)];
focus_error_mm = norm(actual_peak_mm-model.target_mm);
target_operator = build_harmonic_weight_operator( ...
    model.target_mm/1000, model.array, model.frequency_hz, ...
    model.propagation_speed);
target_power = abs(target_operator{1}*weights).^2;
peak_power = raw_power(peak_iz, peak_ix);
lateral = raw_power(peak_iz, :)/max(peak_power, eps);
axial = raw_power(:, peak_ix).'/max(peak_power, eps);
[fwhm_x_mm, lateral_bounded] = safe_half_power_width(x_mm, lateral, peak_ix);
[dof_z_mm, axial_bounded] = safe_half_power_width(z_mm, axial, peak_iz);
energy_ratio = xz_energy_concentration(raw_power, x_mm, z_mm, ...
    model.target_mm, roi_radius_mm, roi_half_depth_mm);
pslr = peak_to_sidelobe_ratio(raw_power, peak_ix, peak_iz, ...
    x_mm, z_mm, fwhm_x_mm, dof_z_mm);
normalized = raw_power/max(global_peak_power, eps);
phase_error = angle(transmission.*conj(model.desired_transmission));
if isempty(codes)
    phase_error_rms_deg = 0;
else
    phase_error_rms_deg = sqrt(mean(phase_error.^2))*180/pi;
end
metrics = struct( ...
    'actual_peak_mm', actual_peak_mm, ...
    'focus_error_mm', focus_error_mm, ...
    'focus_constraint_satisfied', focus_error_mm <= tolerance_mm, ...
    'target_power', target_power, ...
    'peak_power', peak_power, ...
    'global_peak_power', global_peak_power, ...
    'global_peak_mm', actual_peak_mm, ...
    'target_to_global_db', 10*log10(max(target_power, eps)/max(global_peak_power, eps)), ...
    'fwhm_x_mm', fwhm_x_mm, 'dof_z_mm', dof_z_mm, ...
    'fwhm_bounded', lateral_bounded, 'dof_bounded', axial_bounded, ...
    'energy_concentration_ratio', energy_ratio, ...
    'peak_to_sidelobe_ratio_db', pslr, ...
    'phase_error_rms_deg', phase_error_rms_deg, ...
    'field_plane_y_mm', model.target_mm(2), ...
    'x_mm', x_mm, 'z_mm', z_mm, ...
    'normalized_power_xz', normalized, ...
    'lateral_profile', struct('position_mm', x_mm, ...
        'normalized_power', lateral), ...
    'axial_profile', struct('position_mm', z_mm, ...
        'normalized_power', axial));
end

function [x_mm, z_mm] = evaluation_axes(model)
aperture_x_mm = (max(model.array.x)-min(model.array.x))*1000 ...
    +model.unit_size_mm(1);
span_x_mm = max([3*model.lambda_mm, aperture_x_mm/2, 1]);
span_z_mm = max([3*model.lambda_mm, 0.35*model.target_mm(3), 1]);
x_mm = linspace(model.target_mm(1)-span_x_mm, ...
    model.target_mm(1)+span_x_mm, 201);
z_lower = max(0.25*model.lambda_mm, model.target_mm(3)-span_z_mm);
z_mm = linspace(z_lower, model.target_mm(3)+span_z_mm, 201);
end

function [width, bounded] = safe_half_power_width(coordinates, power, peak_index)
left = peak_index;
while left > 1 && power(left-1) >= 0.5, left = left-1; end
right = peak_index;
while right < numel(power) && power(right+1) >= 0.5, right = right+1; end
bounded = left > 1 && right < numel(power);
if bounded
    left_cross = interpolate_crossing(coordinates(left-1:left), power(left-1:left));
    right_cross = interpolate_crossing(coordinates(right:right+1), power(right:right+1));
    width = right_cross-left_cross;
else
    width = coordinates(end)-coordinates(1);
end
end

function crossing = interpolate_crossing(x, y)
if abs(y(2)-y(1)) <= eps
    crossing = mean(x);
else
    crossing = x(1)+(0.5-y(1))*(x(2)-x(1))/(y(2)-y(1));
end
end

function ratio = xz_energy_concentration( ...
    power, x_mm, z_mm, target_mm, radius_mm, half_depth_mm)
x_mask = abs(x_mm-target_mm(1)) <= radius_mm;
z_mask = abs(z_mm-target_mm(3)) <= half_depth_mm;
assert(nnz(x_mask) >= 2 && nnz(z_mask) >= 2, ...
    'hermes:MetasurfaceRoiTooSmall', ...
    'ROI must include at least two samples per axis.');
total = trapz(z_mm, trapz(x_mm, power, 2));
roi = trapz(z_mm(z_mask), trapz(x_mm(x_mask), power(z_mask, x_mask), 2));
ratio = min(max(roi/max(total, eps), 0), 1);
end

function ratio_db = peak_to_sidelobe_ratio( ...
    power, peak_ix, peak_iz, x_mm, z_mm, width_mm, depth_mm)
x_main = abs(x_mm-x_mm(peak_ix)) <= max(width_mm/2, mean(diff(x_mm)));
z_main = abs(z_mm-z_mm(peak_iz)) <= max(depth_mm/2, mean(diff(z_mm)));
outside = true(size(power));
outside(z_main, x_main) = false;
outside_values = power(outside);
if isempty(outside_values)
    sidelobe = eps;
else
    sidelobe = max(outside_values, [], 'all');
end
ratio_db = 10*log10(max(power(peak_iz, peak_ix), eps)/max(sidelobe, eps));
end

function result = empty_result()
result = struct('status', "", 'candidate_id', "", 'mode', "", ...
    'optimizer', "", 'guard_weight', [], 'max_iterations', [], ...
    'iterations_used', [], 'seed', [], 'phase_codes', [], ...
    'phase_deg', [], 'transmission_amplitudes', [], ...
    'element_positions_mm', [], 'input_power_proxy', [], ...
    'transmitted_power_proxy', [], 'transmission_efficiency_proxy', [], ...
    'command_target_mm', [], 'actual_peak_mm', [], 'focus_error_mm', [], ...
    'focus_constraint_satisfied', false, 'target_power', [], ...
    'peak_power', [], 'global_peak_power', [], 'global_peak_mm', [], ...
    'target_to_global_db', [], 'fwhm_x_mm', [], 'dof_z_mm', [], ...
    'fwhm_bounded', false, 'dof_bounded', false, ...
    'energy_concentration_ratio', [], 'peak_to_sidelobe_ratio_db', [], ...
    'phase_error_rms_deg', [], 'field_plane_y_mm', [], ...
    'x_mm', [], 'z_mm', [], 'normalized_power_xz', [], ...
    'lateral_profile', struct(), 'axial_profile', struct(), ...
    'matlab_runtime_sec', [], 'error_type', "", 'message', "");
end

function value = positive_scalar(value, name)
value = double(value);
validateattributes(value, {'numeric'}, ...
    {'real', 'finite', 'scalar', 'positive'}, mfilename, name);
end

function initialize_paths()
root = fileparts(fileparts(mfilename('fullpath')));
folders = {root, fullfile(root, 'core'), fullfile(root, 'solver'), ...
    fullfile(root, 'evaluation'), fullfile(root, 'config')};
for index = 1:numel(folders), addpath(folders{index}, '-begin'); end
end
