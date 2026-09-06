"""Project-local Hermes tools for near-field electromagnetic focusing."""

from __future__ import annotations

from typing import Any

from .schemas import (
    CREATE_FOCUS_TASK_SCHEMA,
    EM_FOCUS_PING_SCHEMA,
    EVALUATE_FOCUS_SCHEMA,
    REFINE_FOCUS_SCHEMA,
    RUN_FOCUS_SIMULATION_SCHEMA,
)
from .tools import (
    create_focus_task,
    em_focus_ping,
    evaluate_focus,
    refine_focus,
    run_focus_simulation,
)


def register(ctx: Any) -> None:
    """Register the diagnostic and four formal task tools with Hermes."""
    registrations = (
        ("em_focus_ping", EM_FOCUS_PING_SCHEMA, em_focus_ping),
        ("create_focus_task", CREATE_FOCUS_TASK_SCHEMA, create_focus_task),
        (
            "run_focus_simulation",
            RUN_FOCUS_SIMULATION_SCHEMA,
            run_focus_simulation,
        ),
        ("evaluate_focus", EVALUATE_FOCUS_SCHEMA, evaluate_focus),
        ("refine_focus", REFINE_FOCUS_SCHEMA, refine_focus),
    )
    for name, schema, handler in registrations:
        ctx.register_tool(
            name=name,
            toolset="em_focus",
            schema=schema,
            handler=handler,
        )
