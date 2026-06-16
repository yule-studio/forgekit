"""Avatar panel widget — mounts the chosen avatar renderer in the intro.

Thin textual wrapper: it asks :mod:`tui.image_renderer` for the renderer the
terminal's capability selected (real inline image first, text mark fallback) and
displays its renderable. All the selection logic is pure and lives in
``image_renderer``; this file only does the widget plumbing.
"""

from __future__ import annotations

from typing import Optional

from textual.widgets import Static

from . import image_renderer


class AvatarPanel(Static):
    """Small avatar mark (left of the intro). Real image when supported."""

    DEFAULT_CSS = """
    AvatarPanel {
        width: auto;
        height: auto;
        min-width: 6;
        padding: 0 1 0 0;
    }
    """

    def __init__(self, *, renderer: Optional[image_renderer.AvatarRenderer] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._renderer = renderer or image_renderer.make_renderer()

    @property
    def renderer_id(self) -> str:
        return self._renderer.renderer_id

    def on_mount(self) -> None:
        self.update(self._renderer.renderable())


__all__ = ("AvatarPanel",)
