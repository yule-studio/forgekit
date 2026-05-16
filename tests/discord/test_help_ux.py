"""Help + intake-escalation UX regression.

This suite locks in the new conversational entry-point behaviour shipped
on ``fix/engineering-write-reply-routing``:

  1. ``/help`` and ``/engineer_help`` are registered on the engineering
     bot tree and render the canonical help body.
  2. Natural-language help phrases ("help", "도움말", "어떻게 써?",
     "뭘 할 수 있어?", "사용법") route to ``GENERAL_ENGINEERING_HELP``
     and emit the same help body — users never have to learn a slash
     command to find the usage guide.
  3. Status questions ("지금 진행 상황 알려줘") still resolve to
     ``STATUS_DIAGNOSTIC`` instead of being shoved into intake.
  4. Substantive implementation requests still escalate to
     ``TASK_INTAKE_CANDIDATE`` (no regression in the existing intake
     path).
  5. The forced-``/engineer_intake`` fallback copy in
     ``bot/_legacy._default_engineering_conversation_fn`` has been
     replaced with a softer escalation message that always surfaces
     the help body first.
"""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


# ---------------------------------------------------------------------------
# Discord stub shared with test_command_role_split.
# Kept self-contained so this file can run without the real discord client.
# ---------------------------------------------------------------------------


def _install_fake_discord_modules() -> tuple[Any, Any]:
    discord_mod = types.ModuleType("discord")

    class _Object:
        def __init__(self, *, id):
            self.id = id

    class _AllowedMentions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _NotFound(Exception):
        pass

    class _Interaction:  # noqa: D401 — annotation stub only
        pass

    discord_mod.Object = _Object
    discord_mod.AllowedMentions = _AllowedMentions
    discord_mod.NotFound = _NotFound
    discord_mod.Interaction = _Interaction

    ext_mod = types.ModuleType("discord.ext")
    discord_mod.ext = ext_mod

    app_commands_mod = types.ModuleType("discord.app_commands")

    def _describe(**_kwargs):
        def _wrap(fn):
            return fn

        return _wrap

    class _Range:
        def __class_getitem__(cls, _key):
            return int

    app_commands_mod.describe = _describe
    app_commands_mod.Range = _Range
    discord_mod.app_commands = app_commands_mod

    sys.modules["discord"] = discord_mod
    sys.modules["discord.ext"] = ext_mod
    sys.modules["discord.app_commands"] = app_commands_mod
    return discord_mod, app_commands_mod


class _CapturingTree:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def command(self, *, name, description, guild=None):
        del description, guild

        def _decorator(fn):
            self.handlers[name] = fn
            return fn

        return _decorator


class _CapturingBot:
    def __init__(self) -> None:
        self.tree = _CapturingTree()


# ---------------------------------------------------------------------------
# /help and /engineer_help registration
# ---------------------------------------------------------------------------


class HelpSlashCommandRegistrationTests(unittest.TestCase):
    """``/help`` + ``/engineer_help`` are owned by the engineering role."""

    def setUp(self) -> None:
        self._previous_modules = {
            name: sys.modules.get(name)
            for name in ("discord", "discord.ext", "discord.app_commands")
        }
        _install_fake_discord_modules()
        sys.modules.pop("yule_orchestrator.discord.commands", None)
        from yule_orchestrator.discord import commands as commands_module

        self.commands_module = commands_module

    def tearDown(self) -> None:
        for name, previous in self._previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        sys.modules.pop("yule_orchestrator.discord.commands", None)

    def test_engineering_role_registers_help_and_engineer_help(self) -> None:
        bot = _CapturingBot()

        self.commands_module.register_engineering_commands(bot, guild_id=99)

        self.assertIn("help", bot.tree.handlers)
        self.assertIn("engineer_help", bot.tree.handlers)
        # /engineer_intake regression — registration is untouched.
        self.assertIn("engineer_intake", bot.tree.handlers)

    def test_planning_role_does_not_register_help(self) -> None:
        # Planning bot is intentionally not given /help here: avoiding
        # a name collision in guilds that run both bots side-by-side.
        bot = _CapturingBot()

        self.commands_module.register_planning_commands(bot, guild_id=99)

        self.assertNotIn("help", bot.tree.handlers)
        self.assertNotIn("engineer_help", bot.tree.handlers)


# ---------------------------------------------------------------------------
# /help handler emits the canonical help body
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self) -> None:
        self.deferred = False

    async def defer(self, *, thinking: bool = False) -> None:  # noqa: ARG002
        self.deferred = True

    async def send_message(self, text: str) -> None:  # pragma: no cover
        self.last_text = text


