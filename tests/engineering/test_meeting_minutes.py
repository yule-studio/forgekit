"""Phase 3 — meeting_minutes deterministic builder."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.reports.meeting_minutes import (
    MeetingMinutes,
    build_meeting_minutes,
    format_meeting_minutes_markdown,
)


class BuildMeetingMinutesTests(unittest.TestCase):
    def _full_extra(self) -> dict:
        return {
            "active_research_roles": ["tech-lead", "ai-engineer", "qa-engineer"],
            "role_selection_source": "user_explicit",
            "role_selection_reasons": {
                "tech-lead": "always included",
                "ai-engineer": "user explicit mention",
                "qa-engineer": "user explicit mention",
            },
            "research_pack": {
                "role_summaries": {
                    "ai-engineer": "RAG vs CAG 비교 — RAG 우세",
                    "qa-engineer": "회귀 시나리오 4건 후보",
                },
            },
            "research_synthesis": {
                "v": 1,
                "consensus": "RAG 메모리로 진행. QA 회귀는 4건 모두 자동화.",
                "open_research": ["latency 측정 미실시"],
                "user_decisions_needed": ["embedding model 확정"],
                "todos": ["[ai] embedding 후보 정리"],
            },
            "role_takes": [
                {"role": "ai-engineer", "risks": ["embedding cost overshoot"]},
                {"role": "qa-engineer", "risks": ["test data not available"]},
            ],
        }

    def test_minutes_carry_participants_and_summaries(self) -> None:
        minutes = build_meeting_minutes(
            session_id="abc123",
            topic="[Research] 하네스 RAG 학습 루프 도입",
            extra=self._full_extra(),
        )
        self.assertEqual(minutes.session_id, "abc123")
        self.assertIn("RAG", minutes.topic)
        self.assertEqual(
            minutes.participants,
            ("tech-lead", "ai-engineer", "qa-engineer"),
        )
        self.assertEqual(minutes.selection_source, "user_explicit")
        self.assertEqual(
            minutes.role_summaries["ai-engineer"],
            "RAG vs CAG 비교 — RAG 우세",
        )

    def test_minutes_pull_agreements_and_open_questions(self) -> None:
        minutes = build_meeting_minutes(
            session_id="abc123",
            topic="harness",
            extra=self._full_extra(),
        )
        self.assertEqual(len(minutes.agreements), 1)
        self.assertIn("RAG", minutes.agreements[0])
        self.assertEqual(minutes.open_questions, ("latency 측정 미실시",))
        self.assertEqual(minutes.next_actions, ("embedding model 확정",))

    def test_minutes_collect_risks_from_role_takes(self) -> None:
        minutes = build_meeting_minutes(
            session_id="abc123",
            topic="harness",
            extra=self._full_extra(),
        )
        self.assertIn("embedding cost overshoot", minutes.risks)
        self.assertIn("test data not available", minutes.risks)

    def test_falls_back_to_played_roles_when_active_missing(self) -> None:
        extra = {
            "played_roles": ["tech-lead", "backend-engineer"],
            "research_synthesis": {"consensus": "ok"},
        }
        minutes = build_meeting_minutes(
            session_id="x",
            topic="결제 멱등성",
            extra=extra,
        )
        self.assertEqual(
            minutes.participants,
            ("tech-lead", "backend-engineer"),
        )

    def test_falls_back_to_role_sequence_when_extra_empty(self) -> None:
        minutes = build_meeting_minutes(
            session_id="x",
            topic="결제 멱등성",
            extra={},
            fallback_participants=("tech-lead", "qa-engineer"),
        )
        self.assertEqual(
            minutes.participants,
            ("tech-lead", "qa-engineer"),
        )

    def test_empty_extra_does_not_crash(self) -> None:
        minutes = build_meeting_minutes(
            session_id=None,
            topic="",
            extra={},
        )
        self.assertEqual(minutes.participants, ())
        self.assertEqual(minutes.agreements, ())
        self.assertEqual(minutes.risks, ())


class FormatMeetingMinutesMarkdownTests(unittest.TestCase):
    def test_renders_compact_korean_meeting_note(self) -> None:
        minutes = MeetingMinutes(
            session_id="abc123",
            topic="harness 도입",
            participants=("tech-lead", "ai-engineer"),
            role_summaries={"ai-engineer": "RAG 우세"},
            agreements=("RAG로 진행",),
            risks=("latency 측정 미실시",),
            open_questions=("embedding 비용",),
            next_actions=("embedding model 확정",),
            selection_source="user_explicit",
        )
        body = format_meeting_minutes_markdown(minutes)
        self.assertIn("**Session**: `abc123`", body)
        self.assertIn("**안건**: harness 도입", body)
        self.assertIn("tech-lead, ai-engineer", body)
        self.assertIn("user_explicit", body)
        self.assertIn("**역할별 요약**", body)
        self.assertIn("RAG 우세", body)
        self.assertIn("**합의 / consensus**", body)
        self.assertIn("**위험**", body)
        self.assertIn("**다음 액션**", body)

    def test_empty_sections_are_dropped(self) -> None:
        minutes = MeetingMinutes(
            session_id="x",
            topic="t",
            participants=(),
        )
        body = format_meeting_minutes_markdown(minutes)
        self.assertNotIn("**참가자**", body)
        self.assertNotIn("**합의", body)
        self.assertNotIn("**위험**", body)


if __name__ == "__main__":
    unittest.main()
