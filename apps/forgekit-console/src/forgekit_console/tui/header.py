"""Intro header — TWO modes: a wide HERO art, or a Claude-style COMPACT header.

The header is a state machine driven by the app (see :mod:`tui.intro_state`):

* **compact** (the working default) — a small avatar (left) beside a few quiet meta
  lines (``forgekit vX.Y.Z`` · provider · profile · repo). Tight and out of the way
  so the composer is the star, exactly like Claude Code.
* **hero** (first impression / ``/about``) — the WIDE 56-col hero art
  (:class:`tui.hero_panel.HeroPanel`) shown at full width with a one-line brand
  caption. This is where the dense 56-col art is meant to live, NOT the tiny slot.

Both are composed once; :meth:`set_mode` toggles which is displayed. The avatar
(compact) is :class:`tui.avatar_panel.AvatarPanel`; the meta text is the pure
:func:`tui.render.intro_meta_lines` (unit-testable without a terminal).
"""

from __future__ import annotations

from typing import Mapping, Optional

from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from . import hero_art, image_renderer, intro_state, render
from .avatar_panel import AvatarPanel
from .hero_panel import HeroPanel


class IntroHeader(Vertical):
    """Top intro block — a HERO art view XOR a COMPACT (avatar + meta) view."""

    DEFAULT_CSS = """
    IntroHeader {
        height: auto;
        padding: 0 1 0 1;   /* no top padding — a tight product header */
    }
    IntroHeader #intro-hero {
        height: auto;
    }
    IntroHeader #intro-hero-caption {
        height: auto;
        text-align: center;
        padding: 0 0 1 0;
    }
    IntroHeader #intro-body {
        height: auto;
    }
    IntroHeader #intro-meta {
        width: 1fr;
        height: auto;
        padding: 0 0 0 1;
    }
    IntroHeader #intro-renderers {
        height: auto;
        padding: 0 0 0 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        *,
        repo: str,
        version: str,
        profile: str,
        provider: str = "—",
        renderer: Optional[image_renderer.AvatarRenderer] = None,
        brand_renderer: Optional[image_renderer.AvatarRenderer] = None,  # accepted, unused (compat)
        env: Optional[Mapping[str, str]] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._repo = repo
        self._version = version
        self._profile = profile
        self._provider = provider
        self._env = env
        self._avatar = AvatarPanel(renderer=renderer, id="intro-avatar")
        self._hero = HeroPanel(env=env, id="intro-hero")
        self._mode = intro_state.INTRO_COMPACT

    @property
    def avatar_renderer_id(self) -> str:
        return self._avatar.renderer_id

    @property
    def mode(self) -> str:
        return self._mode

    def hero_available(self) -> bool:
        return hero_art.hero_available(self._env)

    def compose(self):
        # HERO view (wide art + a one-line brand caption) — hidden until set_mode.
        with Vertical(id="intro-hero-wrap"):
            yield self._hero
            yield Static(
                f"{render.theme.wordmark('forgekit')} [dim]v{self._version}[/dim]"
                f"  [dim]· {self._profile} · /about[/dim]",
                id="intro-hero-caption",
            )
        # COMPACT view — small avatar (left) + 3 quiet meta lines (right).
        with Horizontal(id="intro-body"):
            yield self._avatar
            yield Static(
                "\n".join(
                    render.intro_meta_lines(
                        repo=self._repo,
                        version=self._version,
                        profile=self._profile,
                        provider=self._provider,
                    )
                ),
                id="intro-meta",
            )
        # opt-in diagnostic: SELECTED→REALIZED renderer ids (FORGEKIT_DEBUG_RENDERERS).
        if image_renderer.debug_renderers_enabled():
            diag = image_renderer.diagnose_renderers()
            yield Static(render.renderer_debug_line(diag), id="intro-renderers")

    def on_mount(self) -> None:
        self._apply_mode()

    def set_mode(self, mode: str) -> None:
        """Switch between ``hero`` and ``compact`` (no-op if already there)."""

        if mode == self._mode:
            return
        self._mode = mode
        self._apply_mode()

    def _apply_mode(self) -> None:
        hero = self._mode == intro_state.INTRO_HERO
        try:
            self.query_one("#intro-hero-wrap").display = hero
            self.query_one("#intro-body").display = not hero
        except Exception:  # noqa: BLE001 - not mounted yet (set_mode before mount)
            pass


__all__ = ("IntroHeader",)
