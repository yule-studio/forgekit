from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sys
from typing import Any, Optional, Sequence

from ..agents.workflow_state import load_session, update_session
from .config import DiscordBotConfig
from .engineering_channel_router import EngineeringRouteContext
from .engineering_team_runtime import (
    ROLE_TURN_KIND_OPEN,
    ROLE_TURN_KIND_SYNTHESIS,
    ROLE_TURN_KIND_TURN,
    ROLE_TURN_STATUS_ERROR,
    ROLE_TURN_STATUS_POSTED,
    ResearchTurnOutcome,
    TeamTurnOutcome,
    handle_research_turn_message,
    handle_team_turn_message,
    mark_turn_played,
    record_role_turn_event,
)
from .member_bots import GATEWAY_ROLE_KEY, MemberBotProfile
from .research_forum import ResearchForumContext, chunk_for_discord_message
from .typing_indicator import typing_context


@dataclass(frozen=True)
class _PermissionTarget:
    label: str
    channel_id: Optional[int]
    channel_name: Optional[str]
    env_hint: str

    @property
    def configured(self) -> bool:
        return self.channel_id is not None or bool((self.channel_name or "").strip())


_MEMBER_BOT_REQUIRED_CHANNEL_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("view_channel", "View Channel"),
    ("read_message_history", "Read Message History"),
    ("send_messages", "Send Messages"),
    ("send_messages_in_threads", "Send Messages in Threads"),
)


def run_member_bot(profile: MemberBotProfile) -> None:
    """Run a single member persona bot using its dedicated token.

    Behavior:

    1. Log in and announce identity (still useful for ops).
    2. Listen for ``[team-turn:<session_id> <role>]`` dispatch directives in
       the channels/threads the bot can see. When the directive targets
       this role, the bot posts the role's scripted opening turn into the
       same channel and appends the next directive so the chain continues.

    The actual conversation logic lives in
    :mod:`engineering_team_runtime`; this function is the Discord wrapper.
    """

    if not profile.active:
        raise ValueError(
            f"{profile.env_key} is required to start {profile.display_label}. "
            f"Add it to .env.local before running this role bot."
        )

    import discord
    from discord.ext import commands

    base_config = DiscordBotConfig.from_env()
    intents = discord.Intents.default()
    intents.message_content = True
    intents.messages = True

    class MemberBot(commands.Bot):
        async def on_ready(self) -> None:
            user_text = str(self.user) if self.user is not None else "unknown-user"
            print(
                f"member bot '{profile.display_label}' logged in as {user_text} "
                f"(guild={base_config.guild_id})",
                file=sys.stderr,
            )
            for line in _member_bot_startup_permission_lines(
                profile=profile,
                bot=self,
                guild_id=base_config.guild_id,
                targets=_member_bot_permission_targets_from_env(),
            ):
                print(line, file=sys.stderr)

        async def on_message(self, message: "discord.Message") -> None:  # noqa: D401 - discord callback
            if message.author == self.user:
                return
            if profile.role == GATEWAY_ROLE_KEY:
                # Gateway bot has its own conversation handlers in bot.py;
                # never let the member-bot loop process gateway traffic.
                return

            text = message.content or ""

            # Research-turn (운영-리서치 forum thread) takes precedence
            # because research markers and team markers can both land in
            # threads the bot can see. We process whichever shows up.
            # Compose + post happens inside a typing context so the
            # member bot's account shows ``입력 중...`` while its take is
            # being assembled.
            #
            # tech-lead role: when ``profile.role == 'tech-lead'`` and the
            # marker is ``RESEARCH_SYNTHESIS_ROLE``, this same wrap covers
            # the synthesis comment so the tech-lead bot account types in
            # the forum thread. The gateway-side legacy synthesis path is
            # covered by the typing wrap in bot.py:on_message.
            research_outcome = handle_research_turn_message(
                role=profile.role,
                text=text,
            )
            if research_outcome is not None:
                async with typing_context(message.channel):
                    await _post_research_turn(message.channel, research_outcome)
                return

            team_outcome = handle_team_turn_message(
                role=profile.role,
                text=text,
            )
            if team_outcome is None:
                return

            async with typing_context(message.channel):
                await _post_team_turn(message.channel, team_outcome)

    bot = MemberBot(command_prefix=commands.when_mentioned, intents=intents)
    print(
        f"starting member bot '{profile.display_label}' (gateway={GATEWAY_ROLE_KEY!r}, "
        f"guild={base_config.guild_id})",
        file=sys.stderr,
    )
    bot.run(profile.token)


