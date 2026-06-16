"""Bake the small display avatar from the source portrait.

The source (`profile_hermes_source.jpg`, ~1MB headphone portrait) is the
high-resolution master kept in the repo. The console never renders the master
directly — it would be a huge, blurry block. Instead this script crops to the
face/headphones and downscales to a SMALL square PNG (`forgekit-avatar.png`),
the Claude-icon-sized mark the intro shows when the terminal supports real
inline images.

Run after replacing the source image::

    python -m forgekit_console.assets.avatar.bake

Requires Pillow (the ``console`` / ``image`` extra). This is a build-time tool,
not imported at runtime: the console ships the already-baked PNG.
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent
SOURCE = _HERE / "profile_hermes_source.jpg"
DISPLAY = _HERE / "forgekit-avatar.png"

# Small square — Claude's brand icon is tiny; we match that scale, not a big
# raster. 96px keeps it crisp on protocol-capable terminals (Kitty/iTerm2/Sixel)
# while staying a small file.
DISPLAY_PX = 96


def _crop_to_portrait_square(img):
    """Crop to a centred square biased slightly upward (face/headphones)."""

    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    # Bias the crop upward so the face/headphones sit in frame, not the chest.
    top = max(0, (h - side) // 2 - int(side * 0.05))
    return img.crop((left, top, left + side, top + side))


def bake(*, source: Path = SOURCE, out: Path = DISPLAY, px: int = DISPLAY_PX) -> Path:
    """Produce the small display PNG from the source portrait. Returns *out*."""

    from PIL import Image  # noqa: WPS433 (build-time optional dep)

    img = Image.open(source).convert("RGB")
    img = _crop_to_portrait_square(img)
    img = img.resize((px, px), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG", optimize=True)
    return out


if __name__ == "__main__":  # pragma: no cover - build-time tool
    path = bake()
    print(f"baked {path} ({path.stat().st_size} bytes)")
