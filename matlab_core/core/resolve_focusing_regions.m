function regions = resolve_focusing_regions(cfg)
%RESOLVE_FOCUSING_REGIONS Return selection, evaluation, and display bounds.

arguments
    cfg struct
end

plan = tma_frequency_plan(cfg);
lambdaCm = 100*plan.wavelength_c_m;

selection = struct('unit', "lambda_c", ...
    'x', cfg.library.x_lambda, 'z', cfg.library.z_lambda, ...
    'source', "library_selection_grid");
evaluation = struct('unit', "lambda_c", ...
    'x', cfg.domain.x_lambda, 'z', cfg.domain.z_lambda, ...
    'source', "legacy_domain");
displayRegion = evaluation;
if isfield(cfg.library, 'display_z_lambda')
    displayRegion.z = cfg.library.display_z_lambda;
    displayRegion.source = "legacy_display_domain";
end

if isfield(cfg, 'regions') && isstruct(cfg.regions)
    selection = configured_region(cfg.regions, 'selection', selection);
    evaluation = configured_region(cfg.regions, 'evaluation', evaluation);
    displayRegion = configured_region(cfg.regions, 'display', displayRegion);
end

regions = struct('selection', resolve_region(selection, lambdaCm), ...
    'evaluation', resolve_region(evaluation, lambdaCm), ...
    'display', resolve_region(displayRegion, lambdaCm), ...
    'lambda_c_cm', lambdaCm);
end

function region = configured_region(regions, name, fallback)
region = fallback;
if isfield(regions, name) && isstruct(regions.(name))
    candidate = regions.(name);
    if all(isfield(candidate, {'unit', 'x', 'z'}))
        region = candidate;
        if ~isfield(region, 'source')
            region.source = "configured";
        end
    end
end
end

function resolved = resolve_region(region, lambdaCm)
unit = lower(string(region.unit));
x = double(region.x(:).');
z = double(region.z(:).');
assert(numel(x) == 2 && numel(z) == 2 && ...
    all(isfinite([x, z])) && x(1) < x(2) && z(1) < z(2), ...
    'A focusing region must contain increasing finite x and z limits.');
if unit == "cm"
    xCm = x;
    zCm = z;
    xLambda = x/lambdaCm;
    zLambda = z/lambdaCm;
elseif any(unit == ["lambda", "lambda_c"])
    xLambda = x;
    zLambda = z;
    xCm = x*lambdaCm;
    zCm = z*lambdaCm;
    unit = "lambda_c";
else
    error('Unsupported focusing-region unit: %s.', unit);
end
resolved = struct('unit', unit, 'source', string(region.source), ...
    'x_lambda', xLambda, 'z_lambda', zLambda, ...
    'x_cm', xCm, 'z_cm', zCm);
end
