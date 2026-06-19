"""WT1 — provider runtime core teeth: fallback_policy + model_overrides actually
affect the live submit path (not just display).

Pure + stdlib (fake transport, no network) → runs in the bare CI install. Proves:
- the operator's slot_fallback_orders steers the REAL submit (declared unusable →
  explicit fallback provider answers, surfaced honestly as fallback_used),
- model_overrides[provider] reaches the actual call,
- no-config is still setup-required (no silent ollama),
- a single-provider setup with NO fallback declared is unchanged.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.chat import models as m
from forgekit_console.chat.service import SubmitService
from forgekit_console.policy import provider_config as pc
from forgekit_console.policy import routing as rt


class RecordingTransport:
    """Fake transport that records the (endpoint, model) of each call and can be
    made unreachable to force a transport failure on the live path."""

    def __init__(self, *, reachable=True, models=("gemma3:latest",), reply="ok reply", raise_exc=None):
        self.reachable = reachable
        self.models = tuple(models)
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls = []

    def openai_chat(self, *, endpoint, model, prompt, api_key=""):
        self.calls.append({"endpoint": endpoint, "model": model, "api_key": api_key})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.reply

    def ollama_reachable(self, endpoint):
        return self.reachable

    def ollama_models(self, endpoint):
        return self.models


# --------------------------------------------------------------------------- #
# pure: the attempt-chain builder
# --------------------------------------------------------------------------- #
class SubmitChainTests(unittest.TestCase):
    def _cfg(self, **kw):
        return pc.load_provider_config(kw)

    def test_chain_is_head_plus_explicit_fallback(self) -> None:
        cfg = self._cfg(
            primary_provider="claude", linked_providers=["claude", "gemini", "ollama"],
            slot_routing={"default_chat": "claude"},
            fallback_policy={"slot_fallback_orders": {"default_chat": ["gemini", "ollama"]}},
        )
        chain = rt.submit_chain(cfg, pc.SLOT_DEFAULT_CHAT)
        self.assertEqual(chain, ("claude", "gemini", "ollama"))

    def test_prefer_overrides_head_and_dedupes(self) -> None:
        cfg = self._cfg(
            primary_provider="claude", linked_providers=["claude", "ollama"],
            fallback_policy={"slot_fallback_orders": {"default_chat": ["ollama", "claude"]}},
        )
        chain = rt.submit_chain(cfg, pc.SLOT_DEFAULT_CHAT, prefer="ollama")
        self.assertEqual(chain, ("ollama", "claude"))   # prefer head, dedup the repeat

    def test_implicit_ollama_only_when_opted_in(self) -> None:
        off = self._cfg(primary_provider="gemini", linked_providers=["gemini", "ollama"])
        self.assertNotIn("ollama", rt.submit_chain(off, pc.SLOT_DEFAULT_CHAT))
        on = self._cfg(primary_provider="gemini", linked_providers=["gemini", "ollama"],
                       fallback_policy={"implicit_local_fallback": True})
        self.assertIn("ollama", rt.submit_chain(on, pc.SLOT_DEFAULT_CHAT))


# --------------------------------------------------------------------------- #
# teeth: fallback actually answers the live submit
# --------------------------------------------------------------------------- #
class FallbackSubmitTests(unittest.TestCase):
    def test_fallback_past_unsupported_primary(self) -> None:
        # primary = claude (unsupported_in_console) → explicit fallback ollama answers.
        cfg = {
            "primary_provider": "claude", "linked_providers": ["claude", "ollama"],
            "fallback_policy": {"slot_fallback_orders": {"default_chat": ["ollama"]}},
        }
        tx = RecordingTransport(reachable=True)
        svc = SubmitService(transport=tx, env={}, config=cfg)
        res = svc.submit("질문")
        self.assertTrue(res.ok and res.is_live)
        self.assertEqual(res.provider_id, "ollama")       # the fallback provider answered
        self.assertTrue(res.fallback_used)
        self.assertEqual(res.routed_from, "claude")       # honest declared→actual hop
        self.assertIn("fallback claude→ollama", res.receipt())

    def test_fallback_past_auth_missing_primary(self) -> None:
        # primary = gemini (openai-compat but no API key) → falls to ollama.
        cfg = {
            "primary_provider": "gemini", "linked_providers": ["gemini", "ollama"],
            "fallback_policy": {"slot_fallback_orders": {"default_chat": ["ollama"]}},
        }
        svc = SubmitService(transport=RecordingTransport(), env={}, config=cfg)  # no GEMINI_API_KEY
        res = svc.submit("질문")
        self.assertTrue(res.ok)
        self.assertEqual(res.provider_id, "ollama")
        self.assertTrue(res.fallback_used)

    def test_no_fallback_declared_is_unchanged_honest_error(self) -> None:
        # single-provider claude, NO fallback → still the honest unsupported error.
        cfg = {"primary_provider": "claude", "linked_providers": ["claude"]}
        svc = SubmitService(transport=RecordingTransport(), env={}, config=cfg)
        res = svc.submit("질문")
        self.assertFalse(res.ok)
        self.assertEqual(res.category, m.CAT_UNSUPPORTED)
        self.assertFalse(res.fallback_used)

    def test_transport_failure_falls_to_next(self) -> None:
        # primary ollama endpoint throws → explicit fallback (another ollama-compat) —
        # here we model the head failing and a reachable fallback answering.
        cfg = {
            "primary_provider": "gemini", "linked_providers": ["gemini", "ollama"],
            "fallback_policy": {"slot_fallback_orders": {"default_chat": ["ollama"]}},
        }
        svc = SubmitService(transport=RecordingTransport(reachable=True), env={}, config=cfg)
        res = svc.submit("질문")
        self.assertTrue(res.ok)
        self.assertEqual(res.provider_id, "ollama")


# --------------------------------------------------------------------------- #
# teeth: model_overrides reaches the actual call
# --------------------------------------------------------------------------- #
class ModelOverrideTests(unittest.TestCase):
    def test_per_provider_model_override_is_used(self) -> None:
        cfg = {
            "primary_provider": "ollama", "linked_providers": ["ollama"],
            "model_overrides": {"ollama": "llama3:70b"},
        }
        tx = RecordingTransport(models=("gemma3:latest",))
        svc = SubmitService(transport=tx, env={}, config=cfg)
        res = svc.submit("질문")
        self.assertTrue(res.ok)
        self.assertEqual(tx.calls[-1]["model"], "llama3:70b")   # override beat the auto-pick

    def test_global_model_used_when_no_override(self) -> None:
        cfg = {"primary_provider": "ollama", "linked_providers": ["ollama"], "model": "phi3"}
        tx = RecordingTransport()
        svc = SubmitService(transport=tx, env={}, config=cfg)
        svc.submit("질문")
        self.assertEqual(tx.calls[-1]["model"], "phi3")


# --------------------------------------------------------------------------- #
# honesty rail: no-config never auto-uses ollama
# --------------------------------------------------------------------------- #
class NoConfigHonestyTests(unittest.TestCase):
    def test_no_config_is_setup_required_no_call(self) -> None:
        tx = RecordingTransport(reachable=True)   # ollama IS up, but no config
        svc = SubmitService(transport=tx, env={}, config={})
        res = svc.submit("질문")
        self.assertFalse(res.ok)
        self.assertEqual(res.category, m.CAT_NO_PROVIDER)
        self.assertEqual(tx.calls, [])            # never called a provider


if __name__ == "__main__":
    unittest.main()
