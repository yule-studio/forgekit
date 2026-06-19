"""Process event model — a REAL, structured timeline of what ForgeKit actually did.

Not UI decoration: every event is emitted by an actual action (slash route, provider
submit, chunked generate, copy, attach, paste) — never a fake ``Reading``/``Bash``/
``Thinking`` label with nothing behind it. ForgeKit is a provider console (not a coding
shell), so the kinds map to ForgeKit's real work, not Claude's coding-agent verbs.

Durations are MEASURED: a ``start`` event records a monotonic timestamp and ``finish``
records the end → ``duration_ms`` is real. Instant markers (``route_done``, ``copy_*``)
carry no duration (``duration_ms is None``) — honestly modelled, never a faked "~1s".

The store keeps ONE turn's event group (cleared when a new turn starts) and caps the
recent window — the feed reflects the current/last action, not an ever-growing log. The
clock is injectable so order + durations are deterministic in tests.

Pure / stdlib → unit-testable; the feed is a SEPARATE surface from the transcript, so it
never pollutes ``/copy`` (which copies transcript plain-text only).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

# --- event kinds (ForgeKit's real actions) ---------------------------------
KIND_ROUTE_START = "route_start"
KIND_ROUTE_DONE = "route_done"
KIND_SUBMIT_START = "submit_start"
KIND_SUBMIT_BLOCKED = "submit_blocked"
KIND_SUBMIT_SENT = "submit_sent"
KIND_GENERATE_START = "generate_start"
KIND_GENERATE_CHUNK = "generate_chunk"
KIND_GENERATE_DONE = "generate_done"
KIND_COPY_SUCCESS = "copy_success"
KIND_COPY_FAILED = "copy_failed"
KIND_ATTACH_STAGED = "attach_staged"
KIND_ATTACH_BLOCKED = "attach_blocked"
KIND_PASTE_STORED = "paste_stored"
KIND_PASTE_EXPANDED = "paste_expanded"
KIND_DONE = "done"
KIND_ERROR = "error"

# --- status ----------------------------------------------------------------
ST_RUNNING = "running"
ST_DONE = "done"
ST_BLOCKED = "blocked"
ST_FAILED = "failed"

# --- severity (drives the dot colour) --------------------------------------
SEV_INFO = "info"
SEV_WARN = "warn"
SEV_ERROR = "error"


@dataclass
class ProcessEvent:
    kind: str
    label: str
    status: str = ST_DONE
    started_at: float = 0.0           # monotonic seconds
    ended_at: Optional[float] = None  # None while running / for pure markers
    detail: str = ""
    source: str = ""                  # e.g. provider id
    copyable: bool = False            # excluded from default /copy regardless
    severity: str = SEV_INFO

    @property
    def duration_ms(self) -> Optional[int]:
        """Real measured duration, or None when there is no start→end span."""

        if self.ended_at is None or self.ended_at == self.started_at:
            return None
        return int((self.ended_at - self.started_at) * 1000)


@dataclass
class ProcessFeed:
    """A turn-scoped, capped timeline of real events. Clock injectable for tests."""

    clock: Callable[[], float] = time.monotonic
    max_recent: int = 8
    _events: List[ProcessEvent] = field(default_factory=list)

    # --- emit ---------------------------------------------------------------
    def start(self, kind: str, label: str, *, detail: str = "", source: str = "",
              severity: str = SEV_INFO) -> ProcessEvent:
        """A RUNNING event (duration measured at :meth:`finish`)."""

        ev = ProcessEvent(kind=kind, label=label, status=ST_RUNNING, started_at=self.clock(),
                          detail=detail, source=source, severity=severity)
        self._events.append(ev)
        self._trim()
        return ev

    def mark(self, kind: str, label: str, *, status: str = ST_DONE, detail: str = "",
             source: str = "", severity: str = SEV_INFO) -> ProcessEvent:
        """An INSTANT marker (no duration — started == ended)."""

        t = self.clock()
        ev = ProcessEvent(kind=kind, label=label, status=status, started_at=t, ended_at=t,
                          detail=detail, source=source, severity=severity)
        self._events.append(ev)
        self._trim()
        return ev

    def finish(self, ev: ProcessEvent, status: str = ST_DONE) -> ProcessEvent:
        """Close a running event → records the end so ``duration_ms`` is real."""

        ev.ended_at = self.clock()
        ev.status = status
        return ev

    # --- turn lifecycle -----------------------------------------------------
    def begin_turn(self) -> None:
        """A new action starts → the feed reflects the current turn, not history."""

        self._events.clear()

    def clear(self) -> None:
        self._events.clear()

    # --- read ---------------------------------------------------------------
    @property
    def events(self) -> Tuple[ProcessEvent, ...]:
        return tuple(self._events)

    def recent(self, n: Optional[int] = None) -> Tuple[ProcessEvent, ...]:
        return tuple(self._events[-(n or self.max_recent):])

    def active(self) -> Optional[ProcessEvent]:
        for ev in reversed(self._events):
            if ev.status == ST_RUNNING:
                return ev
        return None

    @property
    def empty(self) -> bool:
        return not self._events

    def _trim(self) -> None:
        # keep the window bounded even within a long turn (no unbounded growth).
        cap = self.max_recent * 2
        if len(self._events) > cap:
            self._events = self._events[-cap:]


__all__ = (
    "KIND_ROUTE_START", "KIND_ROUTE_DONE", "KIND_SUBMIT_START", "KIND_SUBMIT_BLOCKED",
    "KIND_SUBMIT_SENT", "KIND_GENERATE_START", "KIND_GENERATE_CHUNK", "KIND_GENERATE_DONE",
    "KIND_COPY_SUCCESS", "KIND_COPY_FAILED", "KIND_ATTACH_STAGED", "KIND_ATTACH_BLOCKED",
    "KIND_PASTE_STORED", "KIND_PASTE_EXPANDED", "KIND_DONE", "KIND_ERROR",
    "ST_RUNNING", "ST_DONE", "ST_BLOCKED", "ST_FAILED",
    "SEV_INFO", "SEV_WARN", "SEV_ERROR",
    "ProcessEvent", "ProcessFeed",
)
