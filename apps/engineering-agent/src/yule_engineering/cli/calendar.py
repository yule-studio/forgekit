from __future__ import annotations

import json
from datetime import date, datetime
from typing import Optional

from ..integrations.calendar import (
    CalendarIntegrationError,
    CalendarQueryResult,
    list_naver_calendar_items,
    render_calendar_items,
)
from ..integrations.calendar.cache import CALENDAR_CACHE_NAMESPACE, list_calendar_cache_entries
from ..integrations.calendar.errors import build_calendar_validation_error
from ..storage import (
    CalendarStateRecord,
    cleanup_calendar_state_records,
    cleanup_json_cache,
    list_calendar_state_records,
    local_cache_database_path,
)


def run_calendar_events_command(
    start_date_text: Optional[str],
    end_date_text: Optional[str],
    json_output: bool,
    force_refresh: bool,
) -> int:
    try:
        start_date, end_date = _resolve_date_range(start_date_text, end_date_text)
        result = list_naver_calendar_items(
            start_date=start_date,
            end_date=end_date,
            force_refresh=force_refresh,
        )
    except ValueError as exc:
        if not json_output:
            raise
        print(json.dumps({"error": build_calendar_validation_error(str(exc)).to_dict()}, ensure_ascii=False, indent=2))
        return 1
    except CalendarIntegrationError as exc:
        if not json_output:
            raise
        print(json.dumps({"error": exc.to_dict()}, ensure_ascii=False, indent=2))
        return 1

    if json_output:
        print(json.dumps(_result_to_dict(result), ensure_ascii=False, indent=2))
        return 0

    print(render_calendar_items(result), end="")
    return 0


def run_calendar_warmup_command(
    start_date_text: Optional[str],
    end_date_text: Optional[str],
    json_output: bool,
    force_refresh: bool,
) -> int:
    return _run_calendar_sync_command(
        action="warmup",
        text_verb="warmed calendar cache",
        start_date_text=start_date_text,
        end_date_text=end_date_text,
        json_output=json_output,
        force_refresh=force_refresh,
    )


def run_calendar_sync_command(
    start_date_text: Optional[str],
    end_date_text: Optional[str],
    json_output: bool,
    force_refresh: bool,
) -> int:
    return _run_calendar_sync_command(
        action="sync",
        text_verb="synced calendar state",
        start_date_text=start_date_text,
        end_date_text=end_date_text,
        json_output=json_output,
        force_refresh=force_refresh,
    )


def _run_calendar_sync_command(
    action: str,
    text_verb: str,
    start_date_text: Optional[str],
    end_date_text: Optional[str],
    json_output: bool,
    force_refresh: bool,
) -> int:
    start_date, end_date = _resolve_date_range(start_date_text, end_date_text)
    result = list_naver_calendar_items(
        start_date=start_date,
        end_date=end_date,
        force_refresh=force_refresh,
    )

    payload = {
        "action": action,
        "database_path": str(local_cache_database_path()),
        "force_refresh": force_refresh,
        **_result_to_dict(result),
    }

    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(
        f"{text_verb} for {start_date.isoformat()}..{end_date.isoformat()} "
        f"({len(result.events)} events, {len(result.todos)} todos)"
    )
    print(f"cache db: {local_cache_database_path()}")
    return 0


def run_calendar_cache_inspect_command(
    json_output: bool,
    limit: int,
    fresh_only: bool,
) -> int:
    entries = list_calendar_cache_entries(
        limit=limit,
        include_expired=not fresh_only,
    )
    payload = {
        "database_path": str(local_cache_database_path()),
        "namespace": CALENDAR_CACHE_NAMESPACE,
        "entry_count": len(entries),
        "entries": [_format_cache_entry(entry) for entry in entries],
        "state_record_count": len(list_calendar_state_records()),
    }

    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"cache db: {payload['database_path']}")
    print(f"namespace: {payload['namespace']}")
    print(f"entries: {payload['entry_count']}")
    print(f"state records: {payload['state_record_count']}")
    if not entries:
        print("no cache entries")
        return 0

    for entry in payload["entries"]:
        stale_label = "stale" if entry["is_stale"] else "fresh"
        print(
            f"- {entry['cache_key']} [{stale_label}] "
            f"{entry['range_start']}..{entry['range_end']} "
            f"expires {entry['expires_at_iso']}"
        )
    return 0


