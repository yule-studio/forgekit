"""[G5] Discord intake → GitHub work-order dispatch e2e harness.

Drives the operator-facing Discord side of the agent without a live
gateway:

  1. ``#업무-접수`` coding request lands → session created.
  2. role-selection fan-out picks the active engineering roles.
  3. ``#운영-리서치`` thread carries the role takes + tech-lead
     synthesis.
  4. tech-lead verdict says ``coding_required=True``.
  5. ``#승인-대기`` approval card posted; operator replies "이대로 진행".
  6. The reply is converted to a :class:`GitHubWorkOrder` and
     dispatched against :class:`FakeGitHubAPI`.

The G4 production wiring may still be in flight, so the harness
drives the contract through the fakes from
``tests/github_workos/_fakes.py``. Each step asserts the seam the
real wiring must honour: redacted posts, no main push, dry-run by
default, approval intent gating dispatch.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests.github_workos._fakes import (
    DiscordIntakeOutcome,
    FakeDiscordSurface,
    FakeGitHubAPI,
    FakeGitHubAPIError,
    FakeGitHubAppAuth,
    FakeIssue,
    FakeWorkOrderExecutor,
    GitHubWorkOrder,
    RoleAssignment,
    SeniorQualityValidationError,
    TechLeadVerdict,
    TriageReport,
    make_default_pr_plan,
    redact_secret_blob,
)


# ---------------------------------------------------------------------------
# Reference Discord-intake driver — mirrors the contract the real G4
# wiring must satisfy. When G4 lands, swap this for the production
# DiscordIntake.submit_intake call; the assertions stay the same.
# ---------------------------------------------------------------------------


@dataclass
class _IntakeDriver:
    """Pure-Python stand-in for the G4 surface.

    It owns the order of operations the real DiscordIntake must keep:
    intake → role-select → forum thread → tech-lead → approval card →
    optional dispatch. The driver records every Discord post so tests
    can assert the operator sees the right messages.
    """

    discord: FakeDiscordSurface
    github: FakeGitHubAPI
    auth: FakeGitHubAppAuth

    def submit_intake(
        self,
        *,
        text: str,
        author: str,
        session_id: str,
        verdict: TechLeadVerdict,
        approve_text: Optional[str],
    ) -> DiscordIntakeOutcome:
        # 1. intake — record the request in #업무-접수.
        self.discord.post(
            self.discord.intake_channel,
            f"📝 코딩 요청 접수: `{session_id}` from @{author}\n> {text}",
        )

        # 2. role-selection fan-out — pick the active roles for a
        # backend-leaning bug-fix request.
        selected_roles = ("tech-lead", "backend-engineer", "qa-engineer")
        self.discord.post(
            self.discord.bot_status_channel,
            f"역할 선택: {', '.join(selected_roles)}",
        )

        # 3. operator-research forum thread + role takes.
        forum_thread_id = 50000 + (hash(session_id) % 9999)
        self.discord.post(
            self.discord.research_forum,
            f"thread `{forum_thread_id}` 시작 — {text[:80]}",
        )
        for role in selected_roles:
            self.discord.post(
                self.discord.research_forum,
                f"[{role}] take: 분석 결과 및 제안",
            )

        # 4. tech-lead verdict.
        self.discord.post(
            self.discord.research_forum,
            (
                "[tech-lead] 종합: coding_required="
                f"{verdict.coding_required} — {verdict.rationale}"
            ),
        )

        if not verdict.coding_required:
            return DiscordIntakeOutcome(
                session_id=session_id,
                selected_roles=selected_roles,
                forum_thread_id=forum_thread_id,
                approval_card_message_id=None,
                work_order=None,
            )

        # 5. approval card to #승인-대기.
        card_message_id = 80000 + (hash(session_id) % 9999)
        self.discord.post(
            self.discord.approval_channel,
            (
                f"승인 요청 — `{session_id}` ({verdict.suggested_intent})\n"
                f"branch_plan: feat/{session_id}\n"
                f"dry_run=True"
            ),
        )

        if approve_text is None:
            return DiscordIntakeOutcome(
                session_id=session_id,
                selected_roles=selected_roles,
                forum_thread_id=forum_thread_id,
                approval_card_message_id=card_message_id,
                work_order=None,
            )

        # 6. operator reply → approval intent → work order.
        intent = _classify_reply(approve_text)
        if intent != "approve":
            self.discord.post(
                self.discord.approval_channel,
                f"승인 카드 응답 무시: 의도={intent}",
            )
            return DiscordIntakeOutcome(
                session_id=session_id,
                selected_roles=selected_roles,
                forum_thread_id=forum_thread_id,
                approval_card_message_id=card_message_id,
                work_order=None,
            )

        approved_at = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
        work_order = GitHubWorkOrder(
            session_id=session_id,
            issue_number=None,
            intent=verdict.suggested_intent,
            summary=text,
            requested_by=author,
            approved_by=author,
            approved_at=approved_at,
            dry_run=True,
        )

        # The dispatch path — token issuance is the only place auth
        # is consulted; the executor receives the work order and runs
        # dry-run by default.
        _ = self.auth.installation_token()
        self.discord.post(
            self.discord.bot_status_channel,
            (
                f"GitHub work-order dispatch (dry-run): "
                f"branch=feat/{session_id} intent={work_order.intent}"
            ),
        )
        return DiscordIntakeOutcome(
            session_id=session_id,
            selected_roles=selected_roles,
            forum_thread_id=forum_thread_id,
            approval_card_message_id=card_message_id,
            work_order=work_order,
        )


def _classify_reply(text: str) -> str:
    norm = (text or "").strip().lower()
    if not norm:
        return "unclear"
    for phrase in ("이대로 진행", "이대로 저장", "승인", "approve", "ok"):
        if phrase in norm and len(norm) <= 40:
            return "approve"
    for phrase in ("반려", "거절", "reject"):
        if phrase in norm:
            return "reject"
    return "unclear"


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


def _build_driver() -> _IntakeDriver:
    return _IntakeDriver(
        discord=FakeDiscordSurface(),
        github=FakeGitHubAPI(),
        auth=FakeGitHubAppAuth(),
    )


def _coding_required_verdict() -> TechLeadVerdict:
    return TechLeadVerdict(
        coding_required=True,
        rationale="forum 합의: research-log dedup 코드 패치 필요",
        suggested_intent="bugfix",
    )


# ---------------------------------------------------------------------------
# Happy path — intake → approve → dispatch
# ---------------------------------------------------------------------------


class IntakeToDispatchHappyPathTests(unittest.TestCase):
    def test_intake_creates_session_record_in_intake_channel(self) -> None:
        driver = _build_driver()
        outcome = driver.submit_intake(
            text="research-log dedup 회귀 수정 부탁",
            author="masterway",
            session_id="sess-coding-1",
            verdict=_coding_required_verdict(),
            approve_text="이대로 진행",
        )
        self.assertEqual(outcome.session_id, "sess-coding-1")
        self.assertTrue(driver.discord.intake_channel.posted)
        self.assertIn(
            "코딩 요청 접수", driver.discord.intake_channel.posted[0]
        )

    def test_role_selection_fan_out_records_in_status_channel(self) -> None:
        driver = _build_driver()
        outcome = driver.submit_intake(
            text="x",
            author="masterway",
            session_id="sess-role-fan",
            verdict=_coding_required_verdict(),
            approve_text="이대로 진행",
        )
        # tech-lead must be in the role list — it's the synthesis role.
        self.assertIn("tech-lead", outcome.selected_roles)
        # backend-engineer + qa-engineer are the bug-fix roles.
        self.assertIn("backend-engineer", outcome.selected_roles)
        self.assertIn("qa-engineer", outcome.selected_roles)
        # role list announced in #봇-상태 (not in #운영-리서치).
        self.assertTrue(
            any(
                "역할 선택" in line
                for line in driver.discord.bot_status_channel.posted
            )
        )

    def test_research_forum_carries_role_takes_and_synthesis(self) -> None:
        driver = _build_driver()
        outcome = driver.submit_intake(
            text="dedup 회귀 수정",
            author="masterway",
            session_id="sess-forum",
            verdict=_coding_required_verdict(),
            approve_text="이대로 진행",
        )
        forum_posts = driver.discord.research_forum.posted
        # Thread kickoff + per-role take + tech-lead synthesis.
        self.assertTrue(any("thread" in p for p in forum_posts))
        for role in outcome.selected_roles:
            with self.subTest(role=role):
                self.assertTrue(
                    any(role in p for p in forum_posts),
                    f"expected {role} take in forum log",
                )
        self.assertTrue(
            any("tech-lead" in p and "종합" in p for p in forum_posts)
        )

    def test_approval_card_posted_to_approval_channel(self) -> None:
        driver = _build_driver()
        outcome = driver.submit_intake(
            text="dedup 회귀 수정",
            author="masterway",
            session_id="sess-approval-card",
            verdict=_coding_required_verdict(),
            approve_text="이대로 진행",
        )
        self.assertIsNotNone(outcome.approval_card_message_id)
        self.assertTrue(
            any(
                "승인 요청" in p
                for p in driver.discord.approval_channel.posted
            )
        )

    def test_approve_reply_dispatches_github_work_order(self) -> None:
        driver = _build_driver()
        outcome = driver.submit_intake(
            text="dedup 회귀 수정",
            author="masterway",
            session_id="sess-dispatch",
            verdict=_coding_required_verdict(),
            approve_text="이대로 진행",
        )
        self.assertIsNotNone(outcome.work_order)
        wo = outcome.work_order
        assert wo is not None
        self.assertEqual(wo.session_id, "sess-dispatch")
        self.assertEqual(wo.approved_by, "masterway")
        self.assertEqual(wo.intent, "bugfix")
        self.assertTrue(wo.dry_run)
        # Dispatch went through the auth surface exactly once.
        self.assertEqual(driver.auth.issued_count, 1)
        # Status channel announced the dispatch.
        self.assertTrue(
            any(
                "dispatch" in p
                for p in driver.discord.bot_status_channel.posted
            )
        )


# ---------------------------------------------------------------------------
# Negative branches
# ---------------------------------------------------------------------------


class IntakeNegativeBranchesTests(unittest.TestCase):
    def test_research_only_verdict_does_not_post_approval_card(self) -> None:
        driver = _build_driver()
        verdict = TechLeadVerdict(
            coding_required=False,
            rationale="research 만 필요 — 코드 변경 없음",
        )
        outcome = driver.submit_intake(
            text="자료 정리 부탁",
            author="masterway",
            session_id="sess-research-only",
            verdict=verdict,
            approve_text="이대로 진행",
        )
        self.assertIsNone(outcome.approval_card_message_id)
        self.assertIsNone(outcome.work_order)
        self.assertEqual(driver.discord.approval_channel.posted, [])
        # Auth never consulted because no dispatch happened.
        self.assertEqual(driver.auth.issued_count, 0)

    def test_unclear_reply_does_not_dispatch(self) -> None:
        driver = _build_driver()
        outcome = driver.submit_intake(
            text="dedup 수정",
            author="masterway",
            session_id="sess-unclear",
            verdict=_coding_required_verdict(),
            approve_text="음 좀 더 보고 결정할게요",
        )
        self.assertIsNotNone(outcome.approval_card_message_id)
        self.assertIsNone(outcome.work_order)
        self.assertEqual(driver.auth.issued_count, 0)

    def test_reject_reply_does_not_dispatch(self) -> None:
        driver = _build_driver()
        outcome = driver.submit_intake(
            text="dedup 수정",
            author="masterway",
            session_id="sess-reject",
            verdict=_coding_required_verdict(),
            approve_text="저장 반려",
        )
        self.assertIsNone(outcome.work_order)
        self.assertEqual(driver.auth.issued_count, 0)


# ---------------------------------------------------------------------------
# Safety / redaction across the Discord boundary
# ---------------------------------------------------------------------------


class DiscordSafetyTests(unittest.TestCase):
    def test_intake_text_carrying_a_pem_is_redacted_in_channel(self) -> None:
        # If an operator pastes their PEM into #업무-접수 by mistake,
        # the post must NOT echo it verbatim back into Discord.
        driver = _build_driver()
        leak = (
            "도와줘\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBaaa\n-----END RSA PRIVATE KEY-----"
        )
        outcome = driver.submit_intake(
            text=leak,
            author="masterway",
            session_id="sess-pem-leak",
            verdict=_coding_required_verdict(),
            approve_text=None,  # don't dispatch
        )
        for channel in (
            driver.discord.intake_channel,
            driver.discord.research_forum,
            driver.discord.approval_channel,
        ):
            for post in channel.posted:
                with self.subTest(channel=channel.name):
                    self.assertNotIn("BEGIN RSA PRIVATE KEY", post)


# ---------------------------------------------------------------------------
# Cross-layer: dispatched work order plays cleanly with the executor
# ---------------------------------------------------------------------------


class DispatchToExecutorTests(unittest.TestCase):
    def test_dispatched_work_order_runs_dry_run_through_executor(self) -> None:
        # End of the e2e: the work order becomes a triage + plan, the
        # executor runs dry-run, and no PR opens. This is the contract
        # the production dispatcher must honour — it MAY add a real
        # triage step in between, but the safety surface is the same.
        driver = _build_driver()
        outcome = driver.submit_intake(
            text="dedup 회귀 수정",
            author="masterway",
            session_id="sess-x-layer",
            verdict=_coding_required_verdict(),
            approve_text="이대로 진행",
        )
        wo = outcome.work_order
        assert wo is not None

        triage = TriageReport(
            issue_number=None,
            intent=wo.intent,
            scope_summary=wo.summary,
            role_assignments=(
                RoleAssignment(
                    role="backend-engineer",
                    responsibilities=("dedup 키 보강",),
                    deliverables=("forum_obsidian_handoff.py 패치",),
                ),
            ),
            branch_name_plan=f"feat/{wo.session_id}",
            dry_run=True,
        )
        plan = make_default_pr_plan(
            title=f"{wo.intent}: {wo.summary[:40]}",
            branch=triage.branch_name_plan,
        )
        executor = FakeWorkOrderExecutor(github=driver.github)
        result = executor.run(triage=triage, plan=plan)
        self.assertTrue(result.dry_run)
        self.assertIsNone(result.pull_request_number)
        # Branch is feat/* and main is untouched.
        self.assertIn(triage.branch_name_plan, driver.github.branches)
        self.assertNotIn("main", driver.github.branches)


if __name__ == "__main__":
    unittest.main()