class _FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str, allowed_mentions=None) -> None:  # noqa: ARG002
        self.sent.append(text)


class _FakeInteraction:
    def __init__(self) -> None:
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()
        self.channel_id = 11
        self.user = types.SimpleNamespace(id=22)
        self.command = types.SimpleNamespace(name="help")
        self.channel = types.SimpleNamespace(id=11)


class HelpSlashCommandRendersHelpBodyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_modules = {
            name: sys.modules.get(name)
            for name in ("discord", "discord.ext", "discord.app_commands")
        }
        _install_fake_discord_modules()
        sys.modules.pop("yule_orchestrator.discord.commands", None)
        from yule_orchestrator.discord import commands as commands_module

        self.commands_module = commands_module

    def tearDown(self) -> None:
        for name, previous in self._previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        sys.modules.pop("yule_orchestrator.discord.commands", None)

    def test_help_handler_emits_canonical_help_body(self) -> None:
        from yule_orchestrator.discord.engineering.help_surface import (
            render_engineer_help_message,
        )

        bot = _CapturingBot()
        self.commands_module.register_engineering_commands(bot, guild_id=99)
        handler = bot.tree.handlers["help"]

        interaction = _FakeInteraction()
        asyncio.run(handler(interaction))

        joined = "\n".join(interaction.followup.sent)
        self.assertIn("engineering-agent 사용법", joined)
        # The canonical body must mention both modes so users understand
        # the escalation model.
        self.assertIn("자유 대화", joined)
        self.assertIn("intake", joined)
        # And it must mirror the help_surface helper byte-for-byte —
        # /help and the NL help intent share a single source.
        self.assertIn(render_engineer_help_message(), joined)

    def test_engineer_help_handler_renders_same_body(self) -> None:
        bot = _CapturingBot()
        self.commands_module.register_engineering_commands(bot, guild_id=99)
        handler = bot.tree.handlers["engineer_help"]

        interaction = _FakeInteraction()
        interaction.command = types.SimpleNamespace(name="engineer_help")
        asyncio.run(handler(interaction))

        self.assertTrue(interaction.response.deferred)
        joined = "\n".join(interaction.followup.sent)
        self.assertIn("주요 명령", joined)
        self.assertIn("/engineer_intake", joined)


# ---------------------------------------------------------------------------
# Natural-language help intent
# ---------------------------------------------------------------------------


class NaturalLanguageHelpIntentTests(unittest.TestCase):
    """Plain-language help asks resolve to ``GENERAL_ENGINEERING_HELP``."""

    def test_help_triggers_detect_general_help(self) -> None:
        from yule_orchestrator.discord.engineering_conversation import (
            GENERAL_ENGINEERING_HELP,
            detect_engineering_intent,
        )

        for text in (
            "help",
            "도움말",
            "도움",
            "사용법",
            "사용법 알려줘",
            "어떻게 써?",
            "어떻게 사용해?",
            "뭐 할 수 있어?",
            "뭘 할 수 있어?",
            "엔지니어링 봇 도움말 좀 줘봐",
            "what can you do",
            "쓰는 법 알려줘",
            "command list 알려줘",
            "intake 어떻게 써?",
        ):
            with self.subTest(text=text):
                intent = detect_engineering_intent(text)
                self.assertEqual(intent.intent_id, GENERAL_ENGINEERING_HELP)

    def test_general_help_envelope_renders_canonical_body(self) -> None:
        from yule_orchestrator.discord.engineering.help_surface import (
            render_engineer_help_message,
        )
        from yule_orchestrator.discord.engineering_conversation import (
            GENERAL_ENGINEERING_HELP,
            build_engineering_conversation_response,
        )

        envelope = build_engineering_conversation_response("도움말 알려줘")

        self.assertEqual(envelope.intent_id, GENERAL_ENGINEERING_HELP)
        self.assertFalse(envelope.ready_to_intake)
        self.assertFalse(envelope.needs_clarification)
        self.assertIsNone(envelope.intake_prompt)
        self.assertIn(render_engineer_help_message(), envelope.content)


# ---------------------------------------------------------------------------
# Free-conversation vs intake escalation
# ---------------------------------------------------------------------------


