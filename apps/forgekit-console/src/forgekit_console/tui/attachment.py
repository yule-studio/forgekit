"""Attachment staging model — pending image/file attachments, honestly tracked.

The console can RECEIVE + STAGE an attachment (read its real bytes to a temp file)
before any provider call. Whether it is actually SENT is a separate, honest fact: the
live submit path is text-only (openai-compatible chat), so an image is ``staged_only``
(received + held, NOT sent) with a reason — never a fake "uploaded".

This module owns the pure model + store + status rendering. Reading the bytes (file /
clipboard) is done by the caller (app) via :mod:`tui.clipboard`; the store just holds
the resulting :class:`Attachment`. Pure / stdlib → unit-testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import ingest

# stage outcomes (honest — never a fake success)
STATE_STAGED = "staged"          # bytes read + held (sendable depends on provider)
STATE_MISSING = "missing"        # path does not exist
STATE_BLOCKED = "blocked"        # exists but unreadable (permission)
STATE_NO_PAYLOAD = "no_attachment"  # nothing to stage (no clipboard image / empty)

_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}

# Today the console submit path is text-only → an image can be staged but NOT sent.
PROVIDER_TEXT_ONLY_REASON = "provider text-only (console live-submit) — staged_only, 미전송"


@dataclass(frozen=True)
class Attachment:
    kind: str                 # ingest.KIND_IMAGE
    source: str               # ingest.SRC_FILE / SRC_CLIPBOARD / SRC_REHYDRATED
    mime: str
    path: str = ""
    bytes_len: int = 0
    preview_label: str = ""
    staged_at: str = ""
    sendable: bool = False    # can the provider actually send it? (False today)
    reason_if_blocked: str = ""

    def chip(self) -> str:
        size = f"{self.bytes_len}B" if self.bytes_len else "?"
        tag = "sendable" if self.sendable else "staged_only"
        return f"📎 {self.preview_label or self.path or self.kind} · {self.mime} · {size} · {tag}"


def mime_for(path: str) -> str:
    return _IMAGE_MIME.get(Path(path).suffix.lower(), "application/octet-stream")


def stage_file(path: str, *, source: str = ingest.SRC_FILE, staged_at: str = "",
               sendable: bool = False) -> Tuple[Optional[Attachment], str, str]:
    """Stage a real file path → (Attachment|None, state, message). No fake success."""

    raw = (path or "").strip()
    if not raw:
        return None, STATE_NO_PAYLOAD, "경로를 입력하세요 — `/attach <path>`"
    p = Path(raw).expanduser()
    if not p.exists():
        return None, STATE_MISSING, f"missing: 파일이 없습니다 ({raw})"
    if not p.is_file() or not os.access(p, os.R_OK):
        return None, STATE_BLOCKED, f"blocked: 읽을 수 없습니다 ({raw})"
    size = p.stat().st_size
    mime = mime_for(str(p))
    att = Attachment(
        kind=ingest.KIND_IMAGE if mime.startswith("image/") else "file",
        source=source, mime=mime, path=str(p), bytes_len=size,
        preview_label=p.name, staged_at=staged_at,
        sendable=sendable, reason_if_blocked="" if sendable else PROVIDER_TEXT_ONLY_REASON,
    )
    return att, STATE_STAGED, f"staged: {att.chip()}"


@dataclass
class AttachmentStore:
    """Holds staged attachments for the current composer turn."""

    _items: List[Attachment] = field(default_factory=list)

    def add(self, att: Attachment) -> Attachment:
        self._items.append(att)
        return att

    @property
    def items(self) -> Tuple[Attachment, ...]:
        return tuple(self._items)

    @property
    def pending(self) -> bool:
        return bool(self._items)

    def clear(self) -> int:
        n = len(self._items)
        self._items.clear()
        return n

    def status_lines(self) -> Tuple[str, ...]:
        if not self._items:
            return ("attach: staged attachment 없음 — `/attach <path>` 또는 이미지 붙여넣기.",)
        out = [f"attach: {len(self._items)} staged"]
        for i, a in enumerate(self._items, 1):
            out.append(f"  {i}. {a.chip()}" + (f"  ({a.reason_if_blocked})" if a.reason_if_blocked else ""))
        return tuple(out)


__all__ = (
    "STATE_STAGED", "STATE_MISSING", "STATE_BLOCKED", "STATE_NO_PAYLOAD",
    "PROVIDER_TEXT_ONLY_REASON", "Attachment", "AttachmentStore", "stage_file", "mime_for",
)
