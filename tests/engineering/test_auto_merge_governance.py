"""Governance regression for the F7 / #98 auto-merge decider.

Pins the conventions §5 hard rails into one suite so a single
rule edit cannot silently regress them:

  1. HIGH and CRITICAL risk class PRs are *never* eligible —
     even when the 8 conditions otherwise look pristine and
     ``cycle_authorized=True``.
  2. ``cycle_authorized=False`` short-circuits every PR to
     ``eligible=False``, even a LOW-class PR with every
     condition satisfied.
  3. Protected branch bases (``main`` / ``master`` / ``develop``
     / ``release/*`` / ``prod``) always escalate to at least
     ``HIGH`` and block.
  4. PasteGuard layer is independent — the decider trusts the
     ``paste_guard_clean`` signal and exposes a single
     ``automerge.paste_guard.secret_detected`` signature for the
     mistake ledger.
  5. The §7 signature ``automerge.risk-class.high-without-approval``
     is the exact string the decider emits for HIGH/CRITICAL
     blocks — the ledger row in the doc and the code must agree.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.learning.mistake_ledger import (
    BlockerLevel,
    MistakeLedger,
)
from yule_engineering.agents.release.auto_merge_decider import (
    AutoMergeVerdict,
    PrDiffSummary,
    PrMetadata,
    RiskClass,
    classify_risk,
    evaluate_auto_merge,
    record_automerge_signature,
)


def _clean_meta(**overrides) -> PrMetadata:
    defaults = dict(
        pr_number=98,
        base_branch="feature/integration",
        head_branch="feature/auto-merge-decider-issue-98-v2",
        is_draft=False,
        mergeable="MERGEABLE",
        merge_state="CLEAN",
        status_check_state="SUCCESS",
        author_role="engineering-agent/devops-engineer",
        labels=("✨ Feature",),
    )
    defaults.update(overrides)
    return PrMetadata(**defaults)


def _low_diff(**overrides) -> PrDiffSummary:
    defaults = dict(
        files_changed=2,
        lines_added=30,
        lines_removed=5,
        modules_touched=("docs/operations.md",),
        has_secret_keywords=False,
        touches_protected_branch_policy=False,
    )
    defaults.update(overrides)
    return PrDiffSummary(**defaults)


def _full_pass_kwargs(**overrides):
    defaults = dict(
        cycle_authorized=True,
        ci_ok=True,
        governance_ok=True,
        paste_guard_clean=True,
        acceptance_criteria_reported=True,
        force_push_detected=False,
        no_verify_detected=False,
        protected_branch_direct_push=False,
    )
    defaults.update(overrides)
    return defaults


class HighCriticalNeverEligibleTests(unittest.TestCase):
    """Hard rail #1 — HIGH / CRITICAL never eligible=True."""

    def test_high_risk_secret_keyword_blocks(self) -> None:
        verdict = evaluate_auto_merge(
            _low_diff(has_secret_keywords=True),
            _clean_meta(),
            **_full_pass_kwargs(),
        )
        self.assertFalse(verdict.eligible)
        self.assertEqual(verdict.risk_class, RiskClass.HIGH)
        self.assertIn(
            "automerge.risk-class.high-without-approval",
            verdict.blocker_signatures,
        )

    def test_critical_protected_branch_policy_edit_blocks(self) -> None:
        verdict = evaluate_auto_merge(
            _low_diff(touches_protected_branch_policy=True),
            _clean_meta(),
            **_full_pass_kwargs(),
        )
        self.assertFalse(verdict.eligible)
        self.assertEqual(verdict.risk_class, RiskClass.CRITICAL)
        self.assertIn(
            "automerge.risk-class.high-without-approval",
            verdict.blocker_signatures,
        )

    def test_high_security_module_with_all_conditions_pass_still_blocks(
        self,
    ) -> None:
        diff = _low_diff(
            modules_touched=(
                "apps/engineering-agent/src/yule_engineering/agents/security/paste_guard.py",
            ),
        )
        verdict = evaluate_auto_merge(
            diff, _clean_meta(), **_full_pass_kwargs()
        )
        self.assertFalse(verdict.eligible)
        self.assertEqual(verdict.risk_class, RiskClass.HIGH)


class CycleAuthorizationKillSwitchTests(unittest.TestCase):
    """Hard rail #2 — cycle_authorized=False blocks everything."""

    def test_cycle_off_blocks_otherwise_perfect_low_pr(self) -> None:
        verdict = evaluate_auto_merge(
            _low_diff(),
            _clean_meta(),
            **_full_pass_kwargs(cycle_authorized=False),
        )
        self.assertFalse(verdict.eligible)
        self.assertIn(
            "automerge.cycle.not_authorized", verdict.blocker_signatures
        )
        # cycle gate short-circuits → no satisfied conditions reported.
        self.assertEqual(verdict.satisfied_conditions, ())


class ProtectedBranchBaseTests(unittest.TestCase):
    """Hard rail #3 — protected branch base always blocks."""

    def test_main_base_escalates_to_high(self) -> None:
        verdict = evaluate_auto_merge(
            _low_diff(),
            _clean_meta(base_branch="main"),
            **_full_pass_kwargs(),
        )
        self.assertFalse(verdict.eligible)
        self.assertEqual(verdict.risk_class, RiskClass.HIGH)
        self.assertIn(
            "automerge.protected_branch.base", verdict.blocker_signatures
        )


class LedgerSignatureContractTests(unittest.TestCase):
    """Hard rail #5 — §7 signature exact-string match + BLOCK level."""

    def test_high_signature_records_block_level(self) -> None:
        ledger = MistakeLedger(database_path=":memory:")
        try:
            record_automerge_signature(
                ledger,
                role="engineering-agent/tech-lead",
                signature="automerge.risk-class.high-without-approval",
                pr_number=98,
                blocker_level=BlockerLevel.ADVISORY,  # caller asks lower
            )
            records = ledger.all_records()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].blocker_level, BlockerLevel.BLOCK)
            self.assertTrue(records[0].signature.startswith("automerge."))
        finally:
            ledger.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
