"""research_forum — thread title / comment prefix vocabulary (leaf)."""

from __future__ import annotations

from typing import Optional


PREFIX_RESEARCH = "[Research]"
PREFIX_TOOL = "[Tool]"
PREFIX_REFERENCE = "[Reference]"
PREFIX_DECISION = "[Decision]"
PREFIX_OBSIDIAN = "[Obsidian]"

THREAD_TITLE_PREFIXES = (PREFIX_RESEARCH, PREFIX_TOOL, PREFIX_REFERENCE)
COMMENT_PREFIXES = (PREFIX_DECISION, PREFIX_OBSIDIAN)
ALL_PREFIXES = THREAD_TITLE_PREFIXES + COMMENT_PREFIXES


def detect_thread_prefix(title: str) -> Optional[str]:
    """Return the matching thread prefix, or None if title has none."""

    cleaned = (title or "").strip()
    for known in ALL_PREFIXES:
        if cleaned.startswith(known):
            return known
    return None


__all__ = (
    "PREFIX_RESEARCH",
    "PREFIX_TOOL",
    "PREFIX_REFERENCE",
    "PREFIX_DECISION",
    "PREFIX_OBSIDIAN",
    "THREAD_TITLE_PREFIXES",
    "COMMENT_PREFIXES",
    "ALL_PREFIXES",
    "detect_thread_prefix",
)