class FreeConversationDoesNotForceIntakeTests(unittest.TestCase):
    """Status / help / clarification asks must never set ``ready_to_intake``."""

    def test_status_question_resolves_to_status_diagnostic(self) -> None:
        from yule_orchestrator.discord.engineering_conversation import (
            STATUS_DIAGNOSTIC,
            build_engineering_conversation_response,
        )

        envelope = build_engineering_conversation_response("지금 진행 상황 알려줘")
        self.assertEqual(envelope.intent_id, STATUS_DIAGNOSTIC)
        self.assertFalse(envelope.ready_to_intake)
        self.assertIsNone(envelope.intake_prompt)

    def test_help_question_never_creates_intake(self) -> None:
        from yule_orchestrator.discord.engineering_conversation import (
            build_engineering_conversation_response,
        )

        for text in ("도움말", "어떻게 써?", "help", "사용법"):
            with self.subTest(text=text):
                envelope = build_engineering_conversation_response(text)
                self.assertFalse(envelope.ready_to_intake)
                self.assertIsNone(envelope.intake_prompt)


class SubstantiveRequestStillReachesIntakeTests(unittest.TestCase):
    """Implementation requests must keep their existing TASK_INTAKE_CANDIDATE path."""

    def test_repo_implementation_request_resolves_to_intake_candidate(self) -> None:
        from yule_orchestrator.discord.engineering_conversation import (
            TASK_INTAKE_CANDIDATE,
            build_engineering_conversation_response,
        )

        envelope = build_engineering_conversation_response(
            "codwithyc/yule-studio-agent 에서 /engineer_show 응답 포맷 손보기",
            auto_collect=False,
        )
        self.assertEqual(envelope.intent_id, TASK_INTAKE_CANDIDATE)
        self.assertEqual(
            envelope.intake_prompt,
            "codwithyc/yule-studio-agent 에서 /engineer_show 응답 포맷 손보기",
        )

    def test_explicit_confirmation_still_marks_ready_to_intake(self) -> None:
        from yule_orchestrator.discord.engineering_conversation import (
            CONFIRM_INTAKE,
            build_engineering_conversation_response,
        )

        envelope = build_engineering_conversation_response(
            "이대로 진행",
            last_proposed_prompt="users API 에 email_verified 필드 추가",
        )
        self.assertEqual(envelope.intent_id, CONFIRM_INTAKE)
        self.assertTrue(envelope.ready_to_intake)
        self.assertEqual(
            envelope.intake_prompt,
            "users API 에 email_verified 필드 추가",
        )


# ---------------------------------------------------------------------------
# Legacy forced-intake copy regression
# ---------------------------------------------------------------------------


class ForcedIntakeCopyRegressionTests(unittest.TestCase):
    """No production-facing string may say "지금은 /engineer_intake … 등록해주세요"."""

    BANNED_PHRASES = (
        "지금은 `/engineer_intake` 슬래시 명령으로 작업을 등록해주세요",
        "지금은 `/engineer_intake` 로 작업을 등록해주세요",
    )

    def test_legacy_bot_no_longer_emits_forced_intake_copy(self) -> None:
        import importlib

        legacy = importlib.import_module("yule_orchestrator.discord.bot._legacy")
        source_path = legacy.__file__
        assert source_path  # mypy-style guard
        with open(source_path, encoding="utf-8") as handle:
            text = handle.read()
        for phrase in self.BANNED_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text)

    def test_fallback_outcome_surfaces_help_body_and_softens_intake(self) -> None:
        from yule_orchestrator.discord.engineering.help_surface import (
            render_engineer_help_short,
        )
        from yule_orchestrator.discord.bot._legacy import (
            _build_help_or_intake_fallback,
        )

        outcome = _build_help_or_intake_fallback(reason="테스트 fallback")

        self.assertIn(render_engineer_help_short(), outcome.content)
        # The new copy must offer the option without forcing it.
        self.assertIn("`/engineer_intake", outcome.content)
        self.assertNotIn("등록해주세요", outcome.content)


# ---------------------------------------------------------------------------
# Reject message no longer hard-walls users into /engineer_intake
# ---------------------------------------------------------------------------


class RejectMessageMentionsConversationalOptionTests(unittest.TestCase):
    def test_reject_message_offers_conversation_and_help(self) -> None:
        from yule_orchestrator.discord.commands import (
            _format_engineer_reject_message,
        )

        session = types.SimpleNamespace(
            session_id="sess-1",
            state=types.SimpleNamespace(value="rejected"),
            rejection_reason="scope drift",
        )

        message = _format_engineer_reject_message(session)

        self.assertIn("자연어로 그냥 말씀", message)
        self.assertIn("/engineer_intake", message)
        self.assertIn("/help", message)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
