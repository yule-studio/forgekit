from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import logging
import os
import time as time_module
from typing import Any, Iterable, List, Optional

from yule_storage.calendar_state import sync_calendar_query_result
from .cache import (
    build_calendar_cache_key,
    build_calendar_scope_hash,
    load_stale_calendar_cache,
    load_calendar_cache,
    resolve_calendar_cache_ttl_seconds,
    save_calendar_cache,
)
from .errors import CalendarIntegrationError, build_calendar_error
from .models import CalendarQueryResult
from .parsing import build_event, build_todo, todo_matches_range
from .rendering import render_calendar_events, render_calendar_items


@dataclass(frozen=True)
class NaverCalDAVConfig:
    url: str
    username: str
    password: str
    calendar_name: Optional[str] = None
    todo_calendar_name: Optional[str] = None
    timeout_seconds: int = 15
    include_all_todos: bool = False
    cache_seconds: Optional[int] = None


def list_naver_calendar_items(
    start_date: date,
    end_date: date,
    force_refresh: bool = False,
) -> CalendarQueryResult:
    config = load_naver_caldav_config()
    cache_ttl_seconds = resolve_calendar_cache_ttl_seconds(
        start_date=start_date,
        end_date=end_date,
        configured_ttl_seconds=config.cache_seconds,
    )
    cache_scope_hash = build_calendar_scope_hash(
        "calendar-query-v1",
        config.url,
        config.username,
        config.calendar_name or "",
        config.todo_calendar_name or "",
        str(config.include_all_todos),
    )
    cache_key = build_calendar_cache_key(
        cache_scope_hash,
        start_date.isoformat(),
        end_date.isoformat(),
    )
    stale_cached_result = None
    if not force_refresh:
        cached_result = load_calendar_cache(
            cache_key=cache_key,
            ttl_seconds=cache_ttl_seconds,
        )
        if cached_result is not None:
            _sync_calendar_state_safely(cached_result, scope_hash=cache_scope_hash)
            return cached_result
        stale_cached_result = load_stale_calendar_cache(cache_key=cache_key)

    try:
        result = _fetch_naver_calendar_items(
            config=config,
            start_date=start_date,
            end_date=end_date,
        )
    except CalendarIntegrationError as exc:
        if stale_cached_result is not None and _can_use_stale_cache_on_error(exc):
            _sync_calendar_state_safely(stale_cached_result, scope_hash=cache_scope_hash)
            return stale_cached_result
        raise
    save_calendar_cache(
        cache_key=cache_key,
        scope_hash=cache_scope_hash,
        start_date=start_date,
        end_date=end_date,
        ttl_seconds=cache_ttl_seconds,
        result=result,
    )
    _sync_calendar_state_safely(result, scope_hash=cache_scope_hash)
    return result


