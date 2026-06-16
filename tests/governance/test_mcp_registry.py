"""MCP server registry — vendor-neutral SSoT invariants.

Pins: every integrations/mcp/<id>.json loads + validates, transport/url/command
hard rails hold, no secret VALUES are stored (env key names only), and
supports_providers excludes Ollama (backend, not MCP host).
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.mcp_registry import (
    MCP_CAPABLE_PROVIDERS,
    McpAuth,
    McpServer,
    McpValidationError,
    load_mcp_server_from_dict,
    load_mcp_servers,
    validate_mcp_server,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MCP_DIR = _REPO_ROOT / "integrations" / "mcp"


class RepoMcpFilesTests(unittest.TestCase):
    def test_all_repo_mcp_specs_load_and_validate(self) -> None:
        servers = load_mcp_servers(_MCP_DIR)
        self.assertTrue(servers, "expected at least one MCP server SSoT")
        for s in servers:
            with self.subTest(server=s.id):
                validate_mcp_server(s)  # no raise

    def test_ollama_never_an_mcp_provider(self) -> None:
        self.assertNotIn("ollama", MCP_CAPABLE_PROVIDERS)
        for s in load_mcp_servers(_MCP_DIR):
            self.assertNotIn("ollama", s.supports_providers, s.id)

    def test_no_secret_values_only_env_keys(self) -> None:
        for s in load_mcp_servers(_MCP_DIR):
            if s.auth and s.auth.type not in ("none", ""):
                with self.subTest(server=s.id):
                    self.assertTrue(s.auth.env, "auth.env (key name) required")
                    self.assertNotIn("://", s.auth.env)
                    self.assertEqual(s.auth.env, s.auth.env.strip())


class ValidationTests(unittest.TestCase):
    def _base(self, **over):
        d = {
            "id": "x", "name": "X", "description": "d", "transport": "http",
            "url": "https://example.com/mcp", "supports_providers": ["claude"],
        }
        d.update(over)
        return d

    def test_http_requires_url(self) -> None:
        with self.assertRaises(McpValidationError):
            load_mcp_server_from_dict(self._base(transport="http", url=""))

    def test_stdio_requires_command(self) -> None:
        with self.assertRaises(McpValidationError):
            load_mcp_server_from_dict(self._base(transport="stdio", url="", command=""))

    def test_unknown_transport_rejected(self) -> None:
        with self.assertRaises(McpValidationError):
            load_mcp_server_from_dict(self._base(transport="grpc"))

    def test_ollama_provider_rejected(self) -> None:
        with self.assertRaises(McpValidationError):
            load_mcp_server_from_dict(self._base(supports_providers=["claude", "ollama"]))

    def test_auth_env_must_be_key_not_value(self) -> None:
        with self.assertRaises(McpValidationError):
            validate_mcp_server(
                McpServer(
                    id="y", name="Y", description="d", transport="http",
                    url="https://e/mcp", supports_providers=("claude",),
                    auth=McpAuth(type="bearer", env="https://token-value-leak"),
                )
            )

    def test_valid_stdio_server(self) -> None:
        s = load_mcp_server_from_dict(
            self._base(transport="stdio", url="", command="npx some-mcp")
        )
        self.assertEqual(s.transport, "stdio")
        self.assertEqual(s.command, "npx some-mcp")


if __name__ == "__main__":
    unittest.main()
