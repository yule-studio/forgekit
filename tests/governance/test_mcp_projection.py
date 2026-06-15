"""MCP projection drift-guard + secret-free invariants.

Pins: ``integrations/mcp/*.json`` (SSoT) projects deterministically onto the
per-provider configs, the committed artifacts are in sync, a server only appears
in a provider's projection when it declares that provider, and **no secret
values** are emitted (env-var references only).
"""

from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.mcp_registry import load_mcp_servers

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _gen():
    spec = importlib.util.spec_from_file_location(
        "sync_mcp_projection", _REPO_ROOT / "scripts" / "sync_mcp_projection.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ProjectionDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _gen()
        self.servers = load_mcp_servers(_REPO_ROOT / "integrations" / "mcp")
        self.artifacts = self.mod.build_mcp_artifacts(self.servers)

    def test_no_drift(self) -> None:
        drift = self.mod.check_artifacts(_REPO_ROOT, self.artifacts)
        self.assertEqual(drift, [], f"MCP projection drift — run sync_mcp_projection.py: {drift}")

    def test_expected_targets(self) -> None:
        # figma supports all three → all three provider artifacts exist
        self.assertIn(".mcp.json", self.artifacts)
        self.assertIn(".codex-plugin/mcp.toml", self.artifacts)
        self.assertIn(".gemini-plugin/mcp.json", self.artifacts)

    def test_provider_scoping(self) -> None:
        claude = json.loads(self.artifacts[".mcp.json"])["mcpServers"]
        for sid in claude:
            server = next(s for s in self.servers if s.id == sid)
            self.assertIn("claude", server.supports_providers)


class SecretFreeTests(unittest.TestCase):
    def test_no_secret_values_only_env_refs(self) -> None:
        mod = _gen()
        artifacts = mod.build_mcp_artifacts(load_mcp_servers(_REPO_ROOT / "integrations" / "mcp"))
        for rel, content in artifacts.items():
            with self.subTest(artifact=rel):
                # an env reference is fine; a bare long token-like value is not
                self.assertNotRegex(
                    content,
                    r"(?i)(bearer\s+[A-Za-z0-9_\-]{20,}(?!\}))",
                    "looks like a literal bearer token value",
                )
                # auth must be expressed as ${ENV} or *_env_var, never a value
                if "Authorization" in content:
                    self.assertRegex(content, r"\$\{[A-Z0-9_]+\}")


if __name__ == "__main__":
    unittest.main()
