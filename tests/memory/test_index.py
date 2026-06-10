"""Memory indexer + search tests (Phase 2).

Drives the FTS5-backed local memory layer end-to-end against temp
files: a tiny vault, a tiny policies tree, and a synthetic workflow
session with a research_pack. No network, no real Obsidian.
"""

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

from yule_engineering.agents.research.pack import (
    ResearchPack,
    ResearchSource,
    pack_to_dict,
)
from yule_engineering.agents.workflow_state import WorkflowSession, WorkflowState
from yule_memory import (
    MEMORY_DB_ENV,
    MemoryDocument,
    open_memory_index,
    reindex_paths,
    reindex_workflow_sessions,
    search,
)
from yule_memory.indexer import _document_from_workflow_session
from yule_memory.models import (
    SOURCE_OBSIDIAN,
    SOURCE_POLICY,
    SOURCE_WORKFLOW,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _decision_note() -> str:
    return (
        "---\n"
        'title: "Stripe Pricing 합의"\n'
        "kind: decision\n"
        'roles: ["engineering-agent/tech-lead", "engineering-agent/product-designer"]\n'
        "task_type: landing-page\n"
        "tags: [decision, ux]\n"
        "created_at: 2026-04-30T09:00:00\n"
        "---\n\n"
        "# Stripe Pricing 합의\n\n"
        "## 합의안\n"
        "hero step copy를 분할한다.\n"
        "프론트엔드가 즉시 반영한다.\n"
    )


def _research_note() -> str:
    return (
        "---\n"
        'title: "Stripe Pricing 자료 모음"\n'
        "kind: research\n"
        'roles: ["engineering-agent/product-designer"]\n'
        "task_type: landing-page\n"
        "tags: [research, ux]\n"
        "---\n\n"
        "# Stripe Pricing 자료 모음\n\n"
        "Stripe pricing 페이지의 hero step 패턴 자료.\n"
    )


def _policy_note() -> str:
    return (
        "# Engineering Conversation Policy\n\n"
        "사용자 요청이 들어오면 먼저 자료를 수집한다.\n"
        "그 다음 역할별 검토를 거치고 합의안을 만든다.\n"
    )


class MemoryIndexerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db_path = Path(self._tmp.name) / "memory.sqlite3"
        self._prev_env = os.environ.get(MEMORY_DB_ENV)
        os.environ[MEMORY_DB_ENV] = str(self._db_path)

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop(MEMORY_DB_ENV, None)
        else:
            os.environ[MEMORY_DB_ENV] = self._prev_env

    def test_indexes_and_searches_obsidian_note(self) -> None:
        vault = Path(self._tmp.name) / "vault"
        _write(
            vault / "Agents/Engineering/Decisions/2026-04-30_stripe.md",
            _decision_note(),
        )
        _write(
            vault / "Agents/Engineering/Research/2026-04-30_stripe.md",
            _research_note(),
        )

        with open_memory_index() as index:
            count = reindex_paths(
                paths=[vault],
                source_kind=SOURCE_OBSIDIAN,
                index=index,
                base_dir=vault,
            )
        self.assertEqual(count, 2)

        hits = search("Stripe Pricing")
        self.assertGreaterEqual(len(hits), 1)
        titles = {hit.document.title for hit in hits}
        self.assertIn("Stripe Pricing 합의", titles)

    def test_search_filters_by_role_and_note_kind(self) -> None:
        vault = Path(self._tmp.name) / "vault"
        _write(
            vault / "Decisions/2026-04-30_stripe.md", _decision_note()
        )
        _write(
            vault / "Research/2026-04-30_stripe.md", _research_note()
        )
        with open_memory_index() as index:
            reindex_paths(
                paths=[vault],
                source_kind=SOURCE_OBSIDIAN,
                index=index,
                base_dir=vault,
            )

        decisions = search("Stripe", note_kind="decision")
        self.assertTrue(decisions)
        for hit in decisions:
            self.assertEqual(hit.document.note_kind, "decision")

        designer_hits = search(
            "Stripe", role="engineering-agent/product-designer"
        )
        self.assertTrue(designer_hits)
        for hit in designer_hits:
            self.assertEqual(hit.document.role, "engineering-agent/product-designer")

    def test_reindex_is_idempotent(self) -> None:
        vault = Path(self._tmp.name) / "vault"
        _write(vault / "note.md", _research_note())

        with open_memory_index() as index:
            first = reindex_paths(
                paths=[vault],
                source_kind=SOURCE_OBSIDIAN,
                index=index,
                base_dir=vault,
            )
        with open_memory_index() as index:
            total_after_first = index.count_documents(SOURCE_OBSIDIAN)
            second = reindex_paths(
                paths=[vault],
                source_kind=SOURCE_OBSIDIAN,
                index=index,
                base_dir=vault,
            )
            total_after_second = index.count_documents(SOURCE_OBSIDIAN)
        self.assertEqual(first, 1)
        self.assertEqual(second, 1)
        self.assertEqual(total_after_first, total_after_second)

    def test_reindex_picks_up_deletions(self) -> None:
        vault = Path(self._tmp.name) / "vault"
        kept = vault / "kept.md"
        gone = vault / "gone.md"
        _write(kept, _research_note())
        _write(gone, _decision_note())

        with open_memory_index() as index:
            reindex_paths(
                paths=[vault],
                source_kind=SOURCE_OBSIDIAN,
                index=index,
                base_dir=vault,
            )
        self.assertTrue(search("hero step"))

        gone.unlink()
        with open_memory_index() as index:
            reindex_paths(
                paths=[vault],
                source_kind=SOURCE_OBSIDIAN,
                index=index,
                base_dir=vault,
            )
            count = index.count_documents(SOURCE_OBSIDIAN)
        self.assertEqual(count, 1)
        # The decision body's unique sentence is gone now.
        self.assertEqual(search("프론트엔드가 즉시 반영"), [])

    def test_indexes_policy_docs(self) -> None:
        repo = Path(self._tmp.name) / "repo"
        _write(repo / "policies/runtime/agents/sample.md", _policy_note())
        with open_memory_index() as index:
            reindex_paths(
                paths=[repo / "policies"],
                source_kind=SOURCE_POLICY,
                index=index,
                base_dir=repo,
            )
        hits = search("자료를 수집한다", source_kind=SOURCE_POLICY)
        self.assertTrue(hits)
        self.assertEqual(hits[0].document.source_kind, "policy")

    def test_indexes_workflow_session_research_pack(self) -> None:
        pack = ResearchPack(
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
        session = WorkflowSession(
            session_id="sess-mem",
            prompt="Obsidian 지식 저장 구조 리서치",
            task_type="landing-page",
            state=WorkflowState.APPROVED,
            created_at=datetime(2026, 5, 1, 12, 0),
            updated_at=datetime(2026, 5, 1, 12, 0),
            executor_role="engineering-agent/tech-lead",
            extra={"research_pack": pack_to_dict(pack)},
        )

        with open_memory_index() as index:
            count = reindex_workflow_sessions(sessions=[session], index=index)
        self.assertEqual(count, 1)

        hits = search("Obsidian 지식 저장", source_kind=SOURCE_WORKFLOW)
        self.assertTrue(hits)
        self.assertEqual(hits[0].document.source_kind, "workflow")
        self.assertEqual(hits[0].document.role, "engineering-agent/tech-lead")
        self.assertIn("session_id", hits[0].document.extra)

    def test_workflow_session_without_research_artifacts_skipped(self) -> None:
        session = WorkflowSession(
            session_id="empty-sess",
            prompt="아무것도 없는 세션",
            task_type="landing-page",
            state=WorkflowState.INTAKE,
            created_at=datetime(2026, 5, 1),
            updated_at=datetime(2026, 5, 1),
        )
        self.assertIsNone(_document_from_workflow_session(session))

    def test_empty_query_returns_no_results(self) -> None:
        # Even with content present, search with empty/whitespace query
        # must not blow up — return [].
        vault = Path(self._tmp.name) / "vault"
        _write(vault / "x.md", _research_note())
        with open_memory_index() as index:
            reindex_paths(
                paths=[vault],
                source_kind=SOURCE_OBSIDIAN,
                index=index,
                base_dir=vault,
            )
        self.assertEqual(search(""), [])
        self.assertEqual(search("   "), [])

    def test_search_returns_path_and_score(self) -> None:
        vault = Path(self._tmp.name) / "vault"
        _write(vault / "deep/nested/decision.md", _decision_note())
        with open_memory_index() as index:
            reindex_paths(
                paths=[vault],
                source_kind=SOURCE_OBSIDIAN,
                index=index,
                base_dir=vault,
            )
        hits = search("hero step")
        self.assertTrue(hits)
        first = hits[0]
        self.assertEqual(first.document.path, "deep/nested/decision.md")
        self.assertIsInstance(first.score, float)
        self.assertTrue(first.snippet)


class MemoryIndexUnicodeTokenizerTests(unittest.TestCase):
    """Korean searches must work because we use unicode61 tokenizer."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db_path = Path(self._tmp.name) / "memory.sqlite3"
        self._prev_env = os.environ.get(MEMORY_DB_ENV)
        os.environ[MEMORY_DB_ENV] = str(self._db_path)

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop(MEMORY_DB_ENV, None)
        else:
            os.environ[MEMORY_DB_ENV] = self._prev_env

    def test_korean_query_finds_korean_body(self) -> None:
        vault = Path(self._tmp.name) / "vault"
        _write(vault / "kr.md", _decision_note())
        with open_memory_index() as index:
            reindex_paths(
                paths=[vault],
                source_kind=SOURCE_OBSIDIAN,
                index=index,
                base_dir=vault,
            )
        hits = search("합의안")
        self.assertTrue(hits, "Korean keyword should hit the FTS5 index")


if __name__ == "__main__":
    unittest.main()
