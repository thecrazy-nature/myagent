function result = run_focus_core(targets_mm, options)
%RUN_FOCUS_CORE Run deterministic per-harmonic near-field focusing.
%
% Each target row is assigned to a distinct modulation harmonic. Harmonic
% weights are synthesized independently, so this is an ideal multi-harmonic
% complex-excitation model rather than a rectangular-pulse TMA realization.
% The solver evaluates the x-z plane, so every y must be zero. Polarization
% is persisted as scenario metadata; this scalar point-source model does not
% calculate polarization-dependent fields.

initialize_core_paths();
if nargin < 2
    options = struct();
end
random_seed = double(config_value(options, 'random_seed', 0));
validateattributes(random_seed, {'numeric'}, ...
    {'scalar', 'integer', 'nonnegative'}, mfilename, 'random_seed');
rng(random_seed, 'twister');
validateattributes(targets_mm, {'numeric'}, ...
    {'real', 'finite', '2d', 'ncols', 3, 'nonempty'}, mfilename, 'targets_mm');
targets_mm = double(targets_mm);

cfg = focusing_library_config("planar");
frequency_hz = double(config_value(options, 'frequency_hz', 28e9));
validateattributes(frequency_hz, {'numeric'}, ...
    {'scalar', 'finite', '>=', 1e9, '<=', 100e9}, mfilename, 'frequency_hz');
element_count = double(config_value(options, 'element_count', 256));
validateattributes(element_count, {'numeric'}, ...
    {'scalar', 'integer', '>=', 16, '<=', 1024}, mfilename, 'element_count');
elements_per_side = sqrt(element_count);
if elements_per_side ~= round(elements_per_side)
    error('hermes:InvalidElementCount', ...
        'element_count must be a perfect square for the planar array.');
end
polarization = lower(string(config_value(options, 'polarization', "scalar")));
if ~isscalar(polarization) || ~any(polarization == ...
        ["scalar", "x_linear", "y_linear", "rhcp", "lhcp"])
    error('hermes:InvalidPolarization', ...
        'Unsupported polarization scenario label: %s.', polarization);
end
cfg.physics.carrier_frequency = frequency_hz;
modulation_frequency_hz = double(config_value(options, ...
    'modulation_frequency_hz', 200e6));
validateattributes(modulation_frequency_hz, {'numeric'}, ...
    {'scalar', 'finite', 'positive', '<', frequency_hz}, ...
    mfilename, 'modulation_frequency_hz');
cfg.physics.modulation_frequency = modulation_frequency_hz;
cfg.array.elements_x = elements_per_side;
cfg.array.elements_y = elements_per_side;
user_harmonic_orders = assign_user_harmonics(size(targets_mm, 1));
cfg.physics.harmonics = min(user_harmonic_orders):max(user_harmonic_orders);
cfg.objective.frequency_weights = ones(size(cfg.physics.harmonics));
cfg.evaluation.resolution = 201;
cfg.library.allow_empty_carrier_target = true;

lambda_m = cfg.physics.c/cfg.physics.carrier_frequency;
lambda_mm = 1000*lambda_m;
x_bounds_mm = cfg.library.x_lambda*lambda_mm;
z_bounds_mm = cfg.library.z_lambda*lambda_mm;
if any(abs(targets_mm(:, 2)) > 1e-9)
    error('hermes:TargetOutOfPlane', ...
        'The current solver supports the x-z plane only; every target y must be 0 mm.');
end
if any(targets_mm(:, 1) < x_bounds_mm(1)) || ...
        any(targets_mm(:, 1) > x_bounds_mm(2)) || ...
        any(targets_mm(:, 3) < z_bounds_mm(1)) || ...
        any(targets_mm(:, 3) > z_bounds_mm(2))
    error('hermes:TargetOutOfRange', ...
        ['Every target must satisfy x in [%.6g, %.6g] mm, y = 0 mm, ' ...
         'and z in [%.6g, %.6g] mm.'], ...
        x_bounds_mm(1), x_bounds_mm(2), z_bounds_mm(1), z_bounds_mm(2));
