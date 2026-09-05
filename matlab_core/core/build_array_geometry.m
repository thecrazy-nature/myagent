function array = build_array_geometry(cfg, lambda)
%BUILD_ARRAY_GEOMETRY Generate planar or hemispherical element coordinates.

spec = resolve_array_configuration(cfg, lambda);
geometry = spec.geometry;

switch geometry
    case "planar"
        elementsX = spec.layout.elements_x;
        elementsY = spec.layout.elements_y;
        spacingX = spec.layout.spacing_x_m;
        spacingY = spec.layout.spacing_y_m;
        xPosition = ((1:elementsX)-elementsX/2-0.5)*spacingX;
        yPosition = ((1:elementsY)-elementsY/2-0.5)*spacingY;
        [xElement, yElement] = meshgrid(xPosition, yPosition);
        positions = [xElement(:), yElement(:), zeros(numel(xElement), 1)];
        normals = repmat([0, 0, 1], size(positions, 1), 1);

    case "hemisphere"
        count = spec.count;
        radius = spec.layout.radius_m;
        center = spec.layout.center_m;
        axisDirection = spec.layout.hemisphere_axis;

        index = (0:count-1).';
        axial = (index+0.5)/count;
        azimuth = mod(index*pi*(3-sqrt(5)), 2*pi);
        radial = sqrt(1-axial.^2);
        unitPosition = [radial.*cos(azimuth), radial.*sin(azimuth), axial];
        unitPosition = rotate_from_z(unitPosition, axisDirection);
        positions = center+radius*unitPosition;
        normals = -unitPosition;

    otherwise
        error('Unsupported array geometry: %s.', geometry);
end

array = struct('x', positions(:, 1), 'y', positions(:, 2), ...
    'z', positions(:, 3), 'normal_x', normals(:, 1), ...
    'normal_y', normals(:, 2), 'normal_z', normals(:, 3), ...
    'count', size(positions, 1), 'geometry', geometry, ...
    'layout', spec.layout, 'configuration', spec, ...
    'element_model', spec.element_model, ...
    'element_pattern', spec.element_pattern, ...
    'pattern_exponent', spec.pattern_exponent);
end

function rotated = rotate_from_z(points, axisDirection)
zAxis = [0, 0, 1];
crossAxis = cross(zAxis, axisDirection);
sine = norm(crossAxis);
cosine = dot(zAxis, axisDirection);
if sine < 1e-12
    if cosine > 0
        rotated = points;
    else
        rotated = [points(:, 1), -points(:, 2), -points(:, 3)];
    end
    return;
end
crossAxis = crossAxis/sine;
skew = [0, -crossAxis(3), crossAxis(2); ...
    crossAxis(3), 0, -crossAxis(1); ...
    -crossAxis(2), crossAxis(1), 0];
rotation = eye(3)+sine*skew+(1-cosine)*(skew*skew);
rotated = points*rotation.';
end
