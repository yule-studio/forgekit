"""Discord slash command 헬퍼 회귀 테스트.

`/engineer_approve`, `/engineer_reject`, `/engineer_progress`, `/engineer_complete`
의 백엔드 헬퍼 (`_run_engineer_*`) 가 workflow.py 위에서 정상적으로 상태 전이를
일으키고 사용자 친화적인 한국어 메시지를 만들어내는지 검증한다.

실제 Discord 인터랙션은 의존하지 않고, 임시 SQLite 캐시 위에서 orchestrator를
직접 돌려서 핵심 흐름과 잘못된 상태 전이의 에러 메시지를 함께 본다.
"""

from __future__ import annotations

import os
import tempfile
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

import yule_orchestrator.discord.commands as discord_commands
from yule_orchestrator.agents import WorkflowError


class EngineerWorkflowSlashHelpersTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev_db = os.environ.get("YULE_CACHE_DB_PATH")
        os.environ["YULE_CACHE_DB_PATH"] = os.path.join(self._tmp.name, "cache.sqlite3")

    def tearDown(self) -> None:
        if self._prev_db is None:
            os.environ.pop("YULE_CACHE_DB_PATH", None)
        else:
            os.environ["YULE_CACHE_DB_PATH"] = self._prev_db

    def _intake_session(self, *, write_requested: bool = False) -> str:
        orchestrator = discord_commands._engineer_orchestrator()
        result = orchestrator.intake(
            prompt="새 랜딩 hero 정리",
            write_requested=write_requested,
        )
        return result.session.session_id

    # ---- approve --------------------------------------------------------

    def test_approve_returns_korean_summary_and_unblocks_session(self) -> None:
        session_id = self._intake_session(write_requested=True)

        message = discord_commands._run_engineer_approve(session_id=session_id)

        self.assertIn("세션 승인 완료", message)
        self.assertIn(session_id, message)
        self.assertIn("/engineer_progress", message)

        orchestrator = discord_commands._engineer_orchestrator()
        session = orchestrator.get(session_id)
        assert session is not None
        self.assertEqual(session.state.value, "approved")
        self.assertIsNone(session.write_blocked_reason)

    def test_approve_after_completion_raises_workflow_error(self) -> None:
        session_id = self._intake_session()
        discord_commands._run_engineer_approve(session_id=session_id)
        discord_commands._run_engineer_progress(session_id=session_id, note="시작")
        discord_commands._run_engineer_complete(session_id=session_id, summary="끝")

        with self.assertRaises(WorkflowError) as ctx:
            discord_commands._run_engineer_approve(session_id=session_id)
        self.assertIn("cannot approve", str(ctx.exception))

    def test_approve_rejects_blank_session_id(self) -> None:
        with self.assertRaises(ValueError):
            discord_commands._run_engineer_approve(session_id="   ")

    def test_approve_unknown_session_raises_workflow_error(self) -> None:
        with self.assertRaises(WorkflowError):
            discord_commands._run_engineer_approve(session_id="ses-unknown")

    # ---- reject ---------------------------------------------------------

    def test_reject_records_reason_and_returns_korean_summary(self) -> None:
        session_id = self._intake_session()

        message = discord_commands._run_engineer_reject(
            session_id=session_id,
            reason="요구사항 불명확",
        )

        self.assertIn("세션 거절", message)
        self.assertIn("요구사항 불명확", message)
        self.assertIn("재개할 수 없습니다", message)

        orchestrator = discord_commands._engineer_orchestrator()
        session = orchestrator.get(session_id)
        assert session is not None
        self.assertEqual(session.state.value, "rejected")
        self.assertEqual(session.rejection_reason, "요구사항 불명확")

    def test_reject_blocks_when_already_completed(self) -> None:
        session_id = self._intake_session()
        discord_commands._run_engineer_approve(session_id=session_id)
        discord_commands._run_engineer_progress(session_id=session_id, note="시작")
        discord_commands._run_engineer_complete(session_id=session_id, summary="끝")

        with self.assertRaises(WorkflowError) as ctx:
            discord_commands._run_engineer_reject(
                session_id=session_id,
                reason="late veto",
            )
        self.assertIn("already", str(ctx.exception))

    def test_reject_requires_non_empty_reason(self) -> None:
        session_id = self._intake_session()
        with self.assertRaises(ValueError):
            discord_commands._run_engineer_reject(session_id=session_id, reason="   ")

    # ---- progress -------------------------------------------------------

    def test_progress_appends_note_after_approval(self) -> None:
        session_id = self._intake_session()
        discord_commands._run_engineer_approve(session_id=session_id)

        message = discord_commands._run_engineer_progress(
            session_id=session_id,
            note="디자이너 1차 시안 정리",
        )

        self.assertIn("진행 상황", message)
        self.assertIn("디자이너 1차 시안 정리", message)

        orchestrator = discord_commands._engineer_orchestrator()
        session = orchestrator.get(session_id)
        assert session is not None
        self.assertEqual(session.state.value, "in_progress")
        self.assertEqual(len(session.progress_notes), 1)
        self.assertEqual(session.progress_notes[0], "디자이너 1차 시안 정리")

    def test_progress_blocked_before_approval_returns_korean_friendly_error(self) -> None:
        session_id = self._intake_session(write_requested=True)
        with self.assertRaises(WorkflowError) as ctx:
            discord_commands._run_engineer_progress(
                session_id=session_id,
                note="앞서 시작",
            )
        self.assertIn("not yet approved", str(ctx.exception))

    def test_progress_blocked_after_rejection(self) -> None:
        session_id = self._intake_session()
        discord_commands._run_engineer_reject(session_id=session_id, reason="중단")
        with self.assertRaises(WorkflowError):
            discord_commands._run_engineer_progress(
                session_id=session_id,
                note="이래도 되나",
            )

    def test_progress_requires_note(self) -> None:
        session_id = self._intake_session()
        discord_commands._run_engineer_approve(session_id=session_id)
        with self.assertRaises(ValueError):
            discord_commands._run_engineer_progress(session_id=session_id, note=" ")

    # ---- complete -------------------------------------------------------

    def test_complete_closes_session_with_summary(self) -> None:
        session_id = self._intake_session()
        discord_commands._run_engineer_approve(session_id=session_id)
        discord_commands._run_engineer_progress(session_id=session_id, note="중간 보고")

        message = discord_commands._run_engineer_complete(
            session_id=session_id,
            summary="hero 카피 + CTA 색상 정리",
        )

        self.assertIn("완료 보고", message)
        self.assertIn("hero 카피 + CTA 색상 정리", message)

        orchestrator = discord_commands._engineer_orchestrator()
        session = orchestrator.get(session_id)
        assert session is not None
        self.assertEqual(session.state.value, "completed")
        self.assertEqual(session.summary, "hero 카피 + CTA 색상 정리")

    def test_complete_blocked_from_intake_state(self) -> None:
        session_id = self._intake_session(write_requested=True)
        with self.assertRaises(WorkflowError) as ctx:
            discord_commands._run_engineer_complete(
                session_id=session_id,
                summary="끝났다",
            )
        self.assertIn("not yet approved", str(ctx.exception))

    def test_complete_blocked_after_rejection(self) -> None:
        session_id = self._intake_session()
        discord_commands._run_engineer_reject(session_id=session_id, reason="중단")
        with self.assertRaises(WorkflowError):
            discord_commands._run_engineer_complete(
                session_id=session_id,
                summary="끝",
            )

    def test_complete_requires_summary(self) -> None:
        session_id = self._intake_session()
        discord_commands._run_engineer_approve(session_id=session_id)
        with self.assertRaises(ValueError):
            discord_commands._run_engineer_complete(session_id=session_id, summary="")


if __name__ == "__main__":
    unittest.main()
