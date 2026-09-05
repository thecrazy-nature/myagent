function model = build_multiuser_model(cfg)
%BUILD_MULTIUSER_MODEL Build frequencies, array geometry, and carrier weights.

frequencyPlan = tma_frequency_plan(cfg);
requiredTargets = numel(frequencyPlan.orders);
assert(numel(cfg.objective.frequency_weights) == requiredTargets, ...
    'objective.frequency_weights must have one value per harmonic.');

lambda = frequencyPlan.wavelength_c_m;
harmonics = frequencyPlan.orders;
frequencies = frequencyPlan.frequencies_hz;
array = build_array_geometry(cfg, lambda);

if isfield(cfg.targets, 'by_harmonic_lambda')
    assert(numel(cfg.targets.by_harmonic_lambda) == requiredTargets, ...
        'targets.by_harmonic_lambda must have one cell per harmonic.');
    focusXYZ = cell(requiredTargets, 1);
    for iq = 1:requiredTargets
        xz = cfg.targets.by_harmonic_lambda{iq};
        assert(isempty(xz) || size(xz, 2) == 2, 'Each target array must contain [x,z] rows.');
        focusXYZ{iq} = [xz(:, 1), zeros(size(xz, 1), 1), xz(:, 2)] * lambda;
    end
else
    focusXZ = cfg.targets.xz_lambda * lambda;
    focusXYZ = cell(requiredTargets, 1);
    for iq = 1:requiredTargets
        focusXYZ{iq} = [focusXZ(iq, 1), 0, focusXZ(iq, 2)];
    end
end
carrierIndex = find(harmonics == 0, 1);
assert(~isempty(carrierIndex), 'physics.harmonics must include q = 0.');
carrierTargets = focusXYZ{carrierIndex};
allowEmptyCarrierTarget = isfield(cfg.library, ...
    'allow_empty_carrier_target') && ...
    logical(cfg.library.allow_empty_carrier_target);
if isempty(carrierTargets)
    assert(allowEmptyCarrierTarget, 'At least one q=0 target is required.');
    % This neutral seed is replaced by the joint TMA pulse fit. It is not a
    % hidden q=0 focusing target.
    carrierWeights = complex(ones(array.count, 1));
    carrierInitialization = "neutral_unit_phase_for_sideband_only_fit";
else
    carrierWeights = synthesize_near_field_weights(array, carrierTargets, ...
        2*pi*cfg.physics.carrier_frequency/cfg.physics.c, cfg);
    carrierInitialization = "q0_near_field_synthesis";
end

model = struct();
model.lambda = lambda;
model.harmonics = harmonics;
model.frequencies = frequencies;
model.wavenumbers = 2*pi*frequencies / cfg.physics.c;
model.frequency_plan = frequencyPlan;
model.focus_xyz = focusXYZ;
model.array = array;
model.carrier_weights = carrierWeights;
model.carrier_initialization = carrierInitialization;
model.cfg = cfg;
end
