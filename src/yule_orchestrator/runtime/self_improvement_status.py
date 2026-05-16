"""Self-improvement journal — supervisor status surface integration.

기존 :mod:`runtime.status` 의 ``RuntimeAutonomyJournal`` 과 같은 모양을
가지는 작은 in-process ring buffer. supervisor 의 self-improvement loop
가 한 tick 끝날 때마다 :func:`record_tick` 으로 가장 최근 결과를
journal 에 기록한다.

운영자가 보는 두 종류의 출력:

* **journalctl 한 줄** — supervisor 가 자동으로 ``logger.info`` 한다
  (loop 자체에서). 사용자는 ``journalctl -u yule-eng-supervisor-watch``
  에서 ``self-improvement: detected=N delegated=M operator_wait=K`` 형식의
  요약을 본다.
* **Discord status post 의 self-improvement 섹션** — ``build_status_lines``
  가 가장 최근 tick 의 handled problems 를 표 형태로 렌더링한다.
  ``runtime.status_poster`` 의 markdown body 합성 단계에서 호출한다.

다른 모듈에 강결합되지 않도록 *순수 함수 + 모듈 전역 ring buffer* 만
제공한다.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Mapping, Optional, Sequence, Tuple


DEFAULT_JOURNAL_DEPTH: int = 32


@dataclass(frozen=True)
class SelfImprovementTickSummary:
    """One :class:`SelfImprovementTickReport` projection.

    Frozen so a status renderer can keep a long-lived reference without
    racing the dispatcher.
    """

    recorded_at: str
    detected: int
    new_problems: int
    delegated: int
    waiting_operator: int
    blocked: int
    summary_lines: Tuple[str, ...]

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "recorded_at": self.recorded_at,
            "detected": self.detected,
            "new_problems": self.new_problems,
            "delegated": self.delegated,
            "waiting_operator": self.waiting_operator,
            "blocked": self.blocked,
            "summary_lines": list(self.summary_lines),
        }


class SelfImprovementJournal:
    """Thread-safe ring buffer of recent tick summaries."""

    def __init__(self, *, depth: int = DEFAULT_JOURNAL_DEPTH) -> None:
        self._depth = max(1, int(depth))
        self._buffer: Deque[SelfImprovementTickSummary] = deque(maxlen=self._depth)
        self._lock = threading.Lock()

    def record(self, summary: SelfImprovementTickSummary) -> None:
        with self._lock:
            self._buffer.append(summary)

    def recent(self, *, limit: int = 5) -> Tuple[SelfImprovementTickSummary, ...]:
        with self._lock:
            items = tuple(self._buffer)
        if limit <= 0:
            return ()
        return items[-limit:]

    def latest(self) -> Optional[SelfImprovementTickSummary]:
        with self._lock:
            if not self._buffer:
                return None
            return self._buffer[-1]

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()


_DEFAULT_JOURNAL: SelfImprovementJournal = SelfImprovementJournal()


def get_default_journal() -> SelfImprovementJournal:
    return _DEFAULT_JOURNAL


def record_tick(
    report: Any,
    *,
    journal: Optional[SelfImprovementJournal] = None,
    now: Optional[datetime] = None,
) -> SelfImprovementTickSummary:
    """Project a :class:`SelfImprovementTickReport` onto the journal.

    Accepts the report by duck-typing (``detected_signals`` /
    ``new_problems`` / ``handled`` / etc.) so the dependency runs
    one way: ``status`` → ``runtime``, and the dispatcher doesn't need
    to import ``runtime`` to push status updates.
    """

    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0)
    detected_signals = getattr(report, "detected_signals", ()) or ()
    new_problems = getattr(report, "new_problems", ()) or ()
    delegated = int(getattr(report, "delegated_count", 0) or 0)
    waiting_operator = int(getattr(report, "waiting_operator_count", 0) or 0)
    blocked = int(getattr(report, "blocked_count", 0) or 0)
    summary_fn = getattr(report, "summary_line", None)
    main_line = summary_fn() if callable(summary_fn) else ""
    handled = getattr(report, "handled", ()) or ()
    handled_lines = [
        (
            f"  · {getattr(getattr(h, 'problem', None), 'signal_id', '?')} "
            f"→ owner={getattr(getattr(h, 'verdict', None), 'primary_owner', '?')} "
            f"status={getattr(getattr(h, 'final_status', None), 'value', '?')}"
        )
        for h in handled
    ]
    summary_lines = tuple(
        line for line in (main_line, *handled_lines) if line
    )
    entry = SelfImprovementTickSummary(
        recorded_at=when.isoformat(),
        detected=len(detected_signals),
        new_problems=len(new_problems),
        delegated=delegated,
        waiting_operator=waiting_operator,
        blocked=blocked,
        summary_lines=summary_lines,
    )
    (journal or _DEFAULT_JOURNAL).record(entry)
    return entry


def build_status_lines(
    *,
    journal: Optional[SelfImprovementJournal] = None,
    limit: int = 1,
) -> Tuple[str, ...]:
    """Render the most-recent tick summaries for the Discord status post.

    Returns an empty tuple when nothing was recorded yet — the poster
    skips the section in that case.
    """

    items = (journal or _DEFAULT_JOURNAL).recent(limit=limit)
    lines: list[str] = []
    for item in items:
        lines.append(
            f"self-improvement [{item.recorded_at}] detected={item.detected} "
            f"new={item.new_problems} delegated={item.delegated} "
            f"operator_wait={item.waiting_operator} blocked={item.blocked}"
        )
        for sub in item.summary_lines[1:]:  # skip the duplicate main line
            lines.append(sub)
    return tuple(lines)


__all__ = (
    "DEFAULT_JOURNAL_DEPTH",
    "SelfImprovementJournal",
    "SelfImprovementTickSummary",
    "build_status_lines",
    "get_default_journal",
    "record_tick",
)
