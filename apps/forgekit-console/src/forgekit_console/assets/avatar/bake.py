"""Bake the console display avatar from the portrait master.

**source / master vs display — why they're separate.** `avatar-source.png` is the
human-replaceable portrait master (a prepared, lean copy of the original). The
console NEVER renders the master directly: the master is a large square with a
decorative circuit border, and naively downscaling it to ~12-14 terminal cells
turns the face into a muddy blob. Instead this script crops to the
face/headphones, squares it, lifts the black/white contrast, and mildly sharpens
— producing small **display** PNGs (`forgekit-avatar.png` 128px primary +
`forgekit-avatar-96.png` 96px) whose silhouette reads first even when tiny.

Adopted master: the cleaner of the two 2026-06-17 portraits (the "…10_17_33"
variant — brighter, more defined face vs the "…_38" runner-up which sits more in
shadow and blobs at small size).

Re-bake after replacing the master::

    python -m forgekit_console.assets.avatar.bake

Pure build-time tool (Pillow, the ``console``/``image`` extra). The console ships
the already-baked PNGs, so runtime needs no Pillow.
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent
SOURCE = _HERE / "avatar-source.png"
DISPLAY_PRIMARY = _HERE / "forgekit-avatar.png"      # 128px — what the console renders
DISPLAY_SMALL = _HERE / "forgekit-avatar-96.png"     # 96px — secondary candidate

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
    """Produce the display PNGs from the master. Returns the written paths."""

    from PIL import Image  # noqa: WPS433 (build-time optional dep)

    img = Image.open(source).convert("RGB")
    head = _crop_head_square(img)
    written = []
    for out, px in ((DISPLAY_PRIMARY, PRIMARY_PX), (DISPLAY_SMALL, SMALL_PX)):
        baked = _tune(head.resize((px, px), Image.LANCZOS))
        out.parent.mkdir(parents=True, exist_ok=True)
        baked.save(out, format="PNG", optimize=True)
        written.append(out)
    return tuple(written)


if __name__ == "__main__":  # pragma: no cover - build-time tool
    for p in bake():
        print(f"baked {p.name} ({p.stat().st_size} bytes)")
