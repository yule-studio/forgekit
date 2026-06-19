"""Paste / attachment ingestion — placeholder detection + clipboard rehydration.

EMPIRICAL FACT (verified, not assumed): ForgeKit does NOT generate ``[Pasted text #N
+M lines]`` or ``[Image #N]``. They are substituted by the HOST (the terminal / IDE /
wrapper the operator pastes into) BEFORE the bytes reach ForgeKit — so the console
only ever sees the placeholder string, never the real payload. PromptArea (a TextArea)
DOES accept a genuine multiline bracketed paste; the failure mode is purely the host
placeholder.

So this module is the rehydration seam: detect a host placeholder in the buffer →
recover the real payload from the OS clipboard (text via pbpaste, image via the
clipboard image reader) → submit / stage the REAL content. When the raw payload cannot
be recovered it is surfaced as honestly blocked — the placeholder is NEVER submitted as
if it were the message, and a long paste is never silently truncated.

Pure / stdlib → unit-testable with an injected clipboard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Host placeholder patterns (external — matched, never produced, by ForgeKit).
_TEXT_RE = re.compile(r"\[Pasted text #\d+(?:\s*\+\s*(\d+)\s*lines?)?\]", re.IGNORECASE)
_IMAGE_RE = re.compile(r"\[Image #\d+\]", re.IGNORECASE)

KIND_TEXT = "text"
KIND_IMAGE = "image"

SRC_RAW_INPUT = "raw_input"
SRC_CLIPBOARD = "clipboard"
SRC_REHYDRATED = "placeholder_rehydrated"
SRC_FILE = "file"


def has_text_placeholder(s: str) -> bool:
    return bool(_TEXT_RE.search(s or ""))


def has_image_placeholder(s: str) -> bool:
    return bool(_IMAGE_RE.search(s or ""))


def is_any_placeholder(s: str) -> bool:
    return has_text_placeholder(s) or has_image_placeholder(s)


def looks_like_placeholder_only(s: str) -> bool:
    """True when *s* is nothing but placeholders + whitespace (no real content)."""

    if not (s or "").strip():
        return False
    stripped = _IMAGE_RE.sub("", _TEXT_RE.sub("", s))
    return stripped.strip() == ""


def _preview(text: str, width: int = 60) -> str:
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return (first[:width] + "…") if len(first) > width else first


@dataclass(frozen=True)
class PendingPaste:
    """A recovered large-text paste (the real multiline payload, not a placeholder)."""

    raw_text: str
    line_count: int
    source: str          # SRC_*
    preview: str

    @staticmethod
    def of(text: str, source: str) -> "PendingPaste":
        text = text or ""
        return PendingPaste(raw_text=text, line_count=len(text.splitlines()) or (1 if text else 0),
                            source=source, preview=_preview(text))


@dataclass(frozen=True)
class TextResolution:
    """The result of resolving a submit buffer that may contain a paste placeholder."""

    text: str            # the text to ACTUALLY submit (rehydrated when possible)
    rehydrated: bool
    blocked: bool
    note: str            # honest operator note (empty when nothing special happened)
    pending: Optional[PendingPaste] = None


def resolve_submit_text(buffer: str, clipboard_text: Optional[str]) -> TextResolution:
    """Resolve a submit *buffer* that may be / contain a ``[Pasted text #N]`` placeholder.

    - no placeholder → pass the buffer through unchanged (real multiline paste already
      lives in the buffer; nothing to do),
    - placeholder + a substantial clipboard payload → replace the placeholder span with
      the REAL clipboard text (rehydrated, line breaks preserved),
    - placeholder + no recoverable clipboard → BLOCKED (never submit the bare
      placeholder; surface why + next action).
    """

    buffer = buffer or ""
    if not has_text_placeholder(buffer):
        return TextResolution(text=buffer, rehydrated=False, blocked=False, note="")

    clip = clipboard_text or ""
    # the clipboard must hold REAL content — not empty, and not itself a placeholder.
    if clip.strip() and not has_text_placeholder(clip):
        resolved = _TEXT_RE.sub(lambda _: clip, buffer, count=1)
        # if more than one placeholder remained (clipboard holds only one payload), say so.
        remaining = len(_TEXT_RE.findall(resolved))
        note = f"클립보드에서 본문 복원 ({len(clip.splitlines())} 줄)"
        if remaining:
            note += f" · 추가 placeholder {remaining}개는 복원 불가(클립보드는 1개만 보관)"
        return TextResolution(text=resolved, rehydrated=True, blocked=False, note=note,
                              pending=PendingPaste.of(clip, SRC_REHYDRATED))
    return TextResolution(
        text=buffer, rehydrated=False, blocked=True,
        note="긴 paste 가 감지됐지만 raw 본문을 복구할 수 없습니다 — host 가 placeholder 만 보냈고 "
             "클립보드도 비었거나 placeholder 입니다. 본문을 다시 복사한 뒤 붙여넣으세요.")


__all__ = (
    "KIND_TEXT", "KIND_IMAGE",
    "SRC_RAW_INPUT", "SRC_CLIPBOARD", "SRC_REHYDRATED", "SRC_FILE",
    "has_text_placeholder", "has_image_placeholder", "is_any_placeholder",
    "looks_like_placeholder_only", "PendingPaste", "TextResolution", "resolve_submit_text",
)
