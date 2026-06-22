"""Per-provider daily budget — honest enforcement (no fake), persistence, routing fallback.

Proves the wave-2 multi-brain routing gap closure:
- per-provider daily token limits parse from config (0/absent = unbounded, never invented);
- spend counts only successful non-throttled submits (a held attempt burned nothing);
- an over-budget provider is **unavailable** → routing/submit honestly fall back to the next
  candidate, and a fully-exhausted chain returns a budget_throttled result (never a faked send);
- limits persist to the single canonical config and survive reload.

Pure / stdlib (in-memory rows + a fake transport) → runs in the bare CI install.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_provider.usage import provider_budget as pb
from forgekit_provider.usage import ledger
from forgekit_provider.policy import provider_ops as ops, provider_config as pc, routing as rt
from forgekit_provider.chat import models as m
from forgekit_provider.chat.service import SubmitService


class FakeTransport:
    """openai-compatible live for any provider it's asked to call (ollama/gemini compat)."""

    def __init__(self, *, reply="ok reply"):
        self.reply = reply
        self.calls = []

    def openai_chat(self, *, endpoint, model, prompt, api_key=""):
        self.calls.append((endpoint, model))
        return self.reply

    def ollama_reachable(self, endpoint):
        return True

    def ollama_models(self, endpoint):
        return ("gemma3:latest",)


def _rows(*pairs, throttled=False):
    """Build ledger rows: each pair = (provider, total_tokens)."""
    return [{"provider": p, "total_tokens": t, "throttled": throttled} for p, t in pairs]


class BudgetUnitTests(unittest.TestCase):
    CFG = {"budget_policy": {"provider_daily_limits": {"gemini": 100, "ollama": 0}}}

    def test_limits_drop_zero_and_invalid(self) -> None:
        self.assertEqual(pb.provider_limits(self.CFG), {"gemini": 100})   # ollama 0 = unbounded
        self.assertEqual(pb.provider_limits({}), {})                       # absent = unbounded

    def test_spent_excludes_throttled(self) -> None:
        rows = _rows(("gemini", 80), ("gemini", 30)) + _rows(("gemini", 999), throttled=True)
        self.assertEqual(pb.provider_spent(rows, "gemini"), 110)           # throttled burned nothing

    def test_over_and_availability(self) -> None:
        rows = _rows(("gemini", 120))
        self.assertEqual(sorted(pb.over_budget_providers(self.CFG, rows)), ["gemini"])
        avail = pb.availability(self.CFG, rows)
        self.assertFalse(avail("gemini"))
        self.assertTrue(avail("ollama"))                                   # unbounded → always available

    def test_under_budget_is_available(self) -> None:
        rows = _rows(("gemini", 50))
        self.assertEqual(pb.over_budget_providers(self.CFG, rows), frozenset())

    def test_no_config_is_unbounded(self) -> None:
        self.assertEqual(pb.over_budget_providers({}, _rows(("gemini", 10 ** 9))), frozenset())


class RoutingFallbackTests(unittest.TestCase):
    def test_over_budget_declared_falls_back_to_next(self) -> None:
        # default_chat declared = gemini, explicit fallback = ollama; gemini over budget.
        cfg = pc.load_provider_config({
            "primary_provider": "gemini",
            "linked_providers": ["gemini", "ollama"],
            "slot_routing": {"default_chat": "gemini"},
            "fallback_policy": {"slot_fallback_orders": {"default_chat": ["ollama"]}},
            "budget_policy": {"provider_daily_limits": {"gemini": 100}},
        })
        avail = pb.availability({"budget_policy": {"provider_daily_limits": {"gemini": 100}}},
                                _rows(("gemini", 150)))
        res = rt.resolve_routing(cfg, pc.SLOT_DEFAULT_CHAT, available=avail)
        self.assertEqual(res.actual_provider, "ollama")     # honest fallback, gemini ring-fenced
        self.assertTrue(res.fallback_used)
        self.assertEqual(res.status, rt.RESOLVE_FALLBACK)


class SubmitEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.env = {"FORGEKIT_HOME": str(self.tmp)}

    def _spend(self, pid: str, tokens: int) -> None:
        ledger.append_event(
            ledger.UsageEvent(ts=ledger.now_ts(self.env), provider=pid, total_tokens=tokens,
                              success=True),
            env=self.env)

    def _cfg(self, **over):
        base = {
            "primary_provider": "gemini",
            "linked_providers": ["gemini", "ollama"],
            "slot_routing": {"default_chat": "gemini"},
            "fallback_policy": {"slot_fallback_orders": {"default_chat": ["ollama"]}},
            "budget_policy": {"provider_daily_limits": {"gemini": 100}},
        }
        base.update(over)
        return base

    def test_over_budget_head_falls_back_to_live_provider(self) -> None:
        self._spend("gemini", 150)                          # gemini over its 100/day limit today
        svc = SubmitService(transport=FakeTransport(), env=self.env, config=self._cfg())
        res = svc.submit("hi")
        self.assertTrue(res.ok)
        self.assertEqual(res.provider_id, "ollama")         # skipped gemini → live fallback
        self.assertTrue(res.fallback_used)

    def test_whole_chain_over_budget_returns_honest_throttle(self) -> None:
        self._spend("gemini", 150)
        self._spend("ollama", 150)
        cfg = self._cfg(budget_policy={"provider_daily_limits": {"gemini": 100, "ollama": 100}})
        svc = SubmitService(transport=FakeTransport(), env=self.env, config=cfg)
        res = svc.submit("hi")
        self.assertFalse(res.ok)                            # no faked send
        self.assertEqual(res.category, m.CAT_BUDGET_THROTTLED)
        self.assertTrue(res.throttled)

    def test_unconfigured_budget_is_noop(self) -> None:
        # no per-provider limits → normal submit (gemini head answers live), no budget skip.
        cfg = self._cfg(budget_policy={})
        svc = SubmitService(transport=FakeTransport(), env=self.env, config=cfg)
        self.assertEqual(svc._over_budget_providers(), frozenset())
        self.assertTrue(svc.submit("hi").ok)


class PersistenceTests(unittest.TestCase):
    def test_set_provider_budget_persists_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env = {"FORGEKIT_HOME": home}
            base = ops.set_primary(ops.load_raw_config(env=env), "gemini")   # a valid brain first
            cfg = ops.set_provider_budget(base, "gemini", 50000)
            ok, where = ops.persist_config(cfg, env=env)
            self.assertTrue(ok, where)
            reloaded = ops.load_raw_config(env=env)                    # fresh read = restart
            self.assertEqual(pb.provider_limits(reloaded), {"gemini": 50000})
            # clearing (<=0) removes the limit → unbounded again.
            cleared = ops.set_provider_budget(reloaded, "gemini", 0)
            self.assertEqual(pb.provider_limits(cleared), {})


if __name__ == "__main__":
    unittest.main()
