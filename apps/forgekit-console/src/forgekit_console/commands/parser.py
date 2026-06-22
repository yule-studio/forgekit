"""Slash-command parser + palette matcher — pure.

The prompt accepts either a ``/command [args...]`` line or free text. Parsing is
deliberately tiny and side-effect free so it is trivially testable; the router
decides what each parsed input means.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from ..models import ParsedInput
from .registry import SlashCommand, load_commands


def parse_input(raw: str) -> ParsedInput:
    """Parse one prompt line into a :class:`ParsedInput`.

    A line that starts with ``/`` is a slash command — the first token (minus
    the slash, lowercased) is the name, the rest are positional args. Anything
    else is free text (``is_slash=False``).
    """

    raw = raw or ""
    stripped = raw.strip()
    if not stripped.startswith("/"):
        return ParsedInput(raw=raw, is_slash=False)
    tokens = stripped[1:].split()
    if not tokens:
        # a lone "/" — treat as an empty slash (palette trigger, no command)
        return ParsedInput(raw=raw, is_slash=True, name="", args=())
    name = tokens[0].lower()
    return ParsedInput(raw=raw, is_slash=True, name=name, args=tuple(tokens[1:]))


def palette_matches(
    raw: str, commands: Optional[Sequence[SlashCommand]] = None
) -> Tuple[SlashCommand, ...]:
    """Return the commands to surface in the palette for the current input.

    Empty when the line isn't a slash. For ``/`` it returns every command; for
    ``/st`` it returns commands whose name starts with ``st``.

    Matching is **prefix-first with a substring fallback**: prefix matches are returned
    as-is (so ``/st`` → ``status`` and ``/p`` → the p-prefixed set keep their order). Only
    when NO command name starts with the query does it fall back to substring matches — so a
    meaningful word that previously dead-ended to an EMPTY palette now reaches the command
    that contains it (``/improve`` → ``self-improve``, ``/blue`` → ``red-blue``,
    ``/observer`` → ``ops-observer``). The fallback never shadows prefix results, so existing
    prefix behaviour is unchanged — it only turns dead-ends into hits.
    """

    raw = (raw or "").strip()
    if not raw.startswith("/"):
        return ()
    prefix = raw[1:].split(" ", 1)[0].lower()
    cmds = tuple(commands) if commands is not None else load_commands()
    if not prefix:
        return cmds
    starts = tuple(c for c in cmds if c.name.startswith(prefix))
    if starts:
        return starts
    # no prefix hit → substring fallback (dead-end → reachable), source order preserved.
    return tuple(c for c in cmds if prefix in c.name)


__all__ = ("parse_input", "palette_matches")