def run_calendar_cache_cleanup_command(
    json_output: bool,
    cache_retention_days: int,
    state_retention_days: int,
) -> int:
    cache_deleted_count = cleanup_json_cache(
        namespace=CALENDAR_CACHE_NAMESPACE,
        stale_retention_seconds=max(0, cache_retention_days) * 24 * 60 * 60,
    )
    state_deleted_count = cleanup_calendar_state_records(
        retention_seconds=max(0, state_retention_days) * 24 * 60 * 60,
    )
    payload = {
        "action": "cleanup",
        "database_path": str(local_cache_database_path()),
        "cache_retention_days": cache_retention_days,
        "state_retention_days": state_retention_days,
        "cache_deleted_count": cache_deleted_count,
        "state_deleted_count": state_deleted_count,
    }

    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"cache db: {payload['database_path']}")
    print(f"deleted cache entries: {cache_deleted_count}")
    print(f"deleted state records: {state_deleted_count}")
    return 0


def run_calendar_categories_command(
    start_date_text: Optional[str],
    end_date_text: Optional[str],
    json_output: bool,
    include_completed: bool,
) -> int:
    start_date, end_date = _resolve_date_range(start_date_text, end_date_text)
    records = list_calendar_state_records(
        start_date=start_date,
        end_date=end_date,
        include_completed=include_completed,
    )
    categories = _build_category_summary(records)
    payload = {
        "action": "categories",
        "database_path": str(local_cache_database_path()),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "category_count": len(categories),
        "item_count": sum(category["item_count"] for category in categories),
        "categories": categories,
    }

    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"calendar categories for {start_date.isoformat()}..{end_date.isoformat()}")
    print(f"cache db: {payload['database_path']}")
    if not categories:
        print("no calendar state records. run `yule calendar sync` first.")
        return 0

    for category in categories:
        color_label = category["category_color"] or "no-color"
        print(f"- color {color_label}: {category['item_count']} item(s)")
        for item in category["items"]:
            when_label = item["due_at"] or item["start_at"] or item["end_at"] or "no-date"
            completed_label = " completed" if item["completed"] else ""
            print(f"  - {item['item_type']} | {when_label} | {item['title']}{completed_label}")
    return 0


def _resolve_date_range(
    start_date_text: Optional[str],
    end_date_text: Optional[str],
) -> tuple[date, date]:
    if start_date_text is None and end_date_text is None:
        today = date.today()
        return today, today

    if start_date_text is None:
        raise ValueError("--end-date requires --start-date.")

    start_date = _parse_date(start_date_text, flag_name="--start-date")
    end_date = start_date if end_date_text is None else _parse_date(end_date_text, flag_name="--end-date")

    if end_date < start_date:
        raise ValueError("--end-date must be the same as or later than --start-date.")

    return start_date, end_date


def _parse_date(value: str, flag_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{flag_name} must use YYYY-MM-DD format.") from exc


def _result_to_dict(result: CalendarQueryResult) -> dict:
    return result.to_dict()


def _format_cache_entry(entry: dict) -> dict:
    fetched_at = _timestamp_to_iso(entry["fetched_at"])
    expires_at = _timestamp_to_iso(entry["expires_at"])
    last_accessed_at = _timestamp_to_iso(entry["last_accessed_at"])
    return {
        **entry,
        "fetched_at_iso": fetched_at,
        "expires_at_iso": expires_at,
        "last_accessed_at_iso": last_accessed_at,
    }


def _timestamp_to_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat()


def _build_category_summary(records: list[CalendarStateRecord]) -> list[dict]:
    grouped: dict[tuple[str, str], list[CalendarStateRecord]] = {}
    for record in records:
        group_key = (record.category_color or "", record.item_type)
        grouped.setdefault(group_key, []).append(record)

    summaries: list[dict] = []
    for (category_color, item_type), group_records in grouped.items():
        group_records.sort(key=lambda record: (record.due_at or record.start_at or record.end_at or "", record.title))
        summaries.append(
            {
                "category_color": category_color or None,
                "item_type": item_type,
                "item_count": len(group_records),
                "items": [
                    {
                        "item_type": record.item_type,
                        "calendar_name": record.calendar_name,
                        "title": record.title,
                        "start_at": record.start_at,
                        "end_at": record.end_at,
                        "due_at": record.due_at,
                        "completed": record.completed,
                    }
                    for record in group_records
                ],
            }
        )

    summaries.sort(
        key=lambda category: (
            category["category_color"] is None,
            category["category_color"] or "",
            category["item_type"],
        )
    )
    return summaries
