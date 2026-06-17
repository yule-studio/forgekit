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

Module size
-----------
~750 lines (over the 700 warn line, under the 1000 split line). It is intentionally
kept whole for now: every part shares one tightly-coupled domain — capability →
backend classification → renderer → policy → diagnostics — and the renderers depend
on the backend/policy helpers, so splitting would mean a circular seam. The natural
future split, if it grows past ~1000, is to lift the diagnostics surface
(``RendererDiagnostics`` / ``diagnose_renderers`` / ``image_library_status``) into a
``render_diagnostics`` module while leaving the backend+policy primitives here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Protocol, Tuple

from . import theme

# The DEFAULT asset the console renders: the simplified TERMINAL ICON (a bold
# 2-tone headphone silhouette — Claude-icon scale, crisp on black). This is the
# stable RUNTIME ALIAS, written byte-identical to the canonical
# `forgekit-terminal-icon-128.png` by assets/avatar/bake.py, so this constant never
# churns. The detailed portrait is intentionally NOT the tiny-intro default.
_DISPLAY_PNG = "forgekit-avatar.png"  # == forgekit-terminal-icon-128.png
# The DETAILED PORTRAIT — kept for a larger/GUI surface and the opt-in portrait mode
# (`FORGEKIT_AVATAR=portrait`), NOT the terminal default. (Secondary: -96.)
_PORTRAIT_PNG = "forgekit-avatar-display-128.png"
# The portrait MASTER alias (human-replaceable; == the adopted source archive,
# byte-for-byte). Used ONLY if a baked asset is somehow absent — never the normal
# render path.
_SOURCE_JPG = "avatar-source.png"

# Renderer identifiers (returned by select_renderer; stable for tests).
RENDERER_REAL = "real-image"  # primary — true inline raster (tgp/sixel)
RENDERER_AVATAR_MARK = "avatar-mark"  # DEFAULT non-raster avatar — crisp brand badge
RENDERER_HALFBLOCK = "half-block"  # opt-in portrait half-block (FORGEKIT_AVATAR=portrait)
RENDERER_TEXT = "text-mark"  # hard fallback — last-resort text/logo mark

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
    """The DEFAULT render asset — the simplified terminal icon (runtime alias)."""

    return assets_dir() / _DISPLAY_PNG


def portrait_png_path() -> Path:
    """The DETAILED portrait — for the opt-in portrait mode / a larger surface."""

    return assets_dir() / _PORTRAIT_PNG


def source_image_path() -> Path:
    return assets_dir() / _SOURCE_JPG


def best_image_path() -> Optional[Path]:
    """The default image to render: the terminal icon, else the source, else None."""

    png = display_png_path()
    if png.is_file():
        return png
    src = source_image_path()
    if src.is_file():
        return src
    return None


def best_portrait_path() -> Optional[Path]:
    """The detailed portrait to render (portrait mode): portrait, else icon, else source."""

    portrait = portrait_png_path()
    if portrait.is_file():
        return portrait
    return best_image_path()


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
    # Non-raster default is now the crisp brand BADGE (managed fallback), not the
    # dotty portrait half-block. The portrait is opt-in (FORGEKIT_AVATAR=portrait).
    return RENDERER_REAL if capable else RENDERER_AVATAR_MARK


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
BACKEND_HALFBLOCK = "half-block"   # OUR image-derived rich-Text raster (portrait, opt-in)
BACKEND_AVATAR_MARK = "avatar-mark"  # OUR crisp brand badge — DEFAULT non-raster avatar
BACKEND_BRAND_TEXT = "brand-text"  # OUR cyan→magenta wordmark (brand fallback)
BACKEND_TEXT = "text-mark"         # OUR last-resort text/logo mark (a str)
BACKEND_NONE = "none"              # textual-image not importable at all
BACKEND_UNKNOWN = "unknown"

_TEXTUAL_IMAGE_BACKENDS = frozenset(
    {BACKEND_TGP, BACKEND_SIXEL, BACKEND_HALFCELL, BACKEND_UNICODE}
)
_TRUE_RASTER_BACKENDS = frozenset({BACKEND_TGP, BACKEND_SIXEL})

