"""Stabilisation Phase 4 — explicit "세션 <id> 기준" resolve + Obsidian gate.

Pin live-bug regressions:

  • 사용자가 "세션 abc123def456 기준으로 Obsidian에 저장해줘" 라고 해도
    채널/스레드 매칭만 하느라 무시되거나 엉뚱한 세션에 동작하던 문제.
  • lifecycle 미완성인데 (research_pack 없음 / status=insufficient)
    저장 승인 시 그대로 write 까지 가던 회귀.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests._helpers import (
    FakeChannel,
    FakeMessage,
    extract_prompt as _extract_prompt,
    isolate_cache_for_test as _isolate_cache_for_test,
    run as _run,
)

from yule_orchestrator.discord.engineering_channel_router import (
    EngineeringRouteContext,
    _can_save_to_obsidian,
    _extract_session_id_from_router_text,
    route_engineering_message,
)
from yule_orchestrator.agents.obsidian_writer import ENV_VAULT_PATH
from yule_orchestrator.agents.obsidian_approval import (
    build_save_proposal,
    store_pending_proposal,
)
from yule_orchestrator.agents.research.pack import ResearchPack, ResearchSource
from yule_orchestrator.agents.research.pack import pack_to_dict
from yule_orchestrator.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    load_session,
    save_session,
)


class ExtractSessionIdFromRouterTextTests(unittest.TestCase):
    def test_korean_phrase(self) -> None:
        self.assertEqual(
            _extract_session_id_from_router_text(
                "세션 abc123def456 기준으로 Obsidian에 저장해줘"
            ),
            "abc123def456",
        )

    def test_english_phrase(self) -> None:
        self.assertEqual(
            _extract_session_id_from_router_text("session ABC123DEF456 status please"),
            "abc123def456",
        )

    def test_quoted_session_id(self) -> None:
        self.assertEqual(
            _extract_session_id_from_router_text("세션 `abc123def456` 보여줘"),
            "abc123def456",
        )

    def test_no_session_keyword_returns_none(self) -> None:
        # A bare 12-hex token with no "세션"/"session" keyword does
        # NOT count — random commit hashes / URLs must not hijack.
        self.assertIsNone(
            _extract_session_id_from_router_text("commit abc123def456")
        )


class CanSaveToObsidianGateTests(unittest.TestCase):
    def _session(self, **extra: Any) -> Any:
        class _S:
            session_id = "abc"
            extra: dict = {}

        s = _S()
        s.extra = dict(extra)
        return s

    def test_session_with_pack_and_sources_allowed(self) -> None:
        s = self._session(
            research_pack={"sources": [{"url": "https://a"}, {"url": "https://b"}]},
        )
        ok, reason = _can_save_to_obsidian(s)
        self.assertTrue(ok, reason)
        self.assertIsNone(reason)

    def test_session_with_research_status_ready_allowed(self) -> None:
        s = self._session(research_status="ready", research_source_count=5)
        ok, reason = _can_save_to_obsidian(s)
        self.assertTrue(ok)

    def test_pack_missing_blocks(self) -> None:
        s = self._session()
        ok, reason = _can_save_to_obsidian(s)
        self.assertFalse(ok)
        self.assertIn("자료 0건", reason or "")

    def test_pack_with_zero_sources_blocks(self) -> None:
        s = self._session(research_pack={"sources": []})
        ok, reason = _can_save_to_obsidian(s)
        self.assertFalse(ok)

    def test_work_report_interim_blocks(self) -> None:
        s = self._session(
            research_pack={"sources": [{"url": "https://a"}]},
            work_report={
                "status": "interim",
                "missing_roles": ["qa-engineer"],
            },
        )
        ok, reason = _can_save_to_obsidian(s)
        self.assertFalse(ok)
        self.assertIn("qa-engineer", reason or "")

    def test_work_report_insufficient_blocks(self) -> None:
        s = self._session(
            research_pack={"sources": [{"url": "https://a"}]},
            work_report={"status": "insufficient"},
        )
        ok, reason = _can_save_to_obsidian(s)
        self.assertFalse(ok)
        self.assertIn("insufficient", reason or "")

    def test_work_report_ready_passes(self) -> None:
        s = self._session(
            research_pack={"sources": [{"url": "https://a"}]},
            research_source_count=5,
            work_report={"status": "ready"},
        )
        ok, reason = _can_save_to_obsidian(s)
        self.assertTrue(ok)

    def test_none_session_blocks(self) -> None:
        ok, reason = _can_save_to_obsidian(None)
        self.assertFalse(ok)


class ExplicitSessionIdObsidianResolveTests(unittest.TestCase):
    """Pin: explicit `세션 <id>` in the user's reply resolves to that
    session even when the channel/thread doesn't anchor to it."""

    def setUp(self) -> None:  # noqa: D401
        _isolate_cache_for_test(self)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        os.environ[ENV_VAULT_PATH] = str(Path(self.tmpdir.name))
        self.addCleanup(lambda: os.environ.pop(ENV_VAULT_PATH, None))
        self.context = EngineeringRouteContext(
            intake_channel_id=1001,
            intake_channel_name="업무-접수",
        )
        self.send_chunks = AsyncMock()

    def _seed_session_with_pending(
        self,
        *,
        session_id: str = "abc123def456",
        thread_id: int = 8000,
        with_pack: bool = True,
        work_report_status: str = "ready",
    ) -> WorkflowSession:
        pack = ResearchPack(
            title="harness",
            summary="test",
            sources=(
                ResearchSource(source_url="https://a", title="a"),
                ResearchSource(source_url="https://b", title="b"),
            ),
        )
        extra = {}
        if with_pack:
            extra["research_pack"] = pack_to_dict(pack)
            extra["research_source_count"] = 2
            extra["research_status"] = "ready"
        if work_report_status:
            extra["work_report"] = {"status": work_report_status}
        now = datetime(2026, 5, 6, 9, 0, tzinfo=timezone.utc)
        session = WorkflowSession(
            session_id=session_id,
            prompt="결제 모듈 멱등성 검증",
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=now,
            updated_at=now,
            thread_id=thread_id,
            extra=extra,
        )
        save_session(session)
        proposal = build_save_proposal(session)
        store_pending_proposal(session, proposal)
        return load_session(session_id)

    def test_explicit_id_resolves_outside_anchor_channel(self) -> None:
        # Seed a session under thread_id 8000 with a pending proposal,
        # then send "저장 승인 — 세션 <id> 기준" from a DIFFERENT
        # channel (channel_id=1001 — not the work thread). With the
        # Phase 4 fix the gate looks up by id and proceeds anyway.
        target = self._seed_session_with_pending()

        class _FakeWriter:
            def __init__(self):
                self.calls = []

            def __call__(self, *args, **kwargs):
                from yule_orchestrator.agents.obsidian_writer import ObsidianWriteResult

                self.calls.append({"args": args, "kwargs": kwargs})
                return ObsidianWriteResult(
                    path=Path(self.kwargs_path(kwargs)),
                    written=True,
                    git_committed=False,
                )

            def kwargs_path(self, kwargs):
                from yule_orchestrator.agents.obsidian_writer import resolve_vault_root

                vault = resolve_vault_root()
                relative = kwargs.get("relative_path") or "x.md"
                return vault / relative

        writer = _FakeWriter()
        message = FakeMessage(
            content="세션 abc123def456 기준으로 저장 승인",
            channel=FakeChannel(channel_id=1001, name="업무-접수"),
        )

        result = _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=AsyncMock(
                    side_effect=AssertionError("conversation_fn must not run")
                ),
                intake_fn=AsyncMock(),
                thread_kickoff_fn=AsyncMock(),
                send_chunks=self.send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=None,
                list_sessions_fn=lambda **_kw: [target],
                obsidian_writer_fn=writer,
            )
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "abc123def456")

    def test_explicit_unknown_id_returns_friendly_error(self) -> None:
        message = FakeMessage(
            content="세션 ffffffffffff 기준으로 저장 승인",
            channel=FakeChannel(channel_id=1001, name="업무-접수"),
        )
        result = _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=AsyncMock(
                    side_effect=AssertionError("conversation_fn must not run")
                ),
                intake_fn=AsyncMock(),
                thread_kickoff_fn=AsyncMock(),
                send_chunks=self.send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=None,
                list_sessions_fn=lambda **_kw: [],
            )
        )
        self.assertTrue(result.handled)
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("세션 `ffffffffffff` 을 찾지 못했어요", sent)

    def test_lifecycle_incomplete_blocks_write(self) -> None:
        # Session has a real research_pack (so build_save_proposal
        # passes) but the work_report status is "insufficient" — the
        # Phase 4 gate must still refuse and explain why.
        target = self._seed_session_with_pending(
            with_pack=True,
            work_report_status="insufficient",
        )

        class _NoOpWriter:
            def __init__(self) -> None:
                self.calls: list = []

            def __call__(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                raise AssertionError("writer must NOT run when blocked")

        writer = _NoOpWriter()
        message = FakeMessage(
            content="세션 abc123def456 기준으로 저장 승인",
            channel=FakeChannel(channel_id=1001, name="업무-접수"),
        )
        result = _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=AsyncMock(
                    side_effect=AssertionError("conversation_fn must not run")
                ),
                intake_fn=AsyncMock(),
                thread_kickoff_fn=AsyncMock(),
                send_chunks=self.send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=None,
                list_sessions_fn=lambda **_kw: [target],
                obsidian_writer_fn=writer,
            )
        )
        self.assertTrue(result.handled)
        self.assertEqual(len(writer.calls), 0)
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("Obsidian 저장을 진행하지 않았어요", sent)
        self.assertIn("차단 사유", sent)


if __name__ == "__main__":
    unittest.main()
