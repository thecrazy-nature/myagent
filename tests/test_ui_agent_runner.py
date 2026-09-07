from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent_runner import build_hermes_command, check_proxy


class UIAgentRunnerTests(unittest.TestCase):
    def test_command_uses_verified_hermes_module_and_project_toolset(self) -> None:
        command = build_hermes_command(
            Path("C:/hermes/python.exe"), Path("C:/temp/task.txt"), "ui_source", 900
        )
        self.assertEqual(command[1:4], ["-m", "hermes_cli.main", "chat"])
        self.assertEqual(command[command.index("--toolsets") + 1], "em_focus")
        self.assertIn("--query-file", command)

    def test_proxy_requires_process_variables(self) -> None:
        clean_environment = {
            key: value for key, value in os.environ.items()
            if key not in {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}
        }
        with patch.dict(os.environ, clean_environment, clear=True):
            result = check_proxy()
        self.assertFalse(result.available)
        self.assertIn("HTTP_PROXY", result.message)

    def test_proxy_checks_local_clash_socket(self) -> None:
        environment = {
            "HTTP_PROXY": "http://127.0.0.1:7897",
            "HTTPS_PROXY": "http://127.0.0.1:7897",
            "NO_PROXY": "localhost,127.0.0.1,::1",
        }
        connection = unittest.mock.MagicMock()
        connection.__enter__.return_value = connection
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("app.agent_runner.socket.create_connection", return_value=connection),
        ):
            result = check_proxy()
        self.assertTrue(result.available)


if __name__ == "__main__":
    unittest.main()
