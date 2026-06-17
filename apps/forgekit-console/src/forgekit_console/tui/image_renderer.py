"""Avatar image rendering — image FIRST, with an explicit 3-tier priority.

The forgekit intro shows a small avatar like Claude Code's brand icon. The render
is **image-first**: we always try to show the actual baked portrait, dropping to
a plain text mark only as a last resort. The priority, top→down, is:

1. **REAL inline raster** — the baked PNG drawn pixel-for-pixel with a terminal
   graphics protocol (Kitty / iTerm2 / Sixel, incl. the VS Code integrated
   terminal's iTerm2 inline-image support) via ``textual-image``. Used when
   :func:`detect_image_capability` says the terminal is graphics-capable.
2. **IMAGE-DERIVED half-block** — when inline graphics aren't available we still
   show an *image*: a tiny (~12-16 col) Unicode half-block render derived from
   the **same baked PNG** with Pillow (:mod:`tui.halfblock`). Clean and small,
   genuinely the portrait's pixels — NOT typed text approximating it.
3. **TEXT/logo mark** — only if even Pillow / the asset is missing do we fall to
   the two-line brand mark.

Design for testability
-----------------------
Capability detection and renderer selection are split so tests don't need a real
terminal:

* :func:`detect_image_capability` reads the environment/terminal and returns a
  pure :class:`ImageCapability` decision (injectable ``env`` / overrides).
* :func:`select_renderer` takes that decision (or any bool) and returns which
  renderer to use — no IO, fully pure. Capable → real raster; not-capable → the
  image-derived half-block (tier 2), which itself degrades to text only when
  Pillow/the asset is missing.
* :class:`RealImageRenderer` / :class:`HalfBlockRenderer` / :class:`TextMarkRenderer`
  are the three concrete renderers; each degrades down a tier at *render* time
  (import/asset guarded), so the image-first priority holds wherever it can.

Nothing here imports textual's App; the widget wiring lives in
:mod:`tui.avatar_panel`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Protocol, Tuple

from . import theme

# The baked small DISPLAY PNG (Claude-icon scale) — the asset the console renders.
# Produced from the master by assets/avatar/bake.py; never the raw master.
_DISPLAY_PNG = "forgekit-avatar.png"
# The portrait MASTER (human-replaceable), used only if the baked PNG is absent.
_SOURCE_JPG = "avatar-source.png"

# Renderer identifiers (returned by select_renderer; stable for tests).
RENDERER_REAL = "real-image"  # tier 1 — inline raster
RENDERER_HALFBLOCK = "half-block"  # tier 2 — image-derived unicode half-block
RENDERER_TEXT = "text-mark"  # tier 3 — last-resort text/logo mark

# Env var to force a path regardless of detection (operator/testing override).
#   FORGEKIT_AVATAR=image  → force real image
#   FORGEKIT_AVATAR=text   → force text mark
_FORCE_ENV = "FORGEKIT_AVATAR"

# Terminals/protocols known to support inline graphics. Detection is heuristic
# (textual-image does the real protocol probing at render time); this gates the
# *attempt* so a plain terminal never even tries.
#   iterm.app  — iTerm2 inline image protocol
#   wezterm    — Kitty + iTerm2 protocols
#   vscode     — VS Code integrated terminal (recent versions speak the iTerm2
#                inline-image protocol, so TERM_PROGRAM=vscode is worth attempting)
_GRAPHICS_TERM_PROGRAMS = ("iterm.app", "wezterm", "vscode")
_GRAPHICS_TERMS = ("xterm-kitty", "wezterm")


def assets_dir() -> Path:
    """Directory holding the avatar assets (sibling ``assets/avatar``)."""

    return Path(__file__).resolve().parent.parent / "assets" / "avatar"


def brand_dir() -> Path:
    """Directory holding the brand banner assets (sibling ``assets/brand``)."""

    return Path(__file__).resolve().parent.parent / "assets" / "brand"


# The small baked intro banner (Claude-icon-scale wordmark) — primary brand asset.
_BANNER_INTRO_PNG = "forgekit-banner-intro.png"
# The full-resolution wordmark master (used only if the baked intro is absent).
_BANNER_MASTER_PNG = "forgekit-banner.png"


def banner_intro_path() -> Path:
    return brand_dir() / _BANNER_INTRO_PNG


def banner_master_path() -> Path:
    return brand_dir() / _BANNER_MASTER_PNG


def best_banner_path() -> Optional[Path]:
    """The brand banner to render: the small baked intro, else the master, else None."""

    intro = banner_intro_path()
    if intro.is_file():
        return intro
    master = banner_master_path()
    if master.is_file():
        return master
    return None


def display_png_path() -> Path:
    return assets_dir() / _DISPLAY_PNG


def source_image_path() -> Path:
    return assets_dir() / _SOURCE_JPG


def best_image_path() -> Optional[Path]:
    """The image to render: the baked small PNG, else the source, else None."""

    png = display_png_path()
    if png.is_file():
        return png
    src = source_image_path()
    if src.is_file():
        return src
    return None


@dataclass(frozen=True)
class ImageCapability:
    """A pure decision about whether to attempt a real inline image."""

    capable: bool
    reason: str = ""
    forced: bool = False


def detect_image_capability(
    env: Optional[Mapping[str, str]] = None,
    *,
    force: Optional[bool] = None,
) -> ImageCapability:
    """Decide if the terminal can show a real inline image. Pure given *env*.

    Priority: explicit ``force`` arg → ``FORGEKIT_AVATAR`` env override →
    terminal-protocol heuristics (``TERM`` / ``TERM_PROGRAM`` / Kitty / Sixel /
    iTerm). No probing IO so it's deterministic in tests.
    """

    if force is not None:
        return ImageCapability(force, reason="forced (arg)", forced=True)

    environ = os.environ if env is None else env
    forced_env = (environ.get(_FORCE_ENV) or "").strip().lower()
    if forced_env in ("image", "real", "1", "true", "on"):
        return ImageCapability(True, reason=f"{_FORCE_ENV}={forced_env}", forced=True)
    if forced_env in ("text", "fallback", "0", "false", "off", "none"):
        return ImageCapability(False, reason=f"{_FORCE_ENV}={forced_env}", forced=True)

    term = (environ.get("TERM") or "").strip().lower()
    term_program = (environ.get("TERM_PROGRAM") or "").strip().lower()

    if environ.get("KITTY_WINDOW_ID") or "kitty" in term:
        return ImageCapability(True, reason="kitty graphics protocol")
    if term_program == "iterm.app" or environ.get("ITERM_SESSION_ID"):
        return ImageCapability(True, reason="iterm2 inline images")
    if "sixel" in term or environ.get("FORGEKIT_SIXEL"):
        return ImageCapability(True, reason="sixel")
    # VS Code integrated terminal: recent versions support the iTerm2 inline-image
    # protocol, so TERM_PROGRAM=vscode is worth the real-raster attempt (textual-
    # image probes for real at render time; this only gates the attempt).
    if term_program in _GRAPHICS_TERM_PROGRAMS:
        return ImageCapability(True, reason=f"term_program={term_program}")
    if term in _GRAPHICS_TERMS:
        return ImageCapability(True, reason=f"term={term}")

    return ImageCapability(False, reason="no known graphics protocol")


def select_renderer(capability) -> str:
    """Pure selection: capability (truthy/ImageCapability) → renderer id.

    Image-first priority: capable terminal → the REAL inline raster (tier 1);
    otherwise the IMAGE-DERIVED half-block (tier 2). The half-block renderer
    itself degrades to the text mark (tier 3) at render time when Pillow / the
    asset is missing, so "show an image whenever possible" holds. Accepts a bool
    or an :class:`ImageCapability` so callers and tests can inject either.
    """

    capable = capability.capable if isinstance(capability, ImageCapability) else bool(capability)
    return RENDERER_REAL if capable else RENDERER_HALFBLOCK


# --- text/symbol fallback mark (SECONDARY) ---------------------------------

# A small, crisp brand mark — two short lines, no per-pixel colour spans (those
# are what pixelate in a poor terminal). This is the fallback only; the real
# image is the default when the terminal supports inline graphics.
_TEXT_MARK: Tuple[str, ...] = (
    theme.wordmark("forgekit"),
    "[dim]operator console[/dim]",
)


def text_mark_lines() -> Tuple[str, ...]:
    """The fallback text mark (Rich-markup lines). Always available."""

    return _TEXT_MARK


class AvatarRenderer(Protocol):
    """Common shape: produce a renderable for a textual widget."""

    renderer_id: str

    def renderable(self):  # pragma: no cover - protocol
        ...


@dataclass(frozen=True)
class TextMarkRenderer:
    """TIER 3 (last resort) — a small clean text/symbol mark (no image at all)."""

    renderer_id: str = RENDERER_TEXT

    def renderable(self) -> str:
        return "\n".join(text_mark_lines())


@dataclass(frozen=True)
class HalfBlockRenderer:
    """TIER 2 — an image-DERIVED half-block render of the baked PNG (Pillow).

    Shown when inline graphics aren't available: still a real *image* of the
    portrait (downscaled half-block raster, ~12-16 cols), not typed text. Degrades
    to the text mark (tier 3) only when Pillow / the asset is missing.
    """

    renderer_id: str = RENDERER_HALFBLOCK
    cols: int = 14  # cells wide; small & clean

    def renderable(self):
        from . import halfblock  # local import keeps Pillow optional at import time

        path = best_image_path()
        rendered = halfblock.render_halfblock(path, cols=self.cols) if path else None
        if rendered is None:
            return TextMarkRenderer().renderable()
        return rendered


@dataclass(frozen=True)
class RealImageRenderer:
    """TIER 1 (primary) — a real inline image raster via ``textual-image``.

    Falls back DOWN the tiers if the image lib is missing or the asset is
    unreadable at render time: first to the image-derived half-block (tier 2),
    then to the text mark (tier 3). So "show the real image first, else still an
    image" holds wherever it can.
    """

    renderer_id: str = RENDERER_REAL
    width: int = 14  # cells; small Claude-icon scale, aligned with the half-block tier

    def renderable(self):
        path = best_image_path()
        if path is None:
            return TextMarkRenderer().renderable()
        try:
            from textual_image.renderable import Image as _InlineImage  # noqa: WPS433
        except Exception:  # noqa: BLE001 - textual-image absent → image-derived tier 2
            return HalfBlockRenderer().renderable()
        try:
            return _InlineImage(str(path), width=self.width)
        except Exception:  # noqa: BLE001 - raster construction failed → tier 2
            return HalfBlockRenderer().renderable()


# --- brand banner (intro brand mark) ---------------------------------------

# The text-wordmark fallback for the brand mark — the cyan→magenta gradient. It
# stands on its own (clean) when no inline image renders.
RENDERER_BRAND_IMAGE = "brand-image"  # tier 1 — real inline banner raster
RENDERER_BRAND_TEXT = "brand-text"  # fallback — text wordmark gradient


def brand_wordmark_lines() -> Tuple[str, ...]:
    """The compact text wordmark used when the banner can't render inline."""

    return (theme.wordmark("forgekit"),)


