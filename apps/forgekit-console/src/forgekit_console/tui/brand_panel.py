"""Brand panel widget — mounts the forgekit wordmark banner in the intro.

Thin textual wrapper, parallel to :class:`tui.avatar_panel.AvatarPanel`: it asks
:mod:`tui.image_renderer` for the brand renderer the terminal's capability
selected — the REAL inline wordmark banner first (graphics-capable terminals),
otherwise the compact cyan→magenta TEXT wordmark — and displays its renderable.
All the selection logic is pure and lives in ``image_renderer``; this file only
does the widget plumbing. Kept small/compact (Claude-icon scale), not the full
1916×821 master.
"""

from __future__ import annotations

from typing import Optional

from textual.widgets import Static

from . import image_renderer


class BrandPanel(Static):
    """Compact brand mark (the wordmark banner). Real image when supported."""

    DEFAULT_CSS = """
    BrandPanel {
        width: auto;
        height: auto;
        padding: 0 1 0 0;
    }
    """

    def __init__(self, *, renderer: Optional[image_renderer.AvatarRenderer] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._renderer = renderer or image_renderer.make_brand_renderer()

    @property
    def renderer_id(self) -> str:
        return self._renderer.renderer_id

    def on_mount(self) -> None:
        self.update(self._renderer.renderable())


__all__ = ("BrandPanel",)
