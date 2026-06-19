"""UI run mode — full-screen (alt-screen) vs inline (terminal-flow). Pure resolution.

AUDIT (verified against textual 8.2.7, not assumed):
- the DEFAULT ``App.run()`` uses the **alternate screen** (LinuxDriver writes
  ``\\x1b[?1049h``) + mouse capture → no native scrollback, no drag-select.
- ``App.run(inline=True, inline_no_clear=True, mouse=False)`` uses the
  ``LinuxInlineDriver`` which does **NOT** enter the alt-screen (no ``?1049h``) → the
  terminal's native scrollback is preserved, and ``mouse=False`` is honoured (gated by
  the driver's ``self._mouse``) so the terminal handles drag-select/copy itself.

So ``inline`` is a real, Textual-supported mode — not a guess. This module is the pure
chooser: ``FORGEKIT_UI_MODE=full|inline|auto`` (env) and an explicit CLI flag pick the
mode, and :func:`run_kwargs` maps it to the actual ``App.run`` keyword arguments.

``auto`` is HONEST: it does not guess a terminal's preference — it resolves to ``full``
(the default, fully-tested experience). Inline is opt-in via ``--inline`` /
``FORGEKIT_UI_MODE=inline`` until a confident auto-signal exists.
"""

from __future__ import annotations

from typing import Mapping, Optional

MODE_FULL = "full"      # full-screen alternate-screen TUI (default, power-user)
MODE_INLINE = "inline"  # inline terminal-flow (native scrollback + selection friendly)
MODE_AUTO = "auto"

ENV_UI_MODE = "FORGEKIT_UI_MODE"
_VALID = (MODE_FULL, MODE_INLINE, MODE_AUTO)


def resolve_ui_mode(env: Optional[Mapping[str, str]] = None, *, cli: Optional[str] = None) -> str:
    """Resolve the effective UI mode → ``full`` or ``inline``.

    Priority: explicit CLI flag → ``FORGEKIT_UI_MODE`` env → default ``full``.
    ``auto`` resolves to ``full`` (no guessing — inline is opt-in).
    """

    chosen = (cli or "").strip().lower()
    if chosen not in _VALID:
        environ = env or {}
        chosen = str(environ.get(ENV_UI_MODE, "") or "").strip().lower()
    if chosen == MODE_INLINE:
        return MODE_INLINE
    # full / auto / anything-unknown → full (the conservative, tested default)
    return MODE_FULL


def run_kwargs(mode: str) -> dict:
    """The ``App.run`` keyword arguments for *mode*.

    inline → ``LinuxInlineDriver`` (no alt-screen) + ``inline_no_clear`` (leave the final
    frame in scrollback on exit) + ``mouse=False`` (terminal owns drag-select/copy).
    """

    if mode == MODE_INLINE:
        return {"inline": True, "inline_no_clear": True, "mouse": False}
    return {}


def is_inline(mode: str) -> bool:
    return mode == MODE_INLINE


__all__ = (
    "MODE_FULL", "MODE_INLINE", "MODE_AUTO", "ENV_UI_MODE",
    "resolve_ui_mode", "run_kwargs", "is_inline",
)
