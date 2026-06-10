"""Refactor — typing guard helpers."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_discord.ui.typing_indicator import (
    should_type_for_gateway_action,
    should_type_for_member_research,
)


class ShouldTypeForMemberResearchTests(unittest.TestCase):
    def test_inactive_role_returns_false(self) -> None:
        self.assertFalse(
            should_type_for_member_research(
                role="frontend-engineer",
                active_roles=("tech-lead", "ai-engineer", "qa-engineer"),
                will_post=True,
            )
        )

    def test_active_role_returns_true(self) -> None:
        self.assertTrue(
            should_type_for_member_research(
                role="ai-engineer",
                active_roles=("tech-lead", "ai-engineer", "qa-engineer"),
                will_post=True,
            )
        )

    def test_will_post_false_returns_false(self) -> None:
        # Even an active role stays silent when the handler returned
        # None — typing must follow real responses.
        self.assertFalse(
            should_type_for_member_research(
                role="ai-engineer",
                active_roles=("tech-lead", "ai-engineer"),
                will_post=False,
            )
        )

    def test_no_active_roles_falls_back_to_active(self) -> None:
        # Legacy session — no role_selection metadata yet. The
        # handler outcome is the authoritative gate; helper returns
        # True so the bot keeps the pre-Phase-1 behaviour.
        self.assertTrue(
            should_type_for_member_research(
                role="ai-engineer",
                active_roles=(),
                will_post=True,
            )
        )

    def test_empty_role_returns_false(self) -> None:
        self.assertFalse(
            should_type_for_member_research(
                role="",
                active_roles=("tech-lead",),
                will_post=True,
            )
        )


class ShouldTypeForGatewayActionTests(unittest.TestCase):
    def test_engineering_channel_with_branch_returns_true(self) -> None:
        self.assertTrue(
            should_type_for_gateway_action(
                is_engineering_channel=True,
                handled_branch_likely=True,
            )
        )

    def test_non_engineering_channel_returns_false(self) -> None:
        self.assertFalse(
            should_type_for_gateway_action(
                is_engineering_channel=False,
                handled_branch_likely=True,
            )
        )

    def test_engineering_channel_no_branch_returns_false(self) -> None:
        # Bot author / slash command / empty content should set
        # handled_branch_likely=False so typing stays off.
        self.assertFalse(
            should_type_for_gateway_action(
                is_engineering_channel=True,
                handled_branch_likely=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
