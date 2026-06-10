"""Stabilisation Phase 5 — active_research_roles gates the research
forum chain.

Pin the live-bug regression: a member bot whose role isn't in
``session.extra['active_research_roles']`` must NOT respond to
``[research-open:<sid>]`` directives — no typing, no comment. The
gate is implemented in :func:`deliberation_research_role_sequence`
which now prefers ``active_research_roles`` over ``role_sequence``.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_discord.engineering_team_runtime import (
    DEFAULT_RESEARCH_ROLE_SEQUENCE,
    deliberation_research_role_sequence,
    handle_research_turn_message,
)
from yule_engineering.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
)


def _session(*, active_roles=None, role_sequence=()) -> WorkflowSession:
    extra: dict[str, Any] = {}
    if active_roles is not None:
        extra["active_research_roles"] = list(active_roles)
    now = datetime(2026, 5, 6)
    return WorkflowSession(
        session_id="abc12345",
        prompt="harness",
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=now,
        updated_at=now,
        role_sequence=tuple(role_sequence),
        extra=extra,
    )


class DeliberationSequenceHonoursActiveRolesTests(unittest.TestCase):
    def test_active_roles_replace_role_sequence_default(self) -> None:
        # active_research_roles wins over role_sequence + default.
        session = _session(
            active_roles=["tech-lead", "ai-engineer", "qa-engineer"],
            role_sequence=("backend-engineer", "frontend-engineer"),
        )
        sequence = deliberation_research_role_sequence(session)
        self.assertIn("ai-engineer", sequence)
        self.assertIn("qa-engineer", sequence)
        self.assertNotIn("backend-engineer", sequence)
        self.assertNotIn("frontend-engineer", sequence)
        # tech-lead always opens the chain regardless.
        self.assertEqual(sequence[0], "tech-lead")

    def test_no_active_roles_falls_back_to_role_sequence(self) -> None:
        session = _session(
            active_roles=None,
            role_sequence=("backend-engineer", "qa-engineer"),
        )
        sequence = deliberation_research_role_sequence(session)
        self.assertIn("backend-engineer", sequence)
        self.assertIn("qa-engineer", sequence)

    def test_no_active_no_role_sequence_falls_back_to_default(self) -> None:
        session = _session(active_roles=None, role_sequence=())
        sequence = deliberation_research_role_sequence(session)
        # default sequence should populate the chain.
        for role in DEFAULT_RESEARCH_ROLE_SEQUENCE:
            short = role.split("/", 1)[-1]
            self.assertIn(short, sequence)

    def test_explicit_base_overrides_session(self) -> None:
        session = _session(active_roles=["ai-engineer"])
        sequence = deliberation_research_role_sequence(
            session, base=["devops-engineer"]
        )
        self.assertIn("devops-engineer", sequence)
        # When base is explicit, active_research_roles is NOT consulted.
        self.assertNotIn("ai-engineer", sequence)


class ResearchOpenIgnoresInactiveRoleTests(unittest.TestCase):
    """End-to-end: ``handle_research_turn_message`` returns ``None`` for
    a research-open marker when the bot's role is excluded by
    ``active_research_roles`` — the member bot then skips its
    typing+post block, which is exactly the live-bug fix."""

    def _seed_and_handle(
        self,
        *,
        active_roles,
        bot_role: str,
    ) -> Any:
        session = _session(active_roles=active_roles)

        return handle_research_turn_message(
            role=bot_role,
            text="[research-open:abc12345]",
            session_loader=lambda _sid: session,
            pack_loader=lambda _s: None,
        )

    def test_inactive_role_returns_none_silently(self) -> None:
        # frontend-engineer wasn't selected — its bot stays silent.
        outcome = self._seed_and_handle(
            active_roles=["tech-lead", "ai-engineer", "qa-engineer"],
            bot_role="frontend-engineer",
        )
        self.assertIsNone(outcome)

    def test_active_role_returns_outcome(self) -> None:
        # ai-engineer is active — it produces a research_turn outcome.
        outcome = self._seed_and_handle(
            active_roles=["tech-lead", "ai-engineer", "qa-engineer"],
            bot_role="ai-engineer",
        )
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.role, "ai-engineer")

    def test_tech_lead_always_active(self) -> None:
        # tech-lead is the chain opener regardless — even when
        # active_research_roles doesn't list it explicitly,
        # deliberation_research_role_sequence prepends it.
        outcome = self._seed_and_handle(
            active_roles=["ai-engineer"],
            bot_role="tech-lead",
        )
        self.assertIsNotNone(outcome)


if __name__ == "__main__":
    unittest.main()
