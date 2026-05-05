from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.research_pack import (
    ResearchAttachment,
    ResearchPack,
    ResearchSource,
    pack_from_discord_message,
)
from yule_orchestrator.discord.research_forum import (
    ALL_PREFIXES,
    PREFIX_DECISION,
    PREFIX_OBSIDIAN,
    PREFIX_REFERENCE,
    PREFIX_RESEARCH,
    PREFIX_TOOL,
    ForumCommentOutcome,
    ForumPostOutcome,
    ResearchForumContext,
    create_research_post,
    detect_thread_prefix,
    format_agent_comment,
    format_research_post_body,
    format_thread_markdown_fallback,
    normalize_thread_title,
    post_agent_comment,
)


def _run(coro):
    """Run *coro* on a fresh loop so we work in any suite ordering.

    ``asyncio.get_event_loop()`` is deprecated when no loop is already
    running; ``asyncio.run`` would close the loop, so a per-call new
    loop is the safest contract for unit tests that share a process.
    """

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class ForumContextTestCase(unittest.TestCase):
    def test_from_env_reads_keys(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("DISCORD_AGENT_RESEARCH_")}
        env["DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_ID"] = "1499287359483805879"
        env["DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_NAME"] = "운영-리서치"
        with patch.dict(os.environ, env, clear=True):
            ctx = ResearchForumContext.from_env()
        self.assertEqual(ctx.channel_id, 1499287359483805879)
        self.assertEqual(ctx.channel_name, "운영-리서치")
        self.assertTrue(ctx.configured)

    def test_unconfigured_when_blank(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("DISCORD_AGENT_RESEARCH_")}
        with patch.dict(os.environ, env, clear=True):
            ctx = ResearchForumContext.from_env()
        self.assertFalse(ctx.configured)


class NormalizeThreadTitleTestCase(unittest.TestCase):
    def test_keeps_existing_prefix(self) -> None:
        for prefix in ALL_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    normalize_thread_title(f"{prefix} sample"),
                    f"{prefix} sample",
                )

    def test_prepends_default_research_prefix(self) -> None:
        self.assertEqual(
            normalize_thread_title("새 자료"),
            f"{PREFIX_RESEARCH} 새 자료",
        )

    def test_prepends_supplied_thread_prefix(self) -> None:
        self.assertEqual(
            normalize_thread_title("Stripe", prefix=PREFIX_REFERENCE),
            f"{PREFIX_REFERENCE} Stripe",
        )

    def test_falls_back_to_research_for_unknown_prefix(self) -> None:
        # decision/obsidian are comment prefixes — when supplied as title prefix
        # we ignore them and default to [Research].
        self.assertEqual(
            normalize_thread_title("x", prefix=PREFIX_DECISION),
            f"{PREFIX_RESEARCH} x",
        )

    def test_blank_input_becomes_untitled(self) -> None:
        self.assertEqual(normalize_thread_title("  "), f"{PREFIX_RESEARCH} (untitled)")

    def test_long_title_is_truncated_under_discord_limit(self) -> None:
        long_title = (
            "오늘은 Obsidian과 Discord와 Claude를 연결해서 개발팀이 스스로 "
            "학습하는 구조를 만들고 싶다. 그리고 흐름이 어떻게 안정되는지도 "
            "함께 검토하자. 그 다음에는 Obsidian sync까지."
        )
        result = normalize_thread_title(long_title)
        self.assertLessEqual(len(result), 100)
        self.assertTrue(result.startswith(PREFIX_RESEARCH))
        self.assertTrue(result.endswith("…"))

    def test_existing_prefix_does_not_duplicate(self) -> None:
        result = normalize_thread_title(f"{PREFIX_RESEARCH} 자료 모음")
        self.assertEqual(result, f"{PREFIX_RESEARCH} 자료 모음")
        # Caller passing prefix= explicitly with an already-prefixed title
        # also avoids double prefix.
        result2 = normalize_thread_title(
            f"{PREFIX_RESEARCH} 자료 모음", prefix=PREFIX_RESEARCH
        )
        self.assertEqual(result2.count(PREFIX_RESEARCH), 1)

    def test_max_chars_param_overrides_default(self) -> None:
        result = normalize_thread_title(
            "긴 제목을 짧게 자르고 싶어요 매우 길게", max_chars=30
        )
        self.assertLessEqual(len(result), 30)
        self.assertTrue(result.startswith(PREFIX_RESEARCH))