# --- render policy state (3 explicit levels) -------------------------------
#
# Operators (and doctor/debug) must be able to tell, without guessing, WHY the
# screen looks the way it does. Three states, derived purely from the realized
# backend:
#   * true-raster      — a real pixel image (tgp/sixel). Only graphics terminals.
#   * managed-fallback — forgekit DELIBERATELY chose a clean fallback because true
#     raster is unavailable: the crisp avatar BADGE / brand WORDMARK (and the opt-in
#     portrait half-block). Intentional, product-quality — NOT a degraded accident.
#   * hard-fallback    — pushed all the way down to the bare text mark because a
#     dependency / asset / the environment was missing. Something is wrong.
POLICY_TRUE_RASTER = "true-raster"
POLICY_MANAGED_FALLBACK = "managed-fallback"
POLICY_HARD_FALLBACK = "hard-fallback"

_MANAGED_FALLBACK_BACKENDS = frozenset(
    {BACKEND_AVATAR_MARK, BACKEND_BRAND_TEXT, BACKEND_HALFBLOCK}
)


def is_true_raster(backend: str) -> bool:
    """Only TGP/Sixel are real pixel rasters; everything else is a fallback."""

    return backend in _TRUE_RASTER_BACKENDS


def policy_state(backend: str) -> str:
    """Map a realized backend → its render-policy state (the 3 levels above)."""

    if is_true_raster(backend):
        return POLICY_TRUE_RASTER
    if backend in _MANAGED_FALLBACK_BACKENDS:
        return POLICY_MANAGED_FALLBACK
    return POLICY_HARD_FALLBACK  # text-mark / none / unknown


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


def prime_image_backend() -> str:
    """Resolve textual-image's backend EARLY, before Textual starts. Returns it.

    ``textual-image`` probes the terminal for sixel/TGP support the first time
    ``textual_image.renderable`` is imported — and that probe only works while stdin
    is free. Once Textual's app starts it owns stdin, so a lazy import at render time
    resolves to ``halfcell`` even on a sixel/TGP-capable terminal. Calling this from
    the entrypoint (before ``App.run()``) primes the import so the cached backend is
    the CORRECT one. Safe and idempotent: returns the chosen backend label (``none``
    if textual-image is absent); never raises.
    """

    image_cls = _textual_image_class()
    return _module_backend(image_cls) if image_cls is not None else BACKEND_NONE


# --- text/symbol fallback marks --------------------------------------------

# Last-resort text mark — two short lines, no per-pixel colour spans (those are
# what pixelate in a poor terminal). Only reached when even the brand badge can't
# render (essentially never — it is pure markup).
_TEXT_MARK: Tuple[str, ...] = (
    theme.wordmark("forgekit"),
    "[dim]operator console[/dim]",
)

# The DEFAULT non-raster avatar: a crisp, brand-safe badge. A detailed portrait
# cannot survive a ~14-col half-block (it turns to dotty mush), so instead of
# forcing a muddy portrait we show a clean framed "fk" monogram in the cyan→magenta
# brand split (forge=f=cyan, kit=k=magenta). Pure box-drawing + markup → crisp at
# any size, in any terminal, with zero dithering.
_AVATAR_MARK: Tuple[str, ...] = (
    f"[{theme.ACCENT_PRIMARY}]╭──╮[/{theme.ACCENT_PRIMARY}]",
    f"[{theme.ACCENT_PRIMARY}]│[/{theme.ACCENT_PRIMARY}]"
    f"[b {theme.ACCENT_PRIMARY}]f[/b {theme.ACCENT_PRIMARY}]"
    f"[b {theme.ACCENT_SECONDARY}]k[/b {theme.ACCENT_SECONDARY}]"
    f"[{theme.ACCENT_SECONDARY}]│[/{theme.ACCENT_SECONDARY}]",
    f"[{theme.ACCENT_SECONDARY}]╰──╯[/{theme.ACCENT_SECONDARY}]",
)


