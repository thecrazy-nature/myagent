from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.agent_runner import (
    AgentRunnerError,
    build_hermes_command,
    detect_task_kind,
    read_hermes_default_model,
)


def tool_call(call_id: str, name: str) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "function": {
                    "name": "tool_call",
                    "arguments": json.dumps({"name": name, "arguments": {}}),
                },
            }],
        },
        {"role": "tool", "tool_call_id": call_id, "content": "{}"},
    ]


class UIAgentRunnerTests(unittest.TestCase):
    def test_detects_focus_from_actual_tool_calls(self) -> None:
        session = {"messages": tool_call("focus", "create_focus_task")}
        self.assertEqual(detect_task_kind(session), "focus")

    def test_detects_recovered_focus_from_checkpoint_tool(self) -> None:
        session = {"messages": tool_call("checkpoint", "get_focus_task_state")}
        self.assertEqual(detect_task_kind(session), "focus")

    def test_detects_array_design_from_actual_tool_calls(self) -> None:
        session = {"messages": tool_call("design", "create_array_design_task")}
        self.assertEqual(detect_task_kind(session), "array_design")

    def test_detects_metasurface_design_from_actual_tool_calls(self) -> None:
        session = {
            "messages": tool_call("metasurface", "create_metasurface_design_task")
        }
        self.assertEqual(detect_task_kind(session), "metasurface_design")

    def test_rejects_mixed_numerical_workflows(self) -> None:
        session = {
            "messages": tool_call("focus", "create_focus_task")
            + tool_call("design", "create_array_design_task")
        }
        with self.assertRaisesRegex(AgentRunnerError, "混用"):
            detect_task_kind(session)

    def test_session_without_domain_workflow_is_a_conversation_turn(self) -> None:
        self.assertEqual(
            detect_task_kind({"messages": [{"role": "assistant", "content": "hello"}]}),
            "conversation",
        )

    def test_command_uses_verified_hermes_module_and_project_toolset(self) -> None:
        command = build_hermes_command(
            Path("C:/hermes/python.exe"), Path("C:/temp/task.txt"), "ui_source", 900
        )
        self.assertEqual(command[1:4], ["-m", "hermes_cli.main", "chat"])
        self.assertEqual(command[command.index("--toolsets") + 1], "em_focus")
        self.assertIn("--query-file", command)

    def test_command_can_resume_the_same_hermes_conversation(self) -> None:
        command = build_hermes_command(
            Path("C:/hermes/python.exe"), Path("C:/temp/task.txt"), "ui_source", 900,
            "session_123",
        )
        self.assertEqual(command[command.index("--resume") + 1], "session_123")

    def test_command_can_override_model_and_provider(self) -> None:
        command = build_hermes_command(
            Path("C:/hermes/python.exe"),
            Path("C:/temp/task.txt"),
            "ui_source",
            900,
            model="deepseek-v4-flash",
            provider="deepseek",
        )
        self.assertEqual(command[command.index("--model") + 1], "deepseek-v4-flash")
        self.assertEqual(command[command.index("--provider") + 1], "deepseek")

    def test_reads_non_secret_default_model_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.yaml"
            config.write_text(
                "model:\n  default: deepseek-v4-flash\n  provider: deepseek\napi_key: secret\n",
                encoding="utf-8",
            )
            selected = read_hermes_default_model(config)
        self.assertEqual(selected, {"model": "deepseek-v4-flash", "provider": "deepseek"})


if __name__ == "__main__":
    unittest.main()
