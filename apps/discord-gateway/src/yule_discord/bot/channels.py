"""Channel-name helpers (P0-Q step 6).

Tiny pure-string utilities used widely across ``bot/_legacy.py``
to normalize Discord channel name lookups and to format status text
for startup / debug logs. Extracted so downstream split modules
(startup / message_routing / engineering_handlers) can share them
without re-importing the rest of the legacy bot.
"""

from __future__ import annotations


def _normalize_channel_name(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip().lstrip("#").lower()


def _channel_target_text(channel_id: int | None, channel_name: str | None) -> str:
    parts = []
    if channel_id is not None:
        parts.append(f"channel_id={channel_id}")
    if channel_name:
        parts.append(f"channel_name={channel_name}")
    return ", ".join(parts) if parts else "channel=unconfigured"
