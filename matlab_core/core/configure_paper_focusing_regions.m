function cfg = configure_paper_focusing_regions(cfg, force)
%CONFIGURE_PAPER_FOCUSING_REGIONS Add the paper display/evaluation regions.

arguments
    cfg struct
    force (1,1) logical = false
end

if ~isfield(cfg, 'regions') || ~isstruct(cfg.regions)
    cfg.regions = struct();
end
selection = struct('unit', "lambda_c", ...
    'x', cfg.library.x_lambda, 'z', cfg.library.z_lambda, ...
    'source', "library_selection_grid");
if force || ~isfield(cfg.regions, 'selection')
    cfg.regions.selection = selection;
end

geometry = "planar";
if isfield(cfg.array, 'geometry')
    geometry = lower(string(cfg.array.geometry));
end
if geometry == "planar"
    paper = struct('unit', "cm", 'x', [-10, 10], 'z', [2, 16], ...
        'source', "paper_2022");
    if force || ~isfield(cfg.regions, 'evaluation')
        cfg.regions.evaluation = paper;
    end
    if force || ~isfield(cfg.regions, 'display')
        cfg.regions.display = paper;
    end
    if ~isfield(cfg, 'plot') || ~isstruct(cfg.plot)
        cfg.plot = struct();
    end
    if force || ~isfield(cfg.plot, 'coordinate_unit')
        cfg.plot.coordinate_unit = "cm";
    end
else
    evaluation = struct('unit', "lambda_c", ...
        'x', cfg.domain.x_lambda, 'z', cfg.domain.z_lambda, ...
        'source', "geometry_domain");
    displayRegion = evaluation;
    if isfield(cfg.library, 'display_z_lambda')
        displayRegion.z = cfg.library.display_z_lambda;
    end
    if force || ~isfield(cfg.regions, 'evaluation')
        cfg.regions.evaluation = evaluation;
    end
    if force || ~isfield(cfg.regions, 'display')
        cfg.regions.display = displayRegion;
    end
    if ~isfield(cfg, 'plot') || ~isstruct(cfg.plot)
        cfg.plot = struct();
    end
    if force || ~isfield(cfg.plot, 'coordinate_unit')
        cfg.plot.coordinate_unit = "lambda_c";
    end
end
end
