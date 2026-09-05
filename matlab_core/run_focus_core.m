function result = run_focus_core(target_mm)
%RUN_FOCUS_CORE Run one deterministic carrier-frequency near-field focus.
%
% target_mm is [x,y,z] in millimetres. Version 1 evaluates the x-z plane,
% so y must be zero. peak_power is the unnormalised scalar-model |E|^2 at
% the measured local focal peak; it is a model-unit power proxy, not watts.

initialize_core_paths();
validateattributes(target_mm, {'numeric'}, ...
    {'real', 'finite', 'vector', 'numel', 3}, mfilename, 'target_mm');
target_mm = double(target_mm(:).');

cfg = focusing_library_config("planar");
cfg.physics.harmonics = 0;
cfg.objective.frequency_weights = 1;
cfg.evaluation.resolution = 201;

lambda_m = cfg.physics.c/cfg.physics.carrier_frequency;
lambda_mm = 1000*lambda_m;
x_bounds_mm = cfg.library.x_lambda*lambda_mm;
z_bounds_mm = cfg.library.z_lambda*lambda_mm;
if abs(target_mm(2)) > 1e-9
    error('hermes:TargetOutOfPlane', ...
        'Version 1 supports the x-z plane only; target y must be 0 mm.');
end
if target_mm(1) < x_bounds_mm(1) || target_mm(1) > x_bounds_mm(2) || ...
        target_mm(3) < z_bounds_mm(1) || target_mm(3) > z_bounds_mm(2)
    error('hermes:TargetOutOfRange', ...
        ['Target must satisfy x in [%.6g, %.6g] mm, y = 0 mm, ' ...
         'and z in [%.6g, %.6g] mm.'], ...
        x_bounds_mm(1), x_bounds_mm(2), z_bounds_mm(1), z_bounds_mm(2));
end

cfg.targets.by_harmonic_lambda = ...
    {[target_mm(1)/lambda_mm, target_mm(3)/lambda_mm]};
model = build_multiuser_model(cfg);
harmonic_weights = model.carrier_weights;
maps = compute_harmonic_weight_maps(harmonic_weights, model, ...
    struct('resolution', cfg.evaluation.resolution));

spot = measure_focal_spots(maps.local_power, maps, model);
peak_xz_lambda = spot.peak_xz_lambda{1}(1, :);
actual_peak_mm = [peak_xz_lambda(1), 0, peak_xz_lambda(2)]*lambda_mm;
[~, peak_ix] = min(abs(maps.x_lambda-peak_xz_lambda(1)));
[~, peak_iz] = min(abs(maps.z_lambda-peak_xz_lambda(2)));
raw_power = maps.raw_power_absolute(:, :, 1);
peak_power = raw_power(peak_iz, peak_ix);

requested_field = compute_harmonic_weight_field( ...
    harmonic_weights, model, target_mm/1000);
requested_power = abs(requested_field)^2;
[global_peak_power, global_linear] = max(raw_power, [], 'all', 'linear');
[global_iz, global_ix] = ind2sub(size(raw_power), global_linear);
global_peak_mm = [maps.x_lambda(global_ix), 0, ...
    maps.z_lambda(global_iz)]*lambda_mm;

result = struct();
result.requested_focus_mm = target_mm;
result.actual_peak_mm = actual_peak_mm;
result.peak_power = peak_power;
result.peak_power_definition = ...
    "raw scalar-field |E|^2 in model units at the local focal peak";
result.requested_power = requested_power;
result.global_peak_mm = global_peak_mm;
result.global_peak_power = global_peak_power;
result.local_peak_radius_mm = cfg.evaluation.local_peak_radius_lambda*lambda_mm;
result.frequency_hz = cfg.physics.carrier_frequency;
result.wavelength_mm = lambda_mm;
result.method = "planar_q0_axial_null_near_field_synthesis";
result.array_geometry = model.array.configuration.signature;
result.target_bounds_mm = struct('x', x_bounds_mm, 'y', [0, 0], ...
    'z', z_bounds_mm);
result.excitation = struct('harmonic_weights', harmonic_weights, ...
    'normalization', "maximum element magnitude equals one");
result.maps = maps;
end

function initialize_core_paths()
root = string(fileparts(mfilename('fullpath')));
folders = [root; fullfile(root, ["core"; "solver"; ...
    "evaluation"; "config"])];
for folder = folders(:).'
    assert(isfolder(folder), 'hermes:MissingCoreFolder', ...
        'Required core folder is missing: %s', folder);
    addpath(char(folder), '-begin');
end
end
