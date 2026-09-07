function results = evaluate_array_geometry_batch_core(config)
%EVALUATE_ARRAY_GEOMETRY_BATCH_CORE Evaluate real fields for N geometries.
% This independent design path preserves the established run_focus_core API.

initialize_paths();
target_mm = double(config.target_mm(:).');
validateattributes(target_mm, {'numeric'}, {'real', 'finite', 'numel', 3});
assert(abs(target_mm(2)) <= 1e-9, 'hermes:ArrayDesignOutOfPlane', ...
    'The current design evaluator supports only the XZ plane (y=0).');
tolerance_mm = positive_scalar(config.focus_tolerance_mm, 'focus_tolerance_mm');
roi_radius_mm = positive_scalar(config.roi_radius_mm, 'roi_radius_mm');
roi_half_depth_mm = positive_scalar(config.roi_half_depth_mm, 'roi_half_depth_mm');

cfg = focusing_library_config("planar");
cfg.physics.harmonics = 0;
cfg.objective.frequency_weights = 1;
cfg.evaluation.resolution = 201;
lambda_m = cfg.physics.c/cfg.physics.carrier_frequency;
lambda_mm = lambda_m*1000;
cfg.targets.by_harmonic_lambda = {[target_mm(1)/lambda_mm, target_mm(3)/lambda_mm]};
baseline_model = build_multiuser_model(cfg);
baseline_input_power = sum(abs(baseline_model.carrier_weights).^2, 'all');

geometries = config.geometries;
if isempty(geometries)
    results = struct([]);
    return;
end
results = repmat(empty_result(), numel(geometries), 1);
for index = 1:numel(geometries)
    geometry_started = tic;
    geometry = geometries(index);
    result = empty_result();
    result.geometry_id = string(geometry.geometry_id);
    result.family = string(geometry.family);
    try
        positions_mm = double(geometry.element_positions_mm);
        normals = double(geometry.element_normals);
        [geometry_ok, geometry_check] = validate_geometry(positions_mm, config, baseline_model);
        if ~geometry_ok
            error('hermes:ArrayDesignConstraintViolation', ...
                'Candidate violates element-count, aperture, spacing, or depth constraint.');
        end
        array = baseline_model.array;
        array.x = positions_mm(:, 1)/1000;
        array.y = positions_mm(:, 2)/1000;
        array.z = positions_mm(:, 3)/1000;
        array.normal_x = normals(:, 1);
        array.normal_y = normals(:, 2);
        array.normal_z = normals(:, 3);
        array.count = size(positions_mm, 1);
        array.geometry = string(geometry.family);
        array.configuration.signature = string(geometry.geometry_id);

        target_m = target_mm/1000;
        weights = synthesize_near_field_weights(array, target_m, ...
            2*pi*cfg.physics.carrier_frequency/cfg.physics.c, cfg);
        candidate_power = sum(abs(weights).^2, 'all');
        weights = weights*sqrt(baseline_input_power/max(candidate_power, eps));
        input_power = sum(abs(weights).^2, 'all');

        model = baseline_model;
        model.array = array;
        model.carrier_weights = weights;
        maps = compute_harmonic_weight_maps(weights, model, ...
            struct('resolution', cfg.evaluation.resolution));
        raw_power = maps.raw_power_absolute(:, :, 1);
        [peak_ix, peak_iz] = local_peak_indices(raw_power, maps, target_mm, ...
            cfg.evaluation.local_peak_radius_lambda, lambda_mm);
        actual_peak_mm = [maps.x_lambda(peak_ix)*lambda_mm, 0, ...
            maps.z_lambda(peak_iz)*lambda_mm];
        focus_error_mm = norm(actual_peak_mm-target_mm);
        peak_power = raw_power(peak_iz, peak_ix);
        lateral = raw_power(peak_iz, :)/max(peak_power, eps);
        axial = raw_power(:, peak_ix).'/max(peak_power, eps);
        x_mm = maps.x_lambda*lambda_mm;
        z_mm = maps.z_lambda*lambda_mm;
        fwhm_x_mm = half_power_width(x_mm, lateral, peak_ix);
        dof_z_mm = half_power_width(z_mm, axial, peak_iz);
        energy_ratio = xz_energy_concentration(raw_power, x_mm, z_mm, ...
            target_mm, roi_radius_mm, roi_half_depth_mm);
        assert(all(isfinite([focus_error_mm, fwhm_x_mm, dof_z_mm, ...
            energy_ratio, peak_power, input_power])), ...
            'hermes:ArrayDesignNonFiniteMetric', 'A computed metric is non-finite.');

        result.status = "success";
        result.actual_peak_mm = actual_peak_mm;
        result.focus_error_mm = focus_error_mm;
        result.fwhm_x_mm = fwhm_x_mm;
        result.lateral_metric_axis = "x in the simulated XZ plane";
        result.dof_z_mm = dof_z_mm;
        result.energy_concentration_ratio = energy_ratio;
        result.energy_concentration_definition = ...
            "2D XZ evaluation-area integral inside desired-target ROI divided by full XZ area integral";
        result.roi = struct('center_mm', target_mm, 'radius_x_mm', roi_radius_mm, ...
            'half_depth_z_mm', roi_half_depth_mm, 'plane', "XZ");
        result.peak_power = peak_power;
        result.peak_power_definition = ...
            "raw scalar-field |E|^2 in model units at local peak, fixed total sum(|w|^2)";
        result.input_power_proxy = input_power;
        result.baseline_input_power_proxy = baseline_input_power;
        result.input_power_relative_error = abs(input_power-baseline_input_power)/baseline_input_power;
        result.geometry_constraint_satisfied = geometry_ok;
        result.geometry_constraint_check = geometry_check;
        result.focus_constraint_satisfied = focus_error_mm <= tolerance_mm;
        result.constraint_satisfied = geometry_ok && focus_error_mm <= tolerance_mm;
        result.frequency_hz = cfg.physics.carrier_frequency;
        result.wavelength_mm = lambda_mm;
        result.element_count = array.count;
        result.element_model = string(array.element_model);
        result.element_pattern = string(array.element_pattern);
        result.evaluation_grid = struct('plane', "XZ", 'resolution_x', numel(x_mm), ...
            'resolution_z', numel(z_mm), 'x_bounds_mm', [x_mm(1), x_mm(end)], ...
            'z_bounds_mm', [z_mm(1), z_mm(end)]);
        result.lateral_profile = struct('position_mm', x_mm, ...
            'normalized_power', lateral, 'half_power_threshold', 0.5, ...
            'fwhm_mm', fwhm_x_mm, 'fixed_z_mm', actual_peak_mm(3));
        result.axial_profile = struct('position_mm', z_mm, ...
            'normalized_power', axial, 'half_power_threshold', 0.5, ...
            'dof_mm', dof_z_mm, 'fixed_x_mm', actual_peak_mm(1));
        result.matlab_runtime_sec = toc(geometry_started);
    catch exception
        result.status = "error";
        result.error_type = string(exception.identifier);
        result.message = string(exception.message);
        result.matlab_runtime_sec = toc(geometry_started);
    end
    results(index) = result;
end
end

function result = empty_result()
result = struct('status', "", 'geometry_id', "", 'family', "", ...
    'actual_peak_mm', [], 'focus_error_mm', [], 'fwhm_x_mm', [], ...
    'lateral_metric_axis', "", 'dof_z_mm', [], ...
    'energy_concentration_ratio', [], 'energy_concentration_definition', "", ...
    'roi', struct(), 'peak_power', [], 'peak_power_definition', "", ...
    'input_power_proxy', [], 'baseline_input_power_proxy', [], ...
    'input_power_relative_error', [], 'geometry_constraint_satisfied', false, ...
    'geometry_constraint_check', struct(), 'focus_constraint_satisfied', false, ...
    'constraint_satisfied', false, 'frequency_hz', [], 'wavelength_mm', [], ...
    'element_count', [], 'element_model', "", 'element_pattern', "", ...
    'evaluation_grid', struct(), 'lateral_profile', struct(), ...
    'axial_profile', struct(), 'matlab_runtime_sec', [], ...
    'error_type', "", 'message', "");
end

function [valid, details] = validate_geometry(positions_mm, config, baseline_model)
assert(isnumeric(positions_mm) && size(positions_mm, 2) == 3 && ...
    all(isfinite(positions_mm), 'all'), 'hermes:ArrayDesignInvalidCoordinates', ...
    'element_positions_mm must be a finite N-by-3 matrix.');
constraints = config.geometries(1).constraints;
count = size(positions_mm, 1);
aperture_x = max(positions_mm(:, 1))-min(positions_mm(:, 1));
aperture_y = max(positions_mm(:, 2))-min(positions_mm(:, 2));
surface_depth = max(positions_mm(:, 3))-min(positions_mm(:, 3));
minimum_spacing = inf;
for index = 1:count-1
    distances = vecnorm(positions_mm(index+1:end, :)-positions_mm(index, :), 2, 2);
    minimum_spacing = min(minimum_spacing, min(distances));
end
checks = struct(...
    'element_count', count == baseline_model.array.count, ...
    'aperture_x', aperture_x <= double(constraints.max_aperture_x_mm)+1e-8, ...
    'aperture_y', aperture_y <= double(constraints.max_aperture_y_mm)+1e-8, ...
    'minimum_spacing', minimum_spacing >= double(constraints.min_element_spacing_mm)-1e-8, ...
    'surface_depth', surface_depth <= double(constraints.max_surface_depth_mm)+1e-8);
valid = all(structfun(@(value) logical(value), checks));
details = struct('checks', checks, 'measured', struct('element_count', count, ...
    'aperture_x_mm', aperture_x, 'aperture_y_mm', aperture_y, ...
    'minimum_spacing_mm', minimum_spacing, 'surface_depth_mm', surface_depth));
end

function [peak_ix, peak_iz] = local_peak_indices(raw_power, maps, target_mm, radius_lambda, lambda_mm)
target_lambda = target_mm([1, 3])/lambda_mm;
x_mask = abs(maps.x_lambda-target_lambda(1)) <= radius_lambda;
z_mask = abs(maps.z_lambda-target_lambda(2)) <= radius_lambda;
local_map = raw_power(z_mask, x_mask);
[~, local_index] = max(local_map, [], 'all', 'linear');
[local_iz, local_ix] = ind2sub(size(local_map), local_index);
x_indices = find(x_mask);
z_indices = find(z_mask);
peak_ix = x_indices(local_ix);
peak_iz = z_indices(local_iz);
end

function width = half_power_width(coordinates, normalized_power, peak_index)
threshold = 0.5;
left = peak_index;
while left > 1 && normalized_power(left-1) >= threshold, left = left-1; end
right = peak_index;
while right < numel(normalized_power) && normalized_power(right+1) >= threshold, right = right+1; end
assert(left > 1 && right < numel(normalized_power), ...
    'hermes:ArrayDesignHalfPowerBoundary', 'Half-power region reaches evaluation boundary.');
left_cross = interpolate_crossing(coordinates(left-1:left), normalized_power(left-1:left), threshold);
right_cross = interpolate_crossing(coordinates(right:right+1), normalized_power(right:right+1), threshold);
width = right_cross-left_cross;
end

function crossing = interpolate_crossing(x, y, threshold)
if abs(y(2)-y(1)) <= eps
    crossing = mean(x);
else
    crossing = x(1)+(threshold-y(1))*(x(2)-x(1))/(y(2)-y(1));
end
end

function ratio = xz_energy_concentration(power, x_mm, z_mm, target_mm, radius_mm, half_depth_mm)
x_mask = abs(x_mm-target_mm(1)) <= radius_mm;
z_mask = abs(z_mm-target_mm(3)) <= half_depth_mm;
assert(nnz(x_mask) >= 2 && nnz(z_mask) >= 2, ...
    'hermes:ArrayDesignRoiTooSmall', 'ROI must include at least two grid points per axis.');
total = trapz(z_mm, trapz(x_mm, power, 2));
roi = trapz(z_mm(z_mask), trapz(x_mm(x_mask), power(z_mask, x_mask), 2));
ratio = roi/max(total, eps);
assert(ratio >= -1e-12 && ratio <= 1+1e-12, ...
    'hermes:ArrayDesignEnergyRatio', 'Energy concentration ratio is outside [0,1].');
ratio = min(max(ratio, 0), 1);
end

function value = positive_scalar(value, name)
value = double(value);
validateattributes(value, {'numeric'}, {'real', 'finite', 'scalar', 'positive'}, mfilename, name);
end

function initialize_paths()
root = fileparts(fileparts(mfilename('fullpath')));
folders = {root, fullfile(root, 'core'), fullfile(root, 'solver'), ...
    fullfile(root, 'evaluation'), fullfile(root, 'config')};
for index = 1:numel(folders), addpath(folders{index}, '-begin'); end
end
