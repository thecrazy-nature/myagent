function cfg = focusing_library_config(geometry)
%FOCUSING_LIBRARY_CONFIG Configuration shared by training and diagnostics.

arguments
    geometry (1,1) string = "planar"
end
geometry = lower(geometry);

cfg.physics = struct('carrier_frequency', 28e9, ...
    'modulation_frequency', 200e6, 'c', 3e8, 'harmonics', [-1, 0, 1]);
cfg.array = struct('geometry', geometry, ...
    'element_model', "scalar_point_source", ...
    'elements_x', 16, 'elements_y', 16, ...
    'spacing_x_lambda', 0.7, 'spacing_y_lambda', 0.7, ...
    'element_pattern', "isotropic", ...
    'pattern_exponent', 1, ...
    'carrier_synthesis', "axial_null", 'ideal_synthesis', "hybrid", ...
    'axial_guard_offsets_lambda', [-3, -2, 2, 3], ...
    'axial_guard_weight', 0.30, 'multifocus_guard_weight', 0.15, ...
    'carrier_regularization', 5e-3);
cfg.array.mask_synthesis = struct('grid_step_lambda', 0.5, ...
    'width_lambda', 0.65, 'depth_lambda', 0.90, ...
    'regularization', 2e-2, 'iterations', 6);
cfg.domain = struct('x_lambda', [-10, 10], 'z_lambda', [2, 16]);
cfg.objective.frequency_weights = [1.4, 0.8, 1.4];
cfg.objective.minimum_target_power = 0.55;
cfg.objective.mask_width_lambda = 0.65;
cfg.objective.mask_depth_lambda = 0.90;
cfg.objective.axial_guard_offsets_lambda = [-3, -2, 2, 3];
cfg.objective.invalid_cost = 1e8;
cfg.objective.term_weights = struct('mask', 8, 'focus', 12, ...
    'axial', 8, 'balance', 5);
cfg.optimization.grid_step_lambda = 0.75;
cfg.optimization.xi_bounds = [0.08, 0.92];
cfg.optimization.initial_xi = 0.35;
cfg.optimization.initial_beta = 1.0;
cfg.optimization.phase_grid_points = 801;
cfg.optimization.joint_fit = struct('xi_grid_points', 61, ...
    'beta_grid_points', 241, 'harmonic_scales', [0.45, 1.0, 0.45]);
cfg.evaluation.objective_power = "raw";
cfg.evaluation.display_power = "raw";
cfg.evaluation.local_peak_radius_lambda = 2.5;
cfg.library.validation_resolution = 141;
cfg.library.x_lambda = [-7, 7];
cfg.library.z_lambda = [5, 14];
cfg.library.focus_count_limits = [0, 2; 1, 3; 0, 2];
cfg.library.default_samples_per_mode = 24;
cfg.library.quality_penalty = 2.0;
cfg.library.warning_minimum_target_power = 0.35;
cfg.library.warning_maximum_error_lambda = 1.5;
cfg.library.warning_maximum_depth_lambda = 4.5;
cfg.library.app_resolution = 161;
cfg.library.display_z_lambda = [4, 16];
cfg.library.error_scale_lambda = 1.0;
cfg.library.depth_scale_lambda = 4.0;
cfg.library.minimum_separation_lambda = 3.0;
cfg.library.selection_grid_size = 32;
cfg.library.concentration_radius_scale = 1.5;
cfg.library.sideband_anchors = [-3, 9; 3, 9; -5, 12; 5, 7];
cfg.library.sideband_templates = cell(2, 1);
cfg.library.sideband_templates{1} = ...
    {[-3, 9], [3, 9], [-5, 12], [5, 7], [0, 8], [-4, 6], [4, 12]};
cfg.library.sideband_templates{2} = ...
    {[-4, 8; 4, 8], [-5, 6; 3, 11], [-3, 12; 5, 7], ...
     [-5, 10; 5, 10], [-2, 7; 4, 13]};
cfg.library.carrier_templates = cell(3, 1);
cfg.library.carrier_templates{1} = ...
    {[0, 10], [3, 8], [-3, 12], [5, 11]};
cfg.library.carrier_templates{2} = ...
    {[-3, 9; 3, 9], [-4, 7; 2, 12], [-2, 12; 4, 8], [-5, 10; 4, 10]};