class DetectThreadPrefixTestCase(unittest.TestCase):
    def test_detects_known_prefix(self) -> None:
        for prefix in ALL_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    detect_thread_prefix(f"{prefix} 테스트"),
                    prefix,
                )

    def test_returns_none_when_missing(self) -> None:
        self.assertIsNone(detect_thread_prefix("일반 제목"))


class FormatBodyTestCase(unittest.TestCase):
    def _pack(self) -> ResearchPack:
        return pack_from_discord_message(
            title="Stripe Pricing 패턴",
            content="hero step copy 강조 — https://stripe.com/pricing 참고",
            author_role="engineering-agent/product-designer",
            channel_id=999,
            thread_id=888,
            message_id=777,
            posted_at=datetime(2026, 4, 30, 10, 0),
            attachments=[
                ResearchAttachment(
                    kind="image",
                    url="https://cdn/x.png",
                    filename="hero.png",
                    description="레퍼런스 캡처",
                )
            ],
            tags=["reference", "ux"],
        )

    def test_body_includes_summary_and_url_and_attachment(self) -> None:
        body = format_research_post_body(self._pack(), posted_by="bot:designer")
        self.assertIn("posted by", body)
        self.assertIn("**요약**", body)
        self.assertIn("https://stripe.com/pricing", body)
        self.assertIn("**첨부**", body)
        self.assertIn("hero.png", body)
        self.assertIn("**태그**", body)
        self.assertIn("`reference`", body)
        self.assertIn("**출처**", body)
        self.assertIn("engineering-agent/product-designer", body)

    def test_body_handles_no_url(self) -> None:
        pack = ResearchPack(title="t", summary="간단 메모")
        body = format_research_post_body(pack)
        self.assertIn("간단 메모", body)
        self.assertNotIn("**자료 링크**", body)

    def test_body_with_multiple_sources(self) -> None:
        s1 = ResearchSource(source_url="https://a", author_role="r1", message_id=1)
        s2 = ResearchSource(source_url="https://b", author_role="r2", message_id=2)
        pack = ResearchPack(title="t", sources=(s1, s2))
        body = format_research_post_body(pack)
        self.assertIn("**출처 2건**", body)
        self.assertIn("https://a", body)
        self.assertIn("https://b", body)


class FormatAgentCommentTestCase(unittest.TestCase):
    def test_renders_all_blocks(self) -> None:
        comment = format_agent_comment(
            role="engineering-agent/backend-engineer",
            collected_materials=(
                "[official_docs] PostgreSQL 14 indexes — https://www.postgresql.org/docs/14/indexes.html",
                "[code_context] users 테이블 스키마 dump",
            ),
            interpretation="현재 schema 변경 없이 처리 가능 — verified column이 이미 존재합니다.",
            risks="migration 시 잠금 가능성 — off-peak 권장",
            next_actions=("verify column index", "draft migration"),
            confidence="high",
            confidence_reason="schema dump 직접 확인",
        )
        self.assertIn("[role:engineering-agent/backend-engineer]", comment)
        self.assertIn("- 역할: engineering-agent/backend-engineer", comment)
        self.assertIn("- 수집 자료:", comment)
        self.assertIn("1. [official_docs] PostgreSQL 14 indexes", comment)
        self.assertIn("2. [code_context] users 테이블 스키마 dump", comment)
        self.assertIn("- 해석: 현재 schema 변경 없이", comment)
        self.assertIn("- 리스크:", comment)
        self.assertIn("- 다음 행동:", comment)
        self.assertIn("1. verify column index", comment)
        self.assertIn("2. draft migration", comment)
        self.assertIn("신뢰도: high — schema dump 직접 확인", comment)

    def test_falls_back_when_materials_empty(self) -> None:
        comment = format_agent_comment(
            role="r",
            interpretation="i",
        )
        self.assertIn("- 수집된 자료 없음 — 추가 조사 필요", comment)

    def test_falls_back_when_actions_empty(self) -> None:
        comment = format_agent_comment(
            role="r",
            collected_materials=("source-1",),
            interpretation="i",
        )
        self.assertIn("- 추가 행동 없음", comment)

    def test_falls_back_for_invalid_confidence(self) -> None:
        comment = format_agent_comment(
            role="r",
            collected_materials=("source-1",),
            interpretation="i",
            confidence="super-high",
        )
        self.assertIn("신뢰도: medium", comment)

    def test_falls_back_when_role_blank(self) -> None:
        comment = format_agent_comment(role="  ", interpretation="i")
        self.assertIn("[role:<unknown-role>]", comment)
        self.assertIn("- 역할: <unknown-role>", comment)

    def test_falls_back_when_interpretation_blank(self) -> None:
        comment = format_agent_comment(
            role="r",
            collected_materials=("x",),
        )
        self.assertIn("- 해석: (해석 미기재)", comment)


