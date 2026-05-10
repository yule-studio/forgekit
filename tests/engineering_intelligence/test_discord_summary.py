"""Discord summary helper — daily role digest renderer."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.engineering_intelligence.discord_summary import (
    render_daily_role_summary,
    render_multi_role_summary,
    share_boundary_breakdown,
)
from yule_orchestrator.agents.engineering_intelligence.models import (
    EngineeringKnowledgeItem,
    Importance,
    KnowledgeShareScope,
    SourceKind,
)


def _it(
    title: str,
    *,
    importance: Importance = Importance.MEDIUM,
    source_name: str = "Spring Engineering Blog",
    source_url: str = "https://spring.io/blog/x",
    share_scope: KnowledgeShareScope = KnowledgeShareScope.PUBLIC,
    share_scope_reason: str = "",
) -> EngineeringKnowledgeItem:
    return EngineeringKnowledgeItem(
        item_id=title,
        topic_key=title.lower().replace(" ", "-"),
        title=title,
        role="backend-engineer",
        stack_tags=("x",),
        source_name=source_name,
        source_url=source_url,
        source_kind=SourceKind.ENGINEERING_BLOG,
        collected_at="2026-05-08T00:00:00Z",
        importance=importance,
        share_scope=share_scope,
        share_scope_reason=share_scope_reason,
    )


class DailyRoleSummaryTests(unittest.TestCase):
    def test_max_five_items_shown(self) -> None:
        items = [_it(f"Item {n}") for n in range(8)]
        text = render_daily_role_summary(
            "backend-engineer", items, today="2026-05-08"
        )
        # Lines that look like "1. ", "2. " up to "5. " should be present.
        for n in range(1, 6):
            self.assertIn(f"{n}. **Item {n - 1}**", text)
        # Item 6 (index 5) MUST NOT appear.
        self.assertNotIn("Item 5**", text)

    def test_empty_list_message(self) -> None:
        text = render_daily_role_summary(
            "qa-engineer", [], today="2026-05-08"
        )
        self.assertIn("새로 수집된 기술 이슈가 없습니다", text)

    def test_role_and_date_in_header(self) -> None:
        text = render_daily_role_summary(
            "ai-engineer", [_it("foo")], today="2026-05-08"
        )
        self.assertIn("ai-engineer", text)
        self.assertIn("2026-05-08", text)

    def test_importance_badge_present(self) -> None:
        items = [_it("Critical issue", importance=Importance.CRITICAL)]
        text = render_daily_role_summary("backend-engineer", items)
        self.assertIn("critical", text.lower())

    def test_obsidian_pointer_in_footer(self) -> None:
        text = render_daily_role_summary(
            "backend-engineer", [_it("foo")]
        )
        self.assertIn("Obsidian", text)
        self.assertIn("engineering-knowledge", text)


class ShareBoundaryFooterTests(unittest.TestCase):
    """Daily digest footer 가 share_scope 분포를 정확히 노출한다."""

    def test_all_public_skips_footer(self) -> None:
        items = [_it("public-only")]
        text = render_daily_role_summary("backend-engineer", items)
        # 정상 footer (Obsidian pointer) 는 그대로 — share boundary
        # 안내는 일부러 추가하지 않는다.
        self.assertNotIn("share boundary", text)
        self.assertIn("Obsidian", text)

    def test_team_internal_item_triggers_footer(self) -> None:
        items = [
            _it("public-doc"),
            _it(
                "internal-doc",
                share_scope=KnowledgeShareScope.TEAM_INTERNAL,
            ),
        ]
        text = render_daily_role_summary("backend-engineer", items)
        self.assertIn("share boundary", text)
        self.assertIn("public 1건", text)
        self.assertIn("team-internal 1건", text)
        self.assertIn("vault link", text)

    def test_restricted_item_marked_in_footer_and_body(self) -> None:
        items = [
            _it(
                "incident",
                share_scope=KnowledgeShareScope.RESTRICTED,
                share_scope_reason="customer PII",
            ),
        ]
        text = render_daily_role_summary("backend-engineer", items)
        # body 에는 제목/URL 모두 마스킹.
        self.assertNotIn("https://spring.io/blog/x", text)
        self.assertIn("🔒 공개 제한된 자료", text)
        # footer 에 restricted 카운트가 잡힌다.
        self.assertIn("share boundary", text)
        self.assertIn("공개 제한 1건", text)

    def test_breakdown_helper_classifies_each_scope(self) -> None:
        items = [
            _it("a"),
            _it("b", share_scope=KnowledgeShareScope.TEAM_INTERNAL),
            _it(
                "c",
                share_scope=KnowledgeShareScope.RESTRICTED,
                share_scope_reason="needed",
            ),
            _it("d"),
        ]
        breakdown = share_boundary_breakdown(items)
        self.assertEqual(breakdown["public"], 2)
        self.assertEqual(breakdown["team_internal"], 1)
        self.assertEqual(breakdown["restricted"], 1)
        self.assertEqual(breakdown["total"], 4)


class MultiRoleSummaryTests(unittest.TestCase):
    def test_combines_blocks_separated_by_blank_lines(self) -> None:
        backend = [_it("backend item")]
        ai = [_it("ai item", source_name="OpenAI News & Research", source_url="https://openai.com/x")]
        text = render_multi_role_summary(
            [("backend-engineer", backend), ("ai-engineer", ai)],
            today="2026-05-08",
        )
        self.assertIn("backend-engineer", text)
        self.assertIn("ai-engineer", text)
        self.assertIn("backend item", text)
        self.assertIn("ai item", text)


if __name__ == "__main__":
    unittest.main()
