function field = compute_harmonic_weight_field( ...
    harmonicWeights, model, pointsM, options)
%COMPUTE_HARMONIC_WEIGHT_FIELD Evaluate fields from authoritative W(n,q).

arguments
    harmonicWeights (:, :) double
    model struct
    pointsM (:, 3) double
    options.ChunkSize (1, 1) double {mustBeInteger, mustBePositive} = 4096
end

assert(size(harmonicWeights, 1) == model.array.count, ...
    'harmonicWeights must contain one row per array element.');
assert(size(harmonicWeights, 2) == numel(model.harmonics), ...
    'harmonicWeights columns must follow model.harmonics.');

pointCount = size(pointsM, 1);
field = complex(zeros(pointCount, numel(model.harmonics)));
for first = 1:options.ChunkSize:pointCount
    last = min(first+options.ChunkSize-1, pointCount);
    operators = build_harmonic_weight_operator(pointsM(first:last, :), ...
        model.array, model.frequencies, model.cfg.physics.c);
    for iq = 1:numel(model.harmonics)
        field(first:last, iq) = operators{iq}*harmonicWeights(:, iq);
    end
end
end
