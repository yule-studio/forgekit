"""Compact intro block — small avatar (left) + brand/version/profile/repo (right).

Claude-style first impression: a small terminal-safe avatar beside a few quiet
lines, nothing more. Pure string composition (no textual) so it's unit-testable;
the avatar is the prebaked asset from :mod:`tui.avatar`.
"""

from __future__ import annotations

from typing import Sequence, Tuple

from . import avatar as avatar_mod
from . import render

# Visible width (terminal cells) of the prebaked avatar — used to pad the gutter
# so the right-hand text column lines up regardless of markup length.
AVATAR_COLS = 18
_GUTTER = "  "


def intro_lines(
    *,
    repo: str,
    version: str,
    profile: str = "operator",
    provider: str = "—",
    avatar_lines: Sequence[str] = (),
) -> Tuple[str, ...]:
    """Compose the intro: avatar column on the left, info column on the right."""

    avatar = tuple(avatar_lines) if avatar_lines else avatar_mod.render_avatar()
    info = (
        f"[b orange1]{render.BRAND}[/b orange1] [dim]v{version}[/dim]",
        f"[dim]{render.TAGLINE}[/dim]",
        f"[dim]provider[/dim] {provider}   [dim]profile[/dim] {profile}",
        f"[dim]{repo}[/dim]",
    )
    rows = max(len(avatar), len(info))
    blank = " " * AVATAR_COLS
    out = []
    for i in range(rows):
        left = avatar[i] if i < len(avatar) else blank
        right = info[i] if i < len(info) else ""
        out.append(f"{left}{_GUTTER}{right}".rstrip())
    return tuple(out)


__all__ = ("AVATAR_COLS", "intro_lines")
