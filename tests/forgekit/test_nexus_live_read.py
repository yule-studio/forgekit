"""WT2 — Nexus live read connection: the surface reflects the REAL root/status and
operator-driven connect/disconnect, never a static not_connected or a fake read.

Pure + stdlib (tempdirs, no network) → bare CI install. Proves:
- /nexus shows not_connected with no root, exists when a real root is set (env OR config),
- /nexus set <path> persists nexus_root and reports the HONEST resulting status
  (missing for a not-yet-cloned path, exists for a real dir); /nexus clear disconnects,
- /resolve surfaces a LIVE nexus line (read/missing counts) when connected,
- the operator's role threads from the context into the read (restricted gating).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.registry import load_agents, load_commands
from forgekit_console.commands.router import ConsoleContext, route
from forgekit_console.hephaistos import nexus_ops as nops


def _ctx(*, env=None, config=None, role=""):
    return ConsoleContext(repo_root=Path("."), agents=load_agents(), commands=load_commands(),
                          env=env or {}, config=config or {}, nexus_role=role)


def _run(cmd, ctx):
    return list(route(parse_input(cmd), ctx).lines)


class NexusStatusSurfaceTests(unittest.TestCase):
    def test_not_connected_with_no_root(self) -> None:
        line = _run("/nexus", _ctx())[0]
        self.assertIn("not_connected", line)

    def test_connected_via_env_root(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        lines = _run("/nexus", _ctx(env={"FORGEKIT_NEXUS_ROOT": str(root)}))
        self.assertIn("exists", lines[0])              # real readable root → exists
        self.assertTrue(any("live read 가능" in l for l in lines))

    def test_connected_via_config_root(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        lines = _run("/nexus", _ctx(config={"nexus_root": str(root)}))
        self.assertIn("exists", lines[0])

    def test_resolve_shows_live_nexus_line_when_connected(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        lines = _run("/resolve Spring Boot JWT refresh token",
                     _ctx(env={"FORGEKIT_NEXUS_ROOT": str(root)}))
        nexus = [l for l in lines if "nexus" in l]
        self.assertTrue(nexus)
        self.assertIn("connected", nexus[0])           # live (not the static not_connected)


class NexusOpsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        self.env = {"FORGEKIT_HOME": str(self.home)}

    def _config(self) -> dict:
        p = self.home / "config.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def test_set_persists_and_reports_missing_for_absent_path(self) -> None:
        ok, msg = nops.apply_set_root(str(self.home / "nope"), env=self.env)
        self.assertTrue(ok)
        self.assertIn("missing", msg)                  # honest: not cloned yet → missing
        self.assertEqual(self._config()["nexus_root"], str(self.home / "nope"))

    def test_set_reports_exists_for_real_dir(self) -> None:
        real = self.home / "vault"
        real.mkdir()
        ok, msg = nops.apply_set_root(str(real), env=self.env)
        self.assertTrue(ok)
        self.assertIn("exists", msg)

    def test_clear_removes_root(self) -> None:
        real = self.home / "vault"
        real.mkdir()
        nops.apply_set_root(str(real), env=self.env)
        ok, _ = nops.apply_clear_root(env=self.env)
        self.assertTrue(ok)
        self.assertNotIn("nexus_root", self._config())

    def test_set_then_status_via_router(self) -> None:
        real = self.home / "vault"
        real.mkdir()
        ctx = _ctx(env=self.env)
        msg = _run(f"/nexus set {real}", ctx)[0]
        self.assertIn("exists", msg)
        # a fresh context reading the persisted config now shows connected
        cfg = self._config()
        self.assertIn("exists", _run("/nexus", _ctx(config=cfg))[0])


class RoleThreadingTests(unittest.TestCase):
    def test_context_role_threads_into_resolve(self) -> None:
        # prove the operator's nexus_role reaches the read path (restricted gating).
        from forgekit_console.hephaistos import projection as proj

        seen = {}
        orig = proj.resolve_with_sources

        def spy(request, **kw):
            seen.update(kw)
            return orig(request, **kw)

        proj.resolve_with_sources = spy
        try:
            _run("/resolve Spring Boot JWT", _ctx(role="security-engineer"))
        finally:
            proj.resolve_with_sources = orig
        self.assertEqual(seen.get("role"), "security-engineer")


if __name__ == "__main__":
    unittest.main()