cfg.library.carrier_templates{3} = ...
    {[-4, 8; 0, 12; 4, 8], [-5, 7; 0, 10; 5, 13], ...
     [-4, 12; 0, 7; 4, 12], [-6, 9; 0, 13; 6, 9]};
cfg.plot = struct('db_limits', [-30, 0], 'colormap', 'turbo');

if geometry == "hemisphere"
    cfg = hemisphere_settings(cfg);
elseif geometry ~= "planar"
    error('Unsupported library geometry: %s.', geometry);
end
cfg = configure_paper_focusing_regions(cfg);
end

function cfg = hemisphere_settings(cfg)
cfg.array.element_count = 512;
cfg.array.radius_lambda = 6.5;
cfg.array.center_lambda = [0, 0, 0];
cfg.array.hemisphere_axis = [0, 0, 1];
cfg.array.element_pattern = "inward_cosine";
cfg.array.pattern_exponent = 1;
cfg.array.ideal_synthesis = "near_field";
cfg.array.axial_guard_offsets_lambda = [-1.5, -0.75, 0.75, 1.5];
cfg.array.axial_guard_weight = 0.38;
cfg.array.multifocus_guard_weight = 0.22;
cfg.array.carrier_regularization = 8e-3;
cfg.array.mask_synthesis = struct('grid_step_lambda', 0.35, ...
    'width_lambda', 0.45, 'depth_lambda', 0.50, ...
    'regularization', 2e-2, 'iterations', 5);

cfg.domain = struct('x_lambda', [-4, 4], 'z_lambda', [0.5, 4.5]);
cfg.objective.mask_width_lambda = 0.45;
cfg.objective.mask_depth_lambda = 0.50;
cfg.objective.axial_guard_offsets_lambda = [-1.5, -0.75, 0.75, 1.5];
cfg.optimization.grid_step_lambda = 0.35;
cfg.evaluation.local_peak_radius_lambda = 1.25;

cfg.library.validation_resolution = 151;
cfg.library.x_lambda = [-3.5, 3.5];
cfg.library.z_lambda = [0.9, 3.8];
cfg.library.default_samples_per_mode = 24;
cfg.library.warning_minimum_target_power = 0.40;
cfg.library.warning_maximum_error_lambda = 0.85;
cfg.library.warning_maximum_depth_lambda = 2.5;
cfg.library.app_resolution = 181;
cfg.library.display_z_lambda = cfg.domain.z_lambda;
cfg.library.error_scale_lambda = 0.65;
cfg.library.depth_scale_lambda = 2.0;
cfg.library.minimum_separation_lambda = 1.4;
cfg.library.shell_clearance_lambda = 1.0;
cfg.library.sideband_anchors = [-2.2, 1.5; 2.2, 1.5; -2.8, 2.8; 2.8, 2.8];
cfg.library.sideband_templates = cell(2, 1);
cfg.library.sideband_templates{1} = ...
    {[-2.2, 1.5], [2.2, 1.5], [-2.8, 2.8], [2.8, 2.8], ...
     [0, 2.0], [-1.6, 3.2], [1.6, 3.2]};
cfg.library.sideband_templates{2} = ...
    {[-2.2, 1.4; 2.2, 1.4], [-2.8, 1.2; 1.8, 3.1], ...
     [-1.8, 3.2; 2.8, 1.3], [-2.8, 2.6; 2.8, 2.6], ...
     [-1.5, 1.2; 2.0, 3.3]};
cfg.library.carrier_templates = cell(3, 1);
cfg.library.carrier_templates{1} = ...
    {[0, 2.0], [1.8, 1.5], [-1.8, 3.0], [2.8, 2.5]};
cfg.library.carrier_templates{2} = ...
    {[-2.0, 2.0; 2.0, 2.0], [-2.6, 1.2; 1.4, 3.2], ...
     [-1.4, 3.2; 2.6, 1.2], [-2.8, 2.6; 2.8, 2.6]};
cfg.library.carrier_templates{3} = ...
    {[-2.5, 1.3; 0, 3.5; 2.5, 1.3], ...
     [-2.8, 1.1; 0, 2.5; 2.8, 3.3], ...
     [-2.5, 3.2; 0, 1.1; 2.5, 3.2], ...
     [-3.0, 2.1; 0, 3.6; 3.0, 2.1]};
end
