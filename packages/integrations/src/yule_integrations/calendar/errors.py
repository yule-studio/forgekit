from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CalendarErrorDetails:
    source: str
    code: str
    category: str
    message: str
    retryable: bool
    retry_strategy: str
    recommended_retry_count: int
    manual_action_required: bool
    alert_recommended: bool
    recovery_hint: str
    raw_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "retryable": self.retryable,
            "retry_strategy": self.retry_strategy,
            "recommended_retry_count": self.recommended_retry_count,
            "manual_action_required": self.manual_action_required,
            "alert_recommended": self.alert_recommended,
            "recovery_hint": self.recovery_hint,
            "raw_message": self.raw_message,
        }


class CalendarIntegrationError(Exception):
    """Raised when calendar items cannot be loaded or interpreted safely."""

    def __init__(self, details: CalendarErrorDetails):
        super().__init__(details.message)
        self.details = details

    @property
    def retryable(self) -> bool:
        return self.details.retryable

    def to_dict(self) -> dict:
        return self.details.to_dict()


def build_calendar_error(
    *,
    code: str,
    category: str,
    message: str,
    retryable: bool,
    retry_strategy: str,
    recommended_retry_count: int,
    manual_action_required: bool,
    alert_recommended: bool,
    recovery_hint: str,
    raw_message: Optional[str] = None,
    source: str = "naver-caldav",
) -> CalendarIntegrationError:
    return CalendarIntegrationError(
        CalendarErrorDetails(
            source=source,
            code=code,
            category=category,
            message=message,
            retryable=retryable,
            retry_strategy=retry_strategy,
            recommended_retry_count=recommended_retry_count,
            manual_action_required=manual_action_required,
            alert_recommended=alert_recommended,
            recovery_hint=recovery_hint,
            raw_message=raw_message,
        )
    )


def build_calendar_validation_error(message: str) -> CalendarIntegrationError:
    return build_calendar_error(
        code="invalid_request",
        category="validation",
        message=message,
        retryable=False,
        retry_strategy="none",
        recommended_retry_count=0,
        manual_action_required=True,
        alert_recommended=False,
        recovery_hint="입력 값을 수정한 뒤 다시 실행하세요.",
        source="calendar-cli",
        raw_message=message,
    )