def text_mark_lines() -> Tuple[str, ...]:
    """The last-resort text mark (Rich-markup lines). Always available."""

    return _TEXT_MARK


def avatar_mark_lines() -> Tuple[str, ...]:
    """The crisp brand badge used as the DEFAULT non-raster avatar (markup lines)."""

    return _AVATAR_MARK


class AvatarRenderer(Protocol):
    """Common shape: a renderable for a textual widget + its realized backend."""

    renderer_id: str

    def renderable(self):  # pragma: no cover - protocol
        ...

    def realized_backend(self) -> str:  # pragma: no cover - protocol
        ...


@dataclass(frozen=True)
class TextMarkRenderer:
    """HARD fallback — a small clean text/symbol mark (no image at all)."""

    renderer_id: str = RENDERER_TEXT

    def renderable(self) -> str:
        return "\n".join(text_mark_lines())

    def realized_backend(self) -> str:
        return BACKEND_TEXT


@dataclass(frozen=True)
class AvatarMarkRenderer:
    """MANAGED fallback (default non-raster avatar) — the crisp brand badge.

    Always renders (pure markup), so it is the clean stand-in whenever a true
    raster portrait is unavailable. Replaces the old muddy half-block-portrait
    default; the portrait is still available via :class:`HalfBlockRenderer` for
    operators who opt in (``FORGEKIT_AVATAR=portrait``).
    """

    renderer_id: str = RENDERER_AVATAR_MARK

    def renderable(self) -> str:
        return "\n".join(avatar_mark_lines())

    def realized_backend(self) -> str:
        return BACKEND_AVATAR_MARK


@dataclass(frozen=True)
class HalfBlockRenderer:
    """OPT-IN portrait fallback — an image-DERIVED half-block of the baked PNG.

    A genuine image of the portrait (downscaled half-block raster), but at ~14 cols
    a detailed line-art face reads dotty, so this is NOT the default fallback any
    more — it is opt-in (``FORGEKIT_AVATAR=portrait``) for terminals/operators that
    prefer it. Degrades to the text mark only when Pillow / the asset is missing.
    """

    renderer_id: str = RENDERER_HALFBLOCK
    cols: int = 14  # cells wide; small & clean

    def _resolve(self):
        from . import halfblock  # local import keeps Pillow optional at import time

        # portrait mode → the DETAILED portrait (not the simplified terminal icon).
        path = best_portrait_path()
        rendered = halfblock.render_halfblock(path, cols=self.cols) if path else None
        if rendered is None:
            return BACKEND_TEXT, TextMarkRenderer().renderable()
        return BACKEND_HALFBLOCK, rendered

    def renderable(self):
        return self._resolve()[1]

    def realized_backend(self) -> str:
        return self._resolve()[0]


@dataclass(frozen=True)
class RealImageRenderer:
    """PRIMARY — a real inline image raster via ``textual-image``.

    Uses textual-image ONLY when it resolved to a TRUE-raster backend (TGP/Sixel).
    When textual-image would instead pick its own cell fallback (halfcell/unicode),
    or the lib / asset is unavailable, we fall to the crisp brand BADGE
    (:class:`AvatarMarkRenderer`) — a clean managed fallback, never a muddy cell
    render. The dotty portrait half-block is no longer an automatic step (opt-in).
    """

    renderer_id: str = RENDERER_REAL
    width: int = 14  # cells; small Claude-icon scale

    def _resolve(self):
        path = best_image_path()
        if path is None:  # no asset → still show the clean badge (managed fallback)
            return BACKEND_AVATAR_MARK, AvatarMarkRenderer().renderable()
        image_cls = _textual_image_class()
        if image_cls is None:  # textual-image absent → managed badge
            return BACKEND_AVATAR_MARK, AvatarMarkRenderer().renderable()
        backend = _module_backend(image_cls)
        if not is_true_raster(backend):
            # textual-image resolved to halfcell/unicode (not a real raster) — the
            # crisp brand badge is far cleaner than either cell fallback.
            return BACKEND_AVATAR_MARK, AvatarMarkRenderer().renderable()
        try:
            return backend, image_cls(str(path), width=self.width)
        except Exception:  # noqa: BLE001 - raster construction failed → managed badge
            return BACKEND_AVATAR_MARK, AvatarMarkRenderer().renderable()

    def renderable(self):
        return self._resolve()[1]

    def realized_backend(self) -> str:
        return self._resolve()[0]


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
    """Managed brand fallback — the cyan→magenta text wordmark (no image)."""

    renderer_id: str = RENDERER_BRAND_TEXT

    def renderable(self) -> str:
        return "\n".join(brand_wordmark_lines())

    def realized_backend(self) -> str:
        return BACKEND_BRAND_TEXT


