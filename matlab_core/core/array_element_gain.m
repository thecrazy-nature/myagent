function gain = array_element_gain(array, elementIndex, dx, dy, dz, distance)
%ARRAY_ELEMENT_GAIN Evaluate the configured scalar element pattern.

if array.element_pattern == "isotropic"
    gain = ones(size(distance));
    return;
end
if array.element_pattern ~= "inward_cosine"
    error('Unsupported element pattern: %s.', array.element_pattern);
end

nx = array.normal_x(elementIndex);
ny = array.normal_y(elementIndex);
nz = array.normal_z(elementIndex);
directionCosine = (nx.*dx+ny.*dy+nz.*dz)./max(distance, eps);
gain = max(real(directionCosine), 0).^array.pattern_exponent;
end
