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

CREATE_FOCUS_TASK_SCHEMA = {
    "name": "create_focus_task",
    "description": (
        "Call first to create and structure a new scientific near-field focusing "
        "task. It records the researcher's immutable desired target and constraints, "
        "plus the initial commanded target used by the workflow. Call it exactly "
        "once per requested task."
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
        },
        "required": ["target_mm"],
        "additionalProperties": False,
    },
}

RUN_FOCUS_SIMULATION_SCHEMA = {
    "name": "run_focus_simulation",
    "description": (
        "Call after create_focus_task or refine_focus to invoke the real MATLAB "
        "electromagnetic solver and focusing algorithm for the current commanded "
        "target. It executes exactly one experiment and returns measured numerical "
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
