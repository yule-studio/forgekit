from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import sys
from typing import Any, Optional, Sequence

from yule_orchestrator.agents.workflow_state import load_session, update_session
from ..config import DiscordBotConfig
from ..engineering_channel_router import EngineeringRouteContext
from ..engineering_team_runtime import (
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
    parse_dispatch_marker,
    parse_research_dispatch_marker,
    parse_research_open_marker,
    record_role_turn_event,
)
from ..member.bots import GATEWAY_ROLE_KEY, MemberBotProfile
from ..research_forum import ResearchForumContext, chunk_for_discord_message
from ..ui.typing_indicator import (
    should_type_for_member_research,
    typing_context,
    typing_keepalive,
)


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
            await _dispatch_member_message(profile=profile, message=message)

    bot = MemberBot(command_prefix=commands.when_mentioned, intents=intents)
    print(
        f"starting member bot '{profile.display_label}' (gateway={GATEWAY_ROLE_KEY!r}, "
        f"guild={base_config.guild_id})",
        file=sys.stderr,
    )
    bot.run(profile.token)


def build_member_bot(profile: MemberBotProfile) -> Any:
    """Construct (without running) the member bot for *profile*.

    P0-C (#132): factored out of :func:`run_member_bot` so the
    SIGTERM-aware runner (``run_member_bot_until_shutdown``) and the
    legacy synchronous launcher (``run_member_bot``) share the exact
    same bot wiring. Returns a ``discord.ext.commands.Bot`` subclass
    instance with all on_ready / on_message handlers already attached
    — caller drives it via ``bot.start(token)`` or ``bot.run(token)``.

    The factory raises ``ValueError`` when the profile carries no
    token (same precondition as the legacy ``run_member_bot``); this
    keeps the runtime ``run-service`` path's graceful-disable check
    self-contained — it can verify the precondition before any
    discord.py import work.
    """

    # Re-use the body of run_member_bot. We avoid running the bot
    # synchronously here so the runtime caller can race ``bot.start``
    # against a shutdown event.
    if not profile.active:
        raise ValueError(
            f"{profile.env_key} is required to start {profile.display_label}. "
            f"Add it to .env.local before running this role bot."
        )

    import discord
    from discord.ext import commands

    # The original ``run_member_bot`` is the production wiring path
    # for ``yule discord up``. We replicate the same construction
    # here — keeping it in lockstep with ``run_member_bot`` is
    # essential, otherwise member bots behave differently depending
    # on which launcher started them. The cleanest factoring would
    # extract the closure body; for the P0-C minimal land we mirror
    # it instead so the diff stays reviewable.
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
                return
            # P0-C v2 (#132): shared dispatcher with the dev/test
            # path (sync ``run_member_bot``). Both launchers reach
            # the same engineering_team_runtime handlers.
            await _dispatch_member_message(profile=profile, message=message)

    return MemberBot(command_prefix=commands.when_mentioned, intents=intents)


async def run_member_bot_until_shutdown(
    *,
    profile: MemberBotProfile,
    shutdown_event: asyncio.Event,
    bot_factory: Optional[Any] = None,
) -> None:
    """SIGTERM-aware member-bot runner — P0-C (#132).

    Mirrors :func:`yule_orchestrator.discord.bot.run_engineering_gateway_until_shutdown`
    so ``yule runtime up`` 's subprocess supervisor can drive a member
    bot the same way it drives the gateway. The runtime owns the main
    loop, so discord.py's internal signal handlers never fire — this
    helper races ``bot.start(token)`` against *shutdown_event* and
    issues ``await bot.close()`` on SIGTERM for a graceful disconnect.

    *bot_factory* defaults to :func:`build_member_bot`; tests can pass
    a fake to exercise the shutdown race without discord.py.

    Returns when the bot exits cleanly or the shutdown event fires.
    Login failures raise the same way the legacy ``bot.run`` did.
    """

    if not profile.active:
        raise ValueError(
            f"{profile.env_key} is required to start {profile.display_label}. "
            f"Add it to .env.local before running this role bot."
        )

    import discord

    factory = bot_factory if bot_factory is not None else lambda: build_member_bot(profile)
    bot = factory()

    async def _waiter() -> None:
        await shutdown_event.wait()
        try:
            await bot.close()
        except Exception:  # noqa: BLE001 - graceful close best-effort
            pass

    waiter_task = asyncio.create_task(_waiter())
    try:
        await bot.start(profile.token)
    except discord.LoginFailure as exc:
        raise ValueError(
            f"member bot '{profile.display_label}' login failed — token in "
            f"{profile.env_key} is rejected by Discord. Regenerate the token "
            "in the Discord developer portal and update .env.local."
        ) from exc
    finally:
        waiter_task.cancel()
        try:
            await waiter_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


