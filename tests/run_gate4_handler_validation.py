"""One-shot Gate 4 integration check using the real plugin handlers and MATLAB."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = PROJECT_ROOT / ".hermes" / "plugins" / "em_focus"


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "gate4_em_focus_plugin",
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not construct the em_focus plugin module.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    plugin = load_plugin()
    created = json.loads(
        plugin.create_focus_task(
            {
                "target_mm": [0, 0, 100],
                "tolerance_mm": 5.0,
                "max_refinements": 2,
            }
        )
    )
    print("CREATE=" + json.dumps(created, ensure_ascii=False), flush=True)
    if not created.get("agent_task_id"):
        raise RuntimeError("create_focus_task did not return agent_task_id")

    agent_task_id = created["agent_task_id"]
    simulated = json.loads(
        plugin.run_focus_simulation({"agent_task_id": agent_task_id})
    )
    print("RUN=" + json.dumps(simulated, ensure_ascii=False), flush=True)
    if simulated.get("success") is not True:
        raise RuntimeError("run_focus_simulation failed")

    evaluated = json.loads(plugin.evaluate_focus({"agent_task_id": agent_task_id}))
    print("EVALUATE=" + json.dumps(evaluated, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
