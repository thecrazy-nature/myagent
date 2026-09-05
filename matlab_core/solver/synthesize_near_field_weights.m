function weights = synthesize_near_field_weights(array, targets, wavenumber, modelCfg)
%SYNTHESIZE_NEAR_FIELD_WEIGHTS Fit targets while nulling axial guard points.

mode = "phase_conjugate";
if isfield(modelCfg.array, 'carrier_synthesis')
    mode = string(modelCfg.array.carrier_synthesis);
end

targetResponse = response_matrix(array, targets, wavenumber);
if mode == "phase_conjugate"
    weights = targetResponse'*ones(size(targetResponse, 1), 1);
else
    lambda = modelCfg.physics.c/modelCfg.physics.carrier_frequency;
    offsets = modelCfg.array.axial_guard_offsets_lambda*lambda;
    zLimits = modelCfg.domain.z_lambda*lambda;
    guards = axial_guards(targets, offsets, zLimits);
    [targetResponse, targetNorms] = normalize_rows(targetResponse);
    guardResponse = normalize_rows(response_matrix(array, guards, wavenumber));
    guardWeight = modelCfg.array.axial_guard_weight;
    if size(targets, 1) >= 3 && isfield(modelCfg.array, 'multifocus_guard_weight')
        guardWeight = modelCfg.array.multifocus_guard_weight;
    end
    systemMatrix = [targetResponse; guardWeight*guardResponse];
    targetLevels = 1./max(targetNorms, eps);
    targetLevels = targetLevels/max(targetLevels);
    desired = [targetLevels; zeros(size(guardResponse, 1), 1)];
    regularization = modelCfg.array.carrier_regularization;
    weights = systemMatrix' * ((systemMatrix*systemMatrix' + ...
        regularization*eye(size(systemMatrix, 1)))\desired);
end
weights = weights/max(abs(weights)+eps);
end

function guards = axial_guards(targets, offsets, zLimits)
guards = zeros(0, 3);
for it = 1:size(targets, 1)
    candidate = [repmat(targets(it, 1:2), numel(offsets), 1), targets(it, 3)+offsets(:)];
    inside = candidate(:, 3) >= zLimits(1) & candidate(:, 3) <= zLimits(2);
    guards = [guards; candidate(inside, :)]; %#ok<AGROW>
end
end

function response = response_matrix(array, points, wavenumber)
response = complex(zeros(size(points, 1), array.count));
elementIndex = (1:array.count).';
for ip = 1:size(points, 1)
    dx = points(ip, 1)-array.x;
    dy = points(ip, 2)-array.y;
    dz = points(ip, 3)-array.z;
    distance = sqrt(dx.^2+dy.^2+dz.^2);
    gain = array_element_gain(array, elementIndex, dx, dy, dz, distance);
    response(ip, :) = (gain.*exp(-1i*wavenumber*distance)./distance).';
end
end

function [matrix, norms] = normalize_rows(matrix)
norms = vecnorm(matrix, 2, 2);
if isempty(matrix), return; end
matrix = matrix./max(norms, eps);
end
