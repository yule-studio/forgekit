"""F13 formatter + dispatcher 회귀."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.digest.dispatcher import (
    ENV_DEPT_CHANNELS,
    ENV_RESEARCH_FORUM,
    build_dispatch_plan,
)
from yule_orchestrator.agents.digest.formatter import format_card


def _make_card(**overrides):
    base = {
        "title": "Spring Security 6.6",
        "url": "https://github.com/spring/spring-security/releases/tag/v6.6",
        "summary": "OAuth 2.1 refresh token 회전 권고",
        "source_host": "spring.io",
        "published_at": datetime(2026, 5, 12, tzinfo=timezone.utc),
        "tags": ("security",),
        "dept_primary": "engineering",
        "affected_depts": ("engineering",),
        "meeting_trigger": False,
        "role_hint": "backend-engineer",
    }
    base.update(overrides)
    return format_card(**base)


class FormatterTests(unittest.TestCase):
    def test_render_includes_title_url_summary(self) -> None:
        card = _make_card()
        text = card.render_text()
        self.assertIn("Spring Security 6.6", text)
        self.assertIn("<https://github.com/spring", text)
        self.assertIn("OAuth 2.1", text)
        self.assertIn("출처: `spring.io`", text)

    def test_long_summary_truncated(self) -> None:
        card = _make_card(summary="A" * 500)
        text = card.render_text()
        self.assertIn("...", text)
        # 본문은 280자 정도로 잘림

    def test_meeting_trigger_marker_in_text(self) -> None:
        card = _make_card(meeting_trigger=True, affected_depts=("engineering", "design"))
        text = card.render_text()
        self.assertIn("운영-리서치", text)
        self.assertIn("engineering, design", text)


class DispatcherTests(unittest.TestCase):
    def _env(self, **overrides) -> dict:
        env = {
            "DISCORD_DEPT_PLANNING_CHANNEL_ID": "111",
            "DISCORD_DEPT_DESIGN_CHANNEL_ID": "222",
            "DISCORD_DEPT_DEV_CHANNEL_ID": "333",
            "DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_ID": "999",
        }
        env.update(overrides)
        return env

    def test_single_dept_card_routes_to_one_channel(self) -> None:
        card = _make_card()
        plan = build_dispatch_plan([card], env=self._env())
        self.assertEqual(len(plan.targets), 1)
        self.assertEqual(plan.targets[0].channel_id, "333")  # engineering
        self.assertEqual(plan.targets[0].target_kind, "dept_feed")

    def test_meeting_trigger_routes_to_multiple_depts_and_forum(self) -> None:
        card = _make_card(meeting_trigger=True, affected_depts=("engineering", "design"))
        plan = build_dispatch_plan([card], env=self._env())
        # 2 dept channel + 1 forum target = 3
        self.assertEqual(len(plan.targets), 3)
        kinds = [t.target_kind for t in plan.targets]
        self.assertEqual(kinds.count("dept_feed"), 2)
        self.assertEqual(kinds.count("research_forum_thread"), 1)
        self.assertEqual(len(plan.research_forum_threads), 1)

    def test_missing_channel_env_recorded_in_skipped(self) -> None:
        card = _make_card(dept_primary="design")
        env = self._env()
        env.pop("DISCORD_DEPT_DESIGN_CHANNEL_ID")  # 환경 누락
        plan = build_dispatch_plan([card], env=env)
        self.assertEqual(len(plan.targets), 0)
        self.assertIn("design", plan.skipped_no_channel)

    def test_research_forum_missing_skips_forum_target(self) -> None:
        card = _make_card(meeting_trigger=True, affected_depts=("engineering",))
        env = self._env()
        env.pop("DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_ID")
        plan = build_dispatch_plan([card], env=env)
        # dept channel target 만, forum target 없음
        kinds = [t.target_kind for t in plan.targets]
        self.assertEqual(kinds.count("research_forum_thread"), 0)


if __name__ == "__main__":
    unittest.main()
