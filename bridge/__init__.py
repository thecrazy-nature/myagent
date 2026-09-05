"""Public Python API for the MATLAB near-field simulation bridge."""

from .matlab_bridge import run_simulation
from .models import (
    InvalidSimulationInput,
    MatlabBridgeError,
    MatlabExecutableNotFound,
    MatlabProcessError,
    MatlabResultError,
    MatlabSimulationError,
    MatlabTimeoutError,
)

__all__ = [
    "run_simulation",
    "MatlabBridgeError",
    "InvalidSimulationInput",
    "MatlabExecutableNotFound",
    "MatlabTimeoutError",
    "MatlabProcessError",
    "MatlabResultError",
    "MatlabSimulationError",
]
