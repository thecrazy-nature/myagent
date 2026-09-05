function metrics = measure_focal_spots(powerMaps, maps, model)
%MEASURE_FOCAL_SPOTS Measure local peak error and contiguous -3 dB spot size.

nHarmonic = numel(model.harmonics);
targetPower = cell(nHarmonic, 1);
peakXZ = cell(nHarmonic, 1);
width3dB = cell(nHarmonic, 1);
depth3dB = cell(nHarmonic, 1);
focusError = cell(nHarmonic, 1);
halfPower = 10^(-3/10);

for iq = 1:nHarmonic
    targets = model.focus_xyz{iq}(:, [1, 3])/model.lambda;
    map = powerMaps(:, :, iq);
    count = size(targets, 1);
    targetPower{iq} = zeros(count, 1);
    peakXZ{iq} = zeros(count, 2);
    width3dB{iq} = nan(count, 1);
    depth3dB{iq} = nan(count, 1);
    focusError{iq} = zeros(count, 1);

    for it = 1:count
        target = targets(it, :);
        [~, ixTarget] = min(abs(maps.x_lambda-target(1)));
        [~, izTarget] = min(abs(maps.z_lambda-target(2)));
        targetPower{iq}(it) = map(izTarget, ixTarget);

        radius = model.cfg.evaluation.local_peak_radius_lambda;
        xMask = abs(maps.x_lambda-target(1)) <= radius;
        zMask = abs(maps.z_lambda-target(2)) <= radius;
        localMap = map(zMask, xMask);
        [~, localIndex] = max(localMap(:));
        [localIz, localIx] = ind2sub(size(localMap), localIndex);
        xIndices = find(xMask);
        zIndices = find(zMask);
        ixPeak = xIndices(localIx);
        izPeak = zIndices(localIz);
        peak = [maps.x_lambda(ixPeak), maps.z_lambda(izPeak)];
        peakXZ{iq}(it, :) = peak;
        focusError{iq}(it) = norm(peak-target);

        localThreshold = halfPower*map(izPeak, ixPeak);
        if localThreshold > 0
            width3dB{iq}(it) = contiguous_width( ...
                map(izPeak, :) >= localThreshold, maps.x_lambda, ixPeak);
            depth3dB{iq}(it) = contiguous_width( ...
                map(:, ixPeak).' >= localThreshold, maps.z_lambda, izPeak);
        end
    end
end

metrics = struct('target_power', {targetPower}, 'peak_xz_lambda', {peakXZ}, ...
    'focus_error_lambda', {focusError}, 'width_3db_lambda', {width3dB}, ...
    'depth_3db_lambda', {depth3dB});
end

function width = contiguous_width(mask, coordinates, centerIndex)
left = centerIndex;
while left > 1 && mask(left-1), left = left-1; end
right = centerIndex;
while right < numel(mask) && mask(right+1), right = right+1; end
width = coordinates(right)-coordinates(left);
end