end

cfg.targets.by_harmonic_lambda = cell(numel(cfg.physics.harmonics), 1);
user_harmonic_indices = zeros(size(user_harmonic_orders));
for user_index = 1:numel(user_harmonic_orders)
    harmonic_index = find(cfg.physics.harmonics == ...
        user_harmonic_orders(user_index), 1);
    user_harmonic_indices(user_index) = harmonic_index;
    cfg.targets.by_harmonic_lambda{harmonic_index} = ...
        targets_mm(user_index, [1, 3])/lambda_mm;
end
for harmonic_index = 1:numel(cfg.physics.harmonics)
    if isempty(cfg.targets.by_harmonic_lambda{harmonic_index})
        cfg.targets.by_harmonic_lambda{harmonic_index} = zeros(0, 2);
    end
end
model = build_multiuser_model(cfg);
harmonic_weights = complex(zeros(model.array.count, numel(model.harmonics)));
for harmonic_index = 1:numel(model.harmonics)
    harmonic_targets = model.focus_xyz{harmonic_index};
    if ~isempty(harmonic_targets)
        harmonic_weights(:, harmonic_index) = ...
            synthesize_near_field_weights(model.array, harmonic_targets, ...
            model.wavenumbers(harmonic_index), cfg);
    end
end
maps = compute_harmonic_weight_maps(harmonic_weights, model, ...
    struct('resolution', cfg.evaluation.resolution));

spot = measure_focal_spots(maps.local_power, maps, model);
peak_power_by_user = zeros(size(targets_mm, 1), 1);
requested_power_by_user = zeros(size(targets_mm, 1), 1);
focus_error_mm_by_user = zeros(size(targets_mm, 1), 1);
fwhm_x_mm_by_user = zeros(size(targets_mm, 1), 1);
dof_z_mm_by_user = zeros(size(targets_mm, 1), 1);
peak_to_sidelobe_ratio_db_by_user = zeros(size(targets_mm, 1), 1);
actual_peak_points_mm = zeros(size(targets_mm));
requested_field = compute_harmonic_weight_field( ...
    harmonic_weights, model, targets_mm/1000);
for user_index = 1:size(targets_mm, 1)
    harmonic_index = user_harmonic_indices(user_index);
    peak_xz_lambda = spot.peak_xz_lambda{harmonic_index}(1, :);
    actual_peak_points_mm(user_index, :) = ...
        [peak_xz_lambda(1), 0, peak_xz_lambda(2)]*lambda_mm;
    [~, peak_ix] = min(abs(maps.x_lambda-peak_xz_lambda(1)));
    [~, peak_iz] = min(abs(maps.z_lambda-peak_xz_lambda(2)));
    raw_power = maps.raw_power_absolute(:, :, harmonic_index);
    peak_power_by_user(user_index) = raw_power(peak_iz, peak_ix);
    requested_power_by_user(user_index) = ...
        abs(requested_field(user_index, harmonic_index)).^2;
    focus_error_mm_by_user(user_index) = ...
        spot.focus_error_lambda{harmonic_index}(1)*lambda_mm;
    fwhm_x_mm_by_user(user_index) = ...
        spot.width_3db_lambda{harmonic_index}(1)*lambda_mm;
    dof_z_mm_by_user(user_index) = ...
        spot.depth_3db_lambda{harmonic_index}(1)*lambda_mm;
    peak_to_sidelobe_ratio_db_by_user(user_index) = ...
        peak_to_sidelobe_ratio(raw_power, maps, peak_ix, peak_iz, ...
        spot.width_3db_lambda{harmonic_index}(1), ...
        spot.depth_3db_lambda{harmonic_index}(1));
end

[global_peak_power, global_linear] = max( ...
    maps.raw_power_absolute, [], 'all', 'linear');
[global_iz, global_ix, global_iq] = ...
    ind2sub(size(maps.raw_power_absolute), global_linear);
