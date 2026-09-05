function plan = tma_frequency_plan(cfg)
%TMA_FREQUENCY_PLAN Resolve carrier, modulation, and harmonic frequencies.

arguments
    cfg struct
end

required = {'carrier_frequency', 'modulation_frequency', 'c', 'harmonics'};
for fieldIndex = 1:numel(required)
    assert(isfield(cfg.physics, required{fieldIndex}), ...
        'physics.%s is required.', required{fieldIndex});
end

carrierHz = double(cfg.physics.carrier_frequency);
modulationHz = double(cfg.physics.modulation_frequency);
waveSpeed = double(cfg.physics.c);
orders = double(cfg.physics.harmonics(:).');
validateattributes(carrierHz, {'numeric'}, {'scalar', 'positive', 'finite'});
validateattributes(modulationHz, {'numeric'}, {'scalar', 'positive', 'finite'});
validateattributes(waveSpeed, {'numeric'}, {'scalar', 'positive', 'finite'});
assert(all(isfinite(orders)) && all(orders == round(orders)), ...
    'physics.harmonics must contain finite integer orders.');
assert(any(orders == 0), 'physics.harmonics must include q = 0.');

frequenciesHz = carrierHz+orders*modulationHz;
assert(all(frequenciesHz > 0), ...
    'Every configured harmonic frequency must be positive.');
componentTypes = strings(size(orders));
for orderIndex = 1:numel(orders)
    if orders(orderIndex) == 0
        componentTypes(orderIndex) = "carrier";
    elseif orders(orderIndex) < 0
        componentTypes(orderIndex) = "lower_sideband";
    else
        componentTypes(orderIndex) = "upper_sideband";
    end
end

plan = struct('carrier_hz', carrierHz, ...
    'modulation_hz', modulationHz, ...
    'modulation_period_s', 1/modulationHz, ...
    'wave_speed_mps', waveSpeed, 'orders', orders, ...
    'frequencies_hz', frequenciesHz, ...
    'wavelength_c_m', waveSpeed/carrierHz, ...
    'wavelengths_m', waveSpeed./frequenciesHz, ...
    'component_types', componentTypes);
end