class CreateResearchPostTestCase(unittest.TestCase):
    def test_returns_error_with_fallback_when_unconfigured(self) -> None:
        ctx = ResearchForumContext()
        async def fn(**_):
            raise AssertionError("should not be called when unconfigured")
        pack = pack_from_discord_message(
            title="새 자료",
            content="https://example.com/a",
            channel_id=1,
            message_id=2,
        )
        outcome = _run(create_research_post(
            pack,
            forum_context=ctx,
            create_thread_fn=fn,
        ))
        self.assertFalse(outcome.posted)
        self.assertIn("not configured", outcome.error or "")
        self.assertIsNotNone(outcome.fallback_markdown)
        self.assertIn(f"## {PREFIX_RESEARCH} 새 자료", outcome.fallback_markdown or "")
        self.assertIn("forum 게시에 실패", outcome.fallback_markdown or "")
        self.assertIn("https://example.com/a", outcome.fallback_markdown or "")

    def test_calls_thread_fn_with_normalized_title_and_body(self) -> None:
        captured: dict = {}

        async def thread_fn(**kwargs):
            captured.update(kwargs)
            return {"id": 12345, "url": "https://discord.com/channels/x/12345"}

        pack = pack_from_discord_message(
            title="새 자료",
            content="https://example.com/a",
            channel_id=1,
            message_id=2,
        )
        ctx = ResearchForumContext(channel_id=999, channel_name="운영-리서치")
        outcome = _run(create_research_post(
            pack,
            forum_context=ctx,
            create_thread_fn=thread_fn,
            prefix=PREFIX_REFERENCE,
        ))
        self.assertTrue(outcome.posted)
        self.assertEqual(outcome.thread_id, 12345)
        self.assertEqual(outcome.thread_url, "https://discord.com/channels/x/12345")
        self.assertTrue(captured["name"].startswith(f"{PREFIX_REFERENCE} "))
        self.assertIn("https://example.com/a", captured["content"])
        self.assertEqual(captured["channel_id"], 999)
        self.assertEqual(captured["channel_name"], "운영-리서치")
        self.assertIsNone(outcome.fallback_markdown)

    def test_propagates_thread_fn_error_with_fallback(self) -> None:
        async def thread_fn(**_):
            raise RuntimeError("403 forbidden")
        pack = pack_from_discord_message(
            title="권한 실패 케이스",
            content="https://example.com/locked",
            channel_id=1,
            message_id=2,
        )
        ctx = ResearchForumContext(channel_id=1)
        outcome = _run(create_research_post(
            pack,
            forum_context=ctx,
            create_thread_fn=thread_fn,
        ))
        self.assertFalse(outcome.posted)
        self.assertIn("403", outcome.error or "")
        self.assertIsNotNone(outcome.title)
        self.assertIsNotNone(outcome.body)
        self.assertIsNotNone(outcome.fallback_markdown)
        self.assertIn("403 forbidden", outcome.fallback_markdown or "")
        self.assertIn("https://example.com/locked", outcome.fallback_markdown or "")


class FormatThreadMarkdownFallbackTestCase(unittest.TestCase):
    def test_includes_title_notice_and_body(self) -> None:
        pack = pack_from_discord_message(
            title="Stripe pricing",
            content="https://stripe.com/pricing 참고",
        )
        markdown = format_thread_markdown_fallback(
            pack,
            posted_by="bot:designer",
            reason="403 forbidden",
        )
        first_line = markdown.splitlines()[0]
        self.assertTrue(first_line.startswith("## [Research] Stripe pricing"))
        self.assertIn("forum 게시에 실패", markdown)
        self.assertIn("403 forbidden", markdown)
        self.assertIn("https://stripe.com/pricing", markdown)
        self.assertIn("posted by", markdown)

    def test_uses_existing_title_prefix(self) -> None:
        pack = ResearchPack(title=f"{PREFIX_TOOL} resend.com")
        markdown = format_thread_markdown_fallback(pack)
        first_line = markdown.splitlines()[0]
        self.assertEqual(first_line, f"## {PREFIX_TOOL} resend.com")

    def test_omits_reason_when_blank(self) -> None:
        pack = ResearchPack(title="t", summary="s")
        markdown = format_thread_markdown_fallback(pack)
        self.assertNotIn("사유:", markdown)
        self.assertIn("forum 게시에 실패", markdown)


