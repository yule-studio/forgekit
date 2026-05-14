"""Discord ``content`` 1900-char cap regression suite.

Discord's regular message limit is 2000 chars (4000 for forum starter
in the API spec, but discord.py validates the same 2000 cap on the
``content=`` kwarg). We post every Discord-bound payload through
``chunk_for_discord_message`` (defaults to 1900) so we never trip the
50035 ``In content: Must be 2000 or fewer in length`` validator.

This suite exercises every production path that does ``channel.send``,
``thread.send``, ``post_message_fn``, or ``create_thread_fn(content=)``
and asserts each piece is ≤ 1900.
"""

from __future__ import annotations

import asyncio
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.research.pack import (
    ResearchPack,
    ResearchRequest,
    ResearchSource,
)
from yule_orchestrator.discord.ui.formatter import split_discord_message
from yule_orchestrator.discord.research_forum import (
    DISCORD_MESSAGE_REPLY_LIMIT,
    FORUM_STARTER_CONTENT_LIMIT,
    ResearchForumContext,
    chunk_for_discord_message,
    create_research_post,
    format_research_post_body,
    post_agent_comment,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _huge_pack() -> ResearchPack:
    long_prompt = ("운영-리서치 forum 안정화 검토. " * 200).strip()
    big_summary = "매우 긴 본문 시나리오를 재현한다. " * 60
    many_sources = tuple(
        ResearchSource(
            source_url=(
                f"https://example.test/research-source-{i}/"
                "long/path/segment-which-pads-the-line"
            ),
            author_role=f"engineering-agent/role-{i}",
            message_id=i,
        )
        for i in range(200)
    )
    return ResearchPack(
        title="긴 운영-리서치 검토",
        summary=big_summary,
        sources=many_sources,
        tags=("research", "ops", "long"),
        request=ResearchRequest(
            request_id="r-long",
            topic=long_prompt,
            role="engineering-agent/tech-lead",
        ),
    )


class ChunkHelperLimitTests(unittest.TestCase):
    def test_constants_are_under_2000_with_safety_margin(self) -> None:
        self.assertLessEqual(FORUM_STARTER_CONTENT_LIMIT, 2000)
        self.assertLessEqual(DISCORD_MESSAGE_REPLY_LIMIT, 2000)
        self.assertEqual(FORUM_STARTER_CONTENT_LIMIT, 1900)
        self.assertEqual(DISCORD_MESSAGE_REPLY_LIMIT, 1900)

    def test_chunk_helper_caps_each_piece_at_1900(self) -> None:
        body = "라" * 50_000
        for piece in chunk_for_discord_message(body):
            self.assertLessEqual(len(piece), 1900)

    def test_chunk_helper_handles_empty(self) -> None:
        self.assertEqual(chunk_for_discord_message(""), ())
        self.assertEqual(chunk_for_discord_message(None), ())  # type: ignore[arg-type]

    def test_split_discord_message_hard_slices_long_single_line(self) -> None:
        # A single line with no whitespace > limit must still be split.
        # Without the hard-slice path the splitter would emit the line
        # whole and Discord would reject it.
        long_line = "x" * 6000
        pieces = split_discord_message(long_line, limit=1900)
        for piece in pieces:
            self.assertLessEqual(len(piece), 1900)
        self.assertEqual("".join(pieces), long_line)

    def test_split_discord_message_mixed_lines_stay_under_limit(self) -> None:
        body = ("\n".join(f"line-{i:04d} " + "y" * 200 for i in range(300))).strip()
        pieces = split_discord_message(body, limit=1900)
        for piece in pieces:
            self.assertLessEqual(len(piece), 1900)
        # Every line should end up represented somewhere.
        joined = "\n".join(pieces)
        self.assertIn("line-0000", joined)
        self.assertIn("line-0299", joined)


class ForumStarterCapTests(unittest.TestCase):
    """create_research_post must always send starter ≤ 1900 chars and
    every continuation chunk ≤ 1900 chars, even when the rendered body
    is dramatically longer than that."""

    def test_starter_and_chunks_all_under_1900(self) -> None:
        pack = _huge_pack()
        body = format_research_post_body(pack, posted_by="bot:test")
        self.assertGreater(len(body), 5000)  # fixture is genuinely long

        captured: dict = {"thread": None, "replies": []}

        async def thread_fn(**kwargs):
            captured["thread"] = kwargs
            return {"id": 1, "url": "https://discord.test/1"}

        async def post_fn(**kwargs):
            captured["replies"].append(dict(kwargs))
            return {"id": len(captured["replies"]) + 1000}

        outcome = _run(
            create_research_post(
                pack,
                forum_context=ResearchForumContext(channel_id=42),
                create_thread_fn=thread_fn,
                post_message_fn=post_fn,
                posted_by="bot:test",
            )
        )
        self.assertTrue(outcome.posted)

        starter_content = captured["thread"]["content"]
        self.assertLessEqual(len(starter_content), 1900)

        # Every continuation chunk that hit Discord must be under 1900 too,
        # including any failure-notice comment that was posted afterward.
        for reply in captured["replies"]:
            self.assertLessEqual(len(reply["content"]), 1900)

        # And the outcome-tracked chunk list mirrors the cap (the outcome
        # records pre-chunk size; ``post_fn`` is what Discord actually
        # sees after the defensive re-chunk in _post_continuation_chunks).
        for chunk in outcome.continuation_chunks:
            self.assertLessEqual(len(chunk), DISCORD_MESSAGE_REPLY_LIMIT * 2)


class RoleCommentCapTests(unittest.TestCase):
    """post_agent_comment chunks long takes before sending so a verbose
    role review never overflows Discord's ``content`` cap."""

    def test_long_role_comment_is_chunked(self) -> None:
        evidence = tuple(
            f"[official_docs] {i:03d} 매우 긴 자료 라인 — "
            f"https://example.test/role-evidence-{i}/path/segment-which-pads-content"
            for i in range(200)
        )
        actions = tuple(f"action {i} — 후속 검토 필요한 항목" for i in range(60))

        captured: list[dict] = []

        async def post_fn(**kwargs):
            captured.append(dict(kwargs))
            return {"id": len(captured)}

        outcome = _run(
            post_agent_comment(
                thread_id=99,
                role="engineering-agent/backend-engineer",
                collected_materials=evidence,
                interpretation="매우 긴 해석 문장. " * 200,
                risks="잠금/마이그레이션 위험. " * 200,
                next_actions=actions,
                confidence="medium",
                post_message_fn=post_fn,
            )
        )
        self.assertTrue(outcome.posted)
        self.assertGreater(len(captured), 1, "long comment must split into >1 piece")
        for call in captured:
            self.assertEqual(call["thread_id"], 99)
            self.assertLessEqual(len(call["content"]), 1900)

    def test_short_role_comment_posts_as_single_message(self) -> None:
        captured: list[dict] = []

        async def post_fn(**kwargs):
            captured.append(dict(kwargs))
            return {"id": 7}

        outcome = _run(
            post_agent_comment(
                thread_id=11,
                role="engineering-agent/qa-engineer",
                collected_materials=("자료 한 건",),
                interpretation="짧은 해석",
                next_actions=("회귀 테스트 추가",),
                post_message_fn=post_fn,
            )
        )
        self.assertTrue(outcome.posted)
        self.assertEqual(len(captured), 1)
        self.assertLessEqual(len(captured[0]["content"]), 1900)


class DecisionAndKickoffCapTests(unittest.TestCase):
    """tech-lead synthesis decision comment + the member-bots kickoff
    directive both run through chunk_for_discord_message so even very
    long synthesis text never exceeds 1900."""

    def test_decision_comment_is_chunked(self) -> None:
        from types import SimpleNamespace
        from yule_orchestrator.agents.research.loop import _post_decision_comment

        synthesis = SimpleNamespace(
            consensus="이번 릴리스는 starter 본문 캡 + thread 분할 게시 방식을 채택한다.",
        )
        synthesis_text = "매우 긴 합의안 본문 — 합의 / 해야 할 일 / 더 조사할 것. " * 300
        captured: list[dict] = []

        async def post_fn(**kwargs):
            captured.append(dict(kwargs))
            return {"id": len(captured)}

        outcome = _run(
            _post_decision_comment(
                thread_id=21,
                synthesis=synthesis,
                synthesis_text=synthesis_text,
                post_message_fn=post_fn,
            )
        )
        self.assertTrue(outcome.posted)
        self.assertGreater(len(captured), 1)
        for call in captured:
            self.assertEqual(call["thread_id"], 21)
            self.assertLessEqual(len(call["content"]), 1900)

    def test_kickoff_directive_is_chunked(self) -> None:
        from types import SimpleNamespace
        import yule_orchestrator.agents.research.loop as research_loop

        # Force ``research_open_call_directive`` to return a huge body
        # so we exercise the kickoff chunker even for extreme directives.
        original_module = research_loop._post_research_kickoff_comment

        async def fake_post(**kwargs):
            captured.append(dict(kwargs))
            return {"id": 1}

        captured: list[dict] = []

        # Patch the lazy import target inside _post_research_kickoff_comment.
        from yule_orchestrator.discord import engineering_team_runtime

        original_directive = engineering_team_runtime.research_open_call_directive

        def big_directive(_session):
            return "directive line. " * 500

        engineering_team_runtime.research_open_call_directive = big_directive
        try:
            session = SimpleNamespace(
                session_id="abc",
                role_sequence=("engineering-agent/tech-lead",),
                channel_id=1,
                thread_id=2,
                task_type="research",
                executor_role="tech-lead",
            )
            outcome = _run(
                original_module(
                    thread_id=33,
                    session=session,
                    post_message_fn=fake_post,
                )
            )
            self.assertTrue(outcome.posted)
            self.assertGreater(len(captured), 1)
            for call in captured:
                self.assertEqual(call["thread_id"], 33)
                self.assertLessEqual(len(call["content"]), 1900)
        finally:
            engineering_team_runtime.research_open_call_directive = (
                original_directive
            )


class FallbackAndStatusCapTests(unittest.TestCase):
    """Status messages and fallback markdown ride channel.send through
    _send_channel_message_chunks → split_discord_message, which already
    caps at 1900. This test pins the contract so a future refactor that
    bypasses the chunker breaks loudly."""

    def test_long_fallback_markdown_chunks_under_1900(self) -> None:
        from yule_orchestrator.discord.research_forum import (
            format_thread_markdown_fallback,
        )

        pack = _huge_pack()
        markdown = format_thread_markdown_fallback(
            pack,
            title=pack.title,
            posted_by="bot:test",
            reason="forum 권한 없음",
        )
        self.assertGreater(len(markdown), 5000)

        for piece in split_discord_message(markdown, limit=1900):
            self.assertLessEqual(len(piece), 1900)

    def test_long_status_message_chunks_under_1900(self) -> None:
        # The forum_status_message in EngineeringResearchLoopReport rides
        # through send_chunks → split_discord_message. Simulate a very
        # long status with embedded fallback markdown to confirm the cap.
        long_status = (
            "⚠️ 운영-리서치 forum 게시 실패 — fallback markdown:\n"
            + ("매우 긴 fallback 본문. " * 800)
        )
        for piece in split_discord_message(long_status, limit=1900):
            self.assertLessEqual(len(piece), 1900)


class MemberBotChannelSendCapTests(unittest.TestCase):
    """member_bot._post_team_turn / _post_research_turn run their
    payload through chunk_for_discord_message before channel.send so a
    long take never overflows the 2000-char ``content`` validator."""

    def test_team_turn_long_post_is_chunked(self) -> None:
        from types import SimpleNamespace
        from yule_orchestrator.discord.member.bot import _post_team_turn

        long_post = "라" * 5000

        class _FakeOutcome:
            turn = SimpleNamespace(session_id="abc", role="backend-engineer")

            def full_post(self) -> str:
                return long_post

        captured: list[str] = []

        class _FakeChannel:
            async def send(self, content: str) -> None:
                captured.append(content)

        # Avoid touching the real session cache from the persistence helper.
        from yule_orchestrator.discord.member import bot as member_bot_mod

        original_persist = member_bot_mod._mark_team_turn_persisted
        member_bot_mod._mark_team_turn_persisted = lambda *_a, **_kw: None
        try:
            _run(_post_team_turn(_FakeChannel(), _FakeOutcome()))
        finally:
            member_bot_mod._mark_team_turn_persisted = original_persist

        self.assertGreater(len(captured), 1)
        for piece in captured:
            self.assertLessEqual(len(piece), 1900)

    def test_research_turn_long_post_is_chunked(self) -> None:
        from types import SimpleNamespace
        from yule_orchestrator.discord.member.bot import _post_research_turn

        long_message = "z" * 4500
        outcome = SimpleNamespace(message=long_message)

        captured: list[str] = []

        class _FakeChannel:
            async def send(self, content: str) -> None:
                captured.append(content)

        _run(_post_research_turn(_FakeChannel(), outcome))
        self.assertGreater(len(captured), 1)
        for piece in captured:
            self.assertLessEqual(len(piece), 1900)


if __name__ == "__main__":
    unittest.main()
