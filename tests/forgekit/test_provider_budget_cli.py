"""`/provider budget` operator console surface (final-completion lane A, axis 1).

Per-provider daily token budgets are already ENFORCED by routing
(:mod:`forgekit_provider.usage.provider_budget`); the only gap was the operator
surface to set/show them. This proves the surface through the REAL router + the
real config / usage ledger on a tmp ``FORGEKIT_HOME`` (no mocks), like
``test_goal_approval`` / ``test_provider_*``:

- ``/provider budget <id> <limit>`` validates id + integer limit, persists it to
  ``~/.forgekit/config.json`` (``budget_policy.provider_daily_limits``), and a
  reload of the surface shows it;
- ``/provider budget show`` (and bare ``/provider budget``) renders the configured
  limit + today's spent/over HONESTLY from the usage ledger (no fake numbers);
- an unset budget = unbounded, shown as such (never invents a limit);
- an unknown provider id / a non-integer limit are surfaced as errors (no silent
  no-op, no silent 0).

The router stays thin (render / CRUD); the budget logic lives in the provider
package (provider-neutral, no coupling).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401  (inserts console + package srcs on sys.path)

from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.router import ConsoleContext, route
from forgekit_contracts.models import KIND_ERROR, KIND_INFO
from forgekit_provider.policy import provider_ops as ops
from forgekit_provider.usage import (
    UsageEvent,
    append_event,
    today,
    usage_ledger_path,
)


def _text(res) -> str:
    return "\n".join(res.lines)


class ProviderBudgetCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.env = {"FORGEKIT_HOME": self._tmp.name}

        # a budget is ring-fencing on an EXISTING brain — persist refuses an invalid/empty
        # config (honest: no orphan budget_policy), so we configure a real brain first via
        # the same router path operators use.
        self._route("/provider set gemini")
        self._route("/provider link ollama")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _route(self, raw: str):
        return route(parse_input(raw), ConsoleContext(repo_root=Path("."), env=self.env))

    def _ledger_row(self, provider: str, total_tokens: int, *, throttled: bool = False) -> None:
        append_event(
            UsageEvent(
                ts=today(),
                session_id="s",
                task_id="t",
                provider=provider,
                total_tokens=total_tokens,
                throttled=throttled,
            ),
            path=usage_ledger_path(self.env),
        )

    # --- set + persist ------------------------------------------------------
    def test_set_persists_and_reload_shows_it(self) -> None:
        res = self._route("/provider budget gemini 50000")
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("gemini", _text(res))
        self.assertIn("50000", _text(res))
        # actually persisted to the canonical config (budget_policy.provider_daily_limits).
        cfg = ops.load_raw_config(env=self.env)
        self.assertEqual(
            cfg.get("budget_policy", {}).get("provider_daily_limits", {}).get("gemini"),
            50000,
        )
        # a fresh `budget show` reflects it.
        shown = self._route("/provider budget show")
        self.assertEqual(shown.kind, KIND_INFO)
        self.assertIn("gemini", _text(shown))
        self.assertIn("50000", _text(shown))

    # --- show renders spent/over honestly -----------------------------------
    def test_show_renders_spent_and_over_from_ledger(self) -> None:
        self._route("/provider budget gemini 1000")
        self._ledger_row("gemini", 400)
        self._ledger_row("gemini", 700)  # cumulative 1100 ≥ 1000 → over
        self._ledger_row("gemini", 999, throttled=True)  # throttled burns nothing
        res = self._route("/provider budget show")
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("1100/1000", _text(res))
        self.assertIn("OVER", _text(res))

    def test_show_counts_only_the_named_provider(self) -> None:
        self._route("/provider budget gemini 5000")
        self._ledger_row("gemini", 1200)
        self._ledger_row("ollama", 9999)  # different provider, must not bleed in
        res = self._route("/provider budget show")
        self.assertIn("1200/5000", _text(res))

    # --- bare /provider budget == show --------------------------------------
    def test_bare_budget_is_show(self) -> None:
        self._route("/provider budget gemini 2000")
        res = self._route("/provider budget")
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("2000", _text(res))

    # --- unset = unbounded, shown honestly ----------------------------------
    def test_unset_is_unbounded_shown(self) -> None:
        res = self._route("/provider budget show")
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("unbounded", _text(res))
        self.assertNotIn("OVER", _text(res))

    def test_zero_limit_clears_to_unbounded(self) -> None:
        self._route("/provider budget gemini 50000")
        res = self._route("/provider budget gemini 0")
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("unbounded", _text(res) + "")
        cfg = ops.load_raw_config(env=self.env)
        self.assertNotIn(
            "gemini",
            cfg.get("budget_policy", {}).get("provider_daily_limits", {}),
        )
        # and `show` now reports unbounded (no configured limit left).
        shown = self._route("/provider budget show")
        self.assertIn("unbounded", _text(shown))

    # --- honest errors ------------------------------------------------------
    def test_unknown_provider_is_error(self) -> None:
        res = self._route("/provider budget bogus 1000")
        self.assertEqual(res.kind, KIND_ERROR)
        self.assertIn("알 수 없는 provider", _text(res))
        # nothing persisted.
        cfg = ops.load_raw_config(env=self.env)
        self.assertEqual(cfg.get("budget_policy", {}).get("provider_daily_limits", {}), {})

    def test_non_integer_limit_is_error(self) -> None:
        res = self._route("/provider budget gemini lots")
        self.assertEqual(res.kind, KIND_ERROR)
        self.assertIn("정수", _text(res))
        cfg = ops.load_raw_config(env=self.env)
        self.assertEqual(cfg.get("budget_policy", {}).get("provider_daily_limits", {}), {})

    def test_missing_limit_is_error(self) -> None:
        res = self._route("/provider budget gemini")
        self.assertEqual(res.kind, KIND_ERROR)
        self.assertIn("limit", _text(res))


if __name__ == "__main__":
    unittest.main()
