"""Notification service — one event → operator inbox + desktop, tracked together.

A :class:`NotificationEvent` is recorded to the durable operator **inbox** (so it is
never lost, headless or not) AND pushed to the **desktop** (best-effort, per
platform). Both reference the same event, so inbox / ledger / desktop track one
incident. The desktop notifier + inbox path are injectable for tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Tuple

from ..runtime_paths import operator_inbox_path
from . import desktop
from .events import NotificationEvent


@dataclass(frozen=True)
class NotificationOutcome:
    event_type: str
    request_type: str
    inbox_written: bool
    desktop_delivered: bool
    channel: str

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "request_type": self.request_type,
            "inbox_written": self.inbox_written,
            "desktop_delivered": self.desktop_delivered,
            "channel": self.channel,
        }


@dataclass
class NotificationService:
    """Send an operator event to the inbox + the desktop (both tracked)."""

    env: Optional[Mapping[str, str]] = None
    inbox_path: Optional[Path] = None
    # (title, body) -> (delivered, channel); defaults to the cross-platform dispatch
    dispatcher: Optional[Callable[..., Tuple[bool, str]]] = None
    desktop_enabled: bool = True

    def __post_init__(self) -> None:
        if self.inbox_path is None:
            self.inbox_path = operator_inbox_path(self.env)
        if self.dispatcher is None:
            self.dispatcher = desktop.dispatch

    def notify(self, event: NotificationEvent) -> NotificationOutcome:
        inbox_ok = self._write_inbox(event)
        delivered, channel = False, desktop.CHANNEL_NONE
        if self.desktop_enabled:
            try:
                delivered, channel = self.dispatcher(event.title, event.desktop_body())
            except Exception:  # noqa: BLE001 - desktop is best-effort
                delivered, channel = False, desktop.CHANNEL_NONE
        return NotificationOutcome(
            event_type=event.event_type,
            request_type=event.request_type,
            inbox_written=inbox_ok,
            desktop_delivered=delivered,
            channel=channel,
        )

    def _write_inbox(self, event: NotificationEvent) -> bool:
        path = self.inbox_path
        if path is None:
            return False
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            existing: List = []
            if p.exists():
                try:
                    loaded = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        existing = loaded
                except ValueError:
                    existing = []
            existing.append(event.to_inbox_entry())
            p.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except OSError:
            return False


__all__ = ("NotificationOutcome", "NotificationService")