def _fetch_naver_calendar_items(
    config: NaverCalDAVConfig,
    start_date: date,
    end_date: date,
) -> CalendarQueryResult:
    client_cls, calendar_cls = _load_caldav_dependencies()

    query_start = _to_local_datetime(start_date)
    query_end = _to_local_datetime(end_date + timedelta(days=1))

    events = []
    todos = []
    seen_events: set[tuple[str, str, str]] = set()
    seen_todos: set[tuple[str, Optional[str], Optional[str], str]] = set()
    metrics = {
        "calendar_fetch_seconds": 0.0,
        "todo_fetch_seconds": 0.0,
        "calendar_discovery_seconds": 0.0,
    }

    try:
        with client_cls(
            url=config.url,
            username=config.username,
            password=config.password,
            timeout=config.timeout_seconds,
        ) as client:
            discovery_started = time_module.perf_counter()
            principal = client.principal()
            all_calendars = list(principal.calendars())
            metrics["calendar_discovery_seconds"] += time_module.perf_counter() - discovery_started
            selected_calendars = _select_calendars(all_calendars, config.calendar_name)
            todo_calendars = _select_todo_calendars(
                all_calendars=all_calendars,
                todo_calendar_name=config.todo_calendar_name,
                fallback_calendars=selected_calendars,
            )
            event_calendars = _select_event_calendars(
                selected_calendars=selected_calendars,
                calendar_name=config.calendar_name,
                todo_calendars=todo_calendars,
            )
            todo_calendar_ids = {_calendar_identifier(calendar) for calendar in todo_calendars}
            event_calendar_ids = {_calendar_identifier(calendar) for calendar in event_calendars}
            processed_calendar_ids: set[str] = set()

            for calendar in [*event_calendars, *todo_calendars]:
                calendar_id = _calendar_identifier(calendar)
                if calendar_id in processed_calendar_ids:
                    continue
                processed_calendar_ids.add(calendar_id)
                calendar_label = _calendar_label(calendar)
                include_events = calendar_id in event_calendar_ids
                include_todos = calendar_id in todo_calendar_ids
                fetch_started = time_module.perf_counter()
                event_count_before = len(events)
                todo_count_before = len(todos)
                _collect_dated_items(
                    calendar=calendar,
                    calendar_label=calendar_label,
                    calendar_cls=calendar_cls,
                    query_start=query_start,
                    query_end=query_end,
                    include_events=include_events,
                    include_todos=include_todos,
                    events=events,
                    todos=todos,
                    seen_events=seen_events,
                    seen_todos=seen_todos,
                )
                elapsed = time_module.perf_counter() - fetch_started
                if include_events or len(events) > event_count_before:
                    metrics["calendar_fetch_seconds"] += elapsed
                if include_todos or len(todos) > todo_count_before:
                    metrics["todo_fetch_seconds"] += elapsed

            if not todos:
                for calendar in todo_calendars:
                    calendar_label = _calendar_label(calendar)
                    todo_fetch_started = time_module.perf_counter()
                    _collect_search_todos(
                        calendar=calendar,
                        calendar_label=calendar_label,
                        calendar_cls=calendar_cls,
                        query_start=query_start,
                        query_end=query_end,
                        start_date=start_date,
                        end_date=end_date,
                        todos=todos,
                        seen_todos=seen_todos,
                    )
                    metrics["todo_fetch_seconds"] += time_module.perf_counter() - todo_fetch_started

                if config.include_all_todos:
                    for calendar in todo_calendars:
                        calendar_label = _calendar_label(calendar)
                        todo_fetch_started = time_module.perf_counter()
                        _collect_all_todos(
                            calendar=calendar,
                            calendar_label=calendar_label,
                            calendar_cls=calendar_cls,
                            start_date=start_date,
                            end_date=end_date,
                            todos=todos,
                            seen_todos=seen_todos,
                        )
                        metrics["todo_fetch_seconds"] += time_module.perf_counter() - todo_fetch_started
    except CalendarIntegrationError:
        raise
    except Exception as exc:
        raise _classify_caldav_error(exc, timeout_seconds=config.timeout_seconds) from exc

    events.sort(key=lambda event: event.sort_key())
    todos.sort(key=lambda todo: todo.sort_key())
    return CalendarQueryResult(
        source="naver-caldav",
        start_date=start_date,
        end_date=end_date,
        events=events,
        todos=todos,
        metrics={key: round(value, 3) for key, value in metrics.items()},
    )


def _sync_calendar_state_safely(result: CalendarQueryResult, scope_hash: str) -> None:
    try:
        sync_calendar_query_result(result, scope_hash=scope_hash)
    except Exception:
        return


def _can_use_stale_cache_on_error(error: CalendarIntegrationError) -> bool:
    details = error.details
    if details.retryable:
        return True
    return details.category in {"network", "query", "unknown"}


def list_naver_calendar_events(
    start_date: date,
    end_date: date,
    force_refresh: bool = False,
) -> CalendarQueryResult:
    return list_naver_calendar_items(
        start_date=start_date,
        end_date=end_date,
        force_refresh=force_refresh,
    )


def load_naver_caldav_config() -> NaverCalDAVConfig:
    url = os.getenv("NAVER_CALDAV_URL", "https://caldav.calendar.naver.com")
    username = os.getenv("NAVER_CALDAV_USERNAME") or os.getenv("NAVER_ID")
    password = os.getenv("NAVER_CALDAV_PASSWORD") or os.getenv("NAVER_APP_PASSWORD")
    calendar_name = os.getenv("NAVER_CALDAV_CALENDAR")
    todo_calendar_name = os.getenv("NAVER_CALDAV_TODO_CALENDAR")
    timeout_seconds = _load_timeout_seconds()
    include_all_todos = _load_bool_env("NAVER_CALDAV_INCLUDE_ALL_TODOS", default=False)
    cache_seconds = _load_cache_seconds()

    missing: List[str] = []
    if not username:
        missing.append("NAVER_CALDAV_USERNAME or NAVER_ID")
    if not password:
        missing.append("NAVER_CALDAV_PASSWORD or NAVER_APP_PASSWORD")

    if missing:
        raise build_calendar_error(
            code="missing_credentials",
            category="configuration",
            message="Missing Naver CalDAV credentials: " + ", ".join(missing),
            retryable=False,
            retry_strategy="none",
            recommended_retry_count=0,
            manual_action_required=True,
            alert_recommended=True,
            recovery_hint="`.env.local`에 NAVER_ID와 NAVER_APP_PASSWORD를 채운 뒤 다시 실행하세요.",
            raw_message=", ".join(missing),
        )

    return NaverCalDAVConfig(
        url=url,
        username=username,
        password=password,
        calendar_name=calendar_name,
        todo_calendar_name=todo_calendar_name,
        timeout_seconds=timeout_seconds,
        include_all_todos=include_all_todos,
        cache_seconds=cache_seconds,
    )


