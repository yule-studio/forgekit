from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from typing import Optional

from yule_storage import list_json_cache_entries, load_json_cache, save_json_cache
from .models import CalendarQueryResult

CALENDAR_CACHE_NAMESPACE = "calendar-query-results"
CALENDAR_CACHE_PROVIDER = "naver-caldav"
CURRENT_RANGE_TTL_SECONDS = 300
FUTURE_RANGE_TTL_SECONDS = 1800
PAST_RANGE_TTL_SECONDS = 86400


def load_calendar_cache(cache_key: str, ttl_seconds: int) -> Optional[CalendarQueryResult]:
    if ttl_seconds <= 0:
        return None

    try:
        entry = load_json_cache(
            namespace=CALENDAR_CACHE_NAMESPACE,
            cache_key=cache_key,
            ttl_seconds=ttl_seconds,
            touch=False,
        )
    except Exception:
        return None

    if entry is None:
        return None

    try:
        return CalendarQueryResult.from_dict(entry.payload)
    except Exception:
        return None


def load_stale_calendar_cache(cache_key: str) -> Optional[CalendarQueryResult]:
    try:
        entry = load_json_cache(
            namespace=CALENDAR_CACHE_NAMESPACE,
            cache_key=cache_key,
            allow_stale=True,
            touch=False,
        )
    except Exception:
        return None

    if entry is None:
        return None

    try:
        return CalendarQueryResult.from_dict(entry.payload)
    except Exception:
        return None


def save_calendar_cache(
    cache_key: str,
    scope_hash: str,
    start_date: date,
    end_date: date,
    ttl_seconds: int,
    result: CalendarQueryResult,
) -> None:
    if ttl_seconds <= 0:
        return

    try:
        save_json_cache(
            namespace=CALENDAR_CACHE_NAMESPACE,
            cache_key=cache_key,
            provider=CALENDAR_CACHE_PROVIDER,
            range_start=start_date.isoformat(),
            range_end=end_date.isoformat(),
            scope_hash=scope_hash,
            ttl_seconds=ttl_seconds,
            payload=result.to_dict(),
            metadata={
                "source": result.source,
                "event_count": len(result.events),
                "todo_count": len(result.todos),
                "metrics": dict(result.metrics or {}),
            },
        )
    except Exception:
        return


def build_calendar_cache_key(*parts: str) -> str:
    return _hash_parts(parts)


def build_calendar_scope_hash(*parts: str) -> str:
    return _hash_parts(parts)


def resolve_calendar_cache_ttl_seconds(
    start_date: date,
    end_date: date,
    configured_ttl_seconds: Optional[int],
    today: Optional[date] = None,
) -> int:
    if configured_ttl_seconds is not None:
        return configured_ttl_seconds

    reference_day = today or date.today()
    if end_date < reference_day:
        return PAST_RANGE_TTL_SECONDS
    if start_date > reference_day:
        return FUTURE_RANGE_TTL_SECONDS
    return CURRENT_RANGE_TTL_SECONDS


def list_calendar_cache_entries(limit: int = 100, include_expired: bool = True) -> list[dict]:
    entries = list_json_cache_entries(
        namespace=CALENDAR_CACHE_NAMESPACE,
        provider=CALENDAR_CACHE_PROVIDER,
        include_expired=include_expired,
        limit=limit,
    )
    return [entry.to_dict() for entry in entries]


def _hash_parts(parts: tuple[str, ...]) -> str:
    normalized = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"))
    return sha256(normalized.encode("utf-8")).hexdigest()
