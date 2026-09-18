function baseline_path = generate_original_baseline(original_root, output_path)
%GENERATE_ORIGINAL_BASELINE Run the read-only source project reference chain.

if nargin < 1 || strlength(string(original_root)) == 0
    original_root = string(getenv('HERMES_REFERENCE_PROJECT_ROOT'));
end
if nargin < 2 || strlength(string(output_path)) == 0
    output_path = fullfile(fileparts(fileparts( ...
        mfilename('fullpath'))), 'runs', 'original_baseline.mat');
end
original_root = string(original_root);
output_path = string(output_path);
assert(strlength(original_root) > 0, ...
    ['Pass the source-project folder as the first argument or set the ' ...
    'process-local HERMES_REFERENCE_PROJECT_ROOT environment variable.']);
assert(isfile(fullfile(original_root, 'run_multiuser.m')), ...
    'Original project root is invalid: %s', original_root);

restoredefaultpath;
folders = ["core", "solver", "evaluation", "library"];
for folder = folders
    addpath(fullfile(original_root, 'src', folder), '-begin');
end
targets_mm = [0, 0, 100; 30, 0, 90; -40, 0, 130];
cases = struct([]);
for index = 1:size(targets_mm, 1)
    item = run_original_case(targets_mm(index, :));
    if index == 1
        cases = repmat(item, size(targets_mm, 1), 1);
    else
        cases(index) = item;
    end
end
source_root = original_root;
generated_at = string(datetime('now', 'Format', 'yyyy-MM-dd''T''HH:mm:ss.SSS'));
parent = fileparts(output_path);
if ~isfolder(parent), mkdir(parent); end
save(output_path, 'targets_mm', 'cases', 'source_root', 'generated_at', '-v7');
baseline_path = output_path;
fprintf('ORIGINAL_BASELINE_OK %s\n', baseline_path);
end

function item = run_original_case(target_mm)
cfg = focusing_library_config("planar");
cfg.physics.harmonics = 0;
cfg.objective.frequency_weights = 1;
cfg.evaluation.resolution = 201;
lambda_mm = 1000*cfg.physics.c/cfg.physics.carrier_frequency;
cfg.targets.by_harmonic_lambda = ...
    {[target_mm(1)/lambda_mm, target_mm(3)/lambda_mm]};
model = build_multiuser_model(cfg);
weights = model.carrier_weights;
maps = compute_harmonic_weight_maps(weights, model, ...
    struct('resolution', cfg.evaluation.resolution));
spot = measure_focal_spots(maps.local_power, maps, model);
peak_xz = spot.peak_xz_lambda{1}(1, :);
actual_peak_mm = [peak_xz(1), 0, peak_xz(2)]*lambda_mm;
[~, ix] = min(abs(maps.x_lambda-peak_xz(1)));
[~, iz] = min(abs(maps.z_lambda-peak_xz(2)));
raw_power = maps.raw_power_absolute(:, :, 1);
[global_peak_power, linear] = max(raw_power, [], 'all', 'linear');
[global_iz, global_ix] = ind2sub(size(raw_power), linear);
requested_field = compute_harmonic_weight_field(weights, model, target_mm/1000);
item = struct('target_mm', target_mm, ...
    'actual_peak_mm', actual_peak_mm, 'peak_power', raw_power(iz, ix), ...
    'requested_power', abs(requested_field)^2, ...
    'global_peak_mm', [maps.x_lambda(global_ix), 0, ...
        maps.z_lambda(global_iz)]*lambda_mm, ...
    'global_peak_power', global_peak_power, ...
    'harmonic_weights', weights, 'raw_power_map', raw_power);
end
