"""Provider onboarding / connect guard — honest diagnosis + /setup wizard + console wiring.

Pure / CI-safe. Proves the control-plane provider-connect layer:
- diagnoses each provider TYPE honestly (CLI attach vs API key vs local daemon) with NO
  fake-live — claude/codex are routing participants (connected but not live_capable),
  gemini/ollama are the live lane only when actually verified;
- the /setup wizard assesses (connect checks + recommendation) and applies (persist+verify)
  the four-brain preset, surviving a reload;
- the console routes /setup and /provider connect|test|recommended.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_provider_connect import diagnose, wizard, surface, status as st


class FakeProbe:
    """Deterministic probe — no real IO."""

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


class DiagnosisHonestyTests(unittest.TestCase):
    def test_claude_cli_attach_is_connected_but_not_live(self) -> None:
        s = diagnose.diagnose_provider("claude", {}, probe=FakeProbe(claude=True))
        self.assertEqual(s.state, st.STATE_CONNECTED)
        self.assertFalse(s.live_capable)            # routing participant, NOT live transport
        self.assertEqual(s.transport, st.TRANSPORT_CLI)

    def test_claude_no_login_is_missing_cli_auth(self) -> None:
        s = diagnose.diagnose_provider("claude", {}, probe=FakeProbe(claude=False))
        self.assertEqual(s.state, st.STATE_MISSING_CLI_AUTH)
        self.assertFalse(s.live_capable)

    def test_gemini_key_present_is_live(self) -> None:
        s = diagnose.diagnose_provider("gemini", {}, probe=FakeProbe(gemini_key=True))
        self.assertEqual(s.state, st.STATE_CONNECTED)
        self.assertTrue(s.live_capable)
        self.assertEqual(s.transport, st.TRANSPORT_OPENAI)

    def test_gemini_no_key_is_missing_key(self) -> None:
        s = diagnose.diagnose_provider("gemini", {}, probe=FakeProbe(gemini_key=False))
        self.assertEqual(s.state, st.STATE_MISSING_KEY)
        self.assertFalse(s.live_capable)

    def test_ollama_daemon_down_then_model_missing_then_connected(self) -> None:
        self.assertEqual(diagnose.diagnose_provider("ollama", {}, probe=FakeProbe(ollama_up=False)).state,
                         st.STATE_DAEMON_DOWN)
        self.assertEqual(diagnose.diagnose_provider("ollama", {}, probe=FakeProbe(ollama_up=True, models=())).state,
                         st.STATE_MODEL_MISSING)
        ok = diagnose.diagnose_provider("ollama", {}, probe=FakeProbe(ollama_up=True, models=("gemma3:latest",)))
        self.assertEqual(ok.state, st.STATE_CONNECTED)
        self.assertTrue(ok.live_capable)

    def test_no_fake_live_unknown_when_undetectable(self) -> None:
        # CLI undetectable (None) → missing_cli_auth, never connected/live
        s = diagnose.diagnose_provider("codex", {}, probe=FakeProbe(codex=None))
        self.assertIn(s.state, (st.STATE_MISSING_CLI_AUTH,))
        self.assertFalse(s.live_capable)


class WizardTests(unittest.TestCase):
    def _probe(self):
        return FakeProbe(claude=True, gemini_key=True, ollama_up=True, models=("gemma3:latest",))

    def test_assess_verdict_and_live_lane(self) -> None:
        b = wizard.assess({}, probe=self._probe())
        self.assertTrue(b.ready)
        self.assertEqual(set(b.live_lane), {"gemini", "ollama"})   # only verified-live providers
        self.assertEqual(b.recommended_preset, "four-brain")

    def test_setup_required_when_no_live_lane(self) -> None:
        # only claude/codex attachable, no gemini key, no ollama → not ready
        b = wizard.assess({}, probe=FakeProbe(claude=True, codex=True))
        self.assertFalse(b.ready)
        self.assertEqual(b.verdict, "setup-required")

    def test_apply_recommended_persists_and_verifies(self) -> None:
        cfgp = Path(tempfile.mkdtemp()) / "config.json"
        ok, msg, post = wizard.apply_recommended(path=cfgp, probe=self._probe())
        self.assertTrue(ok)
        raw = json.loads(cfgp.read_text())
        self.assertEqual(raw["primary_provider"], "claude")
        self.assertEqual(raw["slot_routing"]["default_chat"], "gemini")
        self.assertEqual(raw["model_overrides"]["ollama"], "gemma3:latest")
        # reload (restart) → still ready with the same live lane
        self.assertTrue(wizard.assess(raw, probe=self._probe()).ready)
        self.assertIn("primary brain = claude", msg)
        self.assertIn("gemini", msg)               # actual live lane surfaced

    def test_apply_does_not_claim_cli_became_live(self) -> None:
        # no gemini key / no ollama → claude/codex only; apply persists but live lane honest
        cfgp = Path(tempfile.mkdtemp()) / "config.json"
        ok, msg, post = wizard.apply_recommended(path=cfgp, probe=FakeProbe(claude=True, codex=True))
        self.assertTrue(ok)                         # config written
        self.assertFalse(post.ready)                # but NOT live (honest)
        self.assertIn("없음", msg)                  # honest "no live lane" note


class SurfaceAndRoutingTests(unittest.TestCase):
    def test_setup_surface_separates_brain_vs_transport(self) -> None:
        lines = "\n".join(surface.setup_status_lines(
            {}, probe=FakeProbe(claude=True, gemini_key=True)))
        self.assertIn("routing only", lines)        # claude participant
        self.assertIn("live", lines)                # gemini live
        self.assertIn("four-brain", lines)

    def test_console_routes_setup_and_provider_connect(self) -> None:
        from forgekit_console.commands.parser import parse_input
        from forgekit_console.commands.router import build_default_context, route
        ctx = build_default_context(Path("."))
        self.assertIn("provider onboarding", "\n".join(route(parse_input("/setup"), ctx).lines))
        self.assertIn("provider test", "\n".join(route(parse_input("/provider test gemini"), ctx).lines))
        self.assertIn("추천 brain", "\n".join(route(parse_input("/provider recommended"), ctx).lines))


if __name__ == "__main__":
    unittest.main()