async def _dispatch_member_message(
    *,
    profile: MemberBotProfile,
    message: Any,
) -> None:
    """Member-bot ``on_message`` dispatch — shared across both launchers.

    P0-C v2 (#132 / #134): hoisted from the inline closure in
    :func:`run_member_bot` so the runtime path (``yule runtime up``
    via :func:`build_member_bot` + :func:`run_member_bot_until_shutdown`)
    and the dev/test path (``yule discord up`` via :func:`run_member_bot`)
    invoke the **same** dispatcher. Before this refactor the runtime
    path called a no-op placeholder and member bots logged in but
    silently ignored every ``[research-*/team-*]`` directive — see
    ``docs/runtime-member-bot-dispatch-parity.md`` for the full audit.

    The dispatch order is:

      1. ``handle_research_turn_message`` — research-open / research-turn
         marker. Returns ``None`` for inactive roles (already excluded
         via ``session.extra['active_research_roles']``) so this bot
         stays silent on broadcasts not addressed to it.
      2. ``handle_team_turn_message`` — team-turn marker. Same
         "outcome is None → silent" contract.

    Each ``_post_*`` call runs inside a typing context so the
    member bot's account shows "입력 중..." while composing. Failures
    surface as a ``⚠️`` message in the same channel to avoid leaving
    the user staring at a frozen typing indicator. The 1-shot
    ``typing_context`` here is upgraded to ``typing_keepalive`` in a
    follow-up commit (commit 4 of this PR) to cover 8-15s chained
    synthesis without the ~10s Discord typing fade.
    """

    text = message.content or ""

    # P0-F: cheap pre-parse → identify which dispatch path *might*
    # fire and bail early for ignored / not-for-me messages. The
    # parse_*_marker helpers are simple regex (~microseconds) so we
    # can run all three before deciding. This pre-gate is what
    # lets us wrap the *entire* expensive handler in typing_keepalive
    # (the handlers load_session + run deliberation + queue
    # synthesis — easily 5-15s — so wrapping only the post means
    # the indicator stays dark for most of that time).
    research_marker = parse_research_dispatch_marker(text)
    research_open_sid = parse_research_open_marker(text)
    team_marker = parse_dispatch_marker(text)

    # `(session_id, role_or_None)` shape for the two role-specific markers.
    # research-open is role-less so the bot processes every open
    # broadcast for its session_id; the handler then enforces the
    # active_research_roles guard.
    research_role = research_marker[1] if research_marker else None
    team_role = team_marker[1] if team_marker else None

    pre_gate_research = (
        research_marker is not None
        and (research_role is None or research_role == profile.role)
    ) or research_open_sid is not None
    pre_gate_team = (
        team_marker is not None
        and (team_role is None or team_role == profile.role)
    )
    if not pre_gate_research and not pre_gate_team:
        # No marker for us — silent (typing not shown).
        return

    # Resolve session anchor for the defense-in-depth typing gate
    # (P0-E). Cheap session_id extraction from the matched marker.
    session_id_for_gate: Optional[str] = None
    if research_marker is not None:
        session_id_for_gate = research_marker[0]
    elif research_open_sid is not None:
        session_id_for_gate = research_open_sid
    elif team_marker is not None:
        session_id_for_gate = team_marker[0]

    active_roles = _resolve_active_roles_for_typing_gate(session_id_for_gate)
    will_type = should_type_for_member_research(
        role=profile.role,
        active_roles=active_roles,
        will_post=True,
    )

    # P0-F: typing_keepalive now wraps the *entire* expensive path
    # (handler invocation + post). Previously it only wrapped the
    # post itself, so the 8-15s deliberation / synthesis re-render
    # inside handle_*_turn_message ran with no typing indicator.
    if will_type:
        async with typing_keepalive(
            message.channel,
            interval=6.0,
            label="member:dispatch",
        ):
            await _run_member_dispatch(
                profile=profile,
                message=message,
                text=text,
                try_research=pre_gate_research,
                try_team=pre_gate_team,
            )
    else:
        await _run_member_dispatch(
            profile=profile,
            message=message,
            text=text,
            try_research=pre_gate_research,
            try_team=pre_gate_team,
        )


