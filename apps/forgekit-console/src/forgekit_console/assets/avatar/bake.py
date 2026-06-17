"""Bake the console avatar assets — terminal ICON vs detailed PORTRAIT.

Two families, two sources:

* **TERMINAL ICON** (the runtime DEFAULT the console renders in true-raster
  terminals): baked from ``forgekit-terminal-icon-source.png`` — a **provided
  pixel-art** headphone-girl icon (the user's 2026-06-17 03:05 sheet, the 128/96
  reference). We resize it (preserve the pixel art; no re-creation), NOT invent a
  silhouette. Outputs ``forgekit-terminal-icon-master.png`` (256) / ``-128`` /
  ``-96``; the runtime alias ``forgekit-avatar.png`` is byte-identical to the 128.
* **DETAILED PORTRAIT** (archive / future GUI / opt-in ``FORGEKIT_AVATAR=portrait``):
  baked from the portrait master ``avatar-source.png`` (crop + contrast + sharpen)
  → ``forgekit-avatar-display-128.png`` / ``-96``.

Non-raster terminals (e.g. VS Code) don't render an image at all — they show the
``fk`` brand badge — because a ~14-col half-block of the pixel icon is muddy. So the
pixel icon is the **true-raster** asset; the badge stays the non-raster fallback.

Re-bake after replacing a source::

    python -m forgekit_console.assets.avatar.bake

Pure build-time tool (Pillow, the ``image`` extra). The console ships the
already-baked PNGs, so runtime needs no Pillow. Deterministic: same sources in →
same bytes out.
"""

from __future__ import annotations

import shutil
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# Portrait master (detailed portrait family).
SOURCE = _HERE / "avatar-source.png"
# Terminal-icon source — the PROVIDED pixel-art icon (resized, not re-created).
ICON_SOURCE = _HERE / "forgekit-terminal-icon-source.png"

# --- terminal ICON (the runtime DEFAULT, true-raster) ----------------------
ICON_MASTER = _HERE / "forgekit-terminal-icon-master.png"  # 256px
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

# Crop the head/headphones out of the bordered square portrait master.
_CROP = (0.11, 0.06, 0.89, 0.86)  # left, top, right, bottom (fractions)


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


# Image is imported lazily inside bake() so runtime never needs Pillow.
Image = None  # noqa: N816 - rebound from PIL inside bake()


def bake(*, source: Path = SOURCE, icon_source: Path = ICON_SOURCE) -> tuple:
    """Produce the terminal icon (+runtime alias) and the portrait display PNGs.

    The terminal icon is the PROVIDED pixel art resized (no re-creation). Returns
    every written path. Deterministic; runtime aliases are byte-identical copies of
    their canonical icon file so the alias and canonical never drift.
    """

    global Image
    from PIL import Image  # noqa: WPS433 (build-time optional dep)

    written = []

    # 1) terminal ICON family — resize the provided pixel art (preserve it).
    pix = Image.open(icon_source).convert("RGB")
    ICON_MASTER.parent.mkdir(parents=True, exist_ok=True)
    pix.resize((ICON_MASTER_PX, ICON_MASTER_PX), Image.LANCZOS).save(
        ICON_MASTER, format="PNG", optimize=True
    )
    written.append(ICON_MASTER)
    for canonical, alias, px in (
        (ICON_128, ALIAS_PRIMARY, PRIMARY_PX),
        (ICON_96, ALIAS_SMALL, SMALL_PX),
    ):
        pix.resize((px, px), Image.LANCZOS).save(canonical, format="PNG", optimize=True)
        shutil.copyfile(canonical, alias)  # runtime alias == canonical (git dedups)
        written.extend((canonical, alias))

    # 2) detailed PORTRAIT display (archive / future GUI / opt-in portrait mode).
    img = Image.open(source).convert("RGB")
    head = _crop_head_square(img)
    for canonical, px in ((DISPLAY_128, PRIMARY_PX), (DISPLAY_96, SMALL_PX)):
        portrait = _tune_portrait(head.resize((px, px), Image.LANCZOS))
        portrait.save(canonical, format="PNG", optimize=True)
        written.append(canonical)

    return tuple(written)


if __name__ == "__main__":  # pragma: no cover - build-time tool
    for p in bake():
        print(f"baked {p.name} ({p.stat().st_size} bytes)")
