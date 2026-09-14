"""JSON schemas shown to the Hermes model for em_focus tools."""

VECTOR_SCHEMA = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": 3,
    "maxItems": 3,
}

EM_FOCUS_PING_SCHEMA = {
    "name": "em_focus_ping",
    "description": (
        "Use only to verify that this project-local em_focus plugin is loaded. "
        "It echoes a diagnostic message and never runs MATLAB."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Diagnostic message to echo unchanged.",
            }
        },
        "required": ["message"],
        "additionalProperties": False,
    },
}

GET_FOCUS_TASK_STATE_SCHEMA = {
    "name": "get_focus_task_state",
    "description": (
        "Read a previously persisted focus-task checkpoint. Use this only when "
        "recovering an interrupted Hermes session, then follow valid_next_actions; "
        "never repeat a MATLAB run that is already recorded as simulated."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_task_id": {
                "type": "string",
                "description": "Persisted focus task ID from the interrupted session.",
            }
        },
        "required": ["agent_task_id"],
        "additionalProperties": False,
    },
}

CREATE_FOCUS_TASK_SCHEMA = {
    "name": "create_focus_task",
    "description": (
        "Call first to create and structure a new scientific near-field focusing "
        "task. It records the researcher's immutable desired target(s), solver scenario, "
        "and constraints, plus the initial commanded targets used by the workflow. Call it exactly "
        "once per requested task. Always provide target_mm, tolerance_mm, and "
        "max_refinements. For multiple users, put user 1 in target_mm and the rest "
        "in additional_targets_mm. Copy user-stated constraints exactly; use documented "
        "defaults when omitted. Every user is assigned to a different modulation-harmonic "
        "order; never combine multiple users on q=0. Polarization is recorded as scenario metadata because "
        "the current scalar point-source solver does not model polarization differences."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target_mm": {
                **VECTOR_SCHEMA,
                "description": "User's desired [x, y, z] focus position in millimetres.",
            },
            "tolerance_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 5.0,
                "description": "Maximum Euclidean focus error in millimetres.",
            },
            "max_refinements": {
                "type": "integer",
                "minimum": 0,
                "default": 2,
                "description": "Maximum number of feedback corrections allowed.",
            },
            "additional_targets_mm": {
                "type": "array",
                "items": VECTOR_SCHEMA,
                "maxItems": 7,
                "default": [],
                "description": "Optional desired focus points for users 2 through 8.",
            },
            "frequency_ghz": {
                "type": "number",
                "minimum": 1,
                "maximum": 100,
                "default": 28,
                "description": "Carrier frequency in GHz used by the real MATLAB solver.",
            },
            "modulation_frequency_mhz": {
                "type": "number",
                "minimum": 1,
                "maximum": 1000,
                "default": 200,
                "description": (
                    "TMA modulation frequency in MHz. Each user is assigned a distinct "
                    "order q and is evaluated at carrier + q times this frequency."
                ),
            },
            "element_count": {
                "type": "integer",
                "enum": [64, 144, 256, 400],
                "default": 256,
                "description": "Element count for a square planar array.",
            },
            "polarization": {
                "type": "string",
                "enum": ["scalar", "x_linear", "y_linear", "rhcp", "lhcp"],
                "default": "scalar",
                "description": (
                    "Scenario label persisted with results. It does not alter fields in "
                    "the current polarization-independent scalar point-source solver."
                ),
            },
        },
        "required": ["target_mm", "tolerance_mm", "max_refinements"],
        "additionalProperties": False,
    },
}

RUN_FOCUS_SIMULATION_SCHEMA = {
    "name": "run_focus_simulation",
    "description": (
        "Call after create_focus_task or refine_focus to invoke the real MATLAB "
        "electromagnetic solver and per-harmonic focusing algorithm for the current commanded "
        "target(s). It executes exactly one experiment and returns measured numerical "
        "results. Always evaluate its result before starting another experiment."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_task_id": {
                "type": "string",
                "description": "ID returned by create_focus_task.",
            }
        },
        "required": ["agent_task_id"],
        "additionalProperties": False,
    },
}

EVALUATE_FOCUS_SCHEMA = {
    "name": "evaluate_focus",
    "description": (
        "Call exactly once after every successful MATLAB experiment to perform "
        "task-level evaluation. It compares MATLAB's measured peak with the "
        "researcher's original desired target and decides whether the requested "
        "positioning tolerance is satisfied."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_task_id": {
                "type": "string",
                "description": "ID of the task whose latest simulation is evaluated.",
            }
        },
        "required": ["agent_task_id"],
        "additionalProperties": False,
    },
}

REFINE_FOCUS_SCHEMA = {
    "name": "refine_focus",
    "description": (
        "Call only when the latest MATLAB experiment failed the user's positioning "
        "constraint and refinement budget remains. It applies a deterministic, "
        "lightweight workflow-level feedback correction with fixed alpha=0.7 to the "
        "commanded target while preserving desired_target_mm, then prepares another "
        "experiment. This demonstrates Agent Observation-to-Replanning and does not "
        "replace the MATLAB electromagnetic solver or optimizer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_task_id": {
                "type": "string",
                "description": "ID of the failed task to refine once.",
            }
        },
        "required": ["agent_task_id"],
        "additionalProperties": False,
    },
}