@dataclass(frozen=True)
class BrandTextRenderer:
    """Fallback brand mark — the cyan→magenta text wordmark (no image)."""

    renderer_id: str = RENDERER_BRAND_TEXT

    def renderable(self) -> str:
        return "\n".join(brand_wordmark_lines())


@dataclass(frozen=True)
class BrandBannerRenderer:
    """TIER 1 — the forgekit wordmark banner as a real inline image.

    Renders the small baked intro banner via ``textual-image`` on a
    graphics-capable terminal. Degrades straight to the compact TEXT wordmark
    (the gradient mark) when the lib / asset is unavailable — the wordmark is the
    intended compact fallback, so no half-block tier here.
    """

    renderer_id: str = RENDERER_BRAND_IMAGE
    width: int = 28  # cells; compact, small banner — not the full 1916px master

    def renderable(self):
        path = best_banner_path()
        if path is None:
            return BrandTextRenderer().renderable()
        try:
            from textual_image.renderable import Image as _InlineImage  # noqa: WPS433
        except Exception:  # noqa: BLE001 - textual-image absent → text wordmark
            return BrandTextRenderer().renderable()
        try:
            return _InlineImage(str(path), width=self.width)
        except Exception:  # noqa: BLE001 - raster construction failed → text wordmark
            return BrandTextRenderer().renderable()


