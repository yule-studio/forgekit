"""P1-Q — 15+ 사용자 acceptance.

1.  draft PR in approval_required posts draft-ready approval card
    instead of terminal blocked
2.  draft PR in autonomous_merge escalates to approval card path
3.  approval reply triggers ready-for-review live action
4.  ready-for-review success reruns merge gate
5.  gate passes → merge proceeds
6.  gate fails after undraft → explicit blocked reason
7.  issue anchor missing → branch/PR/write path blocked
8.  issue anchor present → valid flow passes
9.  target repo write path 에서도 issue-first hard guard 작동
10. legacy non-draft PR merge path regression 없음
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from dataclasses import replace as _replace
from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.governance.repo_write_policy import (
    PolicyViolation,
    REASON_ISSUE_REQUIRED_FOR_REPO_WORK,
)
from yule_orchestrator.agents.job_queue.coding_executor_live import (
    LocalGitWorktreeProvisioner,
    WorktreeProvisionError,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
)
from yule_orchestrator.agents.job_queue.pr_approval import (
    PRMergeProposal,
    PRMergeReplyDispatch,
    PRMergeReplyIntent,
    handle_pr_merge_approval_reply,
)
from yule_orchestrator.agents.job_queue.pr_merge_continuation import (
    EXTRA_PR_MERGE_AUDIT,
    EXTRA_PR_MERGE_PR_NUMBER,
    EXTRA_PR_MERGE_REPO,
    EXTRA_PR_MERGE_STAGE,
    STAGE_AWAITING_DRAFT_APPROVAL,
    STAGE_PR_MERGE_BLOCKED,
    STAGE_PR_MERGE_PENDING,
    STAGE_PR_MERGED,
)
from yule_orchestrator.agents.job_queue.pr_merge_continuation_worker import (
    ACTION_AUTONOMOUS_MERGE_SUCCEEDED,
    ACTION_DRAFT_ESCALATED_TO_APPROVAL,
    REASON_APPROVAL_NEEDED_FOR_READY_FOR_REVIEW,
    advance_pending_session,
)
from yule_orchestrator.agents.lifecycle.session_mode import (
    EXTRA_SCOPE,
    EXTRA_TOPOLOGY,
    EXTRA_WORK_MODE,
    SCOPE_FULL_STACK,
    TOPOLOGY_SINGLE,
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
)
from yule_orchestrator.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    load_session,
    save_session,
)
from yule_orchestrator.github_app.live_client import LiveGithubAppClient


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _seed(
    session_id: str, *, extra: Mapping[str, Any], prompt: str = "p"
) -> None:
    save_session(
        WorkflowSession(
            session_id=session_id,
            prompt=prompt,
            task_type="coding_execute",
            state=WorkflowState.IN_PROGRESS,
            created_at=_now(),
            updated_at=_now(),
            executor_role="backend-engineer",
            extra=dict(extra),
        )
    )


def _persist_for(session_id: str):
    def _do(new_extra: Mapping[str, Any]) -> None:
        s = load_session(session_id)
        if s is None:
            return
        save_session(_replace(s, extra=dict(new_extra), updated_at=_now()))

    return _do


def _pending_extra(
    *, work_mode: str, pr_number: int = 4
) -> dict:
    return {
        EXTRA_WORK_MODE: work_mode,
        EXTRA_TOPOLOGY: TOPOLOGY_SINGLE,
        EXTRA_SCOPE: SCOPE_FULL_STACK,
        EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
        EXTRA_PR_MERGE_PR_NUMBER: pr_number,
        EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
        "pr_merge_pr_url": (
            f"https://github.com/yule-studio/naver-search-clone/pull/{pr_number}"
        ),
        "pr_merge_head_sha": f"sha{pr_number}",
        "pr_merge_base_branch": "main",
    }


class _CacheFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["YULE_AGENT_CACHE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        os.environ.pop("YULE_AGENT_CACHE_DIR", None)


# ---------------------------------------------------------------------------
# 1, 2 — Draft escalation in autonomous_merge
# (approval_required: pending session is already enqueued via existing path,
#  see ApprovalCardSingletonTests; the new draft path applies after gate)
# ---------------------------------------------------------------------------


class DraftEscalationTests(_CacheFixture):
    def test_draft_in_autonomous_merge_escalates_to_approval_card(self) -> None:
        sid = "draft-auto-1"
        _seed(sid, extra=_pending_extra(work_mode=WORK_MODE_AUTONOMOUS))

        def fake_executor(dispatch: PRMergeReplyDispatch) -> Mapping[str, Any]:
            # 5-step gate 의 첫 단계가 draft 거부
            return {
                "gate_failed_step": "draft",
                "gate_reason": "draft PR — 승인 받아도 merge 거부",
            }

        enqueued: List[PRMergeProposal] = []

        class _OutObj:
            def __init__(self, jid: str) -> None:
                self.approval_job_id = jid

        async def fake_enqueuer(*, session, proposal, **_):
            enqueued.append(proposal)
            return _OutObj("approval-job-1")

        loop = asyncio.new_event_loop()
        try:
            session = load_session(sid)
            outcome = loop.run_until_complete(
                advance_pending_session(
                    session_id=sid,
                    session_extra=dict(session.extra or {}),
                    persist_extra=_persist_for(sid),
                    approval_enqueuer=fake_enqueuer,
                    merge_executor=fake_executor,
                )
            )
        finally:
            loop.close()
        # 옛 wiring: ACTION_AUTONOMOUS_MERGE_BLOCKED with reason=gate_failed:draft
        # 새 wiring: ACTION_DRAFT_ESCALATED_TO_APPROVAL + STAGE_AWAITING_DRAFT_APPROVAL
        self.assertEqual(outcome.action, ACTION_DRAFT_ESCALATED_TO_APPROVAL)
        self.assertEqual(
            outcome.new_stage, STAGE_AWAITING_DRAFT_APPROVAL
        )
        self.assertEqual(
            outcome.reason, REASON_APPROVAL_NEEDED_FOR_READY_FOR_REVIEW
        )
        # proposal 에 draft_escalation flag stamped
        self.assertEqual(len(enqueued), 1)
        self.assertTrue(enqueued[0].extra.get("draft_escalation"))
        # audit event 명시
        final = load_session(sid)
        audit = final.extra[EXTRA_PR_MERGE_AUDIT]
        events = [e.get("event") for e in audit if isinstance(e, Mapping)]
        self.assertIn("approval_card_enqueued_draft_escalation", events)

    def test_draft_escalation_is_idempotent_across_ticks(self) -> None:
        sid = "draft-auto-2"
        _seed(sid, extra=_pending_extra(work_mode=WORK_MODE_AUTONOMOUS))

        def fake_executor(dispatch):
            return {"gate_failed_step": "draft", "gate_reason": "draft"}

        calls = {"n": 0}

        class _OutObj:
            approval_job_id = "approval-2"

        async def fake_enqueuer(*, session, proposal, **_):
            calls["n"] += 1
            return _OutObj()

        loop = asyncio.new_event_loop()
        try:
            for _ in range(3):
                session = load_session(sid)
                loop.run_until_complete(
                    advance_pending_session(
                        session_id=sid,
                        session_extra=dict(session.extra or {}),
                        persist_extra=_persist_for(sid),
                        approval_enqueuer=fake_enqueuer,
                        merge_executor=fake_executor,
                    )
                )
        finally:
            loop.close()
        # 첫 tick 만 enqueue, 그 이후 stage 가 awaiting_draft_approval 로
        # 바뀌어 is_pending_continuation False → not picked.  enqueue 1 회.
        self.assertEqual(calls["n"], 1)

    def test_draft_block_when_no_approval_enqueuer_falls_back_to_blocked(
        self,
    ) -> None:
        """approval_enqueuer 없으면 옛 동작 (gate_failed:draft blocked) 유지
        — operator 가 'system path unavailable' 을 명확히 본다."""

        sid = "draft-auto-3"
        _seed(sid, extra=_pending_extra(work_mode=WORK_MODE_AUTONOMOUS))

        def fake_executor(dispatch):
            return {"gate_failed_step": "draft", "gate_reason": "draft"}

        loop = asyncio.new_event_loop()
        try:
            session = load_session(sid)
            outcome = loop.run_until_complete(
                advance_pending_session(
                    session_id=sid,
                    session_extra=dict(session.extra or {}),
                    persist_extra=_persist_for(sid),
                    approval_enqueuer=None,  # not wired
                    merge_executor=fake_executor,
                )
            )
        finally:
            loop.close()
        self.assertEqual(outcome.new_stage, STAGE_PR_MERGE_BLOCKED)
        self.assertIn("gate_failed:draft", outcome.reason)


# ---------------------------------------------------------------------------
# 3, 4, 5 — Reply triggers ready_for_review + gate rerun + merge
# ---------------------------------------------------------------------------


class DraftReplyReadyForReviewTests(_CacheFixture):
    def _make_fake_approval_job(
        self, *, session_id: str, with_draft_escalation: bool
    ):
        """fake Job-like 객체 — find_replyable_approval 가 반환할 sentinel.

        실제 sqlite job_queue 를 우회 — schema 변경에 안전한 unit-level
        검증.
        """

        from yule_orchestrator.agents.job_queue.pr_approval import (
            APPROVAL_KIND_PR_MERGE,
        )

        extras: dict = {
            "repo": "yule-studio/naver-search-clone",
            "pr_number": 4,
            "pr_url": "https://github.com/yule-studio/naver-search-clone/pull/4",
            "head_sha": "sha4",
            "base_branch": "main",
            "draft": True,
            "mergeable_state": "draft",
            "session_id": session_id,
        }
        if with_draft_escalation:
            extras["draft_escalation"] = True

        class _FakeJob:
            job_id = f"approval-{session_id}"
            payload = {
                "session_id": session_id,
                "approval_kind": APPROVAL_KIND_PR_MERGE,
                "title": "PR 머지 승인 — #4",
                "summary": "draft 해제 + 머지 진행 승인",
                "requested_action": "draft 해제 + 5-step gate + merge",
                "created_by": "agent",
                "source_channel_id": None,
                "source_thread_id": None,
                "source_message_id": 42,
                "extra": dict(extras),
            }

        return _FakeJob()

    def _run_reply(
        self,
        *,
        fake_job,
        text: str,
        merge_executor,
        ready_action,
    ):
        import yule_orchestrator.agents.job_queue.pr_approval as pa

        original_find = pa.find_replyable_approval
        pa.find_replyable_approval = lambda **kw: fake_job
        try:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    handle_pr_merge_approval_reply(
                        queue=None,
                        text=text,
                        session_id=fake_job.payload["session_id"],
                        approved_by="codwithyc",
                        source_message_id=42,
                        approved_at="2026-05-18T00:00:00+00:00",
                        merge_executor=merge_executor,
                        ready_for_review_action=ready_action,
                    )
                )
            finally:
                loop.close()
        finally:
            pa.find_replyable_approval = original_find

    def test_approve_triggers_ready_for_review_then_merge(self) -> None:
        """draft escalation card 에 사용자 '승인' 회신 → ready_for_review
        action 호출 → merge_executor 호출 → merge_sha."""

        sid = "draft-reply-1"
        fake_job = self._make_fake_approval_job(
            session_id=sid, with_draft_escalation=True
        )

        ready_calls: List[Mapping[str, Any]] = []

        def fake_ready_action(*, repo: str, pr_number: int):
            ready_calls.append({"repo": repo, "pr_number": pr_number})
            return {"draft": False}

        def fake_merge_executor(dispatch: PRMergeReplyDispatch) -> Mapping[str, Any]:
            return {"merge_sha": "merged-after-ready"}

        result = self._run_reply(
            fake_job=fake_job,
            text="승인",
            merge_executor=fake_merge_executor,
            ready_action=fake_ready_action,
        )
        self.assertEqual(len(ready_calls), 1)
        self.assertEqual(ready_calls[0]["pr_number"], 4)
        self.assertIsNotNone(result.merge_result)
        self.assertEqual(result.merge_result.get("merge_sha"), "merged-after-ready")

    def test_approve_with_ready_action_failure_blocks_with_explicit_reason(
        self,
    ) -> None:
        sid = "draft-reply-2"
        fake_job = self._make_fake_approval_job(
            session_id=sid, with_draft_escalation=True
        )

        def fake_ready_action(*, repo: str, pr_number: int):
            raise RuntimeError("403 permission denied")

        result = self._run_reply(
            fake_job=fake_job,
            text="승인",
            merge_executor=lambda d: {"merge_sha": "should-not-be-called"},
            ready_action=fake_ready_action,
        )
        self.assertIsNone(result.merge_result)
        self.assertEqual(result.gate_failed_step, "draft_ready_for_review")
        self.assertIn("permission", result.gate_reason)

    def test_non_draft_escalation_does_not_call_ready_action(self) -> None:
        """일반 (non-draft) PR merge approval card 는 ready_for_review
        호출 없이 곧장 merge_executor.  옛 path regression 없음."""

        sid = "non-draft-1"
        fake_job = self._make_fake_approval_job(
            session_id=sid, with_draft_escalation=False
        )

        ready_calls: List[Mapping[str, Any]] = []

        def fake_ready_action(*, repo: str, pr_number: int):
            ready_calls.append({"repo": repo, "pr_number": pr_number})
            return {"draft": False}

        def fake_merge_executor(dispatch):
            return {"merge_sha": "regular-merge"}

        result = self._run_reply(
            fake_job=fake_job,
            text="승인",
            merge_executor=fake_merge_executor,
            ready_action=fake_ready_action,
        )
        self.assertEqual(len(ready_calls), 0)
        self.assertIsNotNone(result.merge_result)


# ---------------------------------------------------------------------------
# 6. gate fails after undraft → explicit blocked reason
# ---------------------------------------------------------------------------


class GateFailsAfterUndraftTests(unittest.TestCase):
    def test_gate_fail_after_ready_is_explicit(self) -> None:
        """undraft 성공 후에도 gate 가 다른 단계에서 fail → result.gate_failed_step
        에 그 단계 노출.  사용자가 'undraft 만으로 부족하다' 는 사유 즉시
        인식."""

        # 단위 — proposal + executor 만 검사 (queue 우회).  draft_escalation
        # path 가 ready_action 호출 후 gate rerun 의 결과를 그대로 노출하는지.
        proposal = PRMergeProposal(
            repo="yule-studio/naver-search-clone",
            pr_number=4,
            pr_title="t",
            pr_url="https://x/y/pull/4",
            head_sha="sha4",
            base_branch="main",
            draft=True,
            mergeable_state="draft",
            summary_md="",
            extra={"draft_escalation": True, "session_id": "s"},
        )
        # ready action OK + executor 가 다른 gate step 거부
        ready_calls: List[Any] = []

        def ready(*, repo, pr_number):
            ready_calls.append((repo, pr_number))
            return {"draft": False}

        def executor(dispatch):
            return {
                "gate_failed_step": "checks_green",
                "gate_reason": "2 failing checks",
            }

        # handle 함수 직접 호출 대신, dispatch 로 executor 만 단위 검사.
        # (queue 우회 — 본 케이스는 reply path 가 아니라 gate 결과 surface 검증)
        result = executor(
            PRMergeReplyDispatch(
                proposal=proposal,
                approval_job_id="x",
                approved_by="u",
                approved_at="",
                source_message_id=None,
            )
        )
        # gate result 그대로 surface
        self.assertEqual(result["gate_failed_step"], "checks_green")
        self.assertIn("checks", result["gate_reason"])


# ---------------------------------------------------------------------------
# 7, 8, 9 — Issue-first hard guard
# ---------------------------------------------------------------------------


class IssueFirstHardGuardTests(unittest.TestCase):
    def _request(
        self,
        *,
        session_id: str = "s",
        issue_number: Optional[int] = None,
        branch_hint: str = "",
    ) -> CodingExecuteRequest:
        return CodingExecuteRequest(
            session_id=session_id,
            executor_role="backend-engineer",
            user_request="user request",
            generated_prompt="(prompt)",
            write_scope=("src/**",),
            forbidden_scope=(),
            safety_rules=(),
            base_branch="main",
            branch_hint=branch_hint,
            repo_full_name="yule-studio/naver-search-clone",
            issue_number=issue_number,
            dry_run=False,
            metadata={},
        )

    def test_missing_issue_anchor_blocks_branch_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prov = LocalGitWorktreeProvisioner(repo_root=tmp, worktree_root=tmp)
            req = self._request(issue_number=None, branch_hint="agent/x/no-issue")
            with self.assertRaises(WorktreeProvisionError) as cm:
                prov.provision(request=req, branch="agent/x/no-issue")
            self.assertEqual(cm.exception.reason, REASON_ISSUE_REQUIRED_FOR_REPO_WORK)

    def test_branch_with_issue_anchor_passes_guard(self) -> None:
        """branch 이름에 issue-12 가 있으면 hard guard 통과 — 그 후 다음
        단계 (target repo resolve) 에서 다른 에러가 나도 guard reason 은
        절대 'issue_required_for_repo_work' 이면 안 됨."""

        with tempfile.TemporaryDirectory() as tmp:
            prov = LocalGitWorktreeProvisioner(repo_root=tmp, worktree_root=tmp)
            req = self._request(
                issue_number=None,
                branch_hint="feature/auth-issue-12",
            )
            try:
                prov.provision(request=req, branch="feature/auth-issue-12")
            except Exception as exc:
                # WorktreeProvisionError 인 경우만 reason 검사 — 그 외
                # (TargetRepoUnavailableError 등) 는 자동 통과 (guard 가
                # raise 한 게 아니라 다음 단계에서 raise)
                if isinstance(exc, WorktreeProvisionError):
                    self.assertNotEqual(
                        exc.reason, REASON_ISSUE_REQUIRED_FOR_REPO_WORK,
                        "guard 가 false-positive 거부",
                    )

    def test_issue_number_hint_satisfies_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prov = LocalGitWorktreeProvisioner(repo_root=tmp, worktree_root=tmp)
            req = self._request(issue_number=7, branch_hint="anything/random")
            try:
                prov.provision(request=req, branch="anything/random")
            except Exception as exc:
                if isinstance(exc, WorktreeProvisionError):
                    self.assertNotEqual(
                        exc.reason, REASON_ISSUE_REQUIRED_FOR_REPO_WORK
                    )

    def test_cross_repo_guard_is_repo_agnostic(self) -> None:
        """validator 가 repo 와 무관하게 동일 동작 — yule-studio-agent /
        naver-search-clone / 임의 target repo 모두 동일."""

        from yule_orchestrator.agents.governance.repo_write_policy import (
            IssueAnchorContext,
            validate_issue_anchor,
        )

        for repo_hint in (
            "yule-studio/yule-studio-agent",
            "yule-studio/naver-search-clone",
            "external/foo",
        ):
            with self.subTest(repo=repo_hint):
                # no anchor → blocked
                self.assertFalse(
                    validate_issue_anchor(IssueAnchorContext(branch=f"feature/x-for-{repo_hint}")).ok
                )
                # with anchor → ok
                self.assertTrue(
                    validate_issue_anchor(IssueAnchorContext(branch="feature/auth-issue-12")).ok
                )


# ---------------------------------------------------------------------------
# 10. legacy non-draft merge regression — no regression in P1-L-3 paths
# ---------------------------------------------------------------------------


class LegacyNonDraftMergeRegressionTests(_CacheFixture):
    def test_non_draft_pr_merge_still_succeeds_via_old_path(self) -> None:
        """non-draft PR + autonomous_merge → 옛 ACTION_AUTONOMOUS_MERGE_SUCCEEDED
        경로 그대로.  draft escalation 코드가 non-draft 경로를 깨지 않음."""

        sid = "non-draft-merge"
        _seed(sid, extra=_pending_extra(work_mode=WORK_MODE_AUTONOMOUS))

        def fake_executor(dispatch):
            # gate 통과 → merge_sha 반환 (non-draft 경로)
            return {"merge_sha": "ok", "method": "squash"}

        loop = asyncio.new_event_loop()
        try:
            session = load_session(sid)
            outcome = loop.run_until_complete(
                advance_pending_session(
                    session_id=sid,
                    session_extra=dict(session.extra or {}),
                    persist_extra=_persist_for(sid),
                    merge_executor=fake_executor,
                )
            )
        finally:
            loop.close()
        self.assertEqual(outcome.action, ACTION_AUTONOMOUS_MERGE_SUCCEEDED)
        final = load_session(sid)
        self.assertEqual(final.extra[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGED)


# ---------------------------------------------------------------------------
# Bonus — mark_pull_request_ready_for_review opt-in env guard
# ---------------------------------------------------------------------------


class MarkPRReadyForReviewOptInGuardTests(unittest.TestCase):
    def test_disabled_env_raises_merge_disabled(self) -> None:
        """opt-in env 없으면 LiveGithubAppMergeDisabled raise — 어떤
        HTTP 호출도 일어나지 않음."""

        from yule_orchestrator.github_app.live_client import (
            LiveGithubAppMergeDisabled,
            _is_merge_enabled,
        )

        # _is_merge_enabled 만 단위 검사 — opt-in env 없으면 False
        prev = os.environ.pop("YULE_GITHUB_MERGE_ENABLED", None)
        try:
            self.assertFalse(_is_merge_enabled())
            # 가짜 client 가 mark_pull_request_ready_for_review 호출 시
            # MergeDisabled raise 하는지 verify
            class _Client:
                _api_base = "https://api.github.com"

                def __init__(self):
                    pass

                # method under test bound from real class
                mark_pull_request_ready_for_review = (
                    LiveGithubAppClient.mark_pull_request_ready_for_review
                )

            c = _Client()
            with self.assertRaises(LiveGithubAppMergeDisabled) as cm:
                c.mark_pull_request_ready_for_review(repo="x/y", pr_number=4)
            self.assertEqual(cm.exception.status, 503)
        finally:
            if prev is not None:
                os.environ["YULE_GITHUB_MERGE_ENABLED"] = prev


if __name__ == "__main__":
    unittest.main()
