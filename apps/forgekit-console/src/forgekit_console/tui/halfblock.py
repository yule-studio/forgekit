"""Tier-2 avatar fallback — a small image-DERIVED half-block render of the PNG.

This is the middle tier of the image-first priority (see
:mod:`tui.image_renderer`):

1. real inline raster (``textual-image``) in a graphics-capable terminal,
2. **this** — a tiny, clean half-block render *derived from the baked PNG* for
   terminals without inline graphics (so SOMETHING image-based still shows),
3. a plain text/logo mark only as the last resort.

Why a half-block render and not ASCII art or a text mark? Each terminal cell can
show two vertical pixels by drawing the Unicode upper-half block ``▀`` with a
foreground colour (the top pixel) over a background colour (the bottom pixel). So
N rows of cells encode 2N image rows at full colour. Downscaled to ~12-16 cells
wide it stays small and crisp — it is a real *image* of the source portrait, not
typed characters approximating one.

The render is built with Pillow (downscale + read pixels) and returned as a Rich
:class:`~rich.text.Text` so a textual ``Static`` can mount it directly. Pillow is
the ``console`` extra; if it (or the asset) is missing this module returns
``None`` and the caller drops to the text mark.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# Upper-half block: its FG colour paints the top sub-pixel, its BG the bottom.
_UPPER_HALF = "▀"  # ▀

# Small, Claude-icon scale. Two source rows per text row, so 14 cols × 14 rows of
# cells encode a 14×28 image — tiny but recognisably the portrait.
_DEFAULT_COLS = 14


def render_halfblock(
    image_path: Path,
    *,
    cols: int = _DEFAULT_COLS,
    contrast: bool = False,
):
    """Return a Rich ``Text`` half-block render of *image_path*, or ``None``.

    ``None`` means Pillow or the asset is unavailable — the caller then falls
    through to the plain text mark (tier 3). Pure given the file: no terminal IO.
    When *contrast* is set, a mild ``autocontrast`` is applied before the downscale
    so the figure reads a touch more at small (compact) sizes.
    """

    try:
        from PIL import Image, ImageOps  # noqa: WPS433 - optional console extra
        from rich.text import Text  # noqa: WPS433
    except Exception:  # noqa: BLE001 - Pillow/rich missing → caller uses text mark
        return None

    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:  # noqa: BLE001 - unreadable asset → caller uses text mark
        return None

    if contrast:
        # boost dark-hair / light-face separation so the small avatar reads better.
        img = ImageOps.autocontrast(img, cutoff=2)

    cols = max(4, int(cols))
    src_w, src_h = img.size
    # Keep aspect; two image rows map to one text row (the half-block trick), so
    # the row count is half the scaled pixel height.
    rows = max(2, round(cols * (src_h / src_w) / 2))
    img = img.resize((cols, rows * 2), Image.LANCZOS)
    px = img.load()

    text = Text(no_wrap=True, end="")
    for row in range(rows):
        for col in range(cols):
            top = px[col, row * 2]
            bottom = px[col, row * 2 + 1]
            fg = f"#{top[0]:02x}{top[1]:02x}{top[2]:02x}"
            bg = f"#{bottom[0]:02x}{bottom[1]:02x}{bottom[2]:02x}"
            text.append(_UPPER_HALF, style=f"{fg} on {bg}")
        if row != rows - 1:
            text.append("\n")
    return text


# Braille cells pack 2 (cols) x 4 (rows) dots → 8x the half-block resolution. For a
# small line-art pixel avatar that extra resolution recovers the figure (headphone /
# head / face edges) far better than the 2x1 half-block — at the cost of being mono.
# Dot → bit map, indexed [row][col]; the glyph is chr(0x2800 + bitmask).
_BRAILLE_DOT = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


def render_braille(
    image_path: Path,
    *,
    cols: int = 14,
    threshold: int = 110,
    color: str = "#e8eaf0",
):
    """Return a Rich ``Text`` braille render of *image_path*, or ``None``.

    Higher-resolution monochrome render (2x4 dots/cell): a mild ``autocontrast`` then
    a threshold lights the brighter pixels (the figure's highlights / line art). Pure
    given the file; ``None`` when Pillow/rich/asset is unavailable (caller degrades).
    """

    try:
        from PIL import Image, ImageOps  # noqa: WPS433 - optional console extra
        from rich.text import Text  # noqa: WPS433
    except Exception:  # noqa: BLE001 - Pillow/rich missing → caller falls back
        return None
    try:
        img = ImageOps.autocontrast(Image.open(image_path).convert("L"), cutoff=2)
    except Exception:  # noqa: BLE001 - unreadable asset → caller falls back
        return None

    cols = max(4, int(cols))
    px_w = cols * 2
    rows = max(2, round(px_w * (img.height / img.width) / 4))
    img = img.resize((px_w, rows * 4), Image.LANCZOS)
    px = img.load()

    text = Text(no_wrap=True, end="")
    for cy in range(rows):
        for cx in range(cols):
            bits = 0
            for ry in range(4):
                for rx in range(2):
                    if px[cx * 2 + rx, cy * 4 + ry] >= threshold:
                        bits |= _BRAILLE_DOT[ry][rx]
            text.append(chr(0x2800 + bits), style=color)
        if cy != rows - 1:
            text.append("\n")
    return text


def halfblock_available(image_path: Optional[Path]) -> bool:
    """True if a half-block render can be produced (Pillow present + asset readable)."""

    if image_path is None or not Path(image_path).is_file():
        return False
    try:
        import PIL  # noqa: F401,WPS433
    except Exception:  # noqa: BLE001
        return False
    return True


__all__ = ("render_halfblock", "halfblock_available")