def make_brand_renderer(
    capability=None,
    *,
    width: int = 28,
    env: Optional[Mapping[str, str]] = None,
) -> AvatarRenderer:
    """Build the intro brand renderer: real banner image first, else text wordmark.

    Image-first like the avatar path: a graphics-capable terminal gets the real
    inline banner; otherwise (or when the asset/lib is missing) the compact
    cyan→magenta text wordmark, which is clean on its own.
    """

    if capability is None:
        capability = detect_image_capability(env)
    capable = capability.capable if isinstance(capability, ImageCapability) else bool(capability)
    if capable:
        return BrandBannerRenderer(width=width)
    return BrandTextRenderer()


def make_renderer(
    capability=None,
    *,
    width: int = 12,
    env: Optional[Mapping[str, str]] = None,
) -> AvatarRenderer:
    """Build the renderer chosen by *capability* (detected from *env* if None).

    Image-first: capable → real raster (tier 1), not-capable → image-derived
    half-block (tier 2). Both degrade to the text mark (tier 3) at render time.
    """

    if capability is None:
        capability = detect_image_capability(env)
    renderer_id = select_renderer(capability)
    if renderer_id == RENDERER_REAL:
        return RealImageRenderer(width=width)
    if renderer_id == RENDERER_HALFBLOCK:
        return HalfBlockRenderer()
    return TextMarkRenderer()


__all__ = (
    "RENDERER_REAL",
    "RENDERER_HALFBLOCK",
    "RENDERER_TEXT",
    "RENDERER_BRAND_IMAGE",
    "RENDERER_BRAND_TEXT",
    "ImageCapability",
    "AvatarRenderer",
    "RealImageRenderer",
    "HalfBlockRenderer",
    "TextMarkRenderer",
    "BrandBannerRenderer",
    "BrandTextRenderer",
    "detect_image_capability",
    "select_renderer",
    "make_renderer",
    "make_brand_renderer",
    "text_mark_lines",
    "brand_wordmark_lines",
    "assets_dir",
    "brand_dir",
    "display_png_path",
    "source_image_path",
    "best_image_path",
    "banner_intro_path",
    "banner_master_path",
    "best_banner_path",
)
