function maps = compute_harmonic_weight_maps( ...
    harmonicWeights, model, gridSpec, options)
%COMPUTE_HARMONIC_WEIGHT_MAPS Evaluate raw and normalized arbitrary-W maps.

arguments
    harmonicWeights (:, :) double
    model struct
    gridSpec struct
    options.ChunkSize (1, 1) double {mustBeInteger, mustBePositive} = 4096
end

regions = resolve_focusing_regions(model.cfg);
evaluation = regions.evaluation;
if isfield(gridSpec, 'x_lambda') && isfield(gridSpec, 'z_lambda')
    xLambda = double(gridSpec.x_lambda(:).');
    zLambda = double(gridSpec.z_lambda(:).');
elseif isfield(gridSpec, 'resolution')
    xLambda = linspace(evaluation.x_lambda(1), ...
        evaluation.x_lambda(2), gridSpec.resolution);
    zLambda = linspace(evaluation.z_lambda(1), ...
        evaluation.z_lambda(2), gridSpec.resolution);
else
    step = gridSpec.step_lambda;
    xLambda = evaluation.x_lambda(1):step:evaluation.x_lambda(2);
    zLambda = evaluation.z_lambda(1):step:evaluation.z_lambda(2);
end

[xGridLambda, zGridLambda] = meshgrid(xLambda, zLambda);
pointsM = [xGridLambda(:), zeros(numel(xGridLambda), 1), ...
    zGridLambda(:)]*model.lambda;
fieldFlat = compute_harmonic_weight_field(harmonicWeights, model, ...
    pointsM, ChunkSize=options.ChunkSize);
field = reshape(fieldFlat, [size(xGridLambda), numel(model.harmonics)]);
amplitude = abs(field);
rawPower = amplitude.^2;
rangeSquared = (xGridLambda.^2+zGridLambda.^2)*model.lambda^2;
compensatedPower = rawPower.*rangeSquared;
harmonicPeakPower = reshape(max(rawPower, [], [1, 2]), 1, []);
globalPeakPower = max(harmonicPeakPower, [], 'all');

maps = struct();
maps.x_lambda = xLambda;
maps.z_lambda = zLambda;
maps.x_grid_lambda = xGridLambda;
maps.z_grid_lambda = zGridLambda;
maps.x_cm = xLambda*regions.lambda_c_cm;
maps.z_cm = zLambda*regions.lambda_c_cm;
maps.field = field;
maps.raw_amplitude = amplitude;
maps.raw_power_absolute = rawPower;
maps.local_power = normalize_harmonics(rawPower);
maps.global_power = rawPower/max(globalPeakPower, eps);
maps.compensated_power = normalize_harmonics(compensatedPower);
maps.harmonic_peak_power = harmonicPeakPower;
maps.global_peak_power = globalPeakPower;
end

function normalized = normalize_harmonics(power)
normalized = power;
for iq = 1:size(power, 3)
    maximum = max(power(:, :, iq), [], 'all');
    normalized(:, :, iq) = power(:, :, iq)/max(maximum, eps);
end
end
