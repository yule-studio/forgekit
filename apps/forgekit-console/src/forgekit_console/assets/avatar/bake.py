"""Bake the console avatar assets from the portrait master.

**Two output families — terminal ICON (default) vs detailed PORTRAIT (archive).**
A detailed line-art portrait carries far too much detail for a tiny terminal intro
slot: even as a true raster it reads busy, and as a half-block it turns to mush. So
the console's DEFAULT avatar is a deliberately **simplified terminal icon** (a bold
2-tone headphone silhouette — keeps the identity, drops the detail, crisp on black
like Claude Code's brand icon). The detailed portrait is kept as an asset for a
**larger/GUI surface or the opt-in portrait mode**, not the tiny terminal default.

* ``avatar-source.png`` — the human-replaceable portrait MASTER (byte-identical to
  the adopted source archive; see the three ``forgekit-avatar-source-*`` files).
* TERMINAL ICON (the runtime DEFAULT the console renders):
  ``forgekit-terminal-icon-master.png`` (256px) → ``-128.png`` / ``-96.png``.
  Runtime alias ``forgekit-avatar.png`` is byte-identical to the 128 icon, so the
  renderer's stable path resolves to the icon.
* DETAILED PORTRAIT (archive / future GUI / ``FORGEKIT_AVATAR=portrait``):
  ``forgekit-avatar-display-128.png`` / ``-96.png`` (crop + contrast + sharpen).

Re-bake after replacing the master::

    python -m forgekit_console.assets.avatar.bake

Pure build-time tool (Pillow, the ``image`` extra). The console ships the
already-baked PNGs, so runtime needs no Pillow. Deterministic: same master in →
same bytes out.
"""

from __future__ import annotations

import shutil
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# Master alias the bake reads (== the adopted source archive, byte-for-byte).
SOURCE = _HERE / "avatar-source.png"

# --- terminal ICON (the runtime DEFAULT) -----------------------------------
ICON_MASTER = _HERE / "forgekit-terminal-icon-master.png"  # 256px simplified master
ICON_128 = _HERE / "forgekit-terminal-icon-128.png"        # canonical 128
ICON_96 = _HERE / "forgekit-terminal-icon-96.png"          # canonical 96
ALIAS_PRIMARY = _HERE / "forgekit-avatar.png"   # runtime alias == ICON_128
ALIAS_SMALL = _HERE / "forgekit-avatar-96.png"  # runtime alias == ICON_96

# --- detailed PORTRAIT (archive / future GUI / opt-in portrait mode) --------
DISPLAY_128 = _HERE / "forgekit-avatar-display-128.png"
DISPLAY_96 = _HERE / "forgekit-avatar-display-96.png"

ICON_MASTER_PX = 256
PRIMARY_PX = 128
SMALL_PX = 96

# Crop the head/headphones out of the bordered square master. Drop the circuit
# border + chest so the subject fills the frame at small sizes.
_CROP = (0.11, 0.06, 0.89, 0.86)  # left, top, right, bottom (fractions)

# Terminal-icon binarisation threshold (0-255). Tuned so the headphone + head
# silhouette stays bold and readable without speckle in the hair.
_ICON_THRESHOLD = 100


def _crop_head_square(img):
    w, h = img.size
    l, t, r, b = _CROP
    head = img.crop((int(w * l), int(h * t), int(w * r), int(h * b)))
    cw, ch = head.size
    side = min(cw, ch)
    left = (cw - side) // 2
    return head.crop((left, 0, left + side, side))


def _tune_portrait(img):
    """Detailed portrait: lift B/W contrast + mild sharpen (keeps the detail)."""

    from PIL import ImageFilter, ImageOps

    img = ImageOps.autocontrast(img, cutoff=1)
    return img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=80, threshold=2))


def _simplify_icon(head, px):
    """Terminal icon: a bold 2-tone silhouette — simplified, crisp on black."""

    from PIL import ImageFilter, ImageOps

    g = ImageOps.autocontrast(ImageOps.grayscale(head), cutoff=1)
    g = g.resize((px, px), Image.LANCZOS)
    # consolidate fine detail, then hard 2-tone threshold → pictogram-like icon.
    g = g.filter(ImageFilter.GaussianBlur(radius=max(0.4, px / 200)))
    g = g.point(lambda p: 255 if p > _ICON_THRESHOLD else 0)
    return g.convert("RGB")


# Image is imported lazily inside bake(); referenced by the helpers above only when
# bake() runs (build time), so runtime never needs Pillow.
Image = None  # noqa: N816 - rebound from PIL inside bake()


def bake(*, source: Path = SOURCE) -> tuple:
    """Produce the terminal icon (+runtime alias) and the portrait display PNGs.

    Returns every written path. Deterministic; runtime aliases are byte-identical
    copies of their canonical icon file so the alias and canonical never drift.
    """

    global Image
    from PIL import Image  # noqa: WPS433 (build-time optional dep)

    img = Image.open(source).convert("RGB")
    head = _crop_head_square(img)
    written = []

    # 1) terminal ICON family (the runtime default) + master.
    icon_master = _simplify_icon(head, ICON_MASTER_PX)
    ICON_MASTER.parent.mkdir(parents=True, exist_ok=True)
    icon_master.save(ICON_MASTER, format="PNG", optimize=True)
    written.append(ICON_MASTER)
    for canonical, alias, px in (
        (ICON_128, ALIAS_PRIMARY, PRIMARY_PX),
        (ICON_96, ALIAS_SMALL, SMALL_PX),
    ):
        icon = _simplify_icon(head, px)
        icon.save(canonical, format="PNG", optimize=True)
        shutil.copyfile(canonical, alias)  # runtime alias == canonical (git dedups)
        written.extend((canonical, alias))

    # 2) detailed PORTRAIT display (archive / future GUI / opt-in portrait mode).
    for canonical, px in ((DISPLAY_128, PRIMARY_PX), (DISPLAY_96, SMALL_PX)):
        portrait = _tune_portrait(head.resize((px, px), Image.LANCZOS))
        portrait.save(canonical, format="PNG", optimize=True)
        written.append(canonical)

    return tuple(written)


if __name__ == "__main__":  # pragma: no cover - build-time tool
    for p in bake():
        print(f"baked {p.name} ({p.stat().st_size} bytes)")
