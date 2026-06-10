"""Stabilisation Phase 2 — research_pack persistence + insufficient metadata.

Pin the live-bug regressions:

  • active_research_roles 가 박혔는데도 session.extra.research_pack
    가 비어 있어 work_report / Obsidian / supervisor 가 근거 없는
    final 상태로 동작하던 문제.
  • collection_outcome 만 있고 pack 이 None 일 때
    ``research_status: insufficient`` / ``research_source_count`` /
    ``research_stop_reason`` / ``research_missing_roles`` 가
    session.extra 에 남아야 한다 — 그래야 다음 단계가 부족 사유를
    설명할 수 있다.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests._helpers import isolate_cache_for_test as _isolate_cache_for_test

from yule_engineering.agents.research.pack import ResearchPack, ResearchSource
from yule_engineering.agents.research.persistence import (
    persist_research_artifacts,
)
from yule_engineering.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    load_session,
    save_session,
)


@dataclass
class _FakeOutcome:
    """Light stand-in for CollectionOutcome — only the fields the
    persistence helper reads."""

    auto_collected_count: int = 0
    stop_reason: Optional[str] = None
    under_covered_roles: tuple = ()
    active_roles: tuple = ()
    mode: Any = None
    collector_name: str = "mock"
    query: str = ""


def _seed_session() -> WorkflowSession:
    now = datetime(2026, 5, 6)
    session = WorkflowSession(
        session_id="abc12345",
        prompt="harness 도입 검토",
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=now,
        updated_at=now,
    )
    save_session(session)
    return session


def _pack_with(*urls: str) -> ResearchPack:
    sources = tuple(
        ResearchSource(
            source_url=url,
            title=f"title-{i}",
            summary="",
        )
        for i, url in enumerate(urls)
    )
    return ResearchPack(
        title="harness",
        summary="test",
        sources=sources,
    )


class PackPersistenceWritesStatusKeysTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401
        _isolate_cache_for_test(self)

    def test_pack_with_sources_writes_ready_status(self) -> None:
        session = _seed_session()
        pack = _pack_with("https://a", "https://b", "https://c")
        outcome = _FakeOutcome(
            auto_collected_count=3,
            stop_reason="sufficient",
            active_roles=("tech-lead", "ai-engineer"),
        )
        updated = persist_research_artifacts(
            session, pack, collection_outcome=outcome
        )
        reloaded = load_session("abc12345")
        self.assertIsNotNone(reloaded)
        extra = dict(reloaded.extra)
        self.assertIn("research_pack", extra)
        self.assertEqual(extra["research_source_count"], 3)
        self.assertEqual(extra["research_status"], "ready")
        self.assertEqual(extra["research_stop_reason"], "sufficient")
        self.assertEqual(extra["research_active_roles"], ["tech-lead", "ai-engineer"])

    def test_outcome_only_writes_insufficient_status(self) -> None:
        # NEEDS_USER_INPUT branch: pack is None, source_count = 0,
        # stop_reason and missing_roles are present.
        session = _seed_session()
        outcome = _FakeOutcome(
            auto_collected_count=0,
            stop_reason="no_initial_provider_hit",
            under_covered_roles=("ai-engineer", "qa-engineer"),
            active_roles=("tech-lead", "ai-engineer", "qa-engineer"),
        )
        persist_research_artifacts(session, None, collection_outcome=outcome)
        reloaded = load_session("abc12345")
        extra = dict(reloaded.extra)
        # No pack, but explicit insufficient flag and the diagnostic
        # bits all land.
        self.assertNotIn("research_pack", extra)
        self.assertEqual(extra["research_source_count"], 0)
        self.assertEqual(extra["research_status"], "insufficient")
        self.assertEqual(
            extra["research_stop_reason"], "no_initial_provider_hit"
        )
        self.assertEqual(
            extra["research_missing_roles"],
            ["ai-engineer", "qa-engineer"],
        )
        self.assertEqual(
            extra["research_active_roles"],
            ["tech-lead", "ai-engineer", "qa-engineer"],
        )

    def test_pack_with_no_sources_marks_insufficient(self) -> None:
        # pack object exists but its source list is empty — treat as
        # insufficient even though we still serialise the pack itself
        # so callers can read its title/summary later.
        session = _seed_session()
        empty_pack = _pack_with()
        outcome = _FakeOutcome(
            auto_collected_count=0,
            stop_reason="no_progress",
        )
        persist_research_artifacts(session, empty_pack, collection_outcome=outcome)
        reloaded = load_session("abc12345")
        extra = dict(reloaded.extra)
        self.assertIn("research_pack", extra)
        self.assertEqual(extra["research_source_count"], 0)
        self.assertEqual(extra["research_status"], "insufficient")

    def test_persistence_failure_stamps_research_pack_error(self) -> None:
        # Trigger pack_to_dict failure by passing a non-pack object —
        # the helper must catch + stamp an error onto the live extra
        # dict (best-effort) and return the original session.

        @dataclass
        class _LiveSession:
            session_id: str = "live-1"
            prompt: str = "x"
            task_type: str = "research"
            state: WorkflowState = WorkflowState.IN_PROGRESS
            created_at: datetime = datetime(2026, 5, 6)
            updated_at: datetime = datetime(2026, 5, 6)
            thread_id: Any = None
            summary: Any = None
            role_sequence: tuple = ()
            extra: dict = field(default_factory=dict)

        session = _LiveSession()

        class _BogusPack:
            # pack_to_dict will raise on this — no .topic / .sources
            pass

        result = persist_research_artifacts(session, _BogusPack())
        # The helper returned the original session and stamped an error.
        self.assertIs(result, session)
        self.assertIn("research_pack_error", session.extra)
        err = session.extra["research_pack_error"]
        self.assertEqual(err["step"], "persist_research_artifacts")
        self.assertTrue(err["reason"])

    def test_subsequent_success_clears_prior_error(self) -> None:
        session = _seed_session()
        # Plant a stale error first.
        from dataclasses import replace

        session = replace(
            session,
            extra={"research_pack_error": {"step": "old", "reason": "old"}},
        )
        save_session(session)

        pack = _pack_with("https://x")
        outcome = _FakeOutcome(auto_collected_count=1, stop_reason="sufficient")
        persist_research_artifacts(session, pack, collection_outcome=outcome)
        reloaded = load_session("abc12345")
        extra = dict(reloaded.extra)
        # Successful persist clears the stale error stamp.
        self.assertNotIn("research_pack_error", extra)
        self.assertEqual(extra["research_status"], "ready")


if __name__ == "__main__":
    unittest.main()
