"""Bake the small intro banner from the full-resolution brand wordmark.

The master (`forgekit-banner.png`, the 1916×821 pixel-art "FORGEKIT" wordmark on
black with the cyan→magenta gradient) is the high-resolution brand asset committed
to the repo. The console never renders the master inline — at 1916px it would slam
a huge raster into the intro. Instead this build-time tool downscales it to a SMALL
intro banner (`forgekit-banner-intro.png`, ~360px wide, Claude-icon-scale), the
compact mark the intro shows as a real inline image on graphics-capable terminals.

Run after replacing the master::

    python -m forgekit_console.assets.brand.bake

Requires Pillow (the ``console`` / ``image`` extra). Not imported at runtime — the
console ships the already-baked intro PNG. The text-wordmark fallback
(:func:`tui.theme.wordmark`) stands on its own when no image renders.
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent
MASTER = _HERE / "forgekit-banner.png"
INTRO = _HERE / "forgekit-banner-intro.png"

# Small intro width — keeps the wordmark legible without slamming the full banner.
INTRO_WIDTH_PX = 360


def bake(*, master: Path = MASTER, out: Path = INTRO, width: int = INTRO_WIDTH_PX) -> Path:
    """Produce the small intro banner from the master wordmark. Returns *out*."""

    from PIL import Image  # noqa: WPS433 (build-time optional dep)

    img = Image.open(master).convert("RGB")
    w, h = img.size
    height = max(1, round(width * (h / w)))
    img = img.resize((width, height), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG", optimize=True)
    return out


if __name__ == "__main__":  # pragma: no cover - build-time tool
    path = bake()
    print(f"baked {path} ({path.stat().st_size} bytes)")