def _member_bot_permission_targets_from_env() -> tuple[_PermissionTarget, ...]:
    forum = ResearchForumContext.from_env()
    engineering = EngineeringRouteContext.from_env()
    return (
        _PermissionTarget(
            label="운영-리서치 forum",
            channel_id=forum.channel_id,
            channel_name=forum.channel_name,
            env_hint="DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_*",
        ),
        _PermissionTarget(
            label="업무-접수 thread parent",
            channel_id=engineering.intake_channel_id,
            channel_name=engineering.intake_channel_name,
            env_hint="DISCORD_ENGINEERING_INTAKE_CHANNEL_*",
        ),
    )


def _member_bot_startup_permission_lines(
    *,
    profile: MemberBotProfile,
    bot: Any,
    guild_id: int,
    targets: Sequence[_PermissionTarget],
) -> tuple[str, ...]:
    if profile.role == GATEWAY_ROLE_KEY:
        return ()

    lines: list[str] = [
        (
            f"info: member bot '{profile.display_label}' requires Discord Developer "
            "Portal Message Content Intent enabled; this portal toggle cannot be "
            "verified from the runtime."
        )
    ]

    guild = _resolve_member_bot_guild(bot, guild_id)
    if guild is None:
        lines.append(
            f"warning: member bot '{profile.display_label}' cannot resolve guild "
            f"{guild_id}; channel permission checks skipped."
        )
        return tuple(lines)

    member = getattr(guild, "me", None)
    if member is None:
        lines.append(
            f"warning: member bot '{profile.display_label}' cannot resolve its guild "
            "member object; channel permission checks skipped."
        )
        return tuple(lines)

    for target in targets:
        lines.extend(
            _member_bot_permission_lines_for_target(
                profile=profile,
                bot=bot,
                guild=guild,
                member=member,
                target=target,
            )
        )
    return tuple(lines)


def _member_bot_permission_lines_for_target(
    *,
    profile: MemberBotProfile,
    bot: Any,
    guild: Any,
    member: Any,
    target: _PermissionTarget,
) -> tuple[str, ...]:
    if not target.configured:
        return (
            f"warning: {target.env_hint} is not configured; member bot "
            f"'{profile.display_label}' cannot verify {target.label} access.",
        )

    channel = _resolve_member_bot_channel(bot=bot, guild=guild, target=target)
    target_text = _permission_target_text(target)
    if channel is None:
        return (
            f"warning: member bot '{profile.display_label}' cannot resolve "
            f"{target.label} channel {target_text}; it will not see dispatch markers there.",
        )

    try:
        permissions = channel.permissions_for(member)
    except Exception as exc:  # noqa: BLE001
        return (
            f"warning: member bot '{profile.display_label}' cannot inspect "
            f"{target.label} permissions for {target_text}: {exc}",
        )

    missing = [
        label
        for attr, label in _MEMBER_BOT_REQUIRED_CHANNEL_PERMISSIONS
        if not bool(getattr(permissions, attr, False))
    ]
    if missing:
        return (
            f"warning: member bot '{profile.display_label}' missing "
            f"{target.label} permissions for {target_text}: {', '.join(missing)}",
        )
    return (
        f"info: member bot '{profile.display_label}' {target.label} permissions OK "
        f"for {target_text}.",
    )


def _resolve_member_bot_guild(bot: Any, guild_id: int) -> Any:
    getter = getattr(bot, "get_guild", None)
    if callable(getter):
        guild = getter(guild_id)
        if guild is not None:
            return guild
    for guild in getattr(bot, "guilds", ()) or ():
        if getattr(guild, "id", None) == guild_id:
            return guild
    return None


def _resolve_member_bot_channel(
    *,
    bot: Any,
    guild: Any,
    target: _PermissionTarget,
) -> Any:
    if target.channel_id is not None:
        for owner in (bot, guild):
            getter = getattr(owner, "get_channel", None)
            if callable(getter):
                channel = getter(target.channel_id)
                if channel is not None:
                    return channel

    wanted_name = _normalize_channel_name(target.channel_name)
    if wanted_name:
        for channel in _iter_member_bot_channels(bot, guild):
            if _normalize_channel_name(getattr(channel, "name", None)) == wanted_name:
                return channel
    return None


