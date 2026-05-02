"""Phase 3: retrieval wiring tests.

Verifies that ``fetch_role_context`` shapes role-aware queries off the
local memory index and that ``deliberation_role_turn`` populates
``DeliberationContext.memory_context`` without breaking the
deterministic fallback when retrieval is empty or fails.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.deliberation import (
    DeliberationContext,
    RetrievedMemory,
)
from yule_orchestrator.agents.research_pack import ResearchPack
from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState
from yule_orchestrator.memory import (
    MEMORY_DB_ENV,
    open_memory_index,
    reindex_paths,
)
from yule_orchestrator.memory.models import (
    SOURCE_OBSIDIAN,
    SOURCE_POLICY,
)
from yule_orchestrator.memory.retrieval import fetch_role_context


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _decision_note() -> str:
    return (
        "---\n"
        'title: "Stripe Pricing 합의"\n'
        "kind: decision\n"
        'roles: ["engineering-agent/tech-lead"]\n'
        "task_type: landing-page\n"
        "tags: [decision]\n"
        "---\n\n"
        "# Stripe Pricing 합의\n\n"
        "hero step copy를 분할한다.\n"
    )


def _reference_note() -> str:
    return (
        "---\n"
        'title: "Stripe Pricing 시각 reference"\n'
        "kind: reference\n"
        'roles: ["engineering-agent/product-designer"]\n'
        "tags: [reference]\n"
        "---\n\n"
        "# Stripe Pricing 시각 reference\n\n"
        "이미지 모음을 정리한 노트.\n"
    )


def _policy_note() -> str:
    return (
        "# Backend deployment policy\n\n"
        "Stripe pricing 변경은 backend 검토 후에 적용한다.\n"
    )


class FetchRoleContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db_path = Path(self._tmp.name) / "memory.sqlite3"
        self._prev_env = os.environ.get(MEMORY_DB_ENV)
        os.environ[MEMORY_DB_ENV] = str(self._db_path)

        vault = Path(self._tmp.name) / "vault"
        _write(vault / "Decisions/decision.md", _decision_note())
        _write(vault / "References/reference.md", _reference_note())
        repo = Path(self._tmp.name) / "repo"
        _write(repo / "policies/runtime/agents/backend.md", _policy_note())

        with open_memory_index() as index:
            reindex_paths(
                paths=[vault],
                source_kind=SOURCE_OBSIDIAN,
                index=index,
                base_dir=vault,
            )
            reindex_paths(
                paths=[repo / "policies"],
                source_kind=SOURCE_POLICY,
                index=index,
                base_dir=repo,
            )

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop(MEMORY_DB_ENV, None)
        else:
            os.environ[MEMORY_DB_ENV] = self._prev_env

    def test_tech_lead_prefers_decision_then_policy(self) -> None:
        hits = fetch_role_context(
            role="engineering-agent/tech-lead",
            query="Stripe Pricing",
            task_type="landing-page",
            limit=5,
        )
        self.assertTrue(hits)
        sources_in_order = [h.source_kind for h in hits]
        # Decisions must show up before policies.
        decision_idx = next(
            (i for i, s in enumerate(sources_in_order) if s == "obsidian"), None
        )
        policy_idx = next(
            (i for i, s in enumerate(sources_in_order) if s == "policy"), None
        )
        self.assertIsNotNone(decision_idx)
        if policy_idx is not None:
            self.assertLess(decision_idx, policy_idx)

    def test_product_designer_prefers_reference(self) -> None:
        hits = fetch_role_context(
            role="engineering-agent/product-designer",
            query="Stripe Pricing",
            limit=5,
        )
        self.assertTrue(hits)
        first = hits[0]
        self.assertEqual(first.note_kind, "reference")

    def test_empty_query_returns_empty(self) -> None:
        self.assertEqual(
            fetch_role_context(role="tech-lead", query="   ", limit=3), []
        )

    def test_search_failure_is_swallowed(self) -> None:
        with patch(
            "yule_orchestrator.memory.retrieval.search",
            side_effect=RuntimeError("boom"),
        ):
            result = fetch_role_context(
                role="tech-lead", query="anything", limit=3
            )
        self.assertEqual(result, [])

    def test_unknown_role_falls_back_to_default_priority(self) -> None:
        hits = fetch_role_context(
            role="engineering-agent/security-reviewer",  # not in priority table
            query="Stripe Pricing",
            limit=5,
        )
        # Should still produce hits via the default priority chain.
        self.assertTrue(hits)

    def test_citation_ids_stamped_on_results(self) -> None:
        hits = fetch_role_context(
            role="engineering-agent/tech-lead",
            query="Stripe Pricing",
            limit=3,
        )
        self.assertTrue(hits)
        ids = [hit.citation_id for hit in hits]
        # Every hit gets a non-empty id and they are unique.
        self.assertTrue(all(cid for cid in ids))
        self.assertEqual(len(ids), len(set(ids)))


class DeliberationRetrievalIntegrationTests(unittest.TestCase):
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

    def _session(self) -> WorkflowSession:
        return WorkflowSession(
            session_id="sess-retrieval",
            prompt="Stripe pricing hero copy 정리",
            task_type="landing-page",
            state=WorkflowState.APPROVED,
            created_at=datetime(2026, 5, 1),
            updated_at=datetime(2026, 5, 1),
            executor_role="engineering-agent/tech-lead",
        )

    def test_deliberation_role_turn_attaches_memory_context_when_index_has_hits(
        self,
    ) -> None:
        # Seed the memory index so retrieval has something to find.
        vault = Path(self._tmp.name) / "vault"
        _write(vault / "Decisions/decision.md", _decision_note())
        with open_memory_index() as index:
            reindex_paths(
                paths=[vault],
                source_kind=SOURCE_OBSIDIAN,
                index=index,
                base_dir=vault,
            )

        captured: dict[str, DeliberationContext] = {}

        def runner_fn(ctx: DeliberationContext):
            captured["ctx"] = ctx
            return None  # let deterministic fallback run

        from yule_orchestrator.discord.engineering_team_runtime import (
            deliberation_role_turn,
        )

        take, _text = deliberation_role_turn(
            self._session(),
            "engineering-agent/tech-lead",
            research_pack=ResearchPack(title="Stripe Pricing"),
            runner_fn=runner_fn,
        )

        self.assertIsNotNone(take)
        ctx = captured.get("ctx")
        self.assertIsNotNone(ctx)
        self.assertTrue(ctx.memory_context)
        self.assertIsInstance(ctx.memory_context[0], RetrievedMemory)

    def test_deliberation_role_turn_works_without_index(self) -> None:
        captured: dict[str, DeliberationContext] = {}

        def runner_fn(ctx: DeliberationContext):
            captured["ctx"] = ctx
            return None

        from yule_orchestrator.discord.engineering_team_runtime import (
            deliberation_role_turn,
        )

        take, _text = deliberation_role_turn(
            self._session(),
            "engineering-agent/tech-lead",
            research_pack=ResearchPack(title="Stripe Pricing"),
            runner_fn=runner_fn,
        )
        self.assertIsNotNone(take)
        ctx = captured.get("ctx")
        self.assertIsNotNone(ctx)
        # Empty index → memory_context must be empty tuple, not crash.
        self.assertEqual(ctx.memory_context, ())


if __name__ == "__main__":
    unittest.main()
