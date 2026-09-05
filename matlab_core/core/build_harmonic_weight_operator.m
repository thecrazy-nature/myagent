function operators = build_harmonic_weight_operator( ...
    pointsM, array, frequenciesHz, propagationSpeed)
%BUILD_HARMONIC_WEIGHT_OPERATOR Map arbitrary element weights to scalar fields.

arguments
    pointsM (:, 3) double
    array struct
    frequenciesHz (1, :) double {mustBePositive}
    propagationSpeed (1, 1) double {mustBePositive}
end

elementCount = array.count;
pointCount = size(pointsM, 1);
distance = zeros(pointCount, elementCount);
gain = zeros(pointCount, elementCount);
for element = 1:elementCount
    dx = pointsM(:, 1)-array.x(element);
    dy = pointsM(:, 2)-array.y(element);
    dz = pointsM(:, 3)-array.z(element);
    distance(:, element) = sqrt(dx.^2+dy.^2+dz.^2);
    if any(distance(:, element) <= 100*eps(max(1, max(distance(:, element)))))
        error('tma:SourceSingularity', ...
            'An evaluation point coincides with array element %d.', element);
    end
    gain(:, element) = array_element_gain( ...
        array, element, dx, dy, dz, distance(:, element));
end

wavenumbers = 2*pi*frequenciesHz/propagationSpeed;
operators = cell(1, numel(frequenciesHz));
for iq = 1:numel(frequenciesHz)
    operators{iq} = gain.*exp(-1i*wavenumbers(iq)*distance)./distance;
end
end
