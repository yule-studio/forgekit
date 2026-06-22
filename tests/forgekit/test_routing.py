"""Submit routing resolution teeth (forgekit brain).

Proves mode actually steers the slot, declared vs actual are distinguishable,
explicit fallback is used (and surfaced) while implicit-local is NOT unless opted in,
CLI providers route but report unsupported_in_console (never faked live), and a
no-config state resolves to setup-required.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.policy import provider_config as pc
from forgekit_console.policy import routing as r
from forgekit_console.policy import runtime_mode as rm


def _cfg(**kw):
    base = {"primary_provider": "ollama", "linked_providers": ["ollama"]}
    base.update(kw)
    return pc.load_provider_config(base)


class ModeSlotTests(unittest.TestCase):
    def test_mode_steers_slot(self) -> None:
        self.assertEqual(r.mode_submit_slot(rm.MODE_RESEARCH), pc.SLOT_RESEARCH)
        self.assertEqual(r.mode_submit_slot(rm.MODE_DELIVERY), pc.SLOT_EXECUTION)
        self.assertEqual(r.mode_submit_slot(rm.MODE_COST_SAVE), pc.SLOT_COMPRESSION)
        self.assertEqual(r.mode_submit_slot(rm.MODE_INTERACTIVE), pc.SLOT_DEFAULT_CHAT)


class ResolveTests(unittest.TestCase):
    def test_no_config_is_setup_required(self) -> None:
        res = r.resolve_submit(pc.load_provider_config({}), rm.MODE_INTERACTIVE)
        self.assertEqual(res.status, r.RESOLVE_NO_CONFIG)
        self.assertFalse(res.is_live_capable)

    def test_supported_primary_resolves_live(self) -> None:
        res = r.resolve_submit(_cfg(), rm.MODE_INTERACTIVE)
        self.assertEqual(res.status, r.RESOLVE_OK)
        self.assertEqual(res.actual_provider, "ollama")
        self.assertTrue(res.submit_supported)
        self.assertFalse(res.fallback_used)

    def test_mode_steers_nonchat_work_but_chat_stays_default_chat(self) -> None:
        # research slot routed to gemini. A NON-CHAT work item in research mode resolves to the
        # research slot (gemini); a CHAT turn stays on default_chat (ollama) regardless of mode —
        # chat is never silently re-routed to a work slot by the mode (the honest separation).
        cfg = _cfg(linked_providers=["ollama", "gemini"], slot_routing={"research": "gemini"})
        chat_research = r.resolve_submit(cfg, rm.MODE_RESEARCH)                      # default kind=chat
        work_research = r.resolve_submit(cfg, rm.MODE_RESEARCH, kind=r.WORK_NONCHAT)
        chat_interactive = r.resolve_submit(cfg, rm.MODE_INTERACTIVE)
        self.assertEqual(chat_research.actual_provider, "ollama")   # chat → default_chat, not research
        self.assertEqual(chat_interactive.actual_provider, "ollama")
        self.assertEqual(work_research.actual_provider, "gemini")   # non-chat work → mode's research slot
        # the separation helpers agree.
        self.assertEqual(r.slot_for(rm.MODE_RESEARCH, r.WORK_CHAT), "default_chat")
        self.assertEqual(r.slot_for(rm.MODE_RESEARCH, r.WORK_NONCHAT), "research")

    def test_cli_provider_is_unsupported_not_faked(self) -> None:
        # claude routes (declared) but has no console transport → unsupported, never live
        cfg = _cfg(primary_provider="claude", linked_providers=["claude"])
        res = r.resolve_submit(cfg, rm.MODE_INTERACTIVE)
        self.assertEqual(res.status, r.RESOLVE_UNSUPPORTED)
        self.assertEqual(res.declared_provider, "claude")
        self.assertFalse(res.submit_supported)

    def test_explicit_fallback_used_and_surfaced(self) -> None:
        # default_chat declared = claude (unsupported) → explicit fallback to ollama (live)
        cfg = _cfg(primary_provider="claude", linked_providers=["claude", "ollama"],
                   fallback_policy={"slot_fallback_orders": {"default_chat": ["ollama"]}})
        res = r.resolve_submit(cfg, rm.MODE_INTERACTIVE)
        self.assertEqual(res.status, r.RESOLVE_FALLBACK)
        self.assertEqual(res.declared_provider, "claude")
        self.assertEqual(res.actual_provider, "ollama")
        self.assertTrue(res.fallback_used)

    def test_implicit_local_fallback_off_by_default(self) -> None:
        # claude declared (unsupported), ollama linked but implicit fallback OFF → unsupported
        cfg = _cfg(primary_provider="claude", linked_providers=["claude", "ollama"])
        res = r.resolve_submit(cfg, rm.MODE_INTERACTIVE)
        self.assertEqual(res.status, r.RESOLVE_UNSUPPORTED)  # NOT silently ollama

    def test_implicit_local_fallback_opt_in(self) -> None:
        cfg = _cfg(primary_provider="claude", linked_providers=["claude", "ollama"],
                   fallback_policy={"implicit_local_fallback": True})
        res = r.resolve_submit(cfg, rm.MODE_INTERACTIVE)
        self.assertEqual(res.status, r.RESOLVE_FALLBACK)
        self.assertEqual(res.actual_provider, "ollama")


if __name__ == "__main__":
    unittest.main()
