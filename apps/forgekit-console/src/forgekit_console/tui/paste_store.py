"""Paste payload store — large pastes kept as REAL retrievable payloads, not placeholders.

A long paste is shown in the transcript as a compact block (`[Pasted #3 · 255 lines]`)
but its RAW text is preserved here, addressable by id, so the operator can later
``/paste expand <id>`` (see the full body), ``/paste resend <id>`` (re-submit the raw),
or ``/copy paste <id>`` (copy the raw, NOT the placeholder). Screen representation and
stored payload are separated — "paste 성공" means the raw was preserved.

Pure / stdlib → unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import ingest


@dataclass(frozen=True)
class PastePayload:
    id: int
    kind: str          # ingest.KIND_TEXT (image pastes go through the attachment store)
    raw_text: str
    line_count: int
    char_count: int
    source: str        # ingest.SRC_*
    preview: str

    def compact_label(self) -> str:
        return f"[Pasted #{self.id} · {self.line_count} lines · {self.char_count} chars] {self.preview}"


@dataclass
class PasteStore:
    """Append-only store of large-paste raw payloads, addressable by id."""

    _items: List[PastePayload] = field(default_factory=list)

    def add(self, raw_text: str, *, source: str = ingest.SRC_REHYDRATED) -> PastePayload:
        raw_text = raw_text or ""
        payload = PastePayload(
            id=len(self._items) + 1, kind=ingest.KIND_TEXT, raw_text=raw_text,
            line_count=len(raw_text.splitlines()) or (1 if raw_text else 0),
            char_count=len(raw_text), source=source,
            preview=ingest._preview(raw_text, 50),
        )
        self._items.append(payload)
        return payload

    def get(self, pid: int) -> Optional[PastePayload]:
        if pid < 1 or pid > len(self._items):
            return None
        return self._items[pid - 1]

    def last(self) -> Optional[PastePayload]:
        return self._items[-1] if self._items else None

    @property
    def items(self):
        return tuple(self._items)

    def clear(self) -> int:
        n = len(self._items)
        self._items.clear()
        return n

    def list_lines(self):
        if not self._items:
            return ("paste: 저장된 large paste 없음 — 큰 텍스트를 붙여넣으면 여기 보존됩니다.",)
        out = [f"paste: {len(self._items)} 보존됨 (`/paste expand|resend <id>` · `/copy paste <id>`)"]
        for p in self._items:
            out.append(f"  #{p.id} · {p.line_count} lines · {p.char_count} chars — {p.preview}")
        return tuple(out)


# threshold (lines) above which a paste is promoted to a stored payload + compact block.
LARGE_PASTE_LINES = 8


def is_large(text: str) -> bool:
    return len((text or "").splitlines()) > LARGE_PASTE_LINES


__all__ = ("PastePayload", "PasteStore", "LARGE_PASTE_LINES", "is_large")