class PostAgentCommentTestCase(unittest.TestCase):
    def test_posts_formatted_comment(self) -> None:
        captured: dict = {}

        async def post_fn(**kwargs):
            captured.update(kwargs)
            return {"id": 555}

        outcome = _run(post_agent_comment(
            thread_id=42,
            role="engineering-agent/qa-engineer",
            collected_materials=(
                "[github_issue] #144 onboarding step 2 불안정",
                "[code_context] tests/e2e/onboarding.spec.ts 결손",
            ),
            interpretation="회귀 시나리오 보강이 필요합니다.",
            risks="없음",
            next_actions=("add e2e for step 2",),
            confidence="medium",
            post_message_fn=post_fn,
        ))
        self.assertTrue(outcome.posted)
        self.assertEqual(outcome.message_id, 555)
        self.assertEqual(captured["thread_id"], 42)
        self.assertIn("[role:engineering-agent/qa-engineer]", captured["content"])
        self.assertIn("- 수집 자료:", captured["content"])
        self.assertIn("[github_issue] #144", captured["content"])
        self.assertIn("- 해석:", captured["content"])

    def test_propagates_error(self) -> None:
        async def post_fn(**_):
            raise RuntimeError("rate limit")

        outcome = _run(post_agent_comment(
            thread_id=1,
            role="r",
            interpretation="i",
            post_message_fn=post_fn,
        ))
        self.assertFalse(outcome.posted)
        self.assertIn("rate limit", outcome.error or "")


# ---------------------------------------------------------------------------
# Auto collection block in forum body
# ---------------------------------------------------------------------------


class CollectionBlockInForumBodyTestCase(unittest.TestCase):
    def _make_outcome(self):
        from yule_orchestrator.agents.research_collector import (
            CollectorConfig,
            PROVIDER_MOCK,
            auto_collect_or_request_more_input,
        )

        return auto_collect_or_request_more_input(
            role="engineering-agent/product-designer",
            prompt="새 hero 정리",
            task_type="landing-page",
            config=CollectorConfig(
                enabled=True,
                provider=PROVIDER_MOCK,
                max_results=2,
                max_provider_calls=1,
                max_results_per_role=2,
            ),
        )

    def test_collection_block_appears_in_post_body(self) -> None:
        outcome = self._make_outcome()
        body = format_research_post_body(
            outcome.pack,
            posted_by="bot:designer",
            collection_outcome=outcome,
        )
        self.assertIn("1차 자료 정리 — product-designer", body)
        self.assertIn("**참고 자료**", body)
        self.assertIn("**다음 단계**", body)

    def test_block_omitted_when_no_outcome_passed(self) -> None:
        outcome = self._make_outcome()
        body = format_research_post_body(outcome.pack, posted_by="bot:designer")
        self.assertNotIn("1차 자료 정리 —", body)

    def test_explicit_next_steps_propagate(self) -> None:
        outcome = self._make_outcome()
        body = format_research_post_body(
            outcome.pack,
            collection_outcome=outcome,
            collection_next_steps=("backend 영향 점검", "qa 회귀 시나리오"),
        )
        self.assertIn("- backend 영향 점검", body)
        self.assertIn("- qa 회귀 시나리오", body)

    def test_collection_role_overrides_pack_request_role(self) -> None:
        outcome = self._make_outcome()
        body = format_research_post_body(
            outcome.pack,
            collection_outcome=outcome,
            collection_role="engineering-agent/qa-engineer",
        )
        self.assertIn("1차 자료 정리 — qa-engineer", body)


class CollectionBlockInFallbackTestCase(unittest.TestCase):
    def test_fallback_includes_collection_block(self) -> None:
        from yule_orchestrator.agents.research_collector import (
            CollectorConfig,
            PROVIDER_MOCK,
            auto_collect_or_request_more_input,
        )

        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/product-designer",
            prompt="새 hero",
            config=CollectorConfig(
                enabled=True,
                provider=PROVIDER_MOCK,
                max_results=2,
                max_provider_calls=1,
                max_results_per_role=2,
            ),
        )
        text = format_thread_markdown_fallback(
            outcome.pack,
            title=outcome.pack.title,
            reason="forum 권한 없음",
            collection_outcome=outcome,
        )
        self.assertIn("⚠️", text)
        self.assertIn("1차 자료 정리 — product-designer", text)