global_peak_mm = [maps.x_lambda(global_ix), 0, ...
    maps.z_lambda(global_iz)]*lambda_mm;

result = struct();
result.random_seed = random_seed;
result.rng_algorithm = "twister";
result.requested_focus_mm = targets_mm(1, :);
result.actual_peak_mm = actual_peak_points_mm(1, :);
result.peak_power = peak_power_by_user(1);
result.peak_power_definition = ...
    "raw scalar-field |E|^2 in model units at the local focal peak";
result.requested_power = requested_power_by_user(1);
result.requested_focus_points_mm = targets_mm;
result.actual_peak_points_mm = actual_peak_points_mm;
result.peak_power_by_user = peak_power_by_user;
result.requested_power_by_user = requested_power_by_user;
result.focus_error_mm_by_user = focus_error_mm_by_user;
result.fwhm_x_mm_by_user = fwhm_x_mm_by_user;
result.dof_z_mm_by_user = dof_z_mm_by_user;
result.peak_to_sidelobe_ratio_db_by_user = ...
    peak_to_sidelobe_ratio_db_by_user;
result.peak_to_sidelobe_definition = ...
    "10log10(target-local peak / maximum power outside the FWHM-by-DOF main-lobe rectangle)";
result.user_count = size(targets_mm, 1);
result.global_peak_mm = global_peak_mm;
result.global_peak_power = global_peak_power;
result.global_peak_harmonic_order = model.harmonics(global_iq);
result.local_peak_radius_mm = cfg.evaluation.local_peak_radius_lambda*lambda_mm;
result.frequency_hz = cfg.physics.carrier_frequency;
result.modulation_frequency_hz = cfg.physics.modulation_frequency;
result.harmonic_orders = model.harmonics;
result.harmonic_frequencies_hz = model.frequencies;
result.user_harmonic_orders = user_harmonic_orders;
result.user_harmonic_indices = user_harmonic_indices;
result.wavelength_mm = lambda_mm;
result.element_count = model.array.count;
result.element_positions_mm = 1000*[model.array.x, model.array.y, model.array.z];
result.polarization = polarization;
result.polarization_model = ...
    "scenario metadata only; scalar point-source fields are polarization independent";
result.method = ...
    "ideal_independent_per_harmonic_axial_null_near_field_synthesis";
result.hardware_realization = ...
    "not projected to a coupled rectangular-pulse TMA control sequence";
result.array_geometry = model.array.configuration.signature;
result.target_bounds_mm = struct('x', x_bounds_mm, 'y', [0, 0], ...
    'z', z_bounds_mm);
result.excitation = struct('harmonic_weights', harmonic_weights, ...
    'normalization', "maximum element magnitude equals one");
result.maps = maps;
end

function orders = assign_user_harmonics(user_count)
if mod(user_count, 2) == 1
    half_count = (user_count-1)/2;
    orders = -half_count:half_count;
else
    half_count = user_count/2;
    orders = [-half_count:-1, 1:half_count];
end
end

function ratio_db = peak_to_sidelobe_ratio( ...
    power_map, maps, peak_ix, peak_iz, width_lambda, depth_lambda)
x_step = max(mean(diff(maps.x_lambda)), eps);
z_step = max(mean(diff(maps.z_lambda)), eps);
half_width = max(width_lambda/2, x_step);
half_depth = max(depth_lambda/2, z_step);
main_mask = abs(maps.x_lambda-maps.x_lambda(peak_ix)) <= half_width;
main_z_mask = abs(maps.z_lambda-maps.z_lambda(peak_iz)) <= half_depth;
exclusion = main_z_mask(:)*main_mask(:).';
sidelobes = power_map;
sidelobes(logical(exclusion)) = 0;
main_peak = power_map(peak_iz, peak_ix);
sidelobe_peak = max(sidelobes, [], 'all');
ratio_db = 10*log10(max(main_peak, eps)/max(sidelobe_peak, eps));
end

function value = config_value(options, name, fallback)
value = fallback;
if isstruct(options) && isfield(options, name)
    value = options.(name);
end
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