def _load_caldav_dependencies() -> tuple[Any, Any]:
    try:
        from caldav import DAVClient
    except ImportError as exc:
        raise build_calendar_error(
            code="missing_dependency",
            category="dependency",
            message="The `caldav` package is required. Run `python3 -m pip install -e .`.",
            retryable=False,
            retry_strategy="none",
            recommended_retry_count=0,
            manual_action_required=True,
            alert_recommended=True,
            recovery_hint="가상환경을 활성화한 뒤 `python3 -m pip install -e .`를 다시 실행하세요.",
            raw_message=str(exc).strip() or None,
        ) from exc

    try:
        from icalendar import Calendar
    except ImportError as exc:
        raise build_calendar_error(
            code="missing_dependency",
            category="dependency",
            message="The `icalendar` package is required. Run `python3 -m pip install -e .`.",
            retryable=False,
            retry_strategy="none",
            recommended_retry_count=0,
            manual_action_required=True,
            alert_recommended=True,
            recovery_hint="가상환경을 활성화한 뒤 `python3 -m pip install -e .`를 다시 실행하세요.",
            raw_message=str(exc).strip() or None,
        ) from exc

    logging.getLogger("caldav").setLevel(logging.ERROR)
    return DAVClient, Calendar


def _collect_dated_items(
    calendar: Any,
    calendar_label: str,
    calendar_cls: Any,
    query_start: datetime,
    query_end: datetime,
    include_events: bool,
    include_todos: bool,
    events: List[Any],
    todos: List[Any],
    seen_events: set[tuple[str, str, str]],
    seen_todos: set[tuple[str, Optional[str], Optional[str], str]],
) -> None:
    resources = calendar.date_search(start=query_start, end=query_end, expand=True)

    for resource in resources:
        raw_ical = _resource_ical_payload(resource)
        calendar_obj = calendar_cls.from_ical(raw_ical)

        if include_events:
            for component in calendar_obj.walk("VEVENT"):
                event = build_event(component, calendar_label)
                if event is None:
                    continue
                dedupe_key = (event.item_uid, event.start, event.end)
                if dedupe_key in seen_events:
                    continue
                seen_events.add(dedupe_key)
                events.append(event)

        if include_todos:
            for component in calendar_obj.walk("VTODO"):
                todo = build_todo(component, calendar_label)
                dedupe_key = (todo.item_uid, todo.start, todo.due, todo.status)
                if dedupe_key in seen_todos:
                    continue
                seen_todos.add(dedupe_key)
                todos.append(todo)


def _collect_all_todos(
    calendar: Any,
    calendar_label: str,
    calendar_cls: Any,
    start_date: date,
    end_date: date,
    todos: List[Any],
    seen_todos: set[tuple[str, Optional[str], Optional[str], str]],
) -> None:
    for resource in _list_todo_resources(calendar):
        raw_ical = _resource_ical_payload(resource)
        calendar_obj = calendar_cls.from_ical(raw_ical)

        for component in calendar_obj.walk("VTODO"):
            todo = build_todo(component, calendar_label)
            if not todo_matches_range(todo, start_date, end_date):
                continue

            dedupe_key = (todo.item_uid, todo.start, todo.due, todo.status)
            if dedupe_key in seen_todos:
                continue
            seen_todos.add(dedupe_key)
            todos.append(todo)