OBJECTIVE_WEIGHTS_SCHEMA = {
    "type": "object",
    "properties": {
        "lateral_spot": {"type": "number", "exclusiveMinimum": 0},
        "depth_of_focus": {"type": "number", "exclusiveMinimum": 0},
        "energy_concentration": {"type": "number", "exclusiveMinimum": 0},
    },
    "required": ["lateral_spot", "depth_of_focus", "energy_concentration"],
    "additionalProperties": False,
}

CREATE_ARRAY_DESIGN_TASK_SCHEMA = {
    "name": "create_array_design_task",
    "description": (
        "Call exactly once for a new array-geometry design request. It freezes the real planar "
        "baseline's element count, aperture, actual 7.5 mm minimum spacing, frequency, element "
        "model, XZ evaluation grid, and equal-total-input-power rule. It structures goals only; "
        "it does not calculate fields. The currently supported searchable family is spherical_cap."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "focus_target_mm": {**VECTOR_SCHEMA, "description": "Immutable desired [x,y,z] target in mm; y must be 0."},
            "focus_tolerance_mm": {"type": "number", "exclusiveMinimum": 0, "description": "Hard maximum focus error in mm."},
            "search_budget": {"type": "integer", "minimum": 1, "maximum": 12, "description": "Total number of non-baseline MATLAB candidates allowed."},
            "allowed_geometry_families": {
                "type": "array", "items": {"type": "string", "enum": ["spherical_cap"]},
                "minItems": 1, "uniqueItems": True,
                "description": "Low-dimensional families Hermes may choose to search."
            },
            "objective_weights": {**OBJECTIVE_WEIGHTS_SCHEMA, "description": "Positive priorities normalized by the deterministic scorer."},
            "roi_radius_mm": {"type": "number", "exclusiveMinimum": 0, "default": 5.0, "description": "XZ ROI half-width around desired x."},
            "roi_half_depth_mm": {"type": "number", "exclusiveMinimum": 0, "default": 10.0, "description": "XZ ROI half-depth around desired z."},
        },
        "required": ["focus_target_mm", "focus_tolerance_mm", "search_budget", "allowed_geometry_families", "objective_weights"],
        "additionalProperties": False,
    },
}

EVALUATE_ARRAY_GEOMETRY_SCHEMA = {
    "name": "evaluate_array_geometry",
    "description": (
        "Evaluate exactly one deterministic geometry with the dedicated real MATLAB path. "
        "Every task must call this first with geometry_family='baseline' and empty parameters. "
        "It returns measured X-direction FWHM, Z depth of focus, XZ ROI energy concentration, "
        "focus error, and fixed-input-power peak power. Never infer a Y FWHM from this XZ model."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "design_task_id": {"type": "string"},
            "geometry_family": {"type": "string", "enum": ["baseline", "spherical_cap"]},
            "parameters": {
                "type": "object",
                "properties": {"depth_mm": {"type": "number", "minimum": 0, "maximum": 20}},
                "additionalProperties": False,
            },
            "seed": {"type": "integer", "default": 0},
        },
        "required": ["design_task_id", "geometry_family", "parameters", "seed"],
        "additionalProperties": False,
    },
}

SEARCH_ARRAY_GEOMETRY_SCHEMA = {
    "name": "search_array_geometry",
    "description": (
        "Run a deterministic coarse parameter grid for one allowed family after baseline evaluation. "
        "The tool, not Hermes, creates all coordinates and rejects physical violations. All requested "
        "candidates are evaluated in one real MATLAB cold start and ranked by the documented "
        "baseline-normalized multi-objective score (lower is better). Hermes chooses whether another "
        "bounded search is warranted after examining the structured result."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "design_task_id": {"type": "string"},
            "geometry_family": {"type": "string", "enum": ["spherical_cap"]},
            "parameter_bounds": {
                "type": "object",
                "properties": {
                    "depth_mm": {
                        "type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                        "description": "Inclusive spherical-cap depth range [low, high] in mm, within [0,20]."
                    }
                },
                "required": ["depth_mm"], "additionalProperties": False,
            },
            "candidate_budget": {"type": "integer", "minimum": 1, "maximum": 12},
            "seed": {"type": "integer", "default": 0},
        },
        "required": ["design_task_id", "geometry_family", "parameter_bounds", "candidate_budget", "seed"],
        "additionalProperties": False,
    },
}

SAVE_ARRAY_DESIGN_SCHEMA = {
    "name": "save_array_design",
    "description": (
        "Persist one evaluated candidate, or the baseline if no candidate is genuinely better, as the "
        "final design. Saves full deterministic geometry JSON, element coordinate CSV, MATLAB metrics, "
        "baseline comparison, search trajectory, and selection reason. Call once after search decisions end."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "design_task_id": {"type": "string"},
            "geometry_id": {"type": "string", "description": "Evaluated geometry_id returned by baseline evaluation or search."},
            "selection_reason": {"type": "string", "minLength": 1, "description": "Truthful evidence-based reason, including trade-offs or no-improvement finding."},
        },
        "required": ["design_task_id", "geometry_id", "selection_reason"],
        "additionalProperties": False,
    },
}
