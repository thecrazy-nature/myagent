"""Project-local Hermes tools for near-field electromagnetic focusing."""

from __future__ import annotations

from typing import Any

from .schemas import (
    CREATE_ARRAY_DESIGN_TASK_SCHEMA,
    CREATE_FOCUS_TASK_SCHEMA,
    EM_FOCUS_PING_SCHEMA,
    GET_FOCUS_TASK_STATE_SCHEMA,
    EVALUATE_FOCUS_SCHEMA,
    REFINE_FOCUS_SCHEMA,
    RUN_FOCUS_SIMULATION_SCHEMA,
    EVALUATE_ARRAY_GEOMETRY_SCHEMA,
    SEARCH_ARRAY_GEOMETRY_SCHEMA,
    SAVE_ARRAY_DESIGN_SCHEMA,
)
from .tools import (
    create_array_design_task,
    create_focus_task,
    em_focus_ping,
    get_focus_task_state,
    evaluate_focus,
    refine_focus,
    run_focus_simulation,
    evaluate_array_geometry,
    search_array_geometry,
    save_array_design,
)


def register(ctx: Any) -> None:
    """Register the diagnostic and four formal task tools with Hermes."""
    registrations = (
        ("em_focus_ping", EM_FOCUS_PING_SCHEMA, em_focus_ping),
        ("get_focus_task_state", GET_FOCUS_TASK_STATE_SCHEMA, get_focus_task_state),
        ("create_focus_task", CREATE_FOCUS_TASK_SCHEMA, create_focus_task),
        (
            "run_focus_simulation",
            RUN_FOCUS_SIMULATION_SCHEMA,
            run_focus_simulation,
        ),
        ("evaluate_focus", EVALUATE_FOCUS_SCHEMA, evaluate_focus),
        ("refine_focus", REFINE_FOCUS_SCHEMA, refine_focus),
        ("create_array_design_task", CREATE_ARRAY_DESIGN_TASK_SCHEMA, create_array_design_task),
        ("evaluate_array_geometry", EVALUATE_ARRAY_GEOMETRY_SCHEMA, evaluate_array_geometry),
        ("search_array_geometry", SEARCH_ARRAY_GEOMETRY_SCHEMA, search_array_geometry),
        ("save_array_design", SAVE_ARRAY_DESIGN_SCHEMA, save_array_design),
    )
    for name, schema, handler in registrations:
        ctx.register_tool(
            name=name,
            toolset="em_focus",
            schema=schema,
            handler=handler,
        )
