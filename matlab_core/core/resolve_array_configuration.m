function spec = resolve_array_configuration(cfg, lambda)
%RESOLVE_ARRAY_CONFIGURATION Validate and derive array geometry parameters.

arguments
    cfg struct
    lambda double = []
end

if isempty(lambda)
    plan = tma_frequency_plan(cfg);
    lambda = plan.wavelength_c_m;
end
validateattributes(lambda, {'numeric'}, {'scalar', 'positive', 'finite'});
assert(isfield(cfg, 'array') && isstruct(cfg.array), ...
    'cfg.array is required.');
arrayCfg = cfg.array;

geometry = "planar";
if isfield(arrayCfg, 'geometry')
    geometry = lower(string(arrayCfg.geometry));
end
elementModel = "scalar_point_source";
if isfield(arrayCfg, 'element_model')
    elementModel = lower(string(arrayCfg.element_model));
end
assert(elementModel == "scalar_point_source", ...
    'Unsupported array element model: %s.', elementModel);

elementPattern = "isotropic";
if isfield(arrayCfg, 'element_pattern')
    elementPattern = lower(string(arrayCfg.element_pattern));
end
assert(any(elementPattern == ["isotropic", "inward_cosine"]), ...
    'Unsupported element pattern: %s.', elementPattern);
patternExponent = 1;
if isfield(arrayCfg, 'pattern_exponent')
    patternExponent = double(arrayCfg.pattern_exponent);
end
validateattributes(patternExponent, {'numeric'}, ...
    {'scalar', 'positive', 'finite'});

switch geometry
    case "planar"
        elementsX = integer_parameter(arrayCfg, ...
            {'elements_x', 'elements_per_side'}, 'planar element count in x');
        elementsY = integer_parameter(arrayCfg, ...
            {'elements_y', 'elements_per_side'}, 'planar element count in y');
        spacingX = positive_parameter(arrayCfg, ...
            {'spacing_x_lambda', 'spacing_lambda'}, 'planar x spacing');
        spacingY = positive_parameter(arrayCfg, ...
            {'spacing_y_lambda', 'spacing_lambda'}, 'planar y spacing');
        apertureX = (elementsX-1)*spacingX;
        apertureY = (elementsY-1)*spacingY;
        layout = struct('elements_x', elementsX, 'elements_y', elementsY, ...
            'spacing_x_lambda', spacingX, 'spacing_y_lambda', spacingY, ...
            'aperture_x_lambda', apertureX, ...
            'aperture_y_lambda', apertureY, ...
            'spacing_x_m', spacingX*lambda, ...
            'spacing_y_m', spacingY*lambda, ...
            'aperture_x_m', apertureX*lambda, ...
            'aperture_y_m', apertureY*lambda, ...
            'normal', [0, 0, 1]);
        if elementsX == elementsY
            layout.elements_per_side = elementsX;
        end
        if abs(spacingX-spacingY) <= 1e-12
            layout.spacing_lambda = spacingX;
        end
        if elementsX == elementsY && abs(spacingX-spacingY) <= 1e-12
            signature = "planar_"+elementsX+"_"+number_token(spacingX);
        else
            signature = "planar_"+elementsX+"x"+elementsY+"_"+ ...
                number_token(spacingX)+"x"+number_token(spacingY);
        end
        count = elementsX*elementsY;

    case "hemisphere"
        count = integer_parameter(arrayCfg, {'element_count'}, ...
            'hemisphere element count');
        radius = positive_parameter(arrayCfg, {'radius_lambda'}, ...
            'hemisphere radius');
        center = vector_parameter(arrayCfg, 'center_lambda', [0, 0, 0]);
        axisDirection = vector_parameter(arrayCfg, ...
            'hemisphere_axis', [0, 0, 1]);
        assert(norm(axisDirection) > 0, ...
            'array.hemisphere_axis must be nonzero.');
        axisDirection = axisDirection/norm(axisDirection);
        meanSpacing = sqrt(2*pi*radius^2/count);
        layout = struct('radius_lambda', radius, ...
            'diameter_lambda', 2*radius, ...
            'center_lambda', center, ...
            'hemisphere_axis', axisDirection, ...
            'mean_spacing_lambda', meanSpacing, ...
            'radius_m', radius*lambda, ...
            'diameter_m', 2*radius*lambda, ...
            'center_m', center*lambda, ...
            'mean_spacing_m', meanSpacing*lambda);
        signature = "hemisphere_"+count+"_"+number_token(radius);

    otherwise
        error('Unsupported array geometry: %s.', geometry);
end

spec = struct('geometry', geometry, 'element_model', elementModel, ...
    'element_pattern', elementPattern, ...
    'pattern_exponent', patternExponent, 'lambda_m', lambda, ...
    'count', count, 'layout', layout, 'signature', signature);
end

function value = integer_parameter(source, names, label)
value = first_parameter(source, names, label);
validateattributes(value, {'numeric'}, ...
    {'scalar', 'integer', 'positive', 'finite'});
value = double(value);
end

function value = positive_parameter(source, names, label)
value = first_parameter(source, names, label);
validateattributes(value, {'numeric'}, {'scalar', 'positive', 'finite'});
value = double(value);
end

function value = first_parameter(source, names, label)
value = [];
for nameIndex = 1:numel(names)
    if isfield(source, names{nameIndex})
        value = source.(names{nameIndex});
        break;
    end
end
assert(~isempty(value), 'Missing %s parameter.', label);
end

function value = vector_parameter(source, name, fallback)
value = fallback;
if isfield(source, name)
    value = source.(name);
end
validateattributes(value, {'numeric'}, {'vector', 'numel', 3, 'finite'});
value = double(value(:).');
end

function token = number_token(value)
token = string(sprintf('%.15g', value));
end
