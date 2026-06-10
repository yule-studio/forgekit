"""Discord interaction plumbing shared by every slash-command group.

These helpers are pure transport concerns — deferring an interaction,
chunking a followup message, and surfacing an unexpected error back to
the user — so both the planning and engineering command groups reuse
them without dragging in each other's domain logic.

Split out of ``commands/__init__.py`` (command-group split): the facade
keeps the public registration API, while the per-group registration
bodies and these shared helpers live in sibling modules.
"""

from __future__ import annotations

from typing import Any

from ..ui.formatter import split_discord_message


async def _safe_defer(
    interaction: "discord.Interaction",
    *,
    discord_module: Any,
) -> bool:
    try:
        await interaction.response.defer(thinking=True)
    except discord_module.NotFound:
        print(
            "warning: discord interaction expired before defer could complete "
            f"(command={getattr(interaction.command, 'name', 'unknown')}, "
            f"user_id={getattr(interaction.user, 'id', 'unknown')})"
        )
        return False
    return True


async def _send_message_chunks(
    interaction: "discord.Interaction",
    message: str,
    *,
    allowed_mentions: Any,
    discord_module: Any,
) -> None:
    chunks = split_discord_message(message)
    first_chunk, *remaining = chunks
    try:
        await interaction.followup.send(first_chunk, allowed_mentions=allowed_mentions)
        for chunk in remaining:
            await interaction.followup.send(chunk, allowed_mentions=allowed_mentions)
    except discord_module.NotFound:
        print(
            "warning: discord interaction webhook expired before followup could be delivered "
            f"(command={getattr(interaction.command, 'name', 'unknown')}, "
            f"user_id={getattr(interaction.user, 'id', 'unknown')})"
        )


async def _surface_unexpected_engineer_error(
    interaction: "discord.Interaction",
    *,
    command_name: str,
    exc: BaseException,
    discord_module: Any,
) -> None:
    """Surface an unexpected exception via the Discord followup channel.

    Without this, broad exceptions from ``/engineer_*`` handlers bubble
    out before Discord receives any followup, which the Discord client
    displays as a generic "애플리케이션이 응답하지 않았어요" timeout.
    Operators then have no signal as to which command failed or why.

    Best-effort: we try ``followup.send`` first (interaction was already
    deferred in the happy path), fall back to ``response.send_message``
    if it wasn't, and as a last resort log to stderr so the failure
    isn't silent.
    """

    del discord_module  # API parity with other helpers; not needed here.
    text = (
        f"⚠️ `/{command_name}` 처리 중 예상치 못한 오류가 발생했어요.\n"
        f"`{type(exc).__name__}`: {exc}"
    )
    delivered = False
    try:
        await interaction.followup.send(text)
        delivered = True
    except Exception:  # noqa: BLE001 - fall through to response.send_message
        pass
    if not delivered:
        try:
            await interaction.response.send_message(text)
            delivered = True
        except Exception:  # noqa: BLE001 - last-resort logging below
            pass
    if not delivered:
        print(
            "error: failed to surface unexpected /"
            f"{command_name} error to Discord: {type(exc).__name__}: {exc}"
        )