class CreateResearchPostCollectionTestCase(unittest.TestCase):
    def test_collection_block_passes_through_to_thread_body(self) -> None:
        import asyncio

        from yule_orchestrator.agents.research_collector import (
            CollectorConfig,
            PROVIDER_MOCK,
            auto_collect_or_request_more_input,
        )
        from yule_orchestrator.discord.research_forum import (
            ResearchForumContext,
            create_research_post,
        )

        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/product-designer",
            prompt="새 hero",
            config=CollectorConfig(
                enabled=True,
                provider=PROVIDER_MOCK,
                max_results=2,
                max_provider_calls=1,
                max_results_per_role=2,
            ),
        )

        captured: dict = {}

        async def fake_thread_fn(*, channel_id, channel_name, name, content):
            captured["content"] = content
            return {"id": 100, "url": "https://discord.example/threads/100"}

        async def runner():
            return await create_research_post(
                outcome.pack,
                forum_context=ResearchForumContext(channel_id=999),
                create_thread_fn=fake_thread_fn,
                collection_outcome=outcome,
            )

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(runner())
        finally:
            loop.close()

        self.assertTrue(result.posted)
        self.assertIn("1차 자료 정리", captured.get("content", ""))


from yule_orchestrator.discord.research_forum import derive_research_topic  # noqa: E402


class DeriveResearchTopicTests(unittest.TestCase):
    def test_uses_short_pack_title(self) -> None:
        pack = ResearchPack(title="개발팀 학습 루프 설계", summary="...")
        self.assertEqual(derive_research_topic(pack), "개발팀 학습 루프 설계")

    def test_falls_back_to_first_summary_sentence_when_title_long(self) -> None:
        long_title = "오늘은 Obsidian과 Discord와 Claude를 연결해서 개발팀이 스스로 학습하는 구조를 만들고 싶어 길게 적는다"
        pack = ResearchPack(
            title=long_title,
            summary="개발팀 학습 루프 설계. 그 다음에는 Obsidian sync.",
        )
        topic = derive_research_topic(pack)
        self.assertIn("개발팀 학습 루프 설계", topic)
        self.assertLessEqual(len(topic), 60)

    def test_blank_pack_returns_safe_default(self) -> None:
        pack = ResearchPack(title="", summary="")
        self.assertEqual(derive_research_topic(pack), "engineering 작업")


class CreateResearchPostTitleAndBodyTests(unittest.TestCase):
    def _async_run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_long_prompt_does_not_explode_thread_name(self) -> None:
        long_prompt = (
            "오늘은 Obsidian + Discord + Claude를 연결해서 개발팀이 스스로 학습하는 구조를 만들고 싶어. "
            "이 흐름이 잘 굴러가는지 자료 모아줘. 그리고 추가로 어떤 우려가 있는지도 검토해줘."
        )
        pack = ResearchPack(title=long_prompt, summary="개발팀 학습 루프 설계")
        captured: dict = {}

        async def fake_thread_fn(*, channel_id, channel_name, name, content):
            captured["name"] = name
            captured["content"] = content
            return {"id": 4242, "url": "https://discord.test/4242"}

        result = self._async_run(
            create_research_post(
                pack,
                forum_context=ResearchForumContext(channel_id=999),
                create_thread_fn=fake_thread_fn,
            )
        )
        self.assertTrue(result.posted)
        self.assertIn("name", captured)
        self.assertLessEqual(len(captured["name"]), 100)
        self.assertTrue(captured["name"].startswith(PREFIX_RESEARCH))
        # Prefix must not appear twice.
        self.assertEqual(captured["name"].count(PREFIX_RESEARCH), 1)

    def test_body_includes_original_request_section(self) -> None:
        from yule_orchestrator.agents.research_pack import ResearchRequest

        pack = ResearchPack(
            title="개발팀 학습 루프 설계",
            summary="짧은 요약",
            request=ResearchRequest(
                request_id="r1",
                topic="오늘은 개발팀이 스스로 학습하는 구조를 만들고 싶어",
                role="engineering-agent/tech-lead",
            ),
        )
        captured: dict = {}

        async def fake_thread_fn(*, channel_id, channel_name, name, content):
            captured["content"] = content
            return {"id": 1, "url": "https://x"}

        self._async_run(
            create_research_post(
                pack,
                forum_context=ResearchForumContext(channel_id=999),
                create_thread_fn=fake_thread_fn,
            )
        )
        body = captured.get("content", "")
        self.assertIn("## 원문 요청", body)
        self.assertIn("개발팀이 스스로 학습하는 구조", body)


if __name__ == "__main__":
    unittest.main()
