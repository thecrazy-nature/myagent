"""Small command-line demonstration of the Python-MATLAB bridge."""

from __future__ import annotations

import argparse

from bridge import MatlabBridgeError, run_simulation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one real MATLAB focus simulation.")
    parser.add_argument(
        "--target",
        nargs=3,
        type=float,
        default=[0.0, 0.0, 100.0],
        metavar=("X_MM", "Y_MM", "Z_MM"),
    )
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--timeout-sec", type=float, default=300.0)
    arguments = parser.parse_args()

    try:
        result = run_simulation(
            arguments.target,
            task_id=arguments.task_id,
            timeout_sec=arguments.timeout_sec,
        )
    except MatlabBridgeError as exception:
        print(f"Bridge error: {exception}")
        if exception.run_dir is not None:
            print(f"Run directory: {exception.run_dir}")
        return 1

    print(f"Task ID: {result['task_id']}")
    print(f"Requested focus: {result['requested_focus_mm']} mm")
    print(f"Actual peak: {result['actual_peak_mm']} mm")
    print(f"Runtime: {result['runtime_sec']} s")
    print(f"Status: {result['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