@dataclass(frozen=True)
class BrandBannerRenderer:
    """PRIMARY brand — the forgekit wordmark banner as a real inline image.

    Renders the small baked intro banner via ``textual-image`` ONLY when it
    resolved to a TRUE-raster backend (TGP/Sixel). Otherwise — textual-image cell
    fallback, missing lib, or unreadable asset — it degrades to the compact TEXT
    wordmark (the cyan→magenta gradient), which is far cleaner than a halfcell/
    unicode banner. The wordmark is the intended managed fallback.
    """

    renderer_id: str = RENDERER_BRAND_IMAGE
    width: int = 28  # cells; compact, small banner — not the full 1916px master

    def _resolve(self):
        path = best_banner_path()
        if path is None:
            return BACKEND_BRAND_TEXT, BrandTextRenderer().renderable()
        image_cls = _textual_image_class()
        if image_cls is None:  # textual-image absent → text wordmark
            return BACKEND_BRAND_TEXT, BrandTextRenderer().renderable()
        backend = _module_backend(image_cls)
        if not is_true_raster(backend):
            # not a true raster (halfcell/unicode) → the clean text wordmark wins.
            return BACKEND_BRAND_TEXT, BrandTextRenderer().renderable()
        try:
            return backend, image_cls(str(path), width=self.width)
        except Exception:  # noqa: BLE001 - raster construction failed → text wordmark
            return BACKEND_BRAND_TEXT, BrandTextRenderer().renderable()

    def renderable(self):
        return self._resolve()[1]

    def realized_backend(self) -> str:
        return self._resolve()[0]


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


def _avatar_force_mode(env: Optional[Mapping[str, str]] = None) -> str:
    """The ``FORGEKIT_AVATAR`` override value, lowercased ("" if unset)."""

    environ = os.environ if env is None else env
    return (environ.get(_FORCE_ENV) or "").strip().lower()


# Asset-mode labels (which avatar ASSET the path uses, separate from the backend).
ASSET_TERMINAL_ICON = "terminal-icon"  # simplified icon (default tiny-intro asset)
ASSET_PORTRAIT = "portrait"            # detailed portrait (opt-in / larger surface)


def avatar_asset_mode(env: Optional[Mapping[str, str]] = None) -> str:
    """Which avatar ASSET the default path renders: terminal-icon vs portrait.

    The console's tiny-intro DEFAULT is the simplified terminal icon; the detailed
    portrait is opt-in (``FORGEKIT_AVATAR=portrait``). This reports that policy so
    debug/readiness can say "tiny icon policy" vs "portrait mode".
    """

    if _avatar_force_mode(env) in ("portrait", "halfblock", "half-block"):
        return ASSET_PORTRAIT
    return ASSET_TERMINAL_ICON


