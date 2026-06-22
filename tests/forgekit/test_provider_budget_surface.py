"""/provider budget console surface (next-wave P1 #3, gw3 console wiring).

The operator surface for gw2's per-provider daily budget enforcement (#343). Proven via
the REAL router + real config writer (tmp FORGEKIT_HOME), not mocks:

- ``/provider budget`` (show) with NO limits configured → honest "미설정/unbounded";
- ``/provider budget set <id> <tokens>`` → persists ``budget_policy.provider_daily_limits``
  in the canonical config (re-readable by the enforcement policy);
- ``/provider budget set <id> 0`` → clears the limit (unbounded, never invents one);
- after a set, ``/provider budget`` show lists the provider + its limit;
- non-int / negative input is rejected honestly (no coercion).

Surface stays thin: it renders state + applies via the package writer
(``forgekit_provider.policy.provider_ops.set_provider_budget``) — it owns no policy.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.router import ConsoleContext, route
from forgekit_contracts.models import KIND_ERROR, KIND_INFO


def _route(raw: str, env):
    return route(parse_input(raw), ConsoleContext(repo_root=Path("."), env=env))


class ProviderBudgetSurfaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.env = {"FORGEKIT_HOME": self._tmp.name}
        # seed a minimal valid brain config in THIS env (the budget surface is
        # env-isolated; persist_config validation needs primary + linked providers)
        from forgekit_provider.policy import provider_ops as ops
        ok, msg = ops.persist_config(
            {"primary_provider": "ollama", "linked_providers": ["ollama"]}, env=self.env)
        assert ok, msg

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_show_unset_is_honest_unbounded(self) -> None:
        res = _route("/provider budget", self.env)
        self.assertEqual(res.kind, KIND_INFO)
        blob = "\n".join(res.lines)
        self.assertIn("미설정", blob)
        self.assertIn("unbounded", blob)

    def test_set_persists_limit_and_show_lists_it(self) -> None:
        res = _route("/provider budget set gemini 50000", self.env)
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("50000", res.lines[0])
        # persisted under budget_policy.provider_daily_limits (real config writer)
        from forgekit_provider.policy import provider_ops as ops
        from forgekit_provider.usage import provider_budget as pb
        cfg = ops.load_raw_config(env=self.env)
        self.assertEqual(pb.provider_limits(cfg).get("gemini"), 50000)
        # show now lists gemini + its limit
        show = _route("/provider budget show", self.env)
        self.assertTrue(any("gemini" in ln and "50000" in ln for ln in show.lines))

    def test_set_zero_clears_limit(self) -> None:
        _route("/provider budget set gemini 50000", self.env)
        res = _route("/provider budget set gemini 0", self.env)
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("해제", res.lines[0])
        from forgekit_provider.policy import provider_ops as ops
        from forgekit_provider.usage import provider_budget as pb
        self.assertNotIn("gemini", pb.provider_limits(ops.load_raw_config(env=self.env)))

    def test_set_non_integer_rejected(self) -> None:
        res = _route("/provider budget set gemini lots", self.env)
        self.assertEqual(res.kind, KIND_ERROR)
        self.assertIn("정수", res.lines[0])

    def test_set_negative_rejected(self) -> None:
        res = _route("/provider budget set gemini -5", self.env)
        self.assertEqual(res.kind, KIND_ERROR)

    def test_set_missing_provider_rejected(self) -> None:
        res = _route("/provider budget set", self.env)
        self.assertEqual(res.kind, KIND_ERROR)
        self.assertIn("provider id", res.lines[0])

    def test_registry_lists_budget(self) -> None:
        from forgekit_console.commands.registry import find_command
        cmd = find_command("provider")
        self.assertIn("budget", cmd.summary)


if __name__ == "__main__":
    unittest.main()
