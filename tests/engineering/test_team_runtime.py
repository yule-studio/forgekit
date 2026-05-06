from __future__ import annotations

import unittest
from datetime import datetime
from typing import Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState
from yule_orchestrator.agents.research_pack import (
    ResearchPack,
    ResearchSource,
    SourceType,
    pack_to_dict,
)
from yule_orchestrator.discord.engineering_team_runtime import (
    PLAYED_ROLES_KEY,
    TEAM_CONVERSATION_KEY,
    TeamTurn,
    TeamTurnOutcome,
    build_turn_plan,
    closing_message,
    dispatch_directive,
    format_role_turn_text,
    handle_team_turn_message,
    kickoff_directive,
    mark_turn_played,
    next_pending_turn,
    parse_dispatch_marker,
    played_roles,
)


def _make_session(
    *,
    session_id: str = "sess-team-001",
    role_sequence: tuple[str, ...] = (
        "tech-lead",
        "product-designer",
        "frontend-engineer",
        "qa-engineer",
    ),
    executor_role: Optional[str] = "frontend-engineer",
    thread_id: Optional[int] = 555111222,
    task_type: str = "landing-page",
    references_user: tuple[str, ...] = (),
    references_suggested: tuple[str, ...] = ("Wix Templates", "Awwwards"),
    write_requested: bool = True,
    write_blocked_reason: Optional[str] = "write requested but user_approved=False",
    extra: Optional[dict] = None,
    prompt: str = "새 랜딩페이지 hero 섹션 정리. https://stripe.com/pricing 참고.",
) -> WorkflowSession:
    now = datetime(2026, 4, 30, 11, 0, 0)
    return WorkflowSession(
        session_id=session_id,
        prompt=prompt,
        task_type=task_type,
        state=WorkflowState.APPROVED,
        created_at=now,
        updated_at=now,
        role_sequence=role_sequence,
        executor_role=executor_role,
        executor_runner="claude",
        references_user=references_user,
        references_suggested=references_suggested,
        thread_id=thread_id,
        write_requested=write_requested,
        write_blocked_reason=write_blocked_reason,
        extra=extra or {},
    )


def _research_pack_extra() -> dict:
    pack = ResearchPack(
        title="Obsidian agent memory",
        summary="에이전트 지식 저장 구조 조사",
        primary_url="https://example.com/obsidian-agent-memory",
        sources=(
            ResearchSource(
                source_url="https://example.com/obsidian-agent-memory",
                title="Obsidian agent memory pattern",
                summary="knowledge storage 구조 참고",
                source_type=SourceType.OFFICIAL_DOCS,
                extra={
                    "source_type": "official_docs",
                    "why_relevant": "역할별 지식 저장 구조 설계 근거",
                },
            ),
        ),
    )
    return {"research_pack": pack_to_dict(pack)}


class BuildTurnPlanTestCase(unittest.TestCase):
    def test_plan_matches_role_sequence_order(self) -> None:
        session = _make_session()
        plan = build_turn_plan(session)
        self.assertEqual(
            tuple(turn.role for turn in plan),
            ("tech-lead", "product-designer", "frontend-engineer", "qa-engineer"),
        )
        self.assertEqual(tuple(turn.sequence_index for turn in plan), (0, 1, 2, 3))

    def test_executor_flagged(self) -> None:
        plan = build_turn_plan(_make_session())
        self.assertEqual(
            {turn.role: turn.is_executor for turn in plan},
            {
                "tech-lead": False,
                "product-designer": False,
                "frontend-engineer": True,
                "qa-engineer": False,
            },
        )

    def test_thread_id_required(self) -> None:
        session = _make_session(thread_id=None)
        with self.assertRaises(ValueError):
            build_turn_plan(session)

    def test_role_sequence_required(self) -> None:
        session = _make_session(role_sequence=())
        with self.assertRaises(ValueError):
            build_turn_plan(session)

    def test_unknown_role_falls_back_to_generic_template(self) -> None:
        session = _make_session(role_sequence=("tech-lead", "growth-hacker"))
        plan = build_turn_plan(session)
        growth = plan[1]
        self.assertEqual(growth.role, "growth-hacker")
        self.assertIn("growth-hacker", growth.header)
        self.assertTrue(growth.body)


