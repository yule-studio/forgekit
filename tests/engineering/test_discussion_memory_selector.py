"""Tests for ``yule_orchestrator.agents.discussion.memory_selector``."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.discussion import (
    ObsidianNoteRef,
    RelevantMemorySelector,
    score_memory_candidate,
)


def _note(**kwargs) -> ObsidianNoteRef:
    defaults = dict(
        title="t",
        path="p",
        summary="s",
        tags=(),
        kind=None,
        project=None,
        updated_at=None,
    )
    defaults.update(kwargs)
    return ObsidianNoteRef(**defaults)


class ScoreMemoryCandidateTestCase(unittest.TestCase):
    def test_topic_overlap_increases_score(self) -> None:
        cand = score_memory_candidate(
            _note(
                title="Spring Security 인증 마이그레이션",
                summary="기존 인증 흐름을 정리하면서 발견한 케이스",
                tags=("auth", "backend-engineer"),
            ),
            query="Spring Security 인증 마이그레이션 정리",
            task_type=None,
            role=None,
        )
        self.assertGreater(cand.score, 2.5)
        self.assertIn("topic_overlap", "/".join(cand.signals))

    def test_role_match_adds_two(self) -> None:
        cand = score_memory_candidate(
            _note(
                title="bg",
                summary="없음",
                tags=("backend-engineer",),
            ),
            query="x",
            task_type=None,
            role="engineering-agent/backend-engineer",
        )
        self.assertIn("role_match", cand.signals)

    def test_kind_retrospective_adds_one(self) -> None:
        cand = score_memory_candidate(
            _note(
                title="auth post-mortem",
                summary="post-mortem details",
                kind="retrospective",
            ),
            query="auth",
            task_type=None,
            role=None,
        )
        self.assertIn("retrospective_or_decision", cand.signals)

    def test_pr_reference_adds_signal(self) -> None:
        cand = score_memory_candidate(
            _note(
                title="auth",
                summary="see PR #42 for prior fix",
            ),
            query="auth",
            task_type=None,
            role=None,
        )
        self.assertIn("references_pr_or_issue", cand.signals)

    def test_empty_body_penalised(self) -> None:
        cand = score_memory_candidate(
            _note(title="empty", summary=None, tags=()),
            query="anything",
            task_type=None,
            role=None,
        )
        self.assertLess(cand.score, 0)
        self.assertIn("body_empty", cand.signals)


class RelevantMemorySelectorTestCase(unittest.TestCase):
    def test_filters_below_min_score(self) -> None:
        selector = RelevantMemorySelector(min_score=1.0)
        notes = [
            _note(title="auth migration", summary="auth", tags=("backend-engineer",)),
            _note(title="random", summary="lunch was ok"),
        ]
        picked = selector(
            candidates=notes,
            query="auth migration",
            role="backend-engineer",
        )
        # 첫 노트는 topic + role이 모두 매치되어 통과,
        # 두 번째는 0점이라 제외.
        self.assertEqual([n.title for n in picked], ["auth migration"])

    def test_orders_by_score_desc_then_recent(self) -> None:
        selector = RelevantMemorySelector(min_score=0.0)
        notes = [
            _note(
                title="older",
                summary="auth migration old",
                tags=(),
                updated_at="2026-04-01T00:00:00",
            ),
            _note(
                title="newer",
                summary="auth migration new",
                tags=(),
                updated_at="2026-05-01T00:00:00",
            ),
        ]
        picked = selector(
            candidates=notes,
            query="auth migration",
            role=None,
            limit=2,
        )
        # 두 노트 점수가 같으면 updated_at desc → newer가 앞으로.
        self.assertEqual([n.title for n in picked], ["newer", "older"])

    def test_limit_caps_results(self) -> None:
        selector = RelevantMemorySelector(min_score=0.0)
        notes = [
            _note(title=f"n{i}", summary="auth migration", tags=())
            for i in range(8)
        ]
        picked = selector(
            candidates=notes,
            query="auth migration",
            role=None,
            limit=3,
        )
        self.assertEqual(len(picked), 3)


if __name__ == "__main__":
    unittest.main()
