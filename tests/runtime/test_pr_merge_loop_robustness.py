"""P1-P — 사용자 명시 8 acceptance + 보조.

1. fe5eedc65196-like recovery restores mode/topology/scope/backlog/pr metadata
2. recovery 가 stuck approval_required → autonomous_merge 로 전환 + stale audit strip
3. continuation loop 가 recovery 후 영원히 approval_card_already_enqueued 로 빠지지 않음
4. GitHubAppNotFoundError(... body=...) constructor signature 회귀
5. missing PR 시 traceback 대신 deterministic pr_not_found blocked outcome
6. fixture 세션 처리 deterministic + quiet
7. live canonical session vs test fixture 구분 (operator surface)
8. operator audit / log 에 blocked / skip reason 명확히 남음
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from dataclasses import replace as _replace
from datetime import datetime, timezone
from typing import Any, Mapping

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.coding.coding_backlog_seed import (
    EXTRA_CODING_BACKLOG,
)
from yule_orchestrator.agents.job_queue.pr_approval import (
    PRMergeReplyDispatch,
)
from yule_orchestrator.agents.job_queue.pr_merge_continuation import (
    EXTRA_PR_MERGE_AUDIT,
    EXTRA_PR_MERGE_PR_NUMBER,
    EXTRA_PR_MERGE_REPO,
    EXTRA_PR_MERGE_STAGE,
    STAGE_PR_MERGE_BLOCKED,
    STAGE_PR_MERGE_PENDING,
    STAGE_PR_MERGED,
)
from yule_orchestrator.agents.job_queue.pr_merge_continuation_worker import (
    ACTION_APPROVAL_CARD_ENQUEUED,
    ACTION_AUTONOMOUS_MERGE_BLOCKED,
    ACTION_AUTONOMOUS_MERGE_SUCCEEDED,
    ACTION_SKIPPED_ALREADY_ENQUEUED,
    advance_pending_session,
)
from yule_orchestrator.agents.lifecycle.session_mode import (
    EXTRA_DECIDED_BY,
    EXTRA_SCOPE,
    EXTRA_TOPOLOGY,
    EXTRA_WORK_MODE,
    SCOPE_FULL_STACK,
    TOPOLOGY_SINGLE,
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
)
from yule_orchestrator.agents.lifecycle.session_recovery import (
    recover_session_full,
)
from yule_orchestrator.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    load_session,
    save_session,
)
from yule_orchestrator.github_app.client import (
    GitHubAppHTTPError,
    GitHubAppNotFoundError,
)
from yule_orchestrator.github_app.live_client import LiveGithubAppHTTPError


_CANONICAL_PROMPT = (
    "autonomous_merge, single_repo, full_stack_single_repo "
    "네이버 검색 풀스택 MVP 구현해줘 "
    "https://github.com/yule-studio/naver-search-clone"
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _seed_session(session_id: str, *, prompt: str, extra: Mapping[str, Any]) -> None:
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
        save_session(
            _replace(s, extra=dict(new_extra), updated_at=_now())
        )

    return _do


# ---------------------------------------------------------------------------
# 4. GitHubAppNotFoundError constructor signature regression
# ---------------------------------------------------------------------------


class GitHubAppNotFoundErrorSignatureTests(unittest.TestCase):
    """옛 회귀: live_client._get 가 body kwarg 로 raise 하는데 base class
    signature 에 body 가 없어서 TypeError → continuation loop noisy
    traceback.  본 가드는 두 형태의 raise 가 모두 가능함을 강제."""

    def test_body_kwarg_supported(self) -> None:
        exc = GitHubAppNotFoundError(
            "GitHub GET /repos/x/y/pulls/4 -> 404 (not found)",
            status=404,
            body={"message": "Not Found"},
        )
        self.assertEqual(exc.status, 404)
        self.assertEqual(exc.body, {"message": "Not Found"})
        self.assertIn("404", str(exc))

    def test_base_class_supports_body_too(self) -> None:
        exc = GitHubAppHTTPError("x", status=422, url="https://u", body=b"raw")
        self.assertEqual(exc.body, b"raw")

    def test_live_class_supports_body_too(self) -> None:
        exc = LiveGithubAppHTTPError(
            "x", status=500, url="https://u", body={"k": "v"}
        )
        self.assertEqual(exc.body, {"k": "v"})

    def test_old_call_form_still_works(self) -> None:
        # body 미지정 시도 — backwards-compat
        exc = GitHubAppNotFoundError("x", status=404)
        self.assertIsNone(exc.body)


# ---------------------------------------------------------------------------
# 5. Missing PR → deterministic pr_not_found blocked outcome
# 6. Fixture session deterministic + quiet
# ---------------------------------------------------------------------------


class MissingPRDeterministicBlockedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["YULE_AGENT_CACHE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        os.environ.pop("YULE_AGENT_CACHE_DIR", None)

    def test_missing_pr_translates_to_pr_not_found_block(self) -> None:
        sid = "fixture-missing-pr-1"
        _seed_session(
            sid,
            prompt=_CANONICAL_PROMPT,
            extra={
                EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
                EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
                EXTRA_PR_MERGE_PR_NUMBER: 9999,
                EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
                "pr_merge_pr_url": "https://github.com/yule-studio/naver-search-clone/pull/9999",
                "pr_merge_head_sha": "missing-sha",
                "pr_merge_base_branch": "main",
            },
        )

        def fake_executor(dispatch: PRMergeReplyDispatch) -> Mapping[str, Any]:
            raise GitHubAppNotFoundError(
                "GitHub GET /repos/yule-studio/naver-search-clone/pulls/9999 -> 404 (not found)",
                status=404,
                body={"message": "Not Found"},
            )

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
        # 1) outcome is blocked (not traceback)
        self.assertEqual(outcome.action, ACTION_AUTONOMOUS_MERGE_BLOCKED)
        self.assertEqual(outcome.reason, "pr_not_found")
        # 2) stage advanced to blocked → loop won't pick again
        final = load_session(sid)
        self.assertEqual(final.extra[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGE_BLOCKED)
        # 3) audit captured exception class + status for operator
        audit = final.extra[EXTRA_PR_MERGE_AUDIT][-1]
        self.assertEqual(audit["stage"], STAGE_PR_MERGE_BLOCKED)
        self.assertEqual(audit["exception_class"], "GitHubAppNotFoundError")
        self.assertEqual(audit["status"], 404)
        # 4) re-tick 은 not-pending 으로 skip — fixture noise 차단
        loop = asyncio.new_event_loop()
        try:
            session2 = load_session(sid)
            outcome2 = loop.run_until_complete(
                advance_pending_session(
                    session_id=sid,
                    session_extra=dict(session2.extra or {}),
                    persist_extra=_persist_for(sid),
                    merge_executor=fake_executor,
                )
            )
        finally:
            loop.close()
        self.assertNotEqual(outcome2.action, ACTION_AUTONOMOUS_MERGE_BLOCKED)
        # not-pending 분기
        from yule_orchestrator.agents.job_queue.pr_merge_continuation_worker import (
            ACTION_SKIPPED_NOT_PENDING,
        )

        self.assertEqual(outcome2.action, ACTION_SKIPPED_NOT_PENDING)

    def test_unknown_http_error_also_blocks_with_explicit_reason(self) -> None:
        sid = "fixture-http-error-2"
        _seed_session(
            sid,
            prompt=_CANONICAL_PROMPT,
            extra={
                EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
                EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
                EXTRA_PR_MERGE_PR_NUMBER: 4,
                EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
                "pr_merge_pr_url": "https://github.com/yule-studio/naver-search-clone/pull/4",
                "pr_merge_head_sha": "sha",
                "pr_merge_base_branch": "main",
            },
        )

        def fake_executor(dispatch):
            raise LiveGithubAppHTTPError(
                "GitHub GET /x -> HTTP 503", status=503, url="https://x"
            )

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
        self.assertEqual(outcome.action, ACTION_AUTONOMOUS_MERGE_BLOCKED)
        self.assertIn("github_http_error", outcome.reason)

    def test_unexpected_exception_class_still_blocked_not_traceback(self) -> None:
        sid = "fixture-unexpected-3"
        _seed_session(
            sid,
            prompt=_CANONICAL_PROMPT,
            extra={
                EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
                EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
                EXTRA_PR_MERGE_PR_NUMBER: 4,
                EXTRA_PR_MERGE_REPO: "x/y",
                "pr_merge_pr_url": "https://x/y/pull/4",
                "pr_merge_head_sha": "s",
                "pr_merge_base_branch": "main",
            },
        )

        def fake_executor(dispatch):
            raise ValueError("simulated unexpected")

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
        self.assertEqual(outcome.action, ACTION_AUTONOMOUS_MERGE_BLOCKED)
        self.assertIn("merge_executor_raised:ValueError", outcome.reason)


# ---------------------------------------------------------------------------
# 1, 2, 3 — fe5eedc65196 recovery + stuck approval → autonomous_merge flip
# ---------------------------------------------------------------------------


class CanonicalSessionRecoveryFlipTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["YULE_AGENT_CACHE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        os.environ.pop("YULE_AGENT_CACHE_DIR", None)

    def test_recovery_restores_mode_backlog_pr_metadata(self) -> None:
        """fe5eedc65196 shape: work_mode=None, but pr_merge_pending stamped."""

        sid = "fe5eedc65196"
        _seed_session(
            sid,
            prompt=_CANONICAL_PROMPT,
            extra={
                EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
                EXTRA_PR_MERGE_PR_NUMBER: 4,
                EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
                "pr_merge_pr_url": "https://github.com/yule-studio/naver-search-clone/pull/4",
                "pr_merge_head_sha": "pr4sha",
                "pr_merge_base_branch": "main",
            },
        )
        report = recover_session_full(
            session_id=sid, explicit_work_mode=WORK_MODE_AUTONOMOUS
        )
        self.assertTrue(report.found)
        self.assertTrue(report.mode_persisted)
        self.assertEqual(report.work_mode, WORK_MODE_AUTONOMOUS)
        self.assertEqual(report.topology, TOPOLOGY_SINGLE)
        self.assertEqual(report.scope, SCOPE_FULL_STACK)
        self.assertEqual(report.backlog_seeded_count, 8)
        fresh = load_session(sid)
        self.assertEqual(fresh.extra[EXTRA_WORK_MODE], WORK_MODE_AUTONOMOUS)
        self.assertEqual(fresh.extra[EXTRA_PR_MERGE_PR_NUMBER], 4)
        self.assertEqual(len(fresh.extra[EXTRA_CODING_BACKLOG]), 8)

    def test_recovery_strips_stale_approval_card_audit(self) -> None:
        """stuck approval_required → autonomous_merge 전환 시 옛
        approval_card_enqueued audit 가 strip 되어 loop 가 새 모드로 재평가
        가능."""

        sid = "fe5eedc65196-stuck"
        _seed_session(
            sid,
            prompt=_CANONICAL_PROMPT,
            extra={
                EXTRA_WORK_MODE: WORK_MODE_APPROVAL,
                EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
                EXTRA_PR_MERGE_PR_NUMBER: 4,
                EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
                "pr_merge_pr_url": "https://github.com/yule-studio/naver-search-clone/pull/4",
                "pr_merge_head_sha": "pr4sha",
                "pr_merge_base_branch": "main",
                EXTRA_PR_MERGE_AUDIT: [
                    {
                        "event": "approval_card_enqueued",
                        "approval_job_id": "stale-job",
                        "at": "2026-05-17T00:00:00+00:00",
                    }
                ],
            },
        )
        report = recover_session_full(
            session_id=sid, explicit_work_mode=WORK_MODE_AUTONOMOUS
        )
        self.assertTrue(report.mode_persisted)
        # mode_recovered event 가 audit 에 들어가 있고, approval_card_enqueued
        # 는 strip 됨
        fresh = load_session(sid)
        audit = fresh.extra[EXTRA_PR_MERGE_AUDIT]
        events = [e.get("event") for e in audit if isinstance(e, Mapping)]
        self.assertIn("mode_recovered", events)
        self.assertNotIn("approval_card_enqueued", events)
        # mode_recovered 의 메타 — operator 가 strip 횟수를 본다
        mr = next(e for e in audit if e.get("event") == "mode_recovered")
        self.assertEqual(mr["prior_work_mode"], WORK_MODE_APPROVAL)
        self.assertEqual(mr["new_work_mode"], WORK_MODE_AUTONOMOUS)
        self.assertEqual(mr["stripped_approval_card_events"], 1)

    def test_recovery_unstuck_session_now_passes_autonomous_merge_dispatch(
        self,
    ) -> None:
        """recovery 후 continuation loop 가 autonomous_merge 경로로 실제
        dispatch — 옛 approval_card_already_enqueued stuck 회복."""

        sid = "fe5eedc65196-unstuck"
        _seed_session(
            sid,
            prompt=_CANONICAL_PROMPT,
            extra={
                EXTRA_WORK_MODE: WORK_MODE_APPROVAL,
                EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
                EXTRA_PR_MERGE_PR_NUMBER: 4,
                EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
                "pr_merge_pr_url": "https://github.com/yule-studio/naver-search-clone/pull/4",
                "pr_merge_head_sha": "pr4sha",
                "pr_merge_base_branch": "main",
                EXTRA_PR_MERGE_AUDIT: [
                    {
                        "event": "approval_card_enqueued",
                        "approval_job_id": "stale",
                        "at": "2026-05-17T00:00:00+00:00",
                    }
                ],
            },
        )
        recover_session_full(
            session_id=sid, explicit_work_mode=WORK_MODE_AUTONOMOUS
        )

        def fake_executor(dispatch):
            return {"merge_sha": "merged-after-recovery", "method": "squash"}

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
        # 옛 stuck 시그널 (ACTION_SKIPPED_ALREADY_ENQUEUED) 가 더 이상 안 나옴
        self.assertNotEqual(outcome.action, ACTION_SKIPPED_ALREADY_ENQUEUED)
        # autonomous_merge 성공 분기
        self.assertEqual(outcome.action, ACTION_AUTONOMOUS_MERGE_SUCCEEDED)
        self.assertEqual(outcome.merge_sha, "merged-after-recovery")


# ---------------------------------------------------------------------------
# 7. live canonical vs test fixture distinction (operator surface)
# ---------------------------------------------------------------------------


class CanonicalVsFixtureSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["YULE_AGENT_CACHE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        os.environ.pop("YULE_AGENT_CACHE_DIR", None)

    def test_audit_distinguishes_blocked_from_succeeded(self) -> None:
        """operator 가 canonical (succeeded) vs fixture (blocked) 를 audit
        만 보고 구분 가능."""

        # canonical → merge 성공
        canonical = "live-canonical-1"
        _seed_session(
            canonical,
            prompt=_CANONICAL_PROMPT,
            extra={
                EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
                EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
                EXTRA_PR_MERGE_PR_NUMBER: 4,
                EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
                "pr_merge_pr_url": "https://x/y/pull/4",
                "pr_merge_head_sha": "s",
                "pr_merge_base_branch": "main",
            },
        )
        loop = asyncio.new_event_loop()
        try:
            cs = load_session(canonical)
            loop.run_until_complete(
                advance_pending_session(
                    session_id=canonical,
                    session_extra=dict(cs.extra or {}),
                    persist_extra=_persist_for(canonical),
                    merge_executor=lambda d: {"merge_sha": "ok", "method": "squash"},
                )
            )
        finally:
            loop.close()

        # fixture → 404
        fixture = "fixture-404-2"
        _seed_session(
            fixture,
            prompt=_CANONICAL_PROMPT,
            extra={
                EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
                EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
                EXTRA_PR_MERGE_PR_NUMBER: 9999,
                EXTRA_PR_MERGE_REPO: "x/y",
                "pr_merge_pr_url": "https://x/y/pull/9999",
                "pr_merge_head_sha": "s",
                "pr_merge_base_branch": "main",
            },
        )

        def raise_404(dispatch):
            raise GitHubAppNotFoundError(
                "404", status=404, body={"message": "Not Found"}
            )

        loop = asyncio.new_event_loop()
        try:
            fs = load_session(fixture)
            loop.run_until_complete(
                advance_pending_session(
                    session_id=fixture,
                    session_extra=dict(fs.extra or {}),
                    persist_extra=_persist_for(fixture),
                    merge_executor=raise_404,
                )
            )
        finally:
            loop.close()

        c_final = load_session(canonical)
        f_final = load_session(fixture)
        self.assertEqual(c_final.extra[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGED)
        self.assertEqual(f_final.extra[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGE_BLOCKED)
        # operator 가 두 세션의 audit 마지막 stage 토큰만 봐도 구분 가능
        self.assertEqual(c_final.extra[EXTRA_PR_MERGE_AUDIT][-1]["stage"], STAGE_PR_MERGED)
        self.assertEqual(f_final.extra[EXTRA_PR_MERGE_AUDIT][-1]["stage"], STAGE_PR_MERGE_BLOCKED)
        self.assertEqual(
            f_final.extra[EXTRA_PR_MERGE_AUDIT][-1]["exception_class"],
            "GitHubAppNotFoundError",
        )


if __name__ == "__main__":
    unittest.main()
