"""Provider control-plane ops — config edit/persist + brain map + setup review.

Proves the operator can actually build a valid brain config (use/link/unlink/route),
that persistence refuses invalid configs (no fake-ready), and that brain_map /
setup_review surface live-capable vs unsupported providers honestly.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.policy import provider_config as pc
from forgekit_console.policy import provider_ops as ops


class TransformTests(unittest.TestCase):
    def test_set_primary_adds_to_linked(self) -> None:
        cfg = ops.set_primary({}, "claude")
        self.assertEqual(cfg["primary_provider"], "claude")
        self.assertIn("claude", cfg["linked_providers"])

    def test_link_unlink(self) -> None:
        cfg = ops.link_provider(ops.set_primary({}, "claude"), "gemini")
        self.assertIn("gemini", cfg["linked_providers"])
        cfg = ops.unlink_provider(cfg, "gemini")
        self.assertNotIn("gemini", cfg["linked_providers"])

    def test_unlink_drops_dangling_routes(self) -> None:
        cfg = ops.route_slot(ops.link_provider(ops.set_primary({}, "claude"), "gemini"),
                             "research", "gemini")
        cfg = ops.unlink_provider(cfg, "gemini")
        self.assertNotIn("research", cfg["slot_routing"])  # route to removed provider dropped

    def test_route_and_implicit_fallback(self) -> None:
        cfg = ops.route_slot(ops.set_primary({}, "ollama"), "research", "ollama")
        self.assertEqual(cfg["slot_routing"]["research"], "ollama")
        cfg = ops.set_implicit_fallback(cfg, True)
        self.assertTrue(cfg["fallback_policy"]["implicit_local_fallback"])


class PersistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.path = self.tmp / "config.json"

    def test_persist_valid_then_load(self) -> None:
        cfg = ops.route_slot(ops.link_provider(ops.set_primary({}, "ollama"), "gemini"),
                             "research", "gemini")
        ok, where = ops.persist_config(cfg, path=self.path)
        self.assertTrue(ok, where)
        self.assertTrue(self.path.exists())
        loaded = ops.load_raw_config(path=self.path)
        self.assertEqual(loaded["primary_provider"], "ollama")

    def test_persist_refuses_invalid(self) -> None:
        # research routed to gemini but gemini not linked → invalid → not written
        bad = {"primary_provider": "ollama", "linked_providers": ["ollama"],
               "slot_routing": {"research": "gemini"}}
        ok, msg = ops.persist_config(bad, path=self.path)
        self.assertFalse(ok)
        self.assertFalse(self.path.exists())          # nothing persisted
        self.assertIn("research", msg)


class ViewTests(unittest.TestCase):
    def test_brain_map_live_vs_unsupported(self) -> None:
        cfg = pc.load_provider_config({"primary_provider": "claude",
                                       "linked_providers": ["claude", "ollama", "gemini"]})
        bmap = ops.brain_map(cfg)
        self.assertIn("ollama", bmap.live_capable)
        self.assertIn("gemini", bmap.live_capable)
        self.assertIn("claude", bmap.unsupported)      # CLI provider → routable, not live

    def test_review_incomplete(self) -> None:
        self.assertEqual(ops.setup_review({}).verdict, ops.REVIEW_INCOMPLETE)

    def test_review_misconfigured(self) -> None:
        rev = ops.setup_review({"primary_provider": "ollama", "linked_providers": ["ollama"],
                                "slot_routing": {"research": "gemini"}})
        self.assertEqual(rev.verdict, ops.REVIEW_MISCONFIGURED)
        self.assertTrue(rev.issues)

    def test_review_no_live(self) -> None:
        # claude-only brain → routable but no console live-submit
        rev = ops.setup_review({"primary_provider": "claude", "linked_providers": ["claude"]})
        self.assertEqual(rev.verdict, ops.REVIEW_NO_LIVE)

    def test_review_ready(self) -> None:
        rev = ops.setup_review({"primary_provider": "ollama", "linked_providers": ["ollama"]})
        self.assertTrue(rev.ready)


if __name__ == "__main__":
    unittest.main()
