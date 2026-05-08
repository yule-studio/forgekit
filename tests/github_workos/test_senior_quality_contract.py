"""[G5] Senior-quality contract tests.

The G3 work-order executor must NEVER dispatch a PR plan that is
missing one of the six senior-quality fields. Each test below
mutates a single field to "missing" and asserts the validator
raises :class:`SeniorQualityValidationError` — that is the gate the
production executor will call right before
:meth:`FakeGitHubAPI.open_pull_request`.

The tests deliberately operate on the dataclass + validator alone
so they remain green regardless of whether the G3 worktree has
landed yet. When G3 ships, swap the fake validator for the real
one and these contracts move with no changes.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests.github_workos._fakes import (
    FailureRecoveryEntry,
    SeniorQualityPRPlan,
    SeniorQualityValidationError,
    make_default_pr_plan,
    validate_senior_quality_plan,
)


# ---------------------------------------------------------------------------
# Happy path — every field populated → validator passes silently
# ---------------------------------------------------------------------------


class HappyPathTests(unittest.TestCase):
    def test_full_plan_passes_validator(self) -> None:
        plan = make_default_pr_plan(title="GitHub work-order MVP")
        # No assertion needed beyond the absence of an exception.
        validate_senior_quality_plan(plan)


# ---------------------------------------------------------------------------
# Each missing field must fail loudly with a name-tagged error
# ---------------------------------------------------------------------------


class HiddenRisksRequiredTests(unittest.TestCase):
    def test_missing_hidden_risks_fails(self) -> None:
        plan = replace(make_default_pr_plan(title="x"), hidden_risks=())
        with self.assertRaises(SeniorQualityValidationError) as ctx:
            validate_senior_quality_plan(plan)
        self.assertIn("hidden_risks", str(ctx.exception))

    def test_whitespace_only_hidden_risks_fails(self) -> None:
        plan = replace(
            make_default_pr_plan(title="x"),
            hidden_risks=("   ", "\n\n"),
        )
        with self.assertRaises(SeniorQualityValidationError) as ctx:
            validate_senior_quality_plan(plan)
        self.assertIn("hidden_risks", str(ctx.exception))


class OutOfScopeRequiredTests(unittest.TestCase):
    def test_missing_out_of_scope_fails(self) -> None:
        plan = replace(make_default_pr_plan(title="x"), out_of_scope=())
        with self.assertRaises(SeniorQualityValidationError) as ctx:
            validate_senior_quality_plan(plan)
        self.assertIn("out_of_scope", str(ctx.exception))


class TestPlanRequiredTests(unittest.TestCase):
    def test_missing_test_plan_fails(self) -> None:
        plan = replace(make_default_pr_plan(title="x"), test_plan=())
        with self.assertRaises(SeniorQualityValidationError) as ctx:
            validate_senior_quality_plan(plan)
        self.assertIn("test_plan", str(ctx.exception))


class ApprovalRequiredActionsTests(unittest.TestCase):
    def test_missing_approval_required_actions_fails(self) -> None:
        plan = replace(
            make_default_pr_plan(title="x"),
            approval_required_actions=(),
        )
        with self.assertRaises(SeniorQualityValidationError) as ctx:
            validate_senior_quality_plan(plan)
        self.assertIn("approval_required_actions", str(ctx.exception))


class ExcludedRoleRationaleTests(unittest.TestCase):
    def test_missing_excluded_role_rationale_fails(self) -> None:
        plan = replace(
            make_default_pr_plan(title="x"),
            excluded_role_rationale={},
        )
        with self.assertRaises(SeniorQualityValidationError) as ctx:
            validate_senior_quality_plan(plan)
        self.assertIn("excluded_role_rationale", str(ctx.exception))

    def test_empty_rationale_for_excluded_role_fails(self) -> None:
        plan = replace(
            make_default_pr_plan(title="x"),
            excluded_role_rationale={"frontend-engineer": "  "},
        )
        with self.assertRaises(SeniorQualityValidationError) as ctx:
            validate_senior_quality_plan(plan)
        self.assertIn("frontend-engineer", str(ctx.exception))


class FailureRecoveryTests(unittest.TestCase):
    def test_missing_failure_recovery_fails(self) -> None:
        plan = replace(
            make_default_pr_plan(title="x"),
            failure_recovery=(),
        )
        with self.assertRaises(SeniorQualityValidationError) as ctx:
            validate_senior_quality_plan(plan)
        self.assertIn("failure_recovery", str(ctx.exception))

    def test_failure_recovery_missing_cause_fails(self) -> None:
        plan = replace(
            make_default_pr_plan(title="x"),
            failure_recovery=(
                FailureRecoveryEntry(cause="", impact="x", recovery="x"),
            ),
        )
        with self.assertRaises(SeniorQualityValidationError) as ctx:
            validate_senior_quality_plan(plan)
        self.assertIn("cause", str(ctx.exception))

    def test_failure_recovery_missing_impact_fails(self) -> None:
        plan = replace(
            make_default_pr_plan(title="x"),
            failure_recovery=(
                FailureRecoveryEntry(
                    cause="rate limit", impact="", recovery="retry"
                ),
            ),
        )
        with self.assertRaises(SeniorQualityValidationError) as ctx:
            validate_senior_quality_plan(plan)
        self.assertIn("impact", str(ctx.exception))

    def test_failure_recovery_missing_recovery_fails(self) -> None:
        plan = replace(
            make_default_pr_plan(title="x"),
            failure_recovery=(
                FailureRecoveryEntry(
                    cause="rate limit", impact="block", recovery=""
                ),
            ),
        )
        with self.assertRaises(SeniorQualityValidationError) as ctx:
            validate_senior_quality_plan(plan)
        self.assertIn("recovery", str(ctx.exception))


# ---------------------------------------------------------------------------
# Multiple missing fields — error message lists every offender so the
# operator fixes them in one round, not six.
# ---------------------------------------------------------------------------


class MultipleMissingTests(unittest.TestCase):
    def test_multiple_missing_fields_listed_in_error(self) -> None:
        plan = replace(
            make_default_pr_plan(title="x"),
            hidden_risks=(),
            out_of_scope=(),
            test_plan=(),
            approval_required_actions=(),
            excluded_role_rationale={},
            failure_recovery=(),
        )
        with self.assertRaises(SeniorQualityValidationError) as ctx:
            validate_senior_quality_plan(plan)
        msg = str(ctx.exception)
        for needle in (
            "hidden_risks",
            "out_of_scope",
            "test_plan",
            "approval_required_actions",
            "excluded_role_rationale",
            "failure_recovery",
        ):
            with self.subTest(field=needle):
                self.assertIn(needle, msg)


# ---------------------------------------------------------------------------
# PR body rendering — each senior-quality section appears as a heading
# so a human reading the PR sees the agent's reasoning.
# ---------------------------------------------------------------------------


class PRBodyRenderingTests(unittest.TestCase):
    def test_rendered_body_includes_every_senior_section(self) -> None:
        plan = make_default_pr_plan(title="GitHub work-order MVP")
        body = plan.to_pr_body()
        for heading in (
            "## 숨은 리스크",
            "## 비범위",
            "## 테스트 계획",
            "## 사람 승인 필요 작업",
            "## 제외 역할 사유",
            "## 실패 복구 시나리오",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, body)

    def test_failure_recovery_entries_carry_three_columns(self) -> None:
        plan = make_default_pr_plan(title="t")
        body = plan.to_pr_body()
        # Every entry renders as "- 원인: … · 영향: … · 복구: …".
        for entry in plan.failure_recovery:
            self.assertIn(f"원인: {entry.cause}", body)
            self.assertIn(f"영향: {entry.impact}", body)
            self.assertIn(f"복구: {entry.recovery}", body)


if __name__ == "__main__":
    unittest.main()
