from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents import (
    Dispatcher,
    TaskType,
    WorkflowError,
    WorkflowOrchestrator,
    WorkflowState,
    build_participants_pool,
    extract_urls,
)
from yule_orchestrator.agents.workflow_state import find_latest_open_session


REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_orchestrator() -> tuple[WorkflowOrchestrator, list[datetime]]:
    pool = build_participants_pool(REPO_ROOT, "engineering-agent")
    timestamps = [datetime(2026, 4, 29, 9, 0, 0)]

    def fake_now() -> datetime:
        ts = timestamps[-1]
        timestamps.append(ts.replace(minute=ts.minute + 1) if ts.minute < 59 else ts)
        return ts

    return WorkflowOrchestrator(Dispatcher(pool), now_fn=fake_now), timestamps


class _IsolatedCacheTestCase(unittest.TestCase):
    """Each test gets its own SQLite cache DB so sessions don't leak across cases."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev_root = os.environ.get("YULE_REPO_ROOT")
        os.environ["YULE_REPO_ROOT"] = self._tmp.name

    def tearDown(self) -> None:
        if self._prev_root is None:
            os.environ.pop("YULE_REPO_ROOT", None)
        else:
            os.environ["YULE_REPO_ROOT"] = self._prev_root


class ExtractUrlsTestCase(unittest.TestCase):
    def test_finds_multiple_urls(self) -> None:
        text = "참고 https://stripe.com/pricing 그리고 http://example.com/x?y=1, 끝"
        self.assertEqual(
            extract_urls(text),
            ("https://stripe.com/pricing", "http://example.com/x?y=1"),
        )

    def test_strips_trailing_punctuation(self) -> None:
        self.assertEqual(
            extract_urls("see (https://example.com/foo)."),
            ("https://example.com/foo",),
        )

    def test_no_url(self) -> None:
        self.assertEqual(extract_urls("그냥 평범한 문장"), ())


class IntakeTestCase(_IsolatedCacheTestCase):
    def test_intake_classifies_and_picks_executor(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(
            prompt="새 랜딩페이지 hero 섹션 정리",
            write_requested=True,
        )
        self.assertEqual(result.session.state, WorkflowState.INTAKE)
        self.assertEqual(result.plan.task_type, TaskType.LANDING_PAGE)
        self.assertEqual(result.session.executor_role, "frontend-engineer")
        self.assertTrue(result.session.write_requested)
        self.assertIsNotNone(result.session.write_blocked_reason)

    def test_intake_extracts_user_url(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(
            prompt="이메일 캠페인 https://example.com/ad-creative 참고",
            write_requested=False,
        )
        self.assertIn("https://example.com/ad-creative", result.session.references_user)
        self.assertIn("Really Good Emails", result.session.references_suggested)
        # User-provided URL must come first in the message body
        idx_user = result.message.find("https://example.com/ad-creative")
        idx_suggested = result.message.find("Really Good Emails")
        self.assertLess(idx_user, idx_suggested)

    def test_intake_message_omits_references_for_pure_backend(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(prompt="users API schema 추가", write_requested=False)
        self.assertEqual(result.session.references_suggested, ())
        self.assertIn("이 task_type에는 시각 reference를 강제하지 않습니다", result.message)

    def test_latest_open_session_can_be_found_for_thread_reuse(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(
            prompt="열려 있는 thread에서 이어갈 작업",
            write_requested=False,
            channel_id=111,
            user_id=222,
            thread_id=333,
        )

        found = find_latest_open_session(channel_id=111, user_id=222)

        self.assertIsNotNone(found)
        self.assertEqual(found.session_id, result.session.session_id)
        self.assertEqual(found.thread_id, 333)


class TransitionTestCase(_IsolatedCacheTestCase):
    def test_full_happy_path(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(prompt="랜딩 hero 정리", write_requested=True)
        sid = result.session.session_id

        approved = orch.approve(sid)
        self.assertEqual(approved.state, WorkflowState.APPROVED)
        self.assertIsNone(approved.write_blocked_reason)

        progress = orch.progress(sid, note="시안 1차")
        self.assertEqual(progress.session.state, WorkflowState.IN_PROGRESS)
        self.assertEqual(progress.session.progress_notes, ("시안 1차",))

        completion = orch.complete(
            sid,
            summary="hero 카피 정리 완료",
            references_used=[
                {"title": "Stripe", "source": "Mobbin", "url": "https://x", "rationale": "step copy 차용"},
            ],
        )
        self.assertEqual(completion.session.state, WorkflowState.COMPLETED)
        self.assertIn("Stripe", completion.message)
        self.assertIn("step copy 차용", completion.message)

    def test_progress_before_approval_rejected(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(prompt="x", write_requested=True)
        with self.assertRaises(WorkflowError):
            orch.progress(result.session.session_id, note="early")

    def test_complete_before_approval_rejected(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(prompt="x", write_requested=True)
        with self.assertRaises(WorkflowError):
            orch.complete(result.session.session_id, summary="early")

    def test_double_approve_rejected(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(prompt="x", write_requested=True)
        sid = result.session.session_id
        orch.approve(sid)
        with self.assertRaises(WorkflowError):
            orch.approve(sid)

    def test_reject_blocks_further_transitions(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(prompt="x", write_requested=True)
        sid = result.session.session_id
        orch.reject(sid, reason="중복 작업")
        with self.assertRaises(WorkflowError):
            orch.complete(sid, summary="x")
        with self.assertRaises(WorkflowError):
            orch.progress(sid, note="x")

    def test_unknown_session_id(self) -> None:
        orch, _ = _build_orchestrator()
        with self.assertRaises(WorkflowError):
            orch.approve("nonexistent")


class WriteGateTestCase(_IsolatedCacheTestCase):
    def test_write_not_requested_has_no_block(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(prompt="시안 정리", write_requested=False)
        self.assertIsNone(result.session.write_blocked_reason)
        self.assertIn("승인 없이 진행 가능", result.message)

    def test_write_requested_blocks_until_approve(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(prompt="x", write_requested=True)
        self.assertIn("승인 필요", result.message)


class CompletionFormatTestCase(_IsolatedCacheTestCase):
    def test_completion_includes_used_references_block(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(prompt="hero 정리", write_requested=True)
        sid = result.session.session_id
        orch.approve(sid)
        completion = orch.complete(
            sid,
            summary="끝",
            references_used=[
                {"title": "Awwwards SOTD", "source": "Awwwards", "rationale": "scroll 인터랙션 패턴 차용"},
            ],
        )
        self.assertIn("Awwwards SOTD", completion.message)
        self.assertIn("scroll 인터랙션 패턴 차용", completion.message)

    def test_completion_marks_no_references_used(self) -> None:
        orch, _ = _build_orchestrator()
        result = orch.intake(prompt="새 랜딩페이지 hero 정리", write_requested=True)
        sid = result.session.session_id
        orch.approve(sid)
        completion = orch.complete(sid, summary="끝", references_used=[])
        # Suggested references existed (landing-page) but none were actually used.
        self.assertIn("(없음", completion.message)


if __name__ == "__main__":
    unittest.main()
