"""Safe internal token model for a sanitized ANSI icon.

These dataclasses are the ONLY representation that leaves the sanitizer — a doc is
a grid of text spans each carrying a tiny, closed style (foreground / background
truecolor + bold). There is deliberately NO field that can encode a cursor move,
an OSC string, a hyperlink, a clipboard write, or any escape sequence: by
construction the model cannot express anything unsafe, so a renderer built from it
can never replay one.

Pure stdlib (dataclasses + typing) → importable in a bare CI install.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

RGB = Tuple[int, int, int]

# Reason labels recorded when the sanitizer drops an unsafe / malformed sequence.
# Stable strings so diagnostics and tests can assert on them.
REJECT_OSC = "osc"                 # ESC ] … (incl. OSC 8 hyperlink, OSC 52 clipboard)
REJECT_DCS = "dcs-string"          # DCS / PM / APC / SOS string controls
REJECT_CSI = "csi-control"         # CSI final byte that is NOT SGR `m` (cursor/erase/mode)
REJECT_CHARSET = "charset"         # ESC ( ) * + designators
REJECT_UNKNOWN_ESC = "unknown-esc"  # any other ESC x
REJECT_LONE_ESC = "lone-esc"       # trailing/truncated ESC
REJECT_MALFORMED_CSI = "malformed-csi"  # CSI without a valid final byte


@dataclass(frozen=True)
class Style:
    """A closed, safe per-span style — colour + bold only. No escape semantics."""

    fg: Optional[RGB] = None  # None → terminal default foreground
    bg: Optional[RGB] = None  # None → terminal default background
    bold: bool = False

    def key(self) -> Tuple:
        return (self.fg, self.bg, self.bold)


# The reset style (SGR 0) — the default the sanitizer starts each doc with.
RESET_STYLE = Style()


@dataclass(frozen=True)
class Span:
    """A run of printable text under one :class:`Style`."""

    text: str
    style: Style = RESET_STYLE


@dataclass(frozen=True)
class AnsiDoc:
    """A sanitized ANSI icon — rows of spans. The safe unit a renderer consumes."""

    lines: Tuple[Tuple[Span, ...], ...] = ()

    @property
    def height(self) -> int:
        return len(self.lines)

    @property
    def width(self) -> int:
        return max((sum(len(s.text) for s in line) for line in self.lines), default=0)

    @property
    def is_empty(self) -> bool:
        return not any(any(s.text for s in line) for line in self.lines)


@dataclass(frozen=True)
class SanitizeResult:
    """The sanitizer's verdict: the safe doc + every unsafe thing it dropped.

    ``ok`` means a non-empty doc was parsed. ``rejected`` lists the reason labels
    for every unsafe/malformed sequence that was STRIPPED (never replayed); a clean
    asset yields an empty tuple. ``clean`` is True only when a non-empty doc parsed
    AND nothing had to be stripped — the property a shipped/baked asset must hold.
    """

    doc: AnsiDoc
    ok: bool
    rejected: Tuple[str, ...] = ()
    reason: str = ""

    @property
    def clean(self) -> bool:
        return self.ok and not self.rejected

    def rejected_kinds(self) -> Tuple[str, ...]:
        """Distinct reject labels, order-preserving (for compact diagnostics)."""

        return tuple(dict.fromkeys(self.rejected))


__all__ = (
    "RGB",
    "Style",
    "RESET_STYLE",
    "Span",
    "AnsiDoc",
    "SanitizeResult",
    "REJECT_OSC",
    "REJECT_DCS",
    "REJECT_CSI",
    "REJECT_CHARSET",
    "REJECT_UNKNOWN_ESC",
    "REJECT_LONE_ESC",
    "REJECT_MALFORMED_CSI",
)