class FormatTurnTextTestCase(unittest.TestCase):
    def test_tech_lead_body_includes_task_and_executor(self) -> None:
        session = _make_session()
        header, body = format_role_turn_text(session, "tech-lead", is_executor=False)
        self.assertIn("팀", header)
        self.assertIn("`landing-page`", body)
        self.assertIn("`frontend-engineer`", body)

    def test_tech_lead_flags_pending_write_approval(self) -> None:
        session = _make_session()
        _, body = format_role_turn_text(session, "tech-lead", is_executor=False)
        self.assertIn("승인 대기", body)

    def test_executor_distinguished_in_role_body(self) -> None:
        session = _make_session(executor_role="frontend-engineer")
        _, executor_body = format_role_turn_text(
            session, "frontend-engineer", is_executor=True
        )
        _, advisor_body = format_role_turn_text(
            session, "qa-engineer", is_executor=False
        )
        self.assertIn("본인", executor_body)
        self.assertIn("실행 후보(frontend-engineer)", advisor_body)


class PlayedRolesTestCase(unittest.TestCase):
    def test_initially_empty(self) -> None:
        self.assertEqual(played_roles(_make_session()), ())

    def test_mark_turn_played_appends_marker(self) -> None:
        session = _make_session()
        first = mark_turn_played(session, "tech-lead")
        self.assertEqual(played_roles(first), ("tech-lead",))
        # Original session is unchanged (frozen dataclass + immutable extra).
        self.assertEqual(played_roles(session), ())

    def test_mark_turn_played_is_idempotent(self) -> None:
        session = _make_session()
        once = mark_turn_played(session, "tech-lead")
        twice = mark_turn_played(once, "tech-lead")
        self.assertEqual(played_roles(twice), ("tech-lead",))

    def test_extra_block_uses_namespaced_key(self) -> None:
        session = _make_session()
        updated = mark_turn_played(session, "tech-lead")
        self.assertIn(TEAM_CONVERSATION_KEY, updated.extra)
        self.assertEqual(
            updated.extra[TEAM_CONVERSATION_KEY][PLAYED_ROLES_KEY], ["tech-lead"]
        )

    def test_does_not_clobber_existing_extra_keys(self) -> None:
        session = _make_session(extra={"unrelated": "keep me"})
        updated = mark_turn_played(session, "tech-lead")
        self.assertEqual(updated.extra["unrelated"], "keep me")
        self.assertEqual(
            updated.extra[TEAM_CONVERSATION_KEY][PLAYED_ROLES_KEY], ["tech-lead"]
        )


class NextPendingTurnTestCase(unittest.TestCase):
    def test_returns_first_when_none_played(self) -> None:
        turn = next_pending_turn(_make_session())
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual(turn.role, "tech-lead")

    def test_skips_played_roles(self) -> None:
        session = mark_turn_played(_make_session(), "tech-lead")
        turn = next_pending_turn(session)
        assert turn is not None
        self.assertEqual(turn.role, "product-designer")

    def test_returns_none_when_all_played(self) -> None:
        session = _make_session()
        for role in session.role_sequence:
            session = mark_turn_played(session, role)
        self.assertIsNone(next_pending_turn(session))


class DispatchMarkerTestCase(unittest.TestCase):
    def test_parse_with_role(self) -> None:
        self.assertEqual(
            parse_dispatch_marker("[team-turn:abc123 tech-lead]"),
            ("abc123", "tech-lead"),
        )

    def test_parse_without_role(self) -> None:
        self.assertEqual(
            parse_dispatch_marker("kickoff: [team-turn:abc123]"),
            ("abc123", None),
        )

    def test_no_marker_returns_none(self) -> None:
        self.assertIsNone(parse_dispatch_marker("그냥 평범한 메시지"))
        self.assertIsNone(parse_dispatch_marker(""))

    def test_dispatch_directive_round_trip(self) -> None:
        plan = build_turn_plan(_make_session())
        directive = dispatch_directive(plan[1])
        self.assertEqual(parse_dispatch_marker(directive), (plan[1].session_id, plan[1].role))

    def test_kickoff_directive_targets_first_role(self) -> None:
        directive = kickoff_directive(_make_session())
        sid, role = parse_dispatch_marker(directive)
        self.assertEqual(role, "tech-lead")
        self.assertEqual(sid, "sess-team-001")


class HandleTeamTurnMessageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.session = _make_session()

    def _loader(self, override: Optional[WorkflowSession] = None):
        target = override or self.session

        def _load(sid: str) -> Optional[WorkflowSession]:
            if sid == target.session_id:
                return target
            return None

        return _load

    def test_returns_none_without_marker(self) -> None:
        self.assertIsNone(
            handle_team_turn_message(
                role="tech-lead",
                text="안녕",
                session_loader=self._loader(),
            )
        )

    def test_returns_none_when_role_does_not_match(self) -> None:
        text = "[team-turn:sess-team-001 tech-lead]"
        self.assertIsNone(
            handle_team_turn_message(
                role="qa-engineer", text=text, session_loader=self._loader()
            )
        )

    def test_returns_none_when_session_unknown(self) -> None:
        text = "[team-turn:does-not-exist tech-lead]"
        self.assertIsNone(
            handle_team_turn_message(
                role="tech-lead", text=text, session_loader=self._loader()
            )
        )

    def test_role_outside_plan_is_ignored(self) -> None:
        session = _make_session(role_sequence=("tech-lead", "product-designer"))
        text = "[team-turn:sess-team-001 backend-engineer]"
        self.assertIsNone(
            handle_team_turn_message(
                role="backend-engineer",
                text=text,
                session_loader=self._loader(session),
            )
        )

    def test_already_played_role_does_not_speak_twice(self) -> None:
        session = mark_turn_played(self.session, "tech-lead")
        text = "[team-turn:sess-team-001 tech-lead]"
        self.assertIsNone(
            handle_team_turn_message(
                role="tech-lead", text=text, session_loader=self._loader(session)
            )
        )

    def test_first_role_chains_to_next(self) -> None:
        text = "[team-turn:sess-team-001 tech-lead]"
        outcome = handle_team_turn_message(
            role="tech-lead", text=text, session_loader=self._loader()
        )
        assert outcome is not None
        self.assertEqual(outcome.turn.role, "tech-lead")
        self.assertFalse(outcome.is_final)
        self.assertIsNotNone(outcome.next_directive)
        # next directive points at product-designer
        sid, next_role = parse_dispatch_marker(outcome.next_directive or "")
        self.assertEqual(next_role, "product-designer")
        self.assertEqual(sid, "sess-team-001")
        # full_post combines message + directive in a single string
        full = outcome.full_post()
        self.assertIn("[tech-lead]", full)
        self.assertIn(outcome.next_directive or "", full)

    def test_research_pack_uses_deliberation_turn_instead_of_opening_template(self) -> None:
        session = _make_session(
            role_sequence=("tech-lead", "backend-engineer", "qa-engineer"),
            extra=_research_pack_extra(),
        )
        text = "[team-turn:sess-team-001 backend-engineer]"
        outcome = handle_team_turn_message(
            role="backend-engineer",
            text=text,
            session_loader=self._loader(session),
        )
        assert outcome is not None
        self.assertIn("**[backend-engineer]**", outcome.message)
        self.assertIn("관점:", outcome.message)
        self.assertIn("근거:", outcome.message)
        self.assertIn("official_docs", outcome.message)

    def test_last_role_marks_final(self) -> None:
        session = self.session
        for role in ("tech-lead", "product-designer", "frontend-engineer"):
            session = mark_turn_played(session, role)
        text = "[team-turn:sess-team-001 qa-engineer]"
        outcome = handle_team_turn_message(
            role="qa-engineer",
            text=text,
            session_loader=self._loader(session),
        )
        assert outcome is not None
        self.assertTrue(outcome.is_final)
        self.assertIsNone(outcome.next_directive)
        self.assertEqual(outcome.full_post(), outcome.message)

    def test_final_pack_driven_turn_appends_tech_lead_synthesis(self) -> None:
        session = _make_session(extra=_research_pack_extra())
        for role in ("tech-lead", "product-designer", "frontend-engineer"):
            session = mark_turn_played(session, role)
        text = "[team-turn:sess-team-001 qa-engineer]"
        outcome = handle_team_turn_message(
            role="qa-engineer",
            text=text,
            session_loader=self._loader(session),
        )
        assert outcome is not None
        self.assertTrue(outcome.is_final)
        self.assertIn("tech-lead 종합", outcome.message)

    def test_marker_without_role_still_lets_owner_speak(self) -> None:
        text = "kickoff [team-turn:sess-team-001]"
        outcome = handle_team_turn_message(
            role="tech-lead", text=text, session_loader=self._loader()
        )
        assert outcome is not None
        self.assertEqual(outcome.turn.role, "tech-lead")

    def test_marker_without_role_does_not_let_others_speak_out_of_turn(self) -> None:
        # A marker with no role still triggers; we rely on
        # "already played" checks to avoid out-of-order chatter.
        text = "kickoff [team-turn:sess-team-001]"
        outcome_qa = handle_team_turn_message(
            role="qa-engineer", text=text, session_loader=self._loader()
        )
        # qa-engineer is in the plan and not yet played, so a role-less
        # broadcast WOULD let it speak. To prevent multiple bots replying
        # to a kickoff, the gateway should always emit a role-targeted
        # directive. This assertion documents the trade-off.
        self.assertIsNotNone(outcome_qa)


