"""Obsidian 파일명 컨벤션 §4.1 validator (#99 / F8).

컨벤션:

    <kind>-<topic-slug>[-issue-<n>].md

  * ``kind`` ∈ {``task-log``, ``decision``, ``research``, ``knowledge``,
    ``meeting``, ``work-report``}
  * ``topic-slug``: kebab-case, 영문 소문자 / 숫자 / hyphen
  * ``-issue-<n>`` suffix 는 선택 (정수)

본 모듈은 mistake ledger signature ``obsidian.filename.date-prefix`` /
``obsidian.filename.kind-missing`` 의 단일 판정 소스. ≤80 줄 유지.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple


ALLOWED_KINDS: Tuple[str, ...] = (
    "task-log",
    "decision",
    "research",
    "knowledge",
    "meeting",
    "work-report",
)

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}_")
_CANONICAL = re.compile(
    r"^(?P<kind>task-log|decision|research|knowledge|meeting|work-report)"
    r"-(?P<topic>[a-z0-9]+(?:-[a-z0-9]+)*?)"
    r"(?:-issue-(?P<issue>\d+))?\.md$"
)


@dataclass(frozen=True)
class FilenameVerdict:
    """파일명 1건의 검증 결과."""

    valid: bool
    kind: Optional[str] = None
    topic_slug: Optional[str] = None
    issue: Optional[int] = None
    reason: Optional[str] = None
    signature: Optional[str] = None


def validate_filename(name: str) -> FilenameVerdict:
    """파일명을 컨벤션 §4.1 기준으로 검증."""

    if not isinstance(name, str) or not name.endswith(".md"):
        return FilenameVerdict(
            valid=False,
            reason="must end with .md",
            signature="obsidian.filename.not-markdown",
        )
    if _DATE_PREFIX.match(name):
        return FilenameVerdict(
            valid=False,
            reason="date prefix is deprecated by §4.1",
            signature="obsidian.filename.date-prefix",
        )
    match = _CANONICAL.match(name)
    if not match:
        # kind 누락인지, topic-slug 누락인지 구분
        head = name.split("-", 1)[0]
        if head not in ALLOWED_KINDS and not any(
            name.startswith(f"{k}-") for k in ALLOWED_KINDS
        ):
            return FilenameVerdict(
                valid=False,
                reason=f"kind prefix must be one of {ALLOWED_KINDS}",
                signature="obsidian.filename.kind-missing",
            )
        return FilenameVerdict(
            valid=False,
            reason="topic-slug must be kebab-case (a-z0-9 + hyphen)",
            signature="obsidian.filename.topic-malformed",
        )
    issue_raw = match.group("issue")
    return FilenameVerdict(
        valid=True,
        kind=match.group("kind"),
        topic_slug=match.group("topic"),
        issue=int(issue_raw) if issue_raw else None,
    )


__all__ = ("ALLOWED_KINDS", "FilenameVerdict", "validate_filename")
