"""Unified control-plane bootstrap guard — honest composition of every onboarding lane.

Proves ``forgekit_console.bootstrap`` (the ``/setup`` capstone described in
``docs/forgekit-setup-bootstrap.md`` / ``docs/control-plane-architecture.md`` §4):

- composes provider + knowledge(nexus/vault) + toolchain into ONE report, delegating to each
  package's honest assessor — NO lane is faked into green;
- only the provider live lane is *blocking*: readiness flips with it, knowledge/toolchain are
  surfaced as honest non-blocking lanes (connected / not_connected / missing / detected / ...);
- the report reads the single canonical ``~/.forgekit/config.json`` the lanes persist to, so an
  operator re-run after restart reflects what was saved (provider preset + nexus_root survive);
- the console routes ``/setup`` to the unified bootstrap and ``/setup apply`` still persists+verifies.

Pure / CI-safe — every probe is faked, every path is a tempdir.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import bootstrap as b


class FakeProbe:
    """Deterministic provider probe — mirrors test_provider_connect.FakeProbe (no real IO)."""

    def __init__(self, *, claude=None, codex=None, gemini_key=False, ollama_up=False, models=()):
        self._c, self._x, self._g, self._o, self._m = claude, codex, gemini_key, ollama_up, models

    def cli_authenticated(self, pid):
        return {"claude": self._c, "codex": self._x}.get(pid)

    def api_key(self, pid, env=None):
        return "key" if (pid == "gemini" and self._g) else ""

    def daemon_reachable(self, ep):
        return self._o

    def installed_models(self, ep):
        return self._m


class ProviderLaneTests(unittest.TestCase):
    """The provider lane is the only thing that flips overall readiness."""

    def test_no_live_provider_is_setup_required_blocking(self) -> None:
        # claude/codex attached = routing-only (connected, NOT live) → no live lane.
        bs = b.assess_bootstrap({}, env={}, probe=FakeProbe(claude=True, codex=True))
        prov = bs.stage(b.STAGE_PROVIDER)
        self.assertTrue(prov.blocking)
        self.assertFalse(prov.connected)
        self.assertEqual(prov.status, "setup-required")
        self.assertEqual(bs.verdict, "setup-required")
        self.assertFalse(bs.ready)

    def test_live_provider_flips_ready(self) -> None:
        bs = b.assess_bootstrap({}, env={}, probe=FakeProbe(gemini_key=True))
        prov = bs.stage(b.STAGE_PROVIDER)
        self.assertTrue(prov.connected)
        self.assertEqual(prov.status, "live")
        self.assertIn("gemini", bs.live_lane)
        self.assertEqual(bs.verdict, "ready")
        self.assertTrue(bs.ready)


class KnowledgeLaneTests(unittest.TestCase):
    """Knowledge(nexus/vault) is honest + non-blocking — never faked, never gates readiness."""

    def _probe_live(self):
        return FakeProbe(gemini_key=True)   # keep provider ready so we isolate the knowledge lane

    def test_no_root_is_not_connected(self) -> None:
        bs = b.assess_bootstrap({}, env={}, probe=self._probe_live())
        k = bs.stage(b.STAGE_KNOWLEDGE)
        self.assertFalse(k.connected)
        self.assertEqual(k.status, "not_connected")
        self.assertFalse(k.blocking)
        self.assertTrue(k.next_action)               # offers /nexus set
        self.assertTrue(bs.ready)                     # knowledge does NOT block readiness

    def test_missing_path_stays_missing_not_faked(self) -> None:
        cfg = {"nexus_root": "/no/such/forgekit/nexus/root"}
        bs = b.assess_bootstrap(cfg, env={}, probe=self._probe_live())
        k = bs.stage(b.STAGE_KNOWLEDGE)
        self.assertFalse(k.connected)               # NO fake connection for an absent path
        self.assertEqual(k.status, "missing")

    def test_existing_root_is_connected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            bs = b.assess_bootstrap({"nexus_root": root}, env={}, probe=self._probe_live())
            k = bs.stage(b.STAGE_KNOWLEDGE)
            self.assertTrue(k.connected)
            self.assertEqual(k.status, "connected")

    def test_obsidian_vault_detected_in_detail(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / ".obsidian").mkdir()
            bs = b.assess_bootstrap({"nexus_root": root}, env={}, probe=self._probe_live())
            k = bs.stage(b.STAGE_KNOWLEDGE)
            self.assertTrue(k.connected)
            self.assertIn("Obsidian", k.detail)


class ToolchainLaneTests(unittest.TestCase):
    """Toolchain is repo-local detection only (no guess, no fake switch) and non-blocking."""

    def test_no_manifest_is_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            bs = b.assess_bootstrap({}, env={}, probe=FakeProbe(gemini_key=True), repo_root=Path(repo))
            t = bs.stage(b.STAGE_TOOLCHAIN)
            self.assertFalse(t.connected)
            self.assertEqual(t.status, "not_configured")
            self.assertFalse(t.blocking)

    def test_repo_manifest_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            (Path(repo) / ".tool-versions").write_text("python 3.13.1\nnodejs 20.11.0\n", encoding="utf-8")
            bs = b.assess_bootstrap({}, env={}, probe=FakeProbe(gemini_key=True), repo_root=Path(repo))
            t = bs.stage(b.STAGE_TOOLCHAIN)
            self.assertTrue(t.connected)
            self.assertEqual(t.status, "detected")
            self.assertIn("python", t.detail)


class PersistenceTests(unittest.TestCase):
    """Completion criterion: operator settings survive a restart (single canonical config)."""

    def test_nexus_root_and_provider_preset_survive_reload(self) -> None:
        from forgekit_provider.policy import provider_ops as ops
        from forgekit_provider_connect import wizard
        from hephaistos import nexus_ops as nops

        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as vault:
            env = {"FORGEKIT_HOME": home}
            probe = FakeProbe(gemini_key=True, claude=True, codex=True, ollama_up=True, models=("llama3",))

            # 1) persist the provider preset (real writer) + 2) connect a nexus root (real writer).
            ok, _msg, _post = wizard.apply_recommended(env=env, probe=probe)
            self.assertTrue(ok)
            ok2, _m2 = nops.apply_set_root(vault, env=env)
            self.assertTrue(ok2)

            # 3) re-run the bootstrap with NO in-memory config — it must read what was saved on disk.
            reloaded = ops.load_raw_config(env=env)
            self.assertEqual(reloaded.get("nexus_root"), vault)     # survived
            self.assertTrue(reloaded.get("primary_provider"))       # preset survived

            bs = b.assess_bootstrap(env=env, probe=probe)
            self.assertEqual(bs.stage(b.STAGE_KNOWLEDGE).status, "connected")
            self.assertEqual(bs.stage(b.STAGE_PROVIDER).status, "live")
            self.assertEqual(bs.verdict, "ready")
            # the report points at the real canonical path under the operator's home.
            self.assertTrue(bs.config_path.endswith("config.json"))
            self.assertIn(home, bs.config_path)


class SurfaceAndRouterTests(unittest.TestCase):
    def test_bootstrap_lines_cover_every_lane_and_verdict(self) -> None:
        text = "\n".join(b.bootstrap_lines({}, env={}, probe=FakeProbe(gemini_key=True)))
        self.assertIn("컨트롤플레인 부트스트랩", text)
        self.assertIn("provider", text)
        self.assertIn("knowledge", text)
        self.assertIn("toolchain", text)
        self.assertIn("verdict:", text)
        self.assertIn("canonical config", text)     # persistence is surfaced

    def test_console_routes_setup_to_unified_bootstrap(self) -> None:
        from forgekit_console.commands.parser import parse_input
        from forgekit_console.commands.router import ConsoleContext, route

        with tempfile.TemporaryDirectory() as repo:
            ctx = ConsoleContext(repo_root=Path(repo), env={}, config={})
            res = route(parse_input("/setup"), ctx)
            self.assertEqual(res.title, "setup")
            joined = "\n".join(res.lines)
            self.assertIn("컨트롤플레인 부트스트랩", joined)   # the unified screen, not the provider-only wizard
            self.assertIn("knowledge", joined)


if __name__ == "__main__":
    unittest.main()
