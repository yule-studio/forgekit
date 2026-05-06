"""End-to-end regression for research_pack persistence (Phase 1 closure).

Drives ``persist_research_artifacts`` against a real SQLite-backed
workflow cache and verifies that the resulting payload contains the
``research_pack`` key — the exact query operators use to spot-check
("payload_json LIKE '%research_pack%'") that the engineering flow is
populating session.extra correctly.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.research_pack import ResearchPack, ResearchSource
from yule_orchestrator.agents.research_persistence import persist_research_artifacts
from yule_orchestrator.agents.workflow_state import (
    WORKFLOW_NAMESPACE,
    WorkflowSession,
    WorkflowState,
    save_session,
)


def _session() -> WorkflowSession:
    now = datetime(2026, 5, 1, 12, 0)
    return WorkflowSession(
        session_id="e2e-pack-1",
        prompt="Obsidian 지식 저장 구조 리서치",
        task_type="landing-page",
        state=WorkflowState.APPROVED,
        created_at=now,
        updated_at=now,
        executor_role="frontend-engineer",
    )


def _pack() -> ResearchPack:
    return ResearchPack(
        title="Obsidian 지식 저장 구조",
        summary="역할별 노트 저장 구조 조사",
        primary_url="https://help.obsidian.md/",
        sources=(
            ResearchSource(
                source_url="https://help.obsidian.md/",
                title="Obsidian Help",
                author_role="engineering-agent/tech-lead",
            ),
        ),
        tags=("research",),
        created_at=datetime(2026, 5, 1, 12, 0),
    )


class ResearchPackEndToEndPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._cache_path = Path(self._tmp.name) / "cache.sqlite3"
        self._prev_db_env = os.environ.get("YULE_CACHE_DB_PATH")
        os.environ["YULE_CACHE_DB_PATH"] = str(self._cache_path)

    def tearDown(self) -> None:
        if self._prev_db_env is None:
            os.environ.pop("YULE_CACHE_DB_PATH", None)
        else:
            os.environ["YULE_CACHE_DB_PATH"] = self._prev_db_env

    def test_persist_writes_research_pack_key_into_sqlite_payload(self) -> None:
        session = _session()
        save_session(session)

        updated = persist_research_artifacts(
            session,
            _pack(),
            collection_outcome=None,
        )

        self.assertIsNotNone(updated)
        self.assertIn("research_pack", dict(updated.extra))

        with sqlite3.connect(self._cache_path) as conn:
            row = conn.execute(
                "SELECT payload_json FROM local_cache_entries "
                "WHERE namespace = ? AND cache_key = ?",
                (WORKFLOW_NAMESPACE, session.session_id),
            ).fetchone()
        self.assertIsNotNone(row)
        payload_json = row[0] or ""
        self.assertIn("research_pack", payload_json)
        self.assertIn("Obsidian", payload_json)

        with sqlite3.connect(self._cache_path) as conn:
            hits = conn.execute(
                "SELECT cache_key FROM local_cache_entries "
                "WHERE namespace = ? AND payload_json LIKE ?",
                (WORKFLOW_NAMESPACE, "%research_pack%"),
            ).fetchall()
        self.assertEqual({h[0] for h in hits}, {session.session_id})

    def test_persist_is_idempotent(self) -> None:
        session = _session()
        save_session(session)
        first = persist_research_artifacts(session, _pack())
        second = persist_research_artifacts(first, _pack())
        self.assertEqual(
            dict(first.extra)["research_pack"],
            dict(second.extra)["research_pack"],
        )


if __name__ == "__main__":
    unittest.main()
