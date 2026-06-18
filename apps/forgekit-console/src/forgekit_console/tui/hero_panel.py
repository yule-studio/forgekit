"""Hero panel widget — mounts the WIDE hero art (the 56-col unicode-subset icon).

Thin textual wrapper around :mod:`tui.hero_art`: it loads the sanitized hero lines
and renders them as a Rich ``Text`` centered in the available width, preserving the
original art width (no compression). This is the BIG surface the 56-col art was made
for — the empty first-impression intro and ``/about`` — NOT the tiny avatar slot.

If the asset / Rich is unavailable it shows a quiet compact brand fallback so the
hero surface is never an empty box; in practice the intro state machine only routes
here when :func:`tui.hero_art.hero_available` is true.
"""

from __future__ import annotations

from typing import Mapping, Optional

from textual.widgets import Static

from . import hero_art, theme


class HeroPanel(Static):
    """Wide hero art, centered. The 56-col icon shown at full width (not shrunk)."""

    DEFAULT_CSS = """
    HeroPanel {
        width: 1fr;
        height: auto;
        content-align: center top;
        text-align: center;
        padding: 0 0 1 0;
    }
    """

    def __init__(self, *, env: Optional[Mapping[str, str]] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._env = env

    def on_mount(self) -> None:
        self.update(self.renderable())

    def renderable(self):
        art = hero_art.hero_renderable(self._env)
        if art is not None:
            return art
        # asset / Rich absent → a quiet compact brand mark (never an empty hero box).
        return theme.wordmark("forgekit")


__all__ = ("HeroPanel",)
