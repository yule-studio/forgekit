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

# The asset the console RENDERS: the baked small display PNG (Claude-icon scale).
# This is the stable RUNTIME ALIAS, written byte-identical to the canonical
# `forgekit-avatar-display-128.png` by assets/avatar/bake.py. The renderer loads
# the alias (not the dated/canonical name) so this constant never churns; it is
# never the raw master. (Secondary: `forgekit-avatar-96.png` == display-96.)
_DISPLAY_PNG = "forgekit-avatar.png"
# The portrait MASTER alias (human-replaceable; == the adopted source archive,
# byte-for-byte). Used ONLY if the baked display PNG is somehow absent — the
# master is never the normal render path.
_SOURCE_JPG = "avatar-source.png"

# Renderer identifiers (returned by select_renderer; stable for tests).
RENDERER_REAL = "real-image"  # tier 1 — inline raster
RENDERER_HALFBLOCK = "half-block"  # tier 2 — image-derived unicode half-block
RENDERER_TEXT = "text-mark"  # tier 3 — last-resort text/logo mark

# Env var to force a path regardless of detection (operator/testing override).
#   FORGEKIT_AVATAR=image  → force real image
#   FORGEKIT_AVATAR=text   → force text mark
_FORCE_ENV = "FORGEKIT_AVATAR"

# Diagnostic flag: when set, the intro shows the SELECTED→REALIZED renderer ids so
# an operator can tell at a glance whether the real inline image actually rendered
# or silently degraded. Off by default (no chrome).
#   FORGEKIT_DEBUG_RENDERERS=1
_DEBUG_ENV = "FORGEKIT_DEBUG_RENDERERS"

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


# --- textual-image backend classification ----------------------------------
#
# ``textual-image`` binds ``textual_image.renderable.Image`` AT IMPORT TIME to one
# of four backend classes, chosen by probing the terminal:
#   * tgp      — Terminal Graphics Protocol (Kitty)     → TRUE pixel raster
#   * sixel    — Sixel (xterm/mlterm/foot/WezTerm…)     → TRUE pixel raster
#   * halfcell — Unicode half-cells, per-terminal-cell  → FALLBACK (cell/dot look)
#   * unicode  — plain unicode (no tty / no graphics)   → FALLBACK
# Only tgp/sixel are real images; halfcell/unicode are textual-image's OWN cell
# fallbacks (they look "broken" at avatar scale). So "is it a textual_image.Image"
# is NOT enough — we must look at WHICH backend class it is. The probe also only
# works *before* Textual grabs stdin, so a late import (after the app starts) tends
# to resolve to halfcell even on capable terminals — hence we never assume raster.

BACKEND_TGP = "tgp"
BACKEND_SIXEL = "sixel"
BACKEND_HALFCELL = "halfcell"
BACKEND_UNICODE = "unicode"
BACKEND_HALFBLOCK = "half-block"  # OUR image-derived rich-Text raster (tier 2)
BACKEND_TEXT = "text-mark"        # OUR text/logo mark (tier 3, a str)
BACKEND_NONE = "none"             # textual-image not importable at all
BACKEND_UNKNOWN = "unknown"

_TEXTUAL_IMAGE_BACKENDS = frozenset(
    {BACKEND_TGP, BACKEND_SIXEL, BACKEND_HALFCELL, BACKEND_UNICODE}
)
_TRUE_RASTER_BACKENDS = frozenset({BACKEND_TGP, BACKEND_SIXEL})


def is_true_raster(backend: str) -> bool:
    """Only TGP/Sixel are real pixel rasters; everything else is a fallback."""

    return backend in _TRUE_RASTER_BACKENDS


def _module_backend(cls_or_obj) -> str:
    """Map a textual-image class/instance to its backend label via ``__module__``.

    Import-free (pure string check on the module path) so it is safe to call in
    environments without rich/Pillow/textual-image installed.
    """

    mod = getattr(cls_or_obj, "__module__", "") or ""
    if mod.startswith("textual_image.renderable."):
        name = mod.rsplit(".", 1)[-1]
        return name if name in _TEXTUAL_IMAGE_BACKENDS else BACKEND_UNKNOWN
    return BACKEND_UNKNOWN