def make_renderer(
    capability=None,
    *,
    width: int = 12,
    env: Optional[Mapping[str, str]] = None,
) -> AvatarRenderer:
    """Build the avatar renderer for *capability* / *env*.

    Default policy: a true-raster terminal → real image; otherwise the crisp brand
    BADGE (managed fallback — a dotty portrait is not forced). Operators can
    override via ``FORGEKIT_AVATAR``: ``image`` (force raster attempt),
    ``portrait``/``halfblock`` (the image-derived half-block), ``mark``/``badge``
    (the brand badge), ``text`` (the bare text mark).
    """

    mode = _avatar_force_mode(env)
    if mode in ("portrait", "halfblock", "half-block"):
        return HalfBlockRenderer()
    if mode in ("mark", "badge"):
        return AvatarMarkRenderer()
    if mode in ("text", "none"):
        return TextMarkRenderer()

    if capability is None:
        capability = detect_image_capability(env)
    renderer_id = select_renderer(capability)
    if renderer_id == RENDERER_REAL:
        return RealImageRenderer(width=width)
    if renderer_id == RENDERER_AVATAR_MARK:
        return AvatarMarkRenderer()
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
    """What was selected, the REAL backend rendered, and the POLICY state."""

    avatar_selected: str       # our renderer choice: real-image / avatar-mark / half-block / text-mark
    avatar_backend: str        # realized backend (tgp/sixel/avatar-mark/half-block/text-mark)
    avatar_true_raster: bool   # is the avatar an actual pixel raster?
    avatar_policy: str         # true-raster / managed-fallback / hard-fallback
    brand_selected: str        # brand-image / brand-text
    brand_backend: str
    brand_true_raster: bool
    brand_policy: str
    capability_reason: str
    lib_ok: bool               # textual-image importable (NOT the same as true raster)
    lib_reason: str
    lib_backend: str           # backend textual-image WOULD use (tgp/sixel/halfcell/unicode/none)


def diagnose_renderers(env: Optional[Mapping[str, str]] = None) -> RendererDiagnostics:
    """Build the renderer diagnostics for *env* (defaults to the live environment).

    Mirrors what the intro panels do (same ``make_renderer`` / ``make_brand_renderer``
    selection), asks each renderer for its REALIZED backend, and derives the policy
    state — so debug/doctor reflect the real screen and the deliberate policy, never
    calling a managed fallback a "real image".
    """

    cap = detect_image_capability(env)
    avatar = make_renderer(env=env)
    brand = make_brand_renderer(env=env)
    lib_ok, lib_reason, lib_backend = image_library_status()
    avatar_backend = avatar.realized_backend()
    brand_backend = brand.realized_backend()
    return RendererDiagnostics(
        avatar_selected=avatar.renderer_id,
        avatar_backend=avatar_backend,
        avatar_true_raster=is_true_raster(avatar_backend),
        avatar_policy=policy_state(avatar_backend),
        brand_selected=brand.renderer_id,
        brand_backend=brand_backend,
        brand_true_raster=is_true_raster(brand_backend),
        brand_policy=policy_state(brand_backend),
        capability_reason=cap.reason,
        lib_ok=lib_ok,
        lib_reason=lib_reason,
        lib_backend=lib_backend,
    )


__all__ = (
    "RENDERER_REAL",
    "RENDERER_AVATAR_MARK",
    "RENDERER_HALFBLOCK",
    "RENDERER_TEXT",
    "RENDERER_BRAND_IMAGE",
    "RENDERER_BRAND_TEXT",
    "ImageCapability",
    "AvatarRenderer",
    "RealImageRenderer",
    "AvatarMarkRenderer",
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
    "prime_image_backend",
    "is_true_raster",
    "policy_state",
    "renderable_backend",
    "POLICY_TRUE_RASTER",
    "POLICY_MANAGED_FALLBACK",
    "POLICY_HARD_FALLBACK",
    "BACKEND_TGP",
    "BACKEND_SIXEL",
    "BACKEND_HALFCELL",
    "BACKEND_UNICODE",
    "BACKEND_HALFBLOCK",
    "BACKEND_AVATAR_MARK",
    "BACKEND_BRAND_TEXT",
    "BACKEND_TEXT",
    "BACKEND_NONE",
    "BACKEND_UNKNOWN",
    "RendererDiagnostics",
    "diagnose_renderers",
    "avatar_mark_lines",
    "text_mark_lines",
    "brand_wordmark_lines",
    "assets_dir",
    "brand_dir",
    "display_png_path",
    "portrait_png_path",
    "source_image_path",
    "best_image_path",
    "best_portrait_path",
    "avatar_asset_mode",
    "banner_intro_path",
    "banner_master_path",
    "best_banner_path",
)
