"""team-turn legacy path active-roles gate — A-M7.5 regression.

Pin the user-spec fix: ``yule discord up`` legacy team-turn path must
filter dispatch by ``session.extra['active_research_roles']`` so
excluded roles can never speak. Before A-M7.5 the team-turn path
walked ``session.role_sequence`` directly — every role spoke.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.workflow_state import WorkflowSession, WorkflowState
from yule_engineering.discord.engineering_team_runtime import (
    build_turn_plan,
    handle_team_turn_message,
    next_pending_turn,
)


def _make_session(
    *,
    role_sequence,
    active_research_roles=None,
    thread_id: int = 999,
    session_id: str = "sess-team-turn-gate",
):
    when = datetime.now(tz=timezone.utc)
    extra: dict = {}
    if active_research_roles is not None:
        extra["active_research_roles"] = list(active_research_roles)
    return WorkflowSession(
        session_id=session_id,
        prompt="test prompt",
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=when,
        updated_at=when,
        role_sequence=role_sequence,
        thread_id=thread_id,
        extra=extra,
    )


class BuildTurnPlanFilterTests(unittest.TestCase):
    def test_plan_excludes_roles_outside_active_set(self) -> None:
        # Session has 7 legacy roles in role_sequence but only 2
        # active. The plan must surface only the 2 active roles.
        session = _make_session(
            role_sequence=(
                "tech-lead",
                "backend-engineer",
                "qa-engineer",
                "frontend-engineer",
                "product-designer",
                "devops-engineer",
                "ai-engineer",
            ),
            active_research_roles=("tech-lead", "devops-engineer"),
        )
        plan = build_turn_plan(session)
        plan_roles = tuple(t.role for t in plan)
        self.assertEqual(plan_roles, ("tech-lead", "devops-engineer"))

    def test_plan_with_no_active_roles_falls_back_to_sequence(self) -> None:
        # Old session without any role-selection metadata — plan
        # surfaces every role in the sequence (legacy behaviour).
        session = _make_session(
            role_sequence=("tech-lead", "backend-engineer"),
            active_research_roles=None,
        )
        plan = build_turn_plan(session)
        self.assertEqual(
            tuple(t.role for t in plan),
            ("tech-lead", "backend-engineer"),
        )


class NextPendingTurnTests(unittest.TestCase):
    def test_next_pending_skips_excluded_roles(self) -> None:
        session = _make_session(
            role_sequence=(
                "tech-lead",
                "backend-engineer",
                "frontend-engineer",
                "qa-engineer",
            ),
            active_research_roles=("tech-lead", "qa-engineer"),
        )
        # tech-lead hasn't played yet → next is tech-lead.
        first = next_pending_turn(session)
        assert first is not None
        self.assertEqual(first.role, "tech-lead")
        # After tech-lead plays, the next pending must be qa-engineer
        # (not backend-engineer / frontend-engineer — they're excluded).
        from dataclasses import replace as _replace

        played_session = _replace(
            session,
            extra={
                **session.extra,
                "team_conversation": {"played_roles": ["tech-lead"]},
            },
        )
        second = next_pending_turn(played_session)
        assert second is not None
        self.assertEqual(second.role, "qa-engineer")


class HandleTeamTurnMessageGateTests(unittest.TestCase):
    def test_excluded_role_ignores_dispatch_marker(self) -> None:
        # Session: only tech-lead + devops-engineer are active.
        session = _make_session(
            role_sequence=(
                "tech-lead",
                "backend-engineer",
                "devops-engineer",
                "qa-engineer",
            ),
            active_research_roles=("tech-lead", "devops-engineer"),
        )

        def loader(_sid: str):
            return session

        # backend-engineer's bot sees [team-turn:<sid> backend-engineer]
        # — but it's not in the active set, so it must return None.
        text = (
            f"[team-turn:{session.session_id} backend-engineer] "
            "your turn please"
        )
        outcome = handle_team_turn_message(
            role="backend-engineer",
            text=text,
            session_loader=loader,
        )
        self.assertIsNone(
            outcome,
            "backend-engineer is excluded — must not produce a turn outcome",
        )

    def test_active_role_dispatch_still_produces_outcome(self) -> None:
        session = _make_session(
            role_sequence=(
                "tech-lead",
                "backend-engineer",
                "qa-engineer",
            ),
            active_research_roles=("tech-lead", "qa-engineer"),
        )

        def loader(_sid: str):
            return session

        text = f"[team-turn:{session.session_id} qa-engineer] your turn"
        outcome = handle_team_turn_message(
            role="qa-engineer",
            text=text,
            session_loader=loader,
        )
        self.assertIsNotNone(outcome)
        assert outcome is not None
        self.assertEqual(outcome.turn.role, "qa-engineer")


if __name__ == "__main__":
    unittest.main()
