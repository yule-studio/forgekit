"""Phase D — Discord-driven Obsidian save approval flow.

Pins the contract between :mod:`yule_orchestrator.agents.obsidian.approval`
and the engineering channel router:

- "Obsidian에 정리해줘" never creates a new session — the runtime
  preflight builds a preview against the matched session and stores a
  pending proposal on ``session.extra``.
- "저장 승인" / "이대로 저장" with a pending proposal calls the writer
  exactly once.
- Approval phrases without a pending proposal degrade to a clarification
  message rather than intaking a brand-new session.
- Vault-path / writer failures surface as friendly chat lines, not
  tracebacks.
- The collision-safe suffix policy from :mod:`obsidian_writer` is
  preserved through the approval flow.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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

from yule_orchestrator.agents.obsidian.approval import (
    ObsidianApprovalError,
    build_save_proposal,
    execute_pending_proposal,
    get_pending_proposal,
    is_obsidian_approval,
    is_obsidian_save_request,
    store_pending_proposal,
)
from yule_orchestrator.agents.obsidian.export import ExportPath, ObsidianNote
from yule_orchestrator.agents.obsidian.writer import (
    ENV_VAULT_PATH,
    ObsidianWriteError,
    ObsidianWriteResult,
)
from yule_orchestrator.agents.research.pack import (
    ResearchAttachment,
    ResearchPack,
    ResearchSource,
    SourceType,
    pack_to_dict,
)
from yule_orchestrator.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    load_session,
    save_session,
)
from yule_orchestrator.discord.engineering_channel_router import (
    EngineeringRouteContext,
    route_engineering_message,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pack() -> ResearchPack:
    return ResearchPack(
        title="결제 모듈 멱등성 검증",
        summary="결제 PG 응답 재시도에서 idempotency-key 사용 패턴 정리.",
        primary_url="https://stripe.com/docs/idempotency",
        sources=(
            ResearchSource(
                source_url="https://stripe.com/docs/idempotency",
                title="Stripe — Idempotency",
                source_type=SourceType.OFFICIAL_DOCS,
                collected_by_role="engineering-agent/backend-engineer",
                why_relevant="공식 문서 기준 패턴",
            ),
        ),
        tags=("payment", "idempotency"),
        created_at=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
    )


def _make_session(
    *,
    session_id: str = "abc123session",
    channel_id: int = 1001,
    user_id: int = 4242,
    thread_id: int = 8000,
    with_pack: bool = True,
    extra: dict | None = None,
) -> WorkflowSession:
    base_extra: dict[str, Any] = {}
    if with_pack:
        base_extra["research_pack"] = pack_to_dict(_make_pack())
    if extra:
        base_extra.update(extra)
    now = datetime(2026, 5, 6, 9, 0, tzinfo=timezone.utc)
    return WorkflowSession(
        session_id=session_id,
        prompt="결제 모듈 멱등성 검증 흐름을 백엔드에 추가해줘",
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=now - timedelta(hours=2),
        updated_at=now,
        channel_id=channel_id,
        user_id=user_id,
        thread_id=thread_id,
        extra=base_extra,
    )


def _channel(channel_id: int = 1001, name: str = "업무-접수") -> FakeChannel:
    return FakeChannel(channel_id=channel_id, name=name)


def _route_context() -> EngineeringRouteContext:
    return EngineeringRouteContext(
        intake_channel_id=1001,
        intake_channel_name="업무-접수",
    )


class _FakeWriter:
    """Capture a single write_note() call without touching disk."""

    def __init__(
        self,
        *,
        result: ObsidianWriteResult | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self.calls: list[dict] = []
        self._result = result
        self._raise = raise_error

    def __call__(self, note, vault_root, *, overwrite=False, dry_run=False):
        self.calls.append(
            {
                "note": note,
                "vault_root": vault_root,
                "overwrite": overwrite,
                "dry_run": dry_run,
            }
        )
        if self._raise is not None:
            raise self._raise
        if self._result is not None:
            return self._result
        target = vault_root / note.path.full
        return ObsidianWriteResult(
            target_path=target,
            written=True,
            dry_run=False,
            overwrite=False,
            original_target_path=target,
            suffix_applied=False,
        )


# ---------------------------------------------------------------------------
# Phrase detectors
# ---------------------------------------------------------------------------


class PhraseDetectorTests(unittest.TestCase):
    def test_save_request_detects_korean_and_english(self) -> None:
        self.assertTrue(is_obsidian_save_request("Obsidian에 정리해줘"))
        self.assertTrue(is_obsidian_save_request("옵시디언에 저장해 줘"))
        self.assertTrue(is_obsidian_save_request("이 세션 기준으로 저장해줘"))
        self.assertTrue(is_obsidian_save_request("토의 기록 obsidian에 남겨줘"))
        self.assertTrue(is_obsidian_save_request("save to obsidian please"))

    def test_save_request_ignores_unrelated_text(self) -> None:
        self.assertFalse(is_obsidian_save_request("결제 모듈에 멱등성 추가"))
        self.assertFalse(is_obsidian_save_request(""))

    def test_approval_detects_korean_phrases(self) -> None:
        self.assertTrue(is_obsidian_approval("저장 승인"))
        self.assertTrue(is_obsidian_approval("이대로 저장"))
        self.assertTrue(is_obsidian_approval("이대로 저장해줘"))
        self.assertTrue(is_obsidian_approval("네, 저장 승인합니다"))

    def test_approval_does_not_fire_on_unrelated_messages(self) -> None:
        self.assertFalse(is_obsidian_approval("결제 모듈 멱등성 추가"))
        self.assertFalse(is_obsidian_approval(""))
        # Bare "승인" requires a pending proposal — gated by the router.
        self.assertFalse(is_obsidian_approval("승인"))

    def test_bare_approval_token_with_pending_proposal(self) -> None:
        self.assertTrue(is_obsidian_approval("승인", has_pending_proposal=True))
        self.assertTrue(is_obsidian_approval("approve", has_pending_proposal=True))


# ---------------------------------------------------------------------------
# Build proposal
# ---------------------------------------------------------------------------


class BuildSaveProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        _isolate_cache_for_test(self)

    def test_builds_preview_with_path_summary_and_sections(self) -> None:
        session = _make_session()
        proposal = build_save_proposal(session)
        self.assertIn("`abc123session`", proposal.preview_message)
        self.assertIn(proposal.vault_relative_path, proposal.preview_message)
        self.assertIn("저장 승인", proposal.preview_message)
        self.assertIn("결제 PG", proposal.preview_message)
        self.assertGreater(len(proposal.sections), 0)
        self.assertEqual(proposal.payload["session_id"], "abc123session")
        self.assertEqual(proposal.payload["channel_id"], 1001)
        self.assertEqual(proposal.payload["thread_id"], 8000)
        self.assertEqual(proposal.payload["user_id"], 4242)

    def test_missing_research_pack_raises_friendly_error(self) -> None:
        session = _make_session(with_pack=False)
        with self.assertRaises(ObsidianApprovalError) as ctx:
            build_save_proposal(session)
        self.assertIn("research_pack", str(ctx.exception))


# ---------------------------------------------------------------------------
# Execute pending proposal
# ---------------------------------------------------------------------------


class ExecutePendingProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        _isolate_cache_for_test(self)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.vault_root = Path(self.tmpdir.name)
        os.environ[ENV_VAULT_PATH] = str(self.vault_root)
        self.addCleanup(lambda: os.environ.pop(ENV_VAULT_PATH, None))

    def _session_with_proposal(self) -> WorkflowSession:
        session = _make_session()
        save_session(session)
        proposal = build_save_proposal(session)
        return store_pending_proposal(session, proposal)

    def test_no_pending_proposal_raises(self) -> None:
        session = _make_session(extra={"research_pack": None})
        save_session(session)
        with self.assertRaises(ObsidianApprovalError):
            execute_pending_proposal(session)

    def test_writer_invoked_once_with_resolved_vault(self) -> None:
        session = self._session_with_proposal()
        writer = _FakeWriter()
        updated, outcome = execute_pending_proposal(session, writer_fn=writer)
        self.assertTrue(outcome.success)
        self.assertEqual(len(writer.calls), 1)
        call = writer.calls[0]
        self.assertEqual(call["overwrite"], False)
        self.assertEqual(call["dry_run"], False)
        self.assertEqual(call["vault_root"], self.vault_root.resolve())
        self.assertIsNone(get_pending_proposal(updated))
        # session.extra carries last write event
        events = (updated.extra or {}).get("obsidian", {}).get("events") or []
        self.assertEqual(events[-1]["status"], "written")
        self.assertIn("`", outcome.message)

    def test_vault_missing_returns_friendly_error(self) -> None:
        session = self._session_with_proposal()
        os.environ.pop(ENV_VAULT_PATH, None)
        writer = _FakeWriter()
        updated, outcome = execute_pending_proposal(session, writer_fn=writer)
        self.assertFalse(outcome.success)
        self.assertEqual(writer.calls, [])
        # Pending proposal preserved so the operator can retry.
        self.assertIsNotNone(get_pending_proposal(updated))
        events = (updated.extra or {}).get("obsidian", {}).get("events") or []
        self.assertEqual(events[-1]["status"], "vault_unavailable")
        self.assertIn(ENV_VAULT_PATH, outcome.message)

    def test_writer_failure_is_wrapped(self) -> None:
        session = self._session_with_proposal()
        writer = _FakeWriter(raise_error=ObsidianWriteError("permission denied"))
        updated, outcome = execute_pending_proposal(session, writer_fn=writer)
        self.assertFalse(outcome.success)
        self.assertIn("permission denied", outcome.message)
        # Pending proposal preserved on failure.
        self.assertIsNotNone(get_pending_proposal(updated))
        events = (updated.extra or {}).get("obsidian", {}).get("events") or []
        self.assertEqual(events[-1]["status"], "write_failed")

    def test_collision_safe_suffix_surfaces_in_outcome(self) -> None:
        session = self._session_with_proposal()
        original = self.vault_root / "10-projects" / "yule-studio-agent" / "knowledge" / "x.md"
        suffixed = self.vault_root / "10-projects" / "yule-studio-agent" / "knowledge" / "x_2.md"
        writer = _FakeWriter(
            result=ObsidianWriteResult(
                target_path=suffixed,
                written=True,
                dry_run=False,
                overwrite=False,
                original_target_path=original,
                suffix_applied=True,
            )
        )
        _updated, outcome = execute_pending_proposal(session, writer_fn=writer)
        self.assertTrue(outcome.success)
        self.assertTrue(outcome.suffix_applied)
        self.assertIn("자동으로", outcome.message)


# ---------------------------------------------------------------------------
# Router integration
# ---------------------------------------------------------------------------


class RouterApprovalFlowTests(unittest.TestCase):
    """End-to-end: the router intercepts save / approval phrases instead
    of falling through to the legacy intake or research-loop path."""

    def setUp(self) -> None:
        _isolate_cache_for_test(self)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.vault_root = Path(self.tmpdir.name)
        os.environ[ENV_VAULT_PATH] = str(self.vault_root)
        self.addCleanup(lambda: os.environ.pop(ENV_VAULT_PATH, None))

        self.context = _route_context()
        self.send_chunks = AsyncMock()
        # These callables must NOT fire when the Obsidian flow takes over.
        self.intake_fn = AsyncMock(
            side_effect=AssertionError("intake must not run for save/approval")
        )
        self.kickoff_fn = AsyncMock(
            side_effect=AssertionError("kickoff must not run for save/approval")
        )
        self.conversation_fn = AsyncMock(
            side_effect=AssertionError(
                "conversation_fn must not run for save/approval"
            )
        )

    def _route(
        self,
        *,
        message,
        list_sessions_fn,
        writer_fn=None,
    ):
        return _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=self.conversation_fn,
                intake_fn=self.intake_fn,
                thread_kickoff_fn=self.kickoff_fn,
                send_chunks=self.send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=None,
                list_sessions_fn=list_sessions_fn,
                obsidian_writer_fn=writer_fn,
            )
        )

    def test_save_request_in_thread_builds_preview_and_stores_proposal(self) -> None:
        session = _make_session(thread_id=8000)
        save_session(session)
        sessions = [session]
        # Message is in the working thread (channel.id == thread_id) — recall
        # uses thread anchor to match.
        thread_channel = FakeChannel(
            channel_id=8000,
            name="작업-thread",
            parent_id=1001,
            parent_name="업무-접수",
        )
        message = FakeMessage(content="Obsidian에 정리해줘", channel=thread_channel)
        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: sessions,
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "abc123session")
        # Conversation / intake / kickoff didn't fire.
        self.intake_fn.assert_not_awaited()
        self.kickoff_fn.assert_not_awaited()
        self.conversation_fn.assert_not_awaited()
        # Preview surfaced via send_chunks.
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("Obsidian 저장 미리보기", sent)
        self.assertIn("`abc123session`", sent)
        self.assertIn("저장 승인", sent)
        # Pending proposal landed on the persisted session.
        reloaded = load_session("abc123session")
        self.assertIsNotNone(reloaded)
        pending = get_pending_proposal(reloaded)
        self.assertIsNotNone(pending)

    def test_approval_with_pending_proposal_invokes_writer(self) -> None:
        session = _make_session(thread_id=8000)
        save_session(session)
        proposal = build_save_proposal(session)
        store_pending_proposal(session, proposal)
        sessions_with_proposal = [load_session("abc123session")]

        writer = _FakeWriter()
        thread_channel = FakeChannel(
            channel_id=8000,
            name="작업-thread",
            parent_id=1001,
            parent_name="업무-접수",
        )
        message = FakeMessage(content="저장 승인", channel=thread_channel)

        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: sessions_with_proposal,
            writer_fn=writer,
        )
        self.assertTrue(result.handled)
        self.assertEqual(len(writer.calls), 1)
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("Obsidian 저장 완료", sent)

    def test_approval_without_pending_proposal_clarifies(self) -> None:
        # Session exists but no pending proposal stashed.
        session = _make_session(thread_id=8000)
        save_session(session)
        thread_channel = FakeChannel(
            channel_id=8000,
            name="작업-thread",
            parent_id=1001,
            parent_name="업무-접수",
        )
        message = FakeMessage(content="저장 승인", channel=thread_channel)
        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: [session],
        )
        self.assertTrue(result.handled)
        self.intake_fn.assert_not_awaited()
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("대기 중인 Obsidian 저장 제안이 없어요", sent)

    def test_save_request_without_session_match_keeps_clarification(self) -> None:
        # Mirrors the existing preflight contract from
        # ``tests/engineering/test_channel_router_runtime_preflight.py``:
        # no open session ⇒ runtime preflight asks for clarification, no
        # preview is built (the preview branch needs a matched session).
        thread_channel = FakeChannel(
            channel_id=9999,
            name="다른-thread",
            parent_id=1001,
            parent_name="업무-접수",
        )
        message = FakeMessage(content="Obsidian에 정리해줘", channel=thread_channel)
        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: [],
        )
        self.assertTrue(result.handled)
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("어떤 작업을 가리키시는지", sent)
        self.assertIn("기존 작업 후속 실행", sent)

    def test_approval_with_missing_vault_surfaces_friendly_error(self) -> None:
        session = _make_session(thread_id=8000)
        save_session(session)
        proposal = build_save_proposal(session)
        store_pending_proposal(session, proposal)
        sessions_with_proposal = [load_session("abc123session")]
        os.environ.pop(ENV_VAULT_PATH, None)

        writer = _FakeWriter()
        thread_channel = FakeChannel(
            channel_id=8000,
            name="작업-thread",
            parent_id=1001,
            parent_name="업무-접수",
        )
        message = FakeMessage(content="이대로 저장", channel=thread_channel)
        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: sessions_with_proposal,
            writer_fn=writer,
        )
        self.assertTrue(result.handled)
        self.assertEqual(writer.calls, [])
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn(ENV_VAULT_PATH, sent)


if __name__ == "__main__":
    unittest.main()
