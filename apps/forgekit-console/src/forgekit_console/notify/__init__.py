"""Operator notifications (WT4) — desktop (macOS/Windows) + inbox, one event.

Python-first cross-platform desktop notification, mapped to the approval-matrix
request types, tracked alongside the durable operator inbox.
"""

from __future__ import annotations

from .desktop import detect_platform, build_command, dispatch
from .events import (
    EVENT_ACCESS_REQUIRED,
    EVENT_APPROVAL_REQUIRED,
    EVENT_DECISION_REQUIRED,
    EVENT_DOCTOR_CRITICAL,
    EVENT_INFO_REQUIRED,
    EVENT_REPEATED_FAILURE,
    EVENT_RUNTIME_BLOCKED,
    EVENT_SECRET_REQUIRED,
    NotificationEvent,
    request_type_for,
)
from .service import NotificationOutcome, NotificationService

__all__ = (
    "EVENT_APPROVAL_REQUIRED", "EVENT_DECISION_REQUIRED", "EVENT_INFO_REQUIRED",
    "EVENT_ACCESS_REQUIRED", "EVENT_SECRET_REQUIRED", "EVENT_REPEATED_FAILURE",
    "EVENT_DOCTOR_CRITICAL", "EVENT_RUNTIME_BLOCKED",
    "NotificationEvent", "request_type_for",
    "NotificationService", "NotificationOutcome",
    "detect_platform", "build_command", "dispatch",
)
