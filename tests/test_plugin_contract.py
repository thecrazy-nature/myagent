from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = PROJECT_ROOT / ".hermes" / "plugins" / "em_focus"


def load_plugin():
    name = "test_em_focus_plugin"
    spec = importlib.util.spec_from_file_location(
        name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load em_focus plugin.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PluginContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plugin = load_plugin()

    def test_create_requires_all_semantic_parameters(self) -> None:
        required = self.plugin.CREATE_FOCUS_TASK_SCHEMA["parameters"]["required"]
        self.assertEqual(
            required,
            ["target_mm", "tolerance_mm", "max_refinements"],
        )

    def test_handler_does_not_silently_replace_missing_constraints(self) -> None:
        result = json.loads(self.plugin.create_focus_task({"target_mm": [0, 0, 100]}))
        self.assertTrue(result["error"])
        self.assertEqual(result["error_type"], "KeyError")

    def test_array_design_tools_have_bounded_deterministic_schemas(self) -> None:
        search = self.plugin.SEARCH_ARRAY_GEOMETRY_SCHEMA["parameters"]
        self.assertEqual(search["properties"]["geometry_family"]["enum"], ["spherical_cap"])
        self.assertIn("candidate_budget", search["required"])
        self.assertIn("seed", search["required"])

    def test_all_nine_tools_register_without_removing_focus_tools(self) -> None:
        registrations = []
        class Context:
            def register_tool(self, **kwargs):
                registrations.append(kwargs["name"])
        self.plugin.register(Context())
        self.assertEqual(len(registrations), 9)
        self.assertTrue({"create_focus_task", "refine_focus", "create_array_design_task", "save_array_design"}.issubset(registrations))


if __name__ == "__main__":
    unittest.main()
