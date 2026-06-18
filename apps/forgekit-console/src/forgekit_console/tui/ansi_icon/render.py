"""ANSI icon — asset loader, theme policy, and the safe Rich renderable.

This is where the sanitized :class:`~.model.AnsiDoc` becomes something a Textual
``Static`` can mount: a Rich ``Text`` rebuilt span-by-span (NOT the raw bytes), with
a dark/light/auto theme remap so the black-background pixel icon stays readable on a
light terminal.

Theme policy
------------
* ``FORGEKIT_TERM_THEME=dark|light|auto`` is the explicit operator override and
  always wins. ``auto`` infers from ``COLORFGBG`` when present and falls back to
  **dark** when it cannot tell — we never pretend to perfectly auto-detect.
* **dark** renders the icon as authored (it was drawn for a dark terminal).
* **light** does NOT do a naive full invert (that muddies hues). It applies a
  *hue-preserving luminance inversion*: near-black pixels (the icon's field) map to
  the light page colour, and everything else keeps its hue while its lightness flips
  — so a bright figure on black becomes a dark figure on the page, silhouette intact.

The Rich import is guarded (the ``console`` extra), exactly like
:mod:`tui.halfblock`; without it the renderer degrades down the normal fallback
chain (braille → badge → text).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

from . import model as m
from .model import AnsiDoc, Style
from .sanitizer import sanitize

# Renderer / backend identifiers (mirrored in image_renderer's vocabulary).
RENDERER_ANSI_ICON = "ansi-icon"
BACKEND_ANSI_ICON = "ansi-icon"

# Path status of an attempted ANSI render (surfaced to /render + escalation).
STATUS_OK = "ok"                 # clean asset → rendered as the ANSI icon
STATUS_UNSAFE = "unsafe-ansi"    # unsafe sequences present → refused, fell back
STATUS_INVALID = "invalid-ansi"  # empty / unparseable → fell back
STATUS_NO_ASSET = "no-ansi-asset"  # asset file missing → fell back
STATUS_TOO_WIDE = "ansi-too-wide"  # wider than the intro budget → fell back (force to override)

# The intro avatar is a small slot beside the meta column; an ANSI icon wider than
# this would shove the header layout, so the AUTO path caps it and degrades to the
# compact braille. ``FORGEKIT_AVATAR=ansi`` lifts the cap (operator opt-in).
DEFAULT_MAX_COLS = 28

# --- theme policy -----------------------------------------------------------
ENV_THEME = "FORGEKIT_TERM_THEME"
THEME_DARK = "dark"
THEME_LIGHT = "light"
THEME_AUTO = "auto"

THEME_SRC_EXPLICIT = "explicit"
THEME_SRC_COLORFGBG = "auto:COLORFGBG"
THEME_SRC_DEFAULT = "auto:default-dark"

# Light-theme remap anchors.
_LIGHT_PAGE = (0xF4, 0xF5, 0xF7)  # near-white page → where the black field goes
_BG_LUMA_THRESHOLD = 24           # ≤ this luminance counts as the icon's dark field


def _infer_theme(env: Mapping[str, str]) -> Optional[str]:
    """Best-effort terminal theme from ``COLORFGBG``; None when undecidable."""

    raw = (env.get("COLORFGBG") or "").strip()
    if not raw:
        return None
    parts = raw.split(";")
    try:
        bg = int(parts[-1])
    except (ValueError, IndexError):
        return None
    # xterm convention: bg index 7 or 15 (and the bright greys) → a light terminal.
    return THEME_LIGHT if bg in (7, 15) else THEME_DARK


def resolve_theme(env: Optional[Mapping[str, str]] = None) -> str:
    """Resolve the effective theme (``dark``/``light``). Explicit override wins."""

    environ = os.environ if env is None else env
    val = (environ.get(ENV_THEME) or "").strip().lower()
    if val in (THEME_DARK, THEME_LIGHT):
        return val
    return _infer_theme(environ) or THEME_DARK


def theme_source(env: Optional[Mapping[str, str]] = None) -> str:
    """Where the resolved theme came from (for /render diagnostics)."""

    environ = os.environ if env is None else env
    val = (environ.get(ENV_THEME) or "").strip().lower()
    if val in (THEME_DARK, THEME_LIGHT):
        return THEME_SRC_EXPLICIT
    if _infer_theme(environ) is not None:
        return THEME_SRC_COLORFGBG
    return THEME_SRC_DEFAULT


def _clamp(x: int) -> int:
    return 0 if x < 0 else 255 if x > 255 else x


def _luma(rgb: m.RGB) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def remap_color(rgb: Optional[m.RGB], theme: str) -> Optional[m.RGB]:
    """Theme-remap one colour. Dark = identity; light = hue-preserving luma invert."""

    if rgb is None or theme != THEME_LIGHT:
        return rgb
    y = _luma(rgb)
    if y <= _BG_LUMA_THRESHOLD:  # the dark field → the light page colour
        return _LIGHT_PAGE
    new_y = 255.0 - y
    scale = new_y / y
    return tuple(_clamp(round(c * scale)) for c in rgb)  # type: ignore[return-value]


# --- asset paths ------------------------------------------------------------
def _avatar_assets_dir() -> Path:
    # render.py → ansi_icon → tui → forgekit_console ; assets/avatar is a sibling pkg.
    return Path(__file__).resolve().parents[2] / "assets" / "avatar"


_SOURCE_ANSI = "forgekit-avatar-ansi-source.ansi"  # raw archive (lossless original)
_DARK_ANSI = "forgekit-avatar-ansi-dark.ansi"      # runtime asset (sanitized canonical)


def ansi_source_path() -> Path:
    return _avatar_assets_dir() / _SOURCE_ANSI


def ansi_dark_path() -> Path:
    return _avatar_assets_dir() / _DARK_ANSI


def best_ansi_path() -> Optional[Path]:
    """The runtime ANSI asset to load: the baked dark variant, else the raw source."""

    dark = ansi_dark_path()
    if dark.is_file():
        return dark
    src = ansi_source_path()
    if src.is_file():
        return src
    return None


def load_ansi_text(path: Optional[Path] = None) -> Optional[str]:
    """Read the ANSI asset as decoded text (latin-1 keeps every byte). None on miss."""

    p = path or best_ansi_path()
    if p is None:
        return None
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# --- renderable -------------------------------------------------------------
def _rich_style_str(style: Style, theme: str):
    fg = remap_color(style.fg, theme)
    bg = remap_color(style.bg, theme)
    parts = []
    if style.bold:
        parts.append("bold")
    if fg is not None:
        parts.append("#{:02x}{:02x}{:02x}".format(*fg))
    if bg is not None:
        parts.append("on #{:02x}{:02x}{:02x}".format(*bg))
    return " ".join(parts) if parts else ""


def ansi_doc_to_text(doc: AnsiDoc, *, theme: str = THEME_DARK):
    """Rebuild *doc* as a Rich ``Text`` (theme-remapped). None if Rich is absent."""

    try:
        from rich.text import Text  # noqa: WPS433 - optional console extra
    except Exception:  # noqa: BLE001 - rich missing → caller degrades
        return None
    text = Text(no_wrap=True, end="")
    last = doc.height - 1
    for r, line in enumerate(doc.lines):
        for span in line:
            text.append(span.text, style=_rich_style_str(span.style, theme) or None)
        if r != last:
            text.append("\n")
    return text


@dataclass(frozen=True)
class AnsiRenderOutcome:
    """What an ANSI render attempt produced — for diagnostics + escalation."""

    status: str
    backend: str
    rejected: Tuple[str, ...] = ()
    theme: str = THEME_DARK

    @property
    def used_ansi(self) -> bool:
        return self.status == STATUS_OK


@dataclass(frozen=True)
class AnsiIconRenderer:
    """Non-raster avatar via a SANITIZED ANSI icon; degrades to braille/badge/text.

    At render time it loads the baked ANSI asset, re-sanitizes it (defense in depth —
    even our own asset is never trusted blindly), and renders the icon ONLY when the
    result is clean. A missing / empty / unsafe asset degrades down the existing
    fallback chain via :class:`tui.image_renderer.HalfBlockRenderer`, and the reason
    is surfaced (``status``) so /render and the escalator can report it.
    """

    renderer_id: str = RENDERER_ANSI_ICON
    env: Optional[Mapping[str, str]] = None
    path: Optional[Path] = None
    max_cols: int = DEFAULT_MAX_COLS  # 0 → no cap (operator-forced full ANSI)

    def _attempt(self):
        """Return ``(outcome, renderable)``. Never raises."""

        theme = resolve_theme(self.env)
        text_raw = load_ansi_text(self.path)
        if text_raw is None:
            return self._degrade(STATUS_NO_ASSET, (), theme)
        result = sanitize(text_raw)
        if not result.ok:
            return self._degrade(STATUS_INVALID, result.rejected_kinds(), theme)
        if not result.clean:
            # unsafe sequences present → refuse to render a tampered/odd asset.
            return self._degrade(STATUS_UNSAFE, result.rejected_kinds(), theme)
        if self.max_cols and result.doc.width > self.max_cols:
            # too wide for the intro slot → compact braille (force lifts the cap).
            return self._degrade(STATUS_TOO_WIDE, (), theme)
        rendered = ansi_doc_to_text(result.doc, theme=theme)
        if rendered is None:  # Rich absent → degrade (keeps the image family if it can)
            return self._degrade(STATUS_INVALID, (), theme)
        return AnsiRenderOutcome(STATUS_OK, BACKEND_ANSI_ICON, (), theme), rendered

    def _degrade(self, status: str, rejected, theme: str):
        from .. import image_renderer  # lazy → no import cycle at module load

        backend, rendered = image_renderer.HalfBlockRenderer()._resolve()
        return AnsiRenderOutcome(status, backend, tuple(rejected), theme), rendered

    # public surface (mirrors the other renderers) --------------------------
    def resolve(self):
        """``(outcome, renderable)`` in one pass — for callers that need both."""

        return self._attempt()

    def outcome(self) -> AnsiRenderOutcome:
        return self._attempt()[0]

    def renderable(self):
        return self._attempt()[1]

    def realized_backend(self) -> str:
        return self._attempt()[0].backend

    def status(self) -> str:
        return self._attempt()[0].status


__all__ = (
    "RENDERER_ANSI_ICON",
    "BACKEND_ANSI_ICON",
    "STATUS_OK",
    "STATUS_UNSAFE",
    "STATUS_INVALID",
    "STATUS_NO_ASSET",
    "STATUS_TOO_WIDE",
    "DEFAULT_MAX_COLS",
    "ENV_THEME",
    "THEME_DARK",
    "THEME_LIGHT",
    "THEME_AUTO",
    "THEME_SRC_EXPLICIT",
    "THEME_SRC_COLORFGBG",
    "THEME_SRC_DEFAULT",
    "resolve_theme",
    "theme_source",
    "remap_color",
    "ansi_source_path",
    "ansi_dark_path",
    "best_ansi_path",
    "load_ansi_text",
    "ansi_doc_to_text",
    "AnsiRenderOutcome",
    "AnsiIconRenderer",
    "probe_outcome",
)


def probe_outcome(env: Optional[Mapping[str, str]] = None) -> AnsiRenderOutcome:
    """The ANSI path's status regardless of which renderer was selected (diagnostics).

    Always probes the AUTO ANSI renderer so /render and the debug line can report the
    ANSI icon's state (ok / unsafe / invalid / no-asset / too-wide + theme) even when a
    true-raster terminal is using the real image.
    """

    return AnsiIconRenderer(env=env).outcome()