def _iter_member_bot_channels(bot: Any, guild: Any) -> tuple[Any, ...]:
    channels: list[Any] = []
    for owner in (guild, bot):
        for attr in ("channels", "forums"):
            for channel in getattr(owner, attr, ()) or ():
                if channel not in channels:
                    channels.append(channel)
        getter = getattr(owner, "get_all_channels", None)
        if callable(getter):
            for channel in getter() or ():
                if channel not in channels:
                    channels.append(channel)
    return tuple(channels)


def _permission_target_text(target: _PermissionTarget) -> str:
    if target.channel_id is not None:
        return f"`{target.channel_id}`"
    if target.channel_name:
        return f"`#{target.channel_name}`"
    return "`<unconfigured>`"


def _normalize_channel_name(value: Any) -> str:
    return str(value or "").strip().lstrip("#").lower()


async def _post_team_turn(channel, outcome: TeamTurnOutcome) -> None:
    """Send the rendered turn (and chain directive, if any) into *channel*.

    Extracted so tests can drive the post path without a live Discord
    client. Long takes get chunked at ≤ 1900 chars per send so Discord's
    50035 ``content`` validator never rejects a turn for being too long.
    """

    body = outcome.full_post()
    for piece in chunk_for_discord_message(body) or (body,):
        await channel.send(piece)
    _mark_team_turn_persisted(outcome)


async def _post_research_turn(channel, outcome: ResearchTurnOutcome) -> None:
    """Send a research-forum turn comment into *channel*.

    The render already embeds the next directive (``[research-turn:...]``)
    when applicable, so each member bot's comment naturally hands off to
    the next role bot without the gateway impersonating anyone. Long
    takes get chunked the same way as ``_post_team_turn`` so a verbose
    take never trips Discord's per-message limit.

    After the post lands, the bot records a role-turn event under
    ``session.extra["role_turns"][<role>]`` so the gateway diagnostic
    responder can describe which roles actually spoke. Persistence
    failure is silenced (``record_role_turn_event`` swallows internally)
    so a logging miss never blocks the Discord post.
    """

    body = outcome.message
    # Pull recorder-relevant fields defensively so the test/dev seams that
    # pass a partial outcome (e.g. SimpleNamespace with only ``message``)
    # still go through the chunk path. We only record an event when both
    # session_id and role are present — otherwise the recorder has nothing
    # to anchor against.
    session_id = getattr(outcome, "session_id", None) or ""
    role = getattr(outcome, "role", None) or ""
    kind = _research_turn_event_kind(outcome)
    try:
        for piece in chunk_for_discord_message(body) or (body,):
            await channel.send(piece)
    except Exception as exc:  # noqa: BLE001 - record failure then re-raise
        if session_id and role:
            record_role_turn_event(
                session_id=session_id,
                role=role,
                kind=kind,
                status=ROLE_TURN_STATUS_ERROR,
                error=str(exc),
            )
        raise
    if session_id and role:
        record_role_turn_event(
            session_id=session_id,
            role=role,
            kind=kind,
            status=ROLE_TURN_STATUS_POSTED,
        )


def _research_turn_event_kind(outcome: Any) -> str:
    """Pick the role-turn event ``kind`` based on the outcome shape.

    - ``synthesis`` for the closing tech-lead comment.
    - ``open`` for open-call replies (no chained next directive).
    - ``turn`` for legacy chained dispatch turns.

    Tolerant of partial outcomes (``SimpleNamespace`` with only
    ``message`` set, used by chunk-cap tests) — defaults to ``open`` in
    that case so the chunk-cap test path still hits the chunker.
    """

    if getattr(outcome, "is_synthesis", False):
        return ROLE_TURN_KIND_SYNTHESIS
    if getattr(outcome, "next_directive", None) is None:
        return ROLE_TURN_KIND_OPEN
    return ROLE_TURN_KIND_TURN


def _mark_team_turn_persisted(outcome: TeamTurnOutcome) -> None:
    """Best-effort guard against a member bot posting the same turn twice."""

    try:
        session = load_session(outcome.turn.session_id)
        if session is None:
            return
        updated = mark_turn_played(session, outcome.turn.role)
        update_session(updated, now=datetime.now().astimezone())
    except Exception:  # noqa: BLE001 - posting already succeeded; never crash the bot
        return