def _collect_search_todos(
    calendar: Any,
    calendar_label: str,
    calendar_cls: Any,
    query_start: datetime,
    query_end: datetime,
    start_date: date,
    end_date: date,
    todos: List[Any],
    seen_todos: set[tuple[str, Optional[str], Optional[str], str]],
) -> None:
    search = getattr(calendar, "search", None)
    if not callable(search):
        return

    try:
        resources = search(
            todo=True,
            start=query_start,
            end=query_end,
            include_completed=True,
            expand=False,
        )
    except Exception:
        return

    for resource in resources:
        raw_ical = _resource_ical_payload(resource)
        calendar_obj = calendar_cls.from_ical(raw_ical)

        for component in calendar_obj.walk("VTODO"):
            todo = build_todo(component, calendar_label)
            if not todo_matches_range(todo, start_date, end_date):
                continue

            dedupe_key = (todo.item_uid, todo.start, todo.due, todo.status)
            if dedupe_key in seen_todos:
                continue
            seen_todos.add(dedupe_key)
            todos.append(todo)


def _select_calendars(calendars: Iterable[Any], calendar_name: Optional[str]) -> List[Any]:
    calendars = list(calendars)
    if not calendars:
        raise build_calendar_error(
            code="calendar_not_found",
            category="query",
            message="No calendars were found for the authenticated Naver account.",
            retryable=False,
            retry_strategy="none",
            recommended_retry_count=0,
            manual_action_required=True,
            alert_recommended=True,
            recovery_hint="네이버 계정에 CalDAV로 노출되는 캘린더가 있는지 확인하세요.",
        )

    if calendar_name is None:
        return calendars

    selected = [calendar for calendar in calendars if _calendar_label(calendar) == calendar_name]
    if selected:
        return selected

    available = ", ".join(sorted(_calendar_label(calendar) for calendar in calendars))
    raise build_calendar_error(
        code="calendar_selection_failed",
        category="query",
        message=f"Calendar `{calendar_name}` was not found. Available calendars: {available}",
        retryable=False,
        retry_strategy="none",
        recommended_retry_count=0,
        manual_action_required=True,
        alert_recommended=True,
        recovery_hint="NAVER_CALDAV_CALENDAR 또는 NAVER_CALDAV_TODO_CALENDAR 값을 실제 캘린더 이름과 맞춰주세요.",
        raw_message=available,
    )


def _select_todo_calendars(
    all_calendars: Iterable[Any],
    todo_calendar_name: Optional[str],
    fallback_calendars: Iterable[Any],
) -> List[Any]:
    calendars = list(all_calendars)
    fallback = list(fallback_calendars)

    if not calendars:
        return []

    if todo_calendar_name:
        selected = [calendar for calendar in calendars if _calendar_label(calendar) == todo_calendar_name]
        if selected:
            return selected
        auto_detected = _autodetect_todo_calendars(calendars)
        if auto_detected:
            return auto_detected
        return fallback or calendars

    auto_detected = _autodetect_todo_calendars(calendars)
    if auto_detected:
        return auto_detected

    return fallback or calendars


def _select_event_calendars(
    selected_calendars: Iterable[Any],
    calendar_name: Optional[str],
    todo_calendars: Iterable[Any],
) -> List[Any]:
    selected = list(selected_calendars)
    if calendar_name is not None:
        return selected

    todo_calendar_ids = {_calendar_identifier(calendar) for calendar in todo_calendars}
    filtered = [
        calendar
        for calendar in selected
        if _calendar_identifier(calendar) not in todo_calendar_ids
    ]
    return filtered or selected


def _autodetect_todo_calendars(calendars: Iterable[Any]) -> List[Any]:
    matches = [calendar for calendar in calendars if _looks_like_todo_calendar(_calendar_label(calendar))]
    if len(matches) == 1:
        return matches
    if len(matches) > 1:
        return matches
    return []


def _calendar_label(calendar: Any) -> str:
    name = getattr(calendar, "name", None)
    if callable(name):
        try:
            resolved = name()
            if isinstance(resolved, str) and resolved:
                return resolved
        except Exception:
            pass
    if isinstance(name, str) and name:
        return name

    normalized_url = _normalize_calendar_url(getattr(calendar, "url", None))
    if normalized_url:
        return normalized_url.rstrip("/").split("/")[-1] or "unnamed-calendar"

    return "unnamed-calendar"


def _calendar_identifier(calendar: Any) -> str:
    normalized_url = _normalize_calendar_url(getattr(calendar, "url", None))
    if normalized_url:
        return normalized_url
    return _calendar_label(calendar)


def _normalize_calendar_url(url: Any) -> Optional[str]:
    if url is None:
        return None

    try:
        normalized = str(url).strip()
    except Exception:
        return None

    if not normalized:
        return None

    return normalized


