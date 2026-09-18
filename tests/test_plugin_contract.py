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

    def test_focus_schema_exposes_configurable_multi_user_scenario(self) -> None:
        properties = self.plugin.CREATE_FOCUS_TASK_SCHEMA["parameters"]["properties"]
        self.assertEqual(properties["element_count"]["enum"], [64, 144, 256, 400])
        self.assertEqual(properties["additional_targets_mm"]["maxItems"], 7)
        self.assertEqual(properties["modulation_frequency_mhz"]["default"], 200)
        self.assertIn("rhcp", properties["polarization"]["enum"])

    def test_array_design_tools_have_bounded_deterministic_schemas(self) -> None:
        search = self.plugin.SEARCH_ARRAY_GEOMETRY_SCHEMA["parameters"]
        self.assertEqual(search["properties"]["geometry_family"]["enum"], ["spherical_cap"])
        self.assertIn("candidate_budget", search["required"])
        self.assertIn("seed", search["required"])

    def test_metasurface_tools_expose_binary_transmission_and_bounded_search(self) -> None:
        create = self.plugin.CREATE_METASURFACE_DESIGN_TASK_SCHEMA["parameters"]
        optimize = self.plugin.OPTIMIZE_METASURFACE_CANDIDATE_SCHEMA["parameters"]
        self.assertEqual(
            create["properties"]["incident_wave"]["enum"],
            ["plane_wave", "horn_spherical_wave"],
        )
        self.assertEqual(create["properties"]["array_size"]["maxItems"], 2)
        self.assertIn("binary_states", create["required"])
        self.assertEqual(optimize["properties"]["optimizer"]["enum"], ["binary_coordinate_descent_v1"])
        self.assertEqual(optimize["properties"]["max_iterations"]["maximum"], 8)
        self.assertEqual(optimize["properties"]["guard_weight"]["maximum"], 2)

    def test_all_sixteen_tools_register_without_removing_existing_tools(self) -> None:
        registrations = []
        class Context:
            def register_tool(self, **kwargs):
                registrations.append(kwargs["name"])
        self.plugin.register(Context())
        self.assertEqual(len(registrations), 16)
        self.assertTrue({
            "create_focus_task", "get_focus_task_state", "refine_focus",
            "create_array_design_task", "save_array_design",
            "create_metasurface_design_task", "evaluate_metasurface_baseline",
            "optimize_metasurface_candidate", "evaluate_metasurface_design",
            "save_metasurface_design", "build_metasurface_cst_model",
        }.issubset(registrations))


if __name__ == "__main__":
    unittest.main()
