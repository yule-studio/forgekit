"""Bake the console display avatar from the portrait master.

**source / master vs display — why they're separate.** `avatar-source.png` is the
human-replaceable portrait MASTER (a byte-for-byte copy of the adopted original
archive, see below). The console NEVER renders the master directly: it is a large
square with a decorative circuit border, and naively downscaling it to ~12-14
terminal cells turns the face into a muddy blob. Instead this script crops to the
face/headphones, squares it, lifts the black/white contrast, and mildly sharpens —
producing small **display** PNGs whose silhouette reads first even when tiny.

**Naming policy (canonical vs runtime alias).**
* Canonical display outputs (meaningful, self-describing):
  ``forgekit-avatar-display-128.png`` (primary) and ``forgekit-avatar-display-96.png``.
* Runtime aliases the renderer actually loads (kept for code stability):
  ``forgekit-avatar.png`` == display-128, ``forgekit-avatar-96.png`` == display-96.
  Each alias is written byte-identical to its canonical file (git dedups the blob),
  so "canonical" and "alias" never drift.

**Source archive / adoption.** Three portrait candidates are preserved in this
directory so a human can re-pick later:
``forgekit-avatar-source-2026-06-17-33.png`` (ADOPTED — brightest, most defined
face; reads best at small size and as a real inline image), ``…-2026-06-17-38.png``
(more shadow, blobs at small size), ``…-2026-06-15-original.png`` (softer than 33).
The adopted candidate is copied to ``avatar-source.png`` (the master alias this
script reads). To adopt a different candidate, copy it over ``avatar-source.png``
and re-bake.

Re-bake after replacing the master::

    python -m forgekit_console.assets.avatar.bake

Pure build-time tool (Pillow, the ``image`` extra). The console ships the
already-baked PNGs, so runtime needs no Pillow. Deterministic: same master in →
same display bytes out.
"""

from __future__ import annotations

import shutil
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# Master alias the bake reads (== the adopted source archive, byte-for-byte).
SOURCE = _HERE / "avatar-source.png"

# Canonical display outputs — meaningful, self-describing names.
DISPLAY_128 = _HERE / "forgekit-avatar-display-128.png"  # primary — what the console renders
DISPLAY_96 = _HERE / "forgekit-avatar-display-96.png"    # secondary candidate

# Runtime aliases the renderer loads (byte-identical to the canonical files above).
ALIAS_PRIMARY = _HERE / "forgekit-avatar.png"    # == display-128
ALIAS_SMALL = _HERE / "forgekit-avatar-96.png"   # == display-96

PRIMARY_PX = 128
SMALL_PX = 96

# Crop the head/headphones out of the bordered square master. Drop the circuit
# border + chest so the subject fills the frame at small sizes.
_CROP = (0.11, 0.06, 0.89, 0.86)  # left, top, right, bottom (fractions)


def _crop_head_square(img):
    w, h = img.size
    l, t, r, b = _CROP
    head = img.crop((int(w * l), int(h * t), int(w * r), int(h * b)))
    cw, ch = head.size
    side = min(cw, ch)
    left = (cw - side) // 2
    return head.crop((left, 0, left + side, side))


def _tune(img):
    """Lift B/W contrast + mild sharpen so the silhouette reads when tiny."""

    from PIL import ImageFilter, ImageOps

    img = ImageOps.autocontrast(img, cutoff=1)
    return img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=80, threshold=2))


def bake(*, source: Path = SOURCE) -> tuple:
    """Produce the display PNGs (canonical + runtime alias) from the master.

    Returns every written path. Deterministic; aliases are byte-identical copies
    of their canonical file so the renderer's alias and the canonical name never
    diverge.
    """

    from PIL import Image  # noqa: WPS433 (build-time optional dep)

    img = Image.open(source).convert("RGB")
    head = _crop_head_square(img)
    written = []
    for canonical, alias, px in (
        (DISPLAY_128, ALIAS_PRIMARY, PRIMARY_PX),
        (DISPLAY_96, ALIAS_SMALL, SMALL_PX),
    ):
        baked = _tune(head.resize((px, px), Image.LANCZOS))
        canonical.parent.mkdir(parents=True, exist_ok=True)
        baked.save(canonical, format="PNG", optimize=True)
        shutil.copyfile(canonical, alias)  # runtime alias == canonical (git dedups)
        written.extend((canonical, alias))
    return tuple(written)


if __name__ == "__main__":  # pragma: no cover - build-time tool
    for p in bake():
        print(f"baked {p.name} ({p.stat().st_size} bytes)")