def _looks_like_todo_calendar(label: str) -> bool:
    normalized = label.strip().lower()
    return "할 일" in normalized or "todo" in normalized or "task" in normalized


def _resource_ical_payload(resource: Any) -> str:
    data = getattr(resource, "data", None)
    if isinstance(data, str) and data.strip():
        return data
    if isinstance(data, bytes) and data.strip():
        return data.decode("utf-8")

    icalendar_instance = getattr(resource, "icalendar_instance", None)
    if icalendar_instance is not None:
        try:
            return icalendar_instance.to_ical().decode("utf-8")
        except Exception:
            pass

    raise build_calendar_error(
        code="calendar_payload_parse_failed",
        category="parsing",
        message="Could not extract calendar item payload from a CalDAV resource.",
        retryable=False,
        retry_strategy="none",
        recommended_retry_count=0,
        manual_action_required=True,
        alert_recommended=True,
        recovery_hint="CalDAV 응답 형식이 예상과 다른지 확인하고, 반복되면 원본 응답을 점검하세요.",
    )


def _list_todo_resources(calendar: Any) -> List[Any]:
    todos = getattr(calendar, "todos", None)
    if not callable(todos):
        return []

    try:
        return list(todos(include_completed=True))
    except TypeError:
        try:
            return list(todos())
        except Exception:
            return []
    except Exception:
        return []


def _load_timeout_seconds() -> int:
    raw_value = os.getenv("NAVER_CALDAV_TIMEOUT_SECONDS", "15").strip()
    try:
        timeout = int(raw_value)
    except ValueError as exc:
        raise build_calendar_error(
            code="invalid_timeout_configuration",
            category="configuration",
            message="NAVER_CALDAV_TIMEOUT_SECONDS must be an integer.",
            retryable=False,
            retry_strategy="none",
            recommended_retry_count=0,
            manual_action_required=True,
            alert_recommended=True,
            recovery_hint="`.env.local`의 NAVER_CALDAV_TIMEOUT_SECONDS 값을 정수로 수정하세요.",
            raw_message=raw_value,
        ) from exc

    if timeout <= 0:
        raise build_calendar_error(
            code="invalid_timeout_configuration",
            category="configuration",
            message="NAVER_CALDAV_TIMEOUT_SECONDS must be greater than 0.",
            retryable=False,
            retry_strategy="none",
            recommended_retry_count=0,
            manual_action_required=True,
            alert_recommended=True,
            recovery_hint="`.env.local`의 NAVER_CALDAV_TIMEOUT_SECONDS 값을 1 이상의 정수로 수정하세요.",
            raw_message=raw_value,
        )

    return timeout


def _load_cache_seconds() -> Optional[int]:
    raw_value = os.getenv("NAVER_CALDAV_CACHE_SECONDS")
    if raw_value is None or not raw_value.strip():
        return None

    try:
        ttl = int(raw_value.strip())
    except ValueError as exc:
        raise build_calendar_error(
            code="invalid_cache_configuration",
            category="configuration",
            message="NAVER_CALDAV_CACHE_SECONDS must be an integer.",
            retryable=False,
            retry_strategy="none",
            recommended_retry_count=0,
            manual_action_required=True,
            alert_recommended=True,
            recovery_hint="`.env.local`의 NAVER_CALDAV_CACHE_SECONDS 값을 정수로 수정하세요.",
            raw_message=raw_value,
        ) from exc

    if ttl < 0:
        raise build_calendar_error(
            code="invalid_cache_configuration",
            category="configuration",
            message="NAVER_CALDAV_CACHE_SECONDS must be 0 or greater.",
            retryable=False,
            retry_strategy="none",
            recommended_retry_count=0,
            manual_action_required=True,
            alert_recommended=True,
            recovery_hint="`.env.local`의 NAVER_CALDAV_CACHE_SECONDS 값을 0 이상의 정수로 수정하세요.",
            raw_message=raw_value,
        )

    return ttl


def _load_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise build_calendar_error(
        code="invalid_boolean_configuration",
        category="configuration",
        message=f"{name} must be one of: true, false, 1, 0, yes, no, on, off.",
        retryable=False,
        retry_strategy="none",
        recommended_retry_count=0,
        manual_action_required=True,
        alert_recommended=True,
        recovery_hint=f"`.env.local`의 {name} 값을 true/false 형식으로 수정하세요.",
        raw_message=raw_value,
    )