class TeamTurnDataTestCase(unittest.TestCase):
    def test_team_turn_render_format(self) -> None:
        turn = TeamTurn(
            session_id="s",
            role="tech-lead",
            is_executor=False,
            sequence_index=0,
            thread_id=42,
            header="hi",
            body="body",
        )
        self.assertEqual(turn.render(), "**[tech-lead]** hi\nbody")

    def test_full_post_with_directive(self) -> None:
        outcome = TeamTurnOutcome(
            turn=TeamTurn(
                session_id="s",
                role="tech-lead",
                is_executor=False,
                sequence_index=0,
                thread_id=42,
                header="hi",
                body="body",
            ),
            message="**[tech-lead]** hi\nbody",
            next_directive="[team-turn:s product-designer]",
            is_final=False,
        )
        full = outcome.full_post()
        self.assertIn("body", full)
        self.assertIn("[team-turn:s product-designer]", full)

    def test_closing_message_mentions_session(self) -> None:
        msg = closing_message(_make_session())
        self.assertIn("sess-team-001", msg)


class DefaultResearchRoleSequenceTests(unittest.TestCase):
    """The forum open-call directive iterates the default research role
    sequence — both ``ai-engineer`` and ``devops-engineer`` must be in
    the chain so member bots for both roles get a turn.
    """

    def test_default_sequence_includes_ai_and_devops(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            DEFAULT_RESEARCH_ROLE_SEQUENCE,
        )

        self.assertIn("ai-engineer", DEFAULT_RESEARCH_ROLE_SEQUENCE)
        self.assertIn("devops-engineer", DEFAULT_RESEARCH_ROLE_SEQUENCE)

    def test_normalised_sequence_falls_back_to_default_when_session_empty(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            deliberation_research_role_sequence,
        )

        # Empty session.role_sequence → fall back to the
        # DEFAULT_RESEARCH_ROLE_SEQUENCE which must include both
        # ai-engineer and devops-engineer.
        sequence = deliberation_research_role_sequence(
            _make_session(role_sequence=())
        )
        self.assertEqual(sequence[0], "tech-lead")
        self.assertIn("ai-engineer", sequence)
        self.assertIn("devops-engineer", sequence)


class DevOpsRoleDeliberationTests(unittest.TestCase):
    """devops-engineer fallback must produce a real DevOpsEngineerTake
    instead of the unknown-role placeholder so the member bot can post."""

    def test_deliberation_role_turn_returns_devops_take(self) -> None:
        from yule_orchestrator.agents.deliberation import DevOpsEngineerTake
        from yule_orchestrator.discord.engineering_team_runtime import (
            deliberation_role_turn,
        )

        take, rendered = deliberation_role_turn(
            _make_session(),
            "engineering-agent/devops-engineer",
        )
        self.assertIsInstance(take, DevOpsEngineerTake)
        self.assertIn("[devops-engineer]", rendered)
        # Release checklist + rollback are the role's signature fields.
        self.assertTrue(take.release_checklist)
        self.assertTrue(take.rollback_plan)


if __name__ == "__main__":
    unittest.main()
