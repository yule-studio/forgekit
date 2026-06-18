"""ANSI icon avatar — load a baked ANSI asset, SANITIZE it, render it SAFELY.

This subpackage adds an *ANSI icon* avatar path to the console without ever
replaying raw ANSI to the terminal. The flow is strictly:

    raw ANSI text
      → :func:`sanitizer.sanitize`  (allowlist parser → internal token model;
                                     every unsafe escape is dropped + recorded)
      → :class:`model.AnsiDoc`      (printable text + a tiny safe SGR style model)
      → :func:`render.ansi_doc_to_text` (a Rich ``Text`` renderable, theme-remapped)

Responsibilities are split on purpose (the file-size / single-responsibility rule):

* :mod:`model`      — the safe internal token model (no parsing, no IO).
* :mod:`sanitizer`  — the SECURITY core: the allowlist state machine. Pure stdlib,
  so it runs in a bare CI install.
* :mod:`render`     — theme policy (dark/light/auto remap) + the Rich renderable +
  the asset loader + the :class:`render.AnsiIconRenderer`. The Rich import is
  guarded (the ``console`` extra), like :mod:`tui.halfblock`.

Nothing here is allowed to write escape bytes to stdout. The only output is a
Textual-/Rich-safe renderable rebuilt from the sanitized model.
"""

from __future__ import annotations

from .model import (
    AnsiDoc,
    SanitizeResult,
    Span,
    Style,
    REJECT_CHARSET,
    REJECT_CSI,
    REJECT_DCS,
    REJECT_LONE_ESC,
    REJECT_MALFORMED_CSI,
    REJECT_OSC,
    REJECT_UNKNOWN_ESC,
)
from .sanitizer import sanitize, serialize_clean

__all__ = (
    "AnsiDoc",
    "Span",
    "Style",
    "SanitizeResult",
    "sanitize",
    "serialize_clean",
    "REJECT_OSC",
    "REJECT_DCS",
    "REJECT_CSI",
    "REJECT_CHARSET",
    "REJECT_UNKNOWN_ESC",
    "REJECT_LONE_ESC",
    "REJECT_MALFORMED_CSI",
)
