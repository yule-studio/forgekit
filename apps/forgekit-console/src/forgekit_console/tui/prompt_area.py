"""PromptArea — a real multiline composer input (TextArea), Claude-Code keymap.

The console used to use a single-line :class:`textual.widgets.Input`, so a prompt
could never contain a newline. This widget is a thin :class:`TextArea` that keeps
the one-line *feel* (auto-height: 1 row when the text is one line, grows as lines
are added) while supporting genuine multiline editing:

* **Enter** submits the whole buffer (posts :class:`PromptArea.Submitted`) — it does
  NOT insert a newline. This is the chat-composer contract.
* **Shift+Enter** and **Ctrl+J** insert a real ``\\n`` — the buffer actually gains a
  line and the box grows. (Ctrl+J is the reliable cross-terminal newline; many
  terminals cannot distinguish Shift+Enter from Enter, so Ctrl+J is the guaranteed
  path and is what the hint advertises.)

For drop-in compatibility with the old ``Input`` call sites it exposes a ``value``
property (get/set) and ``set_value`` / ``clear`` helpers that also park the cursor
at the end. Tab / Shift+Tab / ↑ / ↓ / Esc stay app-level *priority* bindings (palette
cycle, mode switch) — this widget never steals them.
"""

from __future__ import annotations

from textual import events
from textual.widgets import TextArea


class PromptArea(TextArea):
    """Multiline composer input. Enter submits; Shift+Enter / Ctrl+J make a newline."""

    DEFAULT_CSS = """
    PromptArea {
        width: 1fr;
        height: auto;       /* 1 row for one line, grows with real newlines */
        max-height: 12;     /* bounded so the input never eats the screen … */
        overflow-y: auto;
        scrollbar-size-vertical: 0;   /* … but NO visible gutter — the only visible
                                         scroll surface is the SessionFlow. (large
                                         pastes become compact blocks, so the input
                                         rarely overflows anyway). */
        border: none;
        padding: 0;
        background: transparent;
    }
    PromptArea > .text-area--cursor-line {
        background: transparent;   /* no full-width line highlight — reads like an input bar */
    }
    """

    class Submitted(events.Message):
        """Posted when the operator presses Enter — carries the full buffer."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("show_line_numbers", False)
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("tab_behavior", "focus")  # Tab is the palette key, not indent
        super().__init__(**kwargs)

    # --- key handling -------------------------------------------------------

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        await super()._on_key(event)

    # --- Input-compatible surface ------------------------------------------

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.set_value(text)

    def set_value(self, text: str) -> None:
        """Replace the buffer and park the cursor at the end (Input.value parity)."""

        self.load_text(text or "")
        self.move_cursor(self.document.end)

    def clear(self) -> None:
        self.set_value("")


__all__ = ("PromptArea",)
