from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASKS_PATH = PROJECT_ROOT / "benchmark" / "tasks.json"


class BenchmarkConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
        cls.tasks = cls.document["tasks"]

    def test_suite_is_designed_but_not_executed(self) -> None:
        self.assertEqual(self.document["status"], "designed_not_executed")

    def test_has_three_tasks_of_each_type(self) -> None:
        self.assertEqual(len(self.tasks), 12)
        self.assertEqual(Counter(task["type"] for task in self.tasks), {
            "A": 3,
            "B": 3,
            "C": 3,
            "D": 3,
        })

    def test_ids_are_unique_and_match_type(self) -> None:
        ids = [task["id"] for task in self.tasks]
        self.assertEqual(len(ids), len(set(ids)))
        for task in self.tasks:
            self.assertTrue(task["id"].startswith(task["type"]))

    def test_task_configs_fit_current_stable_capability(self) -> None:
        for task in self.tasks:
            config = task["task_config"]
            target = config["desired_target_mm"]
            self.assertEqual(len(target), 3)
            self.assertGreaterEqual(target[0], -75)
            self.assertLessEqual(target[0], 75)
            self.assertEqual(target[1], 0)
            self.assertGreaterEqual(target[2], 53.5714)
            self.assertLessEqual(target[2], 150)
            self.assertGreater(config["tolerance_mm"], 0)
            self.assertIsInstance(config["max_refinements"], int)
            self.assertGreaterEqual(config["max_refinements"], 0)
            self.assertTrue(task["requires_real_matlab"])

    def test_every_trajectory_starts_create_run_evaluate(self) -> None:
        prefix = ["create_focus_task", "run_focus_simulation", "evaluate_focus"]
        for task in self.tasks:
            self.assertEqual(task["expected_workflow"]["required_sequence"], prefix)

    def test_one_shot_and_replanning_policies_are_separated(self) -> None:
        for task in self.tasks:
            expected = task["expected_workflow"]
            if task["type"] in {"A", "B"}:
                self.assertFalse(expected["allow_refine"])
                self.assertEqual(task["task_config"]["max_refinements"], 0)
                self.assertNotIn("on_failed_evaluation", expected)
            else:
                self.assertTrue(expected["allow_refine"])
                self.assertGreater(task["task_config"]["max_refinements"], 0)
                self.assertEqual(
                    expected["on_failed_evaluation"],
                    ["refine_focus", "run_focus_simulation", "evaluate_focus"],
                )


if __name__ == "__main__":
    unittest.main()
