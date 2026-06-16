"""Command-palette + autocomplete state machine — pure, no textual.

The bottom command palette and Tab autocomplete share one immutable state. The
TUI keeps a :class:`PaletteState`, calls :func:`refilter` as the user types and
:func:`cycle` on Tab / arrows, and reads :func:`completion_text` to reflect the
selected candidate back into the input box. Keeping it pure makes the whole
interaction (open/close, filtering, cycling, completion) unit-testable.

Key distinction: ``refilter`` (genuine typing) rebuilds the match set and resets
the selection; ``cycle`` (Tab / arrow) only moves the selection within the frozen
match set — so cycling through ``/p`` → ``pm-agent`` → ``planning-agent`` works
even as each step rewrites the input text.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence, Tuple

from .parser import palette_matches
from .registry import SlashCommand


@dataclass(frozen=True)
class PaletteState:
    is_open: bool = False
    query: str = ""
    matches: Tuple[SlashCommand, ...] = ()
    index: int = -1  # -1 = nothing selected yet (showing the filtered list)


CLOSED = PaletteState()


def refilter(query: str, commands: Optional[Sequence[SlashCommand]] = None) -> PaletteState:
    """Rebuild the palette for *query*. Closed unless the line is a slash."""

    if not (query or "").strip().startswith("/"):
        return CLOSED
    matches = palette_matches(query, commands)
    return PaletteState(is_open=True, query=query, matches=matches, index=-1)


def cycle(state: PaletteState, direction: int) -> PaletteState:
    """Move the selection by *direction* (+1 next, -1 previous), wrapping."""

    if not state.is_open or not state.matches:
        return state
    n = len(state.matches)
    if state.index == -1:
        new_index = 0 if direction >= 0 else n - 1
    else:
        new_index = (state.index + direction) % n
    return replace(state, index=new_index)


def selected(state: PaletteState) -> Optional[SlashCommand]:
    """The currently highlighted command, or None."""

    if 0 <= state.index < len(state.matches):
        return state.matches[state.index]
    return None


def completion_text(state: PaletteState) -> Optional[str]:
    """Text to reflect into the input for the active candidate.

    Uses the selection when present, else the first match (so a bare Tab on
    ``/he`` completes to ``/help ``). None when there is nothing to complete.
    """

    cmd = selected(state)
    if cmd is None and state.matches:
        cmd = state.matches[0]
    if cmd is None:
        return None
    return f"/{cmd.name} "


def close() -> PaletteState:
    """Collapse the palette."""

    return CLOSED


__all__ = (
    "PaletteState",
    "CLOSED",
    "refilter",
    "cycle",
    "selected",
    "completion_text",
    "close",
)
