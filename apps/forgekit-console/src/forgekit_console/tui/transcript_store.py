"""Transcript store — the COPYABLE plain-text model behind the visual transcript.

The :class:`tui.transcript.Transcript` widget is for DISPLAY (Rich markup, RichLog
strips). It is not a good copy source: a full-screen TUI captures the mouse, so the
operator cannot drag-select the rendered text, and the rendered text carries markup.

This store is the separate, pure copy model: as the app writes turns it records each
block's PLAIN text (markup stripped) with a role and a 1-based index, so the operator
can copy exactly what they mean — ``/copy last`` (last response), ``/copy block <n>``,
``/copy turn <n>`` (a user→response pair), or ``/copy all``. No textual import → pure
and unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

ROLE_USER = "user"
ROLE_RESPONSE = "response"
ROLE_SYSTEM = "system"

_MARKUP = re.compile(r"\[/?[^\[\]]*\]")   # Rich markup tags: [b], [/b], [dim], [#00d8f0] …


def strip_markup(text: str) -> str:
    """Drop Rich markup tags → the plain text an operator would actually paste."""

    return _MARKUP.sub("", text or "")


@dataclass(frozen=True)
class Block:
    index: int     # 1-based block number (stable copy handle)
    role: str      # ROLE_USER / ROLE_RESPONSE / ROLE_SYSTEM
    text: str      # plain text (markup already stripped)


@dataclass
class TranscriptStore:
    """Append-only plain-text record of transcript blocks, indexable for copy."""

    _blocks: List[Block] = field(default_factory=list)

    def add(self, role: str, text: str) -> Optional[Block]:
        plain = strip_markup(text).rstrip("\n")
        if not plain.strip():
            return None
        block = Block(index=len(self._blocks) + 1, role=role, text=plain)
        self._blocks.append(block)
        return block

    def add_user(self, text: str) -> Optional[Block]:
        return self.add(ROLE_USER, text)

    def add_response(self, text: str) -> Optional[Block]:
        return self.add(ROLE_RESPONSE, text)

    def add_lines(self, role: str, lines) -> Optional[Block]:
        return self.add(role, "\n".join(str(l) for l in lines))

    @property
    def blocks(self):
        return tuple(self._blocks)

    def last_response(self) -> Optional[str]:
        for b in reversed(self._blocks):
            if b.role == ROLE_RESPONSE:
                return b.text
        return None

    def block(self, n: int) -> Optional[Block]:
        if n < 1 or n > len(self._blocks):
            return None
        return self._blocks[n - 1]

    def turn(self, n: int) -> Optional[str]:
        """The nth turn (1-based): a user block + everything up to the next user block."""

        turns = []
        cur: List[Block] = []
        for b in self._blocks:
            if b.role == ROLE_USER and cur:
                turns.append(cur)
                cur = []
            cur.append(b)
        if cur:
            turns.append(cur)
        if n < 1 or n > len(turns):
            return None
        return "\n".join(b.text for b in turns[n - 1])

    def all_text(self) -> str:
        return "\n\n".join(b.text for b in self._blocks)

    def clear(self) -> None:
        self._blocks.clear()


__all__ = ("Block", "TranscriptStore", "strip_markup",
           "ROLE_USER", "ROLE_RESPONSE", "ROLE_SYSTEM")
