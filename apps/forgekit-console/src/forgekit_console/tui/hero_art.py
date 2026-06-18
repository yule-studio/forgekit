"""Hero art — the wide 56-col unicode-subset icon, treated as HERO art (not avatar).

The 56-col asset is information-dense: forcing it into the tiny intro avatar slot
made the header heavy and ugly. So it is NOT an avatar here — it is *hero / splash*
art shown WIDE on the surfaces that have room (the empty first-impression intro and
the ``/about`` · ``/welcome`` surface). The tiny working-header keeps using the small
``fk`` badge / brand mark (see :mod:`tui.image_renderer`); this module never shrinks
the 56-col art into that slot.

It is plain unicode TEXT art (box / block glyphs), not ANSI — so it needs no escape
state machine, just **defensive cleanup**: drop C0/C1 control bytes, strip any stray
escape sequence, replace undecodable bytes, and bound the size. Raw text is never
written to the terminal as-is; the cleaned lines become a Rich ``Text`` (guarded
import, like :mod:`tui.halfblock`). The original 56-col WIDTH is preserved — we move
the art to a wider surface instead of compressing it.

Asset paths: a raw ``-source`` archive (lossless original) and the runtime
``-56`` asset; ``FORGEKIT_HERO_PATH`` overrides both (tests / operators).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional, Tuple

# Defensive caps — hero art must not be a runaway band on a corrupt/huge asset.
MAX_LINES = 80
MAX_WIDTH = 200

ENV_HERO_PATH = "FORGEKIT_HERO_PATH"

_SOURCE_TXT = "forgekit-hero-source.txt"  # raw archive (lossless original)
_RUNTIME_TXT = "forgekit-hero-56.txt"     # runtime asset (cleaned, 56-col)


def _avatar_assets_dir() -> Path:
    # hero_art.py → tui → forgekit_console ; assets/avatar is a sibling package.
    return Path(__file__).resolve().parent.parent / "assets" / "avatar"


def hero_source_path() -> Path:
    return _avatar_assets_dir() / _SOURCE_TXT


def hero_runtime_path() -> Path:
    return _avatar_assets_dir() / _RUNTIME_TXT


def best_hero_path(env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """Resolve the hero asset: ``FORGEKIT_HERO_PATH`` → runtime ``-56`` → source."""

    environ = os.environ if env is None else env
    override = (environ.get(ENV_HERO_PATH) or "").strip()
    if override:
        p = Path(override)
        return p if p.is_file() else None
    runtime = hero_runtime_path()
    if runtime.is_file():
        return runtime
    src = hero_source_path()
    if src.is_file():
        return src
    return None


def _is_printable(ch: str) -> bool:
    """Keep printable unicode + space; drop C0/C1 controls, DEL, and the ESC byte."""

    o = ord(ch)
    if o < 0x20 or o == 0x7F:  # C0 controls + DEL
        return False
    if 0x80 <= o <= 0x9F:      # C1 controls
        return False
    return True


def sanitize_hero_text(text: str) -> Tuple[str, ...]:
    """Defensively clean raw hero text → safe printable lines (width preserved).

    Drops control bytes and any stray escape sequence (an ESC and the CSI/OSC body
    that may follow it), so nothing executable is ever carried into a renderable.
    Caps line count + width so a corrupt asset can't blow up the layout. Pure + total.
    """

    if not isinstance(text, str):
        return ()
    # normalize newlines, then strip a trailing UTF-8 BOM if present.
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    out = []
    for raw_line in text.split("\n"):
        if len(out) >= MAX_LINES:
            break
        buf = []
        i, n = 0, len(raw_line)
        while i < n and len(buf) < MAX_WIDTH:
            ch = raw_line[i]
            if ch == "\x1b":  # ESC → skip it AND a following CSI/OSC-ish run
                i += 1
                if i < n and raw_line[i] == "[":
                    i += 1
                    while i < n and not (0x40 <= ord(raw_line[i]) <= 0x7E):
                        i += 1
                    i += 1  # consume the final byte
                continue
            if _is_printable(ch):
                buf.append(ch)
            i += 1
        out.append("".join(buf))
    # trim trailing blank lines (art files often pad with a final newline)
    while out and not out[-1].strip():
        out.pop()
    # trim leading blank lines too (centered hero reads better without top padding)
    while out and not out[0].strip():
        out.pop(0)
    return tuple(out)


def load_hero_lines(
    env: Optional[Mapping[str, str]] = None, path: Optional[Path] = None
) -> Optional[Tuple[str, ...]]:
    """Read + sanitize the hero asset → lines, or ``None`` when absent/empty."""

    p = path or best_hero_path(env)
    if p is None:
        return None
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = sanitize_hero_text(raw)
    return lines or None


def hero_available(env: Optional[Mapping[str, str]] = None, path: Optional[Path] = None) -> bool:
    """True when a non-empty hero asset can be loaded (drives the intro mode)."""

    return load_hero_lines(env, path) is not None


def hero_width(lines: Tuple[str, ...]) -> int:
    return max((len(line) for line in lines), default=0)


def hero_renderable(
    env: Optional[Mapping[str, str]] = None, path: Optional[Path] = None
):
    """Build the hero art as a Rich ``Text`` (brand-tinted), or ``None``.

    ``None`` when the asset is missing OR Rich is unavailable (the caller then keeps
    the compact header). The art is rendered in the brand foreground — crisp and
    theme-consistent — with the ORIGINAL width preserved (no compression).
    """

    lines = load_hero_lines(env, path)
    if lines is None:
        return None
    try:
        from rich.text import Text  # noqa: WPS433 - optional console extra
    except Exception:  # noqa: BLE001 - rich missing → caller keeps compact header
        return None
    from . import theme

    text = Text(no_wrap=True, end="", style=theme.FG)
    last = len(lines) - 1
    for i, line in enumerate(lines):
        text.append(line)
        if i != last:
            text.append("\n")
    return text


__all__ = (
    "MAX_LINES",
    "MAX_WIDTH",
    "ENV_HERO_PATH",
    "hero_source_path",
    "hero_runtime_path",
    "best_hero_path",
    "sanitize_hero_text",
    "load_hero_lines",
    "hero_available",
    "hero_width",
    "hero_renderable",
)