def renderable_backend(obj) -> str:
    """Classify a ``renderable()`` RESULT into its real backend label.

    No imports: distinguishes our own outputs (str text mark, rich-Text half-block)
    from textual-image's four backends purely by type/module name.
    """

    if obj is None or isinstance(obj, str):
        return BACKEND_TEXT
    mod = (type(obj).__module__ or "")
    if mod.startswith("textual_image.renderable."):
        return _module_backend(type(obj))
    if mod.startswith("rich."):  # our HalfBlockRenderer returns a rich.text.Text
        return BACKEND_HALFBLOCK
    return BACKEND_UNKNOWN


def _textual_image_class():
    """Import and return textual-image's resolved ``Image`` class, or ``None``.

    The class is whatever backend textual-image bound at import time for THIS
    process/terminal — inspect ``_module_backend`` on it to see tgp/sixel/halfcell/
    unicode.
    """

    try:
        from textual_image.renderable import Image  # noqa: WPS433
    except Exception:  # noqa: BLE001 - not importable (absent / wrong python)
        return None
    return Image


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

    Uses textual-image ONLY when it resolved to a TRUE-raster backend (TGP/Sixel).
    When textual-image would instead pick its own cell fallback (halfcell/unicode —
    which looks worse than our tuned, image-derived half-block), or when the lib /
    asset is unavailable, we fall to OUR half-block (tier 2), then the text mark
    (tier 3). So we never render textual-image's muddy cell fallback in place of a
    cleaner one we control.
    """

    renderer_id: str = RENDERER_REAL
    width: int = 14  # cells; small Claude-icon scale, aligned with the half-block tier

    def renderable(self):
        path = best_image_path()
        if path is None:
            return TextMarkRenderer().renderable()
        image_cls = _textual_image_class()
        if image_cls is None:  # textual-image absent → our image-derived tier 2
            return HalfBlockRenderer().renderable()
        if not is_true_raster(_module_backend(image_cls)):
            # textual-image resolved to halfcell/unicode (not a real raster) — prefer
            # OUR cleaner half-block over its cell fallback.
            return HalfBlockRenderer().renderable()
        try:
            return image_cls(str(path), width=self.width)
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

    Renders the small baked intro banner via ``textual-image`` ONLY when it
    resolved to a TRUE-raster backend (TGP/Sixel). Otherwise — textual-image cell
    fallback, missing lib, or unreadable asset — it degrades to the compact TEXT
    wordmark (the cyan→magenta gradient), which is far cleaner than a halfcell/
    unicode banner. The wordmark is the intended fallback, so there is no half-block
    tier here.
    """

    renderer_id: str = RENDERER_BRAND_IMAGE
    width: int = 28  # cells; compact, small banner — not the full 1916px master

    def renderable(self):
        path = best_banner_path()
        if path is None:
            return BrandTextRenderer().renderable()
        image_cls = _textual_image_class()
        if image_cls is None:  # textual-image absent → text wordmark
            return BrandTextRenderer().renderable()
        if not is_true_raster(_module_backend(image_cls)):
            # not a true raster (halfcell/unicode) → the clean text wordmark wins.
            return BrandTextRenderer().renderable()
        try:
            return image_cls(str(path), width=self.width)
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


# --- diagnostics (FORGEKIT_DEBUG_RENDERERS) --------------------------------
#
# The honest diagnosis separates FOUR independent things that the old "non-str ⇒
# real-image" check wrongly collapsed into one:
#   1. library import   — is ``textual-image`` importable at all? (lib_ok)
#   2. capability detect — what did our heuristic guess for the terminal? (capability_reason)
#   3. CHOSEN backend    — which class textual-image actually bound (tgp/sixel/
#      halfcell/unicode), i.e. what it WOULD draw. (lib_backend)
#   4. REALIZED backend  — what forgekit actually renders after policy (tgp/sixel =
#      true raster; halfcell/unicode/half-block/text-mark = fallback). (*_backend)
# Only (4) tells the operator whether the screen is a real pixel image.


