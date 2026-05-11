"""Unit tests for the F7 / #98 auto-merge decider.

Covers the 4 RiskClass levels × representative diff/meta
combinations, the 8-condition evaluator's pass/fail wiring, the
env helper, and the mistake-ledger signature helper.

Pure-function tests — no GitHub I/O, no clock reads. The
``cycle_authorized`` flag is fed directly to keep the suite
self-contained.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.learning.mistake_ledger import (
    BlockerLevel,
    MistakeLedger,
)
from yule_orchestrator.agents.release.auto_merge_decider import (
    ENV_AUTOMERGE_CYCLE,
    AutoMergeVerdict,
    PrDiffSummary,
    PrMetadata,
    RiskClass,
    classify_risk,
    evaluate_auto_merge,
    is_cycle_authorized_from_env,
    record_automerge_signature,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _low_diff(**overrides) -> PrDiffSummary:
    defaults = dict(
        files_changed=2,
        lines_added=30,
        lines_removed=5,
        modules_touched=("docs/operations.md", "tests/agents/test_foo.py"),
        has_secret_keywords=False,
        touches_protected_branch_policy=False,
    )
    defaults.update(overrides)
    return PrDiffSummary(**defaults)


def _clean_meta(**overrides) -> PrMetadata:
    defaults = dict(
        pr_number=123,
        base_branch="feature/integration",
        head_branch="feature/auto-merge-decider-issue-98-v2",
        is_draft=False,
        mergeable="MERGEABLE",
        merge_state="CLEAN",
        status_check_state="SUCCESS",
        author_role="engineering-agent/devops-engineer",
        labels=("✨ Feature", "🤖 Agent-runtime"),
    )
    defaults.update(overrides)
    return PrMetadata(**defaults)


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


# ---------------------------------------------------------------------------
# RiskClass — 4 levels × representative inputs
# ---------------------------------------------------------------------------


class ClassifyRiskTests(unittest.TestCase):
    """Static classification covers the four RiskClass levels."""

    def test_low_docs_only_pr_is_low(self) -> None:
        diff = _low_diff(
            modules_touched=("docs/runbook.md",),
            files_changed=1,
            lines_added=10,
            lines_removed=2,
        )
        risk, signals = classify_risk(diff, _clean_meta())
        self.assertEqual(risk, RiskClass.LOW)
        self.assertTrue(any(s.weight is RiskClass.LOW for s in signals))

    def test_medium_large_diff_is_medium(self) -> None:
        diff = _low_diff(
            files_changed=10,
            lines_added=400,
            lines_removed=120,
        )
        risk, signals = classify_risk(diff, _clean_meta())
        self.assertEqual(risk, RiskClass.MEDIUM)
        self.assertTrue(
            any(s.name.startswith("rule.diff") for s in signals),
            "diff-size rule should fire",
        )

    def test_high_protected_base_branch_is_high(self) -> None:
        risk, signals = classify_risk(_low_diff(), _clean_meta(base_branch="main"))
        self.assertEqual(risk, RiskClass.HIGH)
        self.assertTrue(
            any(s.name == "rule.protected_branch.base" for s in signals)
        )

    def test_high_secret_keywords_is_high(self) -> None:
        diff = _low_diff(has_secret_keywords=True)
        risk, _ = classify_risk(diff, _clean_meta())
        self.assertEqual(risk, RiskClass.HIGH)

    def test_high_security_module_is_high(self) -> None:
        diff = _low_diff(
            modules_touched=(
                "src/yule_orchestrator/agents/security/paste_guard.py",
            ),
        )
        risk, _ = classify_risk(diff, _clean_meta())
        self.assertEqual(risk, RiskClass.HIGH)

    def test_critical_protected_branch_policy_edit_is_critical(self) -> None:
        diff = _low_diff(touches_protected_branch_policy=True)
        risk, signals = classify_risk(diff, _clean_meta())
        self.assertEqual(risk, RiskClass.CRITICAL)
        self.assertTrue(
            any(
                s.name == "rule.protected_branch.policy_edit" for s in signals
            )
        )

    def test_critical_deploy_label_is_critical(self) -> None:
        meta = _clean_meta(labels=("🌏 Deploy", "✨ Feature"))
        risk, _ = classify_risk(_low_diff(), meta)
        self.assertEqual(risk, RiskClass.CRITICAL)


# ---------------------------------------------------------------------------
# 8-condition evaluator
# ---------------------------------------------------------------------------


class EvaluateAutoMergeTests(unittest.TestCase):
    """Full 8-condition pass/fail wiring."""

    def test_full_pass_returns_eligible(self) -> None:
        verdict = evaluate_auto_merge(
            _low_diff(),
            _clean_meta(),
            **_full_pass_kwargs(),
        )
        self.assertIsInstance(verdict, AutoMergeVerdict)
        self.assertTrue(verdict.eligible)
        self.assertEqual(verdict.risk_class, RiskClass.LOW)
        self.assertEqual(len(verdict.satisfied_conditions), 8)
        self.assertEqual(verdict.failed_conditions, ())
        self.assertEqual(verdict.blocker_signatures, ())

    def test_cycle_unauthorized_blocks_everything(self) -> None:
        verdict = evaluate_auto_merge(
            _low_diff(),
            _clean_meta(),
            **_full_pass_kwargs(cycle_authorized=False),
        )
        self.assertFalse(verdict.eligible)
        self.assertIn(
            "automerge.cycle.not_authorized", verdict.blocker_signatures
        )
        self.assertEqual(verdict.satisfied_conditions, ())

    def test_ci_failure_blocks(self) -> None:
        verdict = evaluate_auto_merge(
            _low_diff(),
            _clean_meta(status_check_state="FAILURE"),
            **_full_pass_kwargs(ci_ok=False),
        )
        self.assertFalse(verdict.eligible)
        self.assertIn("ci_regression_ok", verdict.failed_conditions)
        self.assertIn(
            "automerge.ci.regression_failed", verdict.blocker_signatures
        )

    def test_governance_failure_blocks(self) -> None:
        verdict = evaluate_auto_merge(
            _low_diff(),
            _clean_meta(),
            **_full_pass_kwargs(governance_ok=False),
        )
        self.assertFalse(verdict.eligible)
        self.assertIn("governance_regression_ok", verdict.failed_conditions)

    def test_dirty_merge_state_or_draft_blocks(self) -> None:
        for meta in (
            _clean_meta(is_draft=True),
            _clean_meta(mergeable="CONFLICTING", merge_state="DIRTY"),
        ):
            with self.subTest(meta=meta):
                verdict = evaluate_auto_merge(
                    _low_diff(), meta, **_full_pass_kwargs()
                )
                self.assertFalse(verdict.eligible)
                self.assertIn(
                    "mergeable_and_clean", verdict.failed_conditions
                )

    def test_force_push_or_no_verify_blocks(self) -> None:
        for kwargs in (
            _full_pass_kwargs(force_push_detected=True),
            _full_pass_kwargs(no_verify_detected=True),
        ):
            with self.subTest(kwargs=kwargs):
                verdict = evaluate_auto_merge(
                    _low_diff(), _clean_meta(), **kwargs
                )
                self.assertFalse(verdict.eligible)
                self.assertIn(
                    "no_force_or_no_verify", verdict.failed_conditions
                )

    def test_paste_guard_failure_blocks(self) -> None:
        verdict = evaluate_auto_merge(
            _low_diff(),
            _clean_meta(),
            **_full_pass_kwargs(paste_guard_clean=False),
        )
        self.assertFalse(verdict.eligible)
        self.assertIn("paste_guard_clean", verdict.failed_conditions)

    def test_acceptance_report_missing_blocks(self) -> None:
        verdict = evaluate_auto_merge(
            _low_diff(),
            _clean_meta(),
            **_full_pass_kwargs(acceptance_criteria_reported=False),
        )
        self.assertFalse(verdict.eligible)
        self.assertIn(
            "acceptance_criteria_reported", verdict.failed_conditions
        )

    def test_medium_risk_class_blocks(self) -> None:
        verdict = evaluate_auto_merge(
            _low_diff(files_changed=10, lines_added=500, lines_removed=200),
            _clean_meta(),
            **_full_pass_kwargs(),
        )
        self.assertFalse(verdict.eligible)
        self.assertEqual(verdict.risk_class, RiskClass.MEDIUM)
        self.assertIn("risk_class_low", verdict.failed_conditions)

    def test_protected_branch_bases_block(self) -> None:
        for base in ("main", "release/2026-05"):
            with self.subTest(base=base):
                verdict = evaluate_auto_merge(
                    _low_diff(),
                    _clean_meta(base_branch=base),
                    **_full_pass_kwargs(),
                )
                self.assertFalse(verdict.eligible)
                self.assertIn(
                    "no_protected_branch_direct_push",
                    verdict.failed_conditions,
                )

    def test_missing_optional_inputs_fail_closed(self) -> None:
        # ci_ok / governance_ok / paste_guard_clean / acceptance left None
        verdict = evaluate_auto_merge(
            _low_diff(),
            _clean_meta(),
            cycle_authorized=True,
        )
        self.assertFalse(verdict.eligible)
        self.assertIn("governance_regression_ok", verdict.failed_conditions)
        self.assertIn("paste_guard_clean", verdict.failed_conditions)
        self.assertIn(
            "acceptance_criteria_reported", verdict.failed_conditions
        )

    def test_reason_payload_carries_block_context(self) -> None:
        # HIGH-class block surfaces the risk class in the reason; a
        # LOW-class block surfaces the failed condition names.
        high = evaluate_auto_merge(
            _low_diff(),
            _clean_meta(base_branch="main"),
            **_full_pass_kwargs(),
        )
        self.assertIn("HIGH", high.reason)
        low_block = evaluate_auto_merge(
            _low_diff(),
            _clean_meta(),
            **_full_pass_kwargs(ci_ok=False, governance_ok=False),
        )
        self.assertIn("ci_regression_ok", low_block.reason)


# ---------------------------------------------------------------------------
# Env helper + ledger integration
# ---------------------------------------------------------------------------


class CycleEnvHelperTests(unittest.TestCase):
    """``YULE_AUTOMERGE_CYCLE`` interpretation."""

    def test_empty_env_returns_false(self) -> None:
        self.assertFalse(is_cycle_authorized_from_env({}))

    def test_explicit_cycle_name_returns_true(self) -> None:
        self.assertTrue(
            is_cycle_authorized_from_env({ENV_AUTOMERGE_CYCLE: "F1-F8"})
        )

    def test_falsy_strings_return_false(self) -> None:
        for value in ("false", "0", "off", "no", "disabled", "FALSE"):
            with self.subTest(value=value):
                self.assertFalse(
                    is_cycle_authorized_from_env(
                        {ENV_AUTOMERGE_CYCLE: value}
                    )
                )

    def test_truthy_strings_return_true(self) -> None:
        for value in ("true", "yes", "on", "1"):
            with self.subTest(value=value):
                self.assertTrue(
                    is_cycle_authorized_from_env(
                        {ENV_AUTOMERGE_CYCLE: value}
                    )
                )


class LedgerIntegrationTests(unittest.TestCase):
    """``record_automerge_signature`` round-trip."""

    def setUp(self) -> None:
        self.ledger = MistakeLedger(database_path=":memory:")
        self.addCleanup(self.ledger.close)

    def test_signature_namespaced_automerge(self) -> None:
        record_automerge_signature(
            self.ledger,
            role="engineering-agent/tech-lead",
            signature="ci.regression_failed",
            pr_number=42,
        )
        records = self.ledger.all_records()
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].signature.startswith("automerge."))
        self.assertEqual(records[0].pattern, "automerge")

    def test_high_signature_escalates_to_block(self) -> None:
        record_automerge_signature(
            self.ledger,
            role="engineering-agent/tech-lead",
            signature="automerge.risk-class.high-without-approval",
            pr_number=98,
            blocker_level=BlockerLevel.ADVISORY,
        )
        records = self.ledger.all_records()
        self.assertEqual(records[0].blocker_level, BlockerLevel.BLOCK)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
