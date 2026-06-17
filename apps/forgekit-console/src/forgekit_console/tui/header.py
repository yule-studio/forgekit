"""Intro header — the forgekit BRAND MARK (wordmark banner) + small avatar + meta.

Claude-Code-style first impression: the forgekit wordmark BANNER as the brand
mark on a slim top line — a REAL inline image of the baked banner PNG on
graphics-capable terminals (via :class:`tui.brand_panel.BrandPanel`), falling
back to the compact cyan→magenta TEXT wordmark otherwise. Below it, a small avatar
(left) beside a few quiet meta lines (``forgekit vX.Y.Z`` · provider · profile ·
repo path). The avatar comes from :class:`tui.avatar_panel.AvatarPanel`; the
right-hand text is built by the pure :func:`tui.render.intro_meta_lines` so it's
unit-testable without a terminal. The banner is kept compact (small/Claude-scale),
never the full 1916×821 master.
"""

from __future__ import annotations

from typing import Optional

from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from . import image_renderer, render
from .avatar_panel import AvatarPanel


class IntroHeader(Vertical):
    """The top intro block: brand wordmark banner line + (avatar + meta) row."""

    DEFAULT_CSS = """
    IntroHeader {
        height: auto;
        padding: 0 1 0 1;   /* no top padding — a tight product header */
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
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._repo = repo
        self._version = version
        self._profile = profile
        self._provider = provider
        self._avatar = AvatarPanel(renderer=renderer, id="intro-avatar")

    @property
    def avatar_renderer_id(self) -> str:
        return self._avatar.renderer_id

    def compose(self):
        # a compact product header: small avatar (left) + 3 quiet meta lines (right).
        # The standalone wordmark banner line is dropped — branding lives in the
        # meta's "forgekit v0.1.0", keeping the top tight (Claude-style).
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
        # Off by default — no chrome unless explicitly enabled.
        if image_renderer.debug_renderers_enabled():
            diag = image_renderer.diagnose_renderers()
            yield Static(render.renderer_debug_line(diag), id="intro-renderers")


__all__ = ("IntroHeader",)
