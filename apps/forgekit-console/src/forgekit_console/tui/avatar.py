"""Forgekit brand avatar — terminal-safe, always renders something.

Tiers (highest first), per the "always works, prettier if supported" goal:

  1. **baked asset** — a pre-converted Rich-markup half-block rendering of the
     brand image shipped in ``assets/forgekit-avatar.txt``. Needs no image lib at
     runtime; this is the default rich rendering.
  2. **runtime Pillow** — if Pillow is installed (the ``console`` extra) and a
     source image path is given, render it live at the requested width.
  3. **text brandmark** — a hand-built Unicode mark that needs nothing at all.

:func:`render_avatar` never raises and never returns empty — a missing asset /
absent Pillow / unreadable image all fall through to the text brandmark.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

_ASSET_NAME = "forgekit-avatar.txt"

# Always-available fallback: a small forge/anvil "FK" mark in Unicode blocks.
_TEXT_BRANDMARK: Tuple[str, ...] = (
    "[orange1]    ███████╗ ██╗  ██╗[/]",
    "[orange1]    ██╔════╝ ██║ ██╔╝[/]",
    "[orange3]    █████╗   █████╔╝ [/]",
    "[orange3]    ██╔══╝   ██╔═██╗ [/]",
    "[dark_orange]    ██║      ██║  ██╗[/]",
    "[dark_orange]    ╚═╝      ╚═╝  ╚═╝[/]",
    "[dim]      forge · kit[/]",
)


def _asset_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / _ASSET_NAME


def text_brandmark() -> Tuple[str, ...]:
    """The block-letter fallback mark (Rich markup lines, ~7 rows)."""

    return _TEXT_BRANDMARK


def mini_brandmark() -> Tuple[str, ...]:
    """A small, crisp, terminal-safe wordmark — the *default* console mark.

    Two lines only, box-drawing-free in the wordmark itself so it renders
    cleanly on any terminal. Deliberately not a raster image: a pixelated photo
    avatar must never be the first impression, so the app shows this by default
    and only renders the baked image on explicit opt-in.
    """

    return (
        "[b orange1]forge[/b orange1][b orange3]kit[/b orange3] [dim]▸ operator console[/dim]",
        "[dim]content-first terminal ops console[/dim]",
    )


def load_baked_asset() -> Optional[Tuple[str, ...]]:
    """Return the pre-baked Rich-markup avatar lines, or None if absent."""

    path = _asset_path()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lines = tuple(line for line in text.splitlines() if line)
    return lines or None


def render_from_image(image_path: Path, *, cols: int = 28) -> Optional[Tuple[str, ...]]:
    """Live-render *image_path* to half-block Rich markup, or None on any miss.

    Optional path — only used when Pillow (``console`` extra) is present.
    """

    try:
        from PIL import Image  # noqa: WPS433 (optional dep)
    except Exception:  # noqa: BLE001 - Pillow not installed → fall through
        return None
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:  # noqa: BLE001 - unreadable image
        return None
    w, h = img.size
    if w <= 0 or h <= 0:
        return None
    rows_px = int(cols * (h / w))
    rows_px -= rows_px % 2
    if rows_px < 2:
        rows_px = 2
    img = img.resize((cols, rows_px))
    px = img.load()
    clamp = lambda v: max(0, min(255, int(v)))  # noqa: E731
    lines = []
    for y in range(0, rows_px, 2):
        parts = []
        for x in range(cols):
            tr, tg, tb = px[x, y]
            br, bg, bb = px[x, y + 1]
            parts.append(
                f"[rgb({clamp(tr)},{clamp(tg)},{clamp(tb)}) on "
                f"rgb({clamp(br)},{clamp(bg)},{clamp(bb)})]▀[/]"
            )
        lines.append("".join(parts))
    return tuple(lines) or None


def render_avatar(*, image_path: Optional[Path] = None, prefer_live: bool = False) -> Tuple[str, ...]:
    """Return the best available avatar as Rich-markup lines. Never empty.

    Order: baked asset → (optional) live Pillow render → text brandmark. Set
    *prefer_live* to try a live render of *image_path* before the baked asset.
    """

    if prefer_live and image_path is not None:
        live = render_from_image(Path(image_path))
        if live:
            return live
    baked = load_baked_asset()
    if baked:
        return baked
    if image_path is not None:
        live = render_from_image(Path(image_path))
        if live:
            return live
    return text_brandmark()


__all__ = (
    "render_avatar",
    "load_baked_asset",
    "render_from_image",
    "text_brandmark",
    "mini_brandmark",
)
