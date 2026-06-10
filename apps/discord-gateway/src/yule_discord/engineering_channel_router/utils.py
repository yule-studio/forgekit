"""engineering_channel_router — env coercion + message parsing + async helpers.

Pure leaf module (no router-internal deps). Collects the type/env/value
coercion primitives plus the discord.py-agnostic message readers
(``extract_user_links_from_message``, ``extract_message_attachments``)
and the ``_maybe_await`` async-or-not helper every gateway hook uses.

Also hosts the F16 ``_attach_recall_coverage`` derived-metadata helper
because it sits between the runtime recall and the routing layer — no
business decisions, just enrichment.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Optional

from yule_agent_runtime import (
    RecallCoverage,
    RuntimeRecallResult,
    compute_recall_coverage,
)


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_channel_name(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip().lstrip("#").lower()


# ---------------------------------------------------------------------------
# Env readers
# ---------------------------------------------------------------------------


def _optional_int_env(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be an integer value, got: {value!r}"
        ) from exc


def _optional_string_env(name: str) -> Optional[str]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _optional_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a boolean envvar — empty/unset returns ``default``.

    Accepted truthy values: ``"true"``, ``"1"``, ``"yes"``, ``"on"``
    (case-insensitive). Anything else is treated as the default. Used
    by F16 ``EngineeringRouteContext.prefer_recall_first_gateway`` so
    operators can opt into the recall-first gateway path without code
    changes.
    """

    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if not value:
        return default
    return value in {"true", "1", "yes", "on"}


# ---------------------------------------------------------------------------
# Message parsing (discord.py-agnostic)
# ---------------------------------------------------------------------------


def extract_user_links_from_message(
    message: Any,
    prompt_text: Optional[str] = None,
) -> tuple[str, ...]:
    """Pull URLs out of the user's message body.

    Lazily delegates to :func:`research_collector.extract_urls` so we get
    the same regex + dedup the collector uses internally. Returns an empty
    tuple if the helper isn't importable (e.g. during a partial install).
    """

    text = (prompt_text or getattr(message, "content", "") or "")
    if not text:
        return ()
    try:
        from yule_engineering.agents.research.collector import extract_urls
    except Exception:  # noqa: BLE001
        return ()
    return tuple(extract_urls(text))


def extract_message_attachments(message: Any) -> tuple[Any, ...]:
    """Return the message's attachments as a stable tuple, discord.py-agnostic.

    discord.py exposes ``message.attachments`` as a list of ``Attachment``
    objects, but tests pass plain dataclasses or dicts. We accept any iterable
    and drop ``None`` entries so the engineering conversation layer can rely
    on a clean sequence regardless of the Discord shape.
    """

    raw = getattr(message, "attachments", None)
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(item for item in raw if item is not None)
    try:
        return tuple(item for item in raw if item is not None)
    except TypeError:
        return ()


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


# ---------------------------------------------------------------------------
# F16 recall coverage enrichment
# ---------------------------------------------------------------------------


def _attach_recall_coverage(recall: RuntimeRecallResult) -> RuntimeRecallResult:
    """F16 — replace ``recall`` with a copy whose ``coverage`` is scored.

    The scorer is **defensive**: any failure (None, malformed hits)
    degrades to ``RecallCoverage(level=low, stale=True)``. Legacy
    callers that ignore ``coverage`` see no behaviour change.
    """

    try:
        coverage = compute_recall_coverage(recall)
    except Exception:  # noqa: BLE001
        coverage = RecallCoverage(
            level="low", stale=True, sources=(), reason="scorer raised"
        )
    return replace(recall, coverage=coverage)


__all__ = (
    "_attach_recall_coverage",
    "_maybe_await",
    "_normalize_channel_name",
    "_optional_bool_env",
    "_optional_int_env",
    "_optional_str",
    "_optional_string_env",
    "_safe_int",
    "extract_message_attachments",
    "extract_user_links_from_message",
)
