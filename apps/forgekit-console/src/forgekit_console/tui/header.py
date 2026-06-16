"""Intro header — small real-image avatar (left) + brand/meta (right).

Claude-Code-style first impression: a small avatar beside a few quiet lines
(``forgekit vX.Y.Z`` · provider · profile · repo path). The avatar comes from
:class:`tui.avatar_panel.AvatarPanel` (real inline image when the terminal
supports it, text mark otherwise). The right-hand text is built by the pure
:func:`tui.render.intro_meta_lines` so it's unit-testable without a terminal.
"""

from __future__ import annotations

from typing import Optional

from textual.containers import Horizontal
from textual.widgets import Static

from . import image_renderer, render
from .avatar_panel import AvatarPanel


class IntroHeader(Horizontal):
    """The top intro block: avatar column + meta column."""

    DEFAULT_CSS = """
    IntroHeader {
        height: auto;
        padding: 1 1 0 1;
    }
    IntroHeader #intro-meta {
        width: 1fr;
        height: auto;
        padding: 0 0 0 1;
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


__all__ = ("IntroHeader",)