def debug_renderers_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """True when ``FORGEKIT_DEBUG_RENDERERS`` is set truthy. Pure given *env*."""

    environ = os.environ if env is None else env
    return (environ.get(_DEBUG_ENV) or "").strip().lower() in ("1", "true", "on", "yes")


def image_library_status() -> Tuple[bool, str, str]:
    """``(importable, reason, chosen_backend)`` for textual-image's inline raster.

    Import success means ONLY that the library is usable — NOT that a true raster
    will render. ``chosen_backend`` is the backend textual-image resolved for this
    process/terminal (tgp/sixel = true raster; halfcell/unicode = its own cell
    fallback; ``none`` when not importable). Splitting these prevents the old false
    positive where "importable" was read as "real image".
    """

    image_cls = _textual_image_class()
    if image_cls is None:
        # re-probe to surface WHY (kept cheap; only runs under the debug flag).
        try:
            from textual_image.renderable import Image as _Probe  # noqa: F401,WPS433
            reason = "textual-image import ok"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}", BACKEND_NONE
        return True, reason, BACKEND_UNKNOWN
    return True, "textual-image import ok", _module_backend(image_cls)


@dataclass(frozen=True)
class RendererDiagnostics:
    """What was selected vs the REAL backend rendered, for the debug line."""

    avatar_selected: str       # our tier choice: real-image / half-block / text-mark
    avatar_backend: str        # realized backend (tgp/sixel/halfcell/unicode/half-block/text-mark)
    avatar_true_raster: bool   # is the avatar an actual pixel raster?
    brand_selected: str        # brand-image / brand-text
    brand_backend: str
    brand_true_raster: bool
    capability_reason: str
    lib_ok: bool               # textual-image importable (NOT the same as true raster)
    lib_reason: str
    lib_backend: str           # backend textual-image WOULD use (tgp/sixel/halfcell/unicode/none)


def diagnose_renderers(env: Optional[Mapping[str, str]] = None) -> RendererDiagnostics:
    """Build the renderer diagnostics for *env* (defaults to the live environment).

    Mirrors what the intro panels do (same ``make_renderer`` / ``make_brand_renderer``
    selection), renders once to capture the REALIZED backend, and separately records
    the backend textual-image itself resolved — so the debug line reflects the real
    screen, not just intent, and never calls halfcell/unicode a "real image".
    """

    cap = detect_image_capability(env)
    avatar = make_renderer(env=env)
    brand = make_brand_renderer(env=env)
    lib_ok, lib_reason, lib_backend = image_library_status()
    avatar_backend = renderable_backend(avatar.renderable())
    brand_backend = renderable_backend(brand.renderable())
    return RendererDiagnostics(
        avatar_selected=avatar.renderer_id,
        avatar_backend=avatar_backend,
        avatar_true_raster=is_true_raster(avatar_backend),
        brand_selected=brand.renderer_id,
        brand_backend=brand_backend,
        brand_true_raster=is_true_raster(brand_backend),
        capability_reason=cap.reason,
        lib_ok=lib_ok,
        lib_reason=lib_reason,
        lib_backend=lib_backend,
    )


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
    "debug_renderers_enabled",
    "image_library_status",
    "is_true_raster",
    "renderable_backend",
    "BACKEND_TGP",
    "BACKEND_SIXEL",
    "BACKEND_HALFCELL",
    "BACKEND_UNICODE",
    "BACKEND_HALFBLOCK",
    "BACKEND_TEXT",
    "BACKEND_NONE",
    "BACKEND_UNKNOWN",
    "RendererDiagnostics",
    "diagnose_renderers",
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
