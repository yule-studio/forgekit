"""Avatar image rendering — real inline image FIRST, text mark SECOND.

The forgekit intro shows a small avatar like Claude Code's brand icon. The
**primary** path is a real inline image of the baked portrait PNG, rendered with
a terminal graphics protocol (Kitty / iTerm2 / Sixel) via the ``textual-image``
package. Only when the terminal can't do that do we fall back to a small, clean
text/symbol mark — never a big pixelated half-block raster.

Design for testability
-----------------------
Capability detection and renderer selection are split so tests don't need a real
terminal:

* :func:`detect_image_capability` reads the environment/terminal and returns a
  pure :class:`ImageCapability` decision (injectable ``env`` / overrides).
* :func:`select_renderer` takes that decision (or any bool) and returns which
  renderer to use — no IO, fully pure.
* :class:`RealImageRenderer` / :class:`TextMarkRenderer` are the two concrete
  renderers; the real one degrades to the fallback if ``textual-image`` is
  missing at *render* time (import guarded), so the priority is "real first,
  fall back if truly unsupported".

Nothing here imports textual's App; the widget wiring lives in
:mod:`tui.avatar_panel`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Protocol, Tuple

# The baked small display PNG (Claude-icon scale) — primary asset.
_DISPLAY_PNG = "forgekit-avatar.png"
# The high-res master, used only if the baked PNG is somehow absent.
_SOURCE_JPG = "profile_hermes_source.jpg"

# Renderer identifiers (returned by select_renderer; stable for tests).
RENDERER_REAL = "real-image"
RENDERER_TEXT = "text-mark"

# Env var to force a path regardless of detection (operator/testing override).
#   FORGEKIT_AVATAR=image  → force real image
#   FORGEKIT_AVATAR=text   → force text mark
_FORCE_ENV = "FORGEKIT_AVATAR"

# Terminals/protocols known to support inline graphics. Detection is heuristic
# (textual-image does the real protocol probing at render time); this gates the
# *attempt* so a plain terminal never even tries.
_GRAPHICS_TERM_PROGRAMS = ("iterm.app", "wezterm", "vscode")
_GRAPHICS_TERMS = ("xterm-kitty", "wezterm")


def assets_dir() -> Path:
    """Directory holding the avatar assets (sibling ``assets/avatar``)."""

    return Path(__file__).resolve().parent.parent / "assets" / "avatar"


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
    if term_program in _GRAPHICS_TERM_PROGRAMS:
        return ImageCapability(True, reason=f"term_program={term_program}")
    if term in _GRAPHICS_TERMS:
        return ImageCapability(True, reason=f"term={term}")

    return ImageCapability(False, reason="no known graphics protocol")


def select_renderer(capability) -> str:
    """Pure selection: capability (truthy/ImageCapability) → renderer id.

    Real image FIRST when capable, else the text mark. Accepts a bool or an
    :class:`ImageCapability` so callers and tests can inject either.
    """

    capable = capability.capable if isinstance(capability, ImageCapability) else bool(capability)
    return RENDERER_REAL if capable else RENDERER_TEXT


# --- text/symbol fallback mark (SECONDARY) ---------------------------------

# A small, crisp brand mark — two short lines, no per-pixel colour spans (those
# are what pixelate in a poor terminal). This is the fallback only; the real
# image is the default when the terminal supports inline graphics.
_TEXT_MARK: Tuple[str, ...] = (
    "[b orange1]◆ forge[/b orange1][b orange3]kit[/b orange3]",
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
    """SECONDARY renderer — a small clean text/symbol mark (no image lib)."""

    renderer_id: str = RENDERER_TEXT

    def renderable(self) -> str:
        return "\n".join(text_mark_lines())


@dataclass(frozen=True)
class RealImageRenderer:
    """PRIMARY renderer — a real inline image via ``textual-image``.

    Falls back to the text mark only if the image lib is missing or the asset is
    unreadable at render time, so "real image first" holds wherever it can.
    """

    renderer_id: str = RENDERER_REAL
    width: int = 12  # cells; small, Claude-icon scale

    def renderable(self):
        path = best_image_path()
        if path is None:
            return TextMarkRenderer().renderable()
        try:
            from textual_image.renderable import Image as _InlineImage  # noqa: WPS433
        except Exception:  # noqa: BLE001 - textual-image not installed → fallback
            return TextMarkRenderer().renderable()
        try:
            return _InlineImage(str(path), width=self.width)
        except Exception:  # noqa: BLE001 - any render construction failure
            return TextMarkRenderer().renderable()


def make_renderer(
    capability=None,
    *,
    width: int = 12,
    env: Optional[Mapping[str, str]] = None,
) -> AvatarRenderer:
    """Build the renderer chosen by *capability* (detected from *env* if None)."""

    if capability is None:
        capability = detect_image_capability(env)
    renderer_id = select_renderer(capability)
    if renderer_id == RENDERER_REAL:
        return RealImageRenderer(width=width)
    return TextMarkRenderer()


__all__ = (
    "RENDERER_REAL",
    "RENDERER_TEXT",
    "ImageCapability",
    "AvatarRenderer",
    "RealImageRenderer",
    "TextMarkRenderer",
    "detect_image_capability",
    "select_renderer",
    "make_renderer",
    "text_mark_lines",
    "assets_dir",
    "display_png_path",
    "source_image_path",
    "best_image_path",
)
