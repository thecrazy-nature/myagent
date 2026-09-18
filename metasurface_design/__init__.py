"""Persistent planar programmable-metasurface design API for Hermes tools."""

from .state import (
    attach_agent_metadata,
    build_cst_model,
    create_design_task,
    evaluate_baseline,
    evaluate_design,
    optimize_candidate,
    save_design,
)

__all__ = [
    "create_design_task",
    "evaluate_baseline",
    "optimize_candidate",
    "evaluate_design",
    "save_design",
    "build_cst_model",
    "attach_agent_metadata",
]