def _classify_caldav_error(exc: Exception, timeout_seconds: int) -> CalendarIntegrationError:
    class_name = exc.__class__.__name__.lower()
    message = str(exc).strip()
    message_lower = message.lower()

    if "timeout" in class_name or "timed out" in message_lower or "read timed out" in message_lower:
        return build_calendar_error(
            code="request_timeout",
            category="network",
            message=(
                "Naver CalDAV request timed out. "
                f"Check your network or reduce server delay, then try again. "
                f"Current timeout: {timeout_seconds}s."
            ),
            retryable=True,
            retry_strategy="backoff",
            recommended_retry_count=3,
            manual_action_required=False,
            alert_recommended=False,
            recovery_hint="잠시 후 재시도하고, 반복되면 NAVER_CALDAV_TIMEOUT_SECONDS 값을 늘리세요.",
            raw_message=message or None,
        )

    if "401" in message_lower or "403" in message_lower or "unauthorized" in message_lower or "forbidden" in message_lower:
        return build_calendar_error(
            code="authentication_failed",
            category="authentication",
            message=(
                "Naver CalDAV authentication failed. "
                "Check NAVER_ID, NAVER_APP_PASSWORD, and CalDAV app-password settings."
            ),
            retryable=False,
            retry_strategy="none",
            recommended_retry_count=0,
            manual_action_required=True,
            alert_recommended=True,
            recovery_hint="앱 비밀번호와 CalDAV 사용 가능 여부를 다시 확인하세요.",
            raw_message=message or None,
        )

    if any(
        keyword in message_lower
        for keyword in (
            "connection refused",
            "connection reset",
            "remote disconnected",
            "name or service not known",
            "temporary failure in name resolution",
            "failed to establish a new connection",
            "max retries exceeded",
            "nodename nor servname provided",
        )
    ):
        return build_calendar_error(
            code="network_unreachable",
            category="network",
            message="Naver CalDAV network connection failed. Check your network and try again.",
            retryable=True,
            retry_strategy="backoff",
            recommended_retry_count=3,
            manual_action_required=False,
            alert_recommended=False,
            recovery_hint="네트워크 상태를 확인한 뒤 잠시 후 다시 시도하세요.",
            raw_message=message or None,
        )

    if any(keyword in message_lower for keyword in ("500", "502", "503", "504", "bad gateway", "service unavailable")):
        return build_calendar_error(
            code="provider_unavailable",
            category="query",
            message="Naver CalDAV provider is temporarily unavailable.",
            retryable=True,
            retry_strategy="backoff",
            recommended_retry_count=3,
            manual_action_required=False,
            alert_recommended=False,
            recovery_hint="잠시 후 재시도하고, 반복되면 서비스 상태를 확인하세요.",
            raw_message=message or None,
        )

    if any(keyword in message_lower for keyword in ("parse", "ical", "invalid calendar", "component")):
        return build_calendar_error(
            code="calendar_parse_failed",
            category="parsing",
            message="Naver CalDAV response could not be parsed.",
            retryable=False,
            retry_strategy="none",
            recommended_retry_count=0,
            manual_action_required=True,
            alert_recommended=True,
            recovery_hint="반복되면 원본 CalDAV 응답이나 특정 일정 데이터를 확인하세요.",
            raw_message=message or None,
        )

    if message:
        return build_calendar_error(
            code="request_failed",
            category="query",
            message=f"Naver CalDAV request failed: {message}",
            retryable=True,
            retry_strategy="backoff",
            recommended_retry_count=2,
            manual_action_required=False,
            alert_recommended=False,
            recovery_hint="잠시 후 다시 시도하고, 반복되면 에러 메시지를 점검하세요.",
            raw_message=message,
        )

    return build_calendar_error(
        code="unknown_failure",
        category="unknown",
        message="Naver CalDAV request failed for an unknown reason.",
        retryable=True,
        retry_strategy="backoff",
        recommended_retry_count=1,
        manual_action_required=False,
        alert_recommended=True,
        recovery_hint="잠시 후 재시도하고, 반복되면 로그를 확인하세요.",
        raw_message=None,
    )


def _to_local_datetime(value: date) -> datetime:
    return datetime.combine(value, time.min).astimezone()


__all__ = [
    "CalendarIntegrationError",
    "NaverCalDAVConfig",
    "list_naver_calendar_items",
    "list_naver_calendar_events",
    "load_naver_caldav_config",
    "render_calendar_items",
    "render_calendar_events",
]