async def _run_member_dispatch(
    *,
    profile: MemberBotProfile,
    message: Any,
    text: str,
    try_research: bool,
    try_team: bool,
) -> None:
    """Run the actual handlers + posts. Caller owns the typing wrap.

    The pre-gate in :func:`_dispatch_member_message` already decided
    which markers might apply; here we just invoke the matching
    handlers and post. Errors land as ⚠️ messages in the same
    channel so a user never sees a frozen typing indicator.
    """

    # Research-turn first — marker takes precedence when both land.
    if try_research:
        try:
            research_outcome = handle_research_turn_message(
                role=profile.role,
                text=text,
            )
        except Exception as exc:  # noqa: BLE001
            research_outcome = None
            print(
                f"warning: member bot '{profile.display_label}' "
                f"research handler failed: {exc}",
                file=sys.stderr,
            )
        if research_outcome is not None:
            try:
                await _post_research_turn(message.channel, research_outcome)
            except Exception as exc:  # noqa: BLE001
                try:
                    await message.channel.send(
                        f"⚠️ {profile.display_label} 댓글 게시 실패: {exc}"
                    )
                except Exception:  # noqa: BLE001
                    pass
            return

    if try_team:
        try:
            team_outcome = handle_team_turn_message(
                role=profile.role,
                text=text,
            )
        except Exception as exc:  # noqa: BLE001
            team_outcome = None
            print(
                f"warning: member bot '{profile.display_label}' "
                f"team handler failed: {exc}",
                file=sys.stderr,
            )
        if team_outcome is None:
            return
        try:
            await _post_team_turn(message.channel, team_outcome)
        except Exception as exc:  # noqa: BLE001
            try:
                await message.channel.send(
                    f"⚠️ {profile.display_label} take 게시 실패: {exc}"
                )
            except Exception:  # noqa: BLE001
                pass


def _resolve_active_roles_for_typing_gate(
    session_id: Optional[str],
) -> Optional[tuple]:
    """Best-effort load of persisted active_research_roles for typing gate.

    Returns ``None`` when the session can't be loaded or has no
    explicit ``active_research_roles`` metadata — caller passes
    ``None`` to :func:`should_type_for_member_research` which
    interprets it as legacy fallback (helper returns True so typing
    follows the handler's outcome contract).

    Never raises — typing must not break dispatch.
    """

    if not session_id:
        return None
    try:
        session = load_session(session_id)
    except Exception:  # noqa: BLE001 - typing decision must never crash
        return None
    if session is None:
        return None
    extra = getattr(session, "extra", None)
    if not isinstance(extra, dict):
        return None
    raw_roles = extra.get("active_research_roles")
    if not raw_roles:
        return None
    cleaned = tuple(
        str(role).strip()
        for role in raw_roles
        if isinstance(role, str) and str(role).strip()
    )
    return cleaned or None


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
