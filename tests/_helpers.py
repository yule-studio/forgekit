"""Shared fixtures/helpers for engineering-router & related tests.

These helpers used to live as `_Channel` / `_Message` / `_FakeSession`
copies inside individual test modules. They're collected here so a
single rename in the production code only touches one helper module.

Nothing here exercises real Discord, real cache, or real network.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator


# ---------------------------------------------------------------------------
# Async / env utilities
# ---------------------------------------------------------------------------


def run(coro):
    """Run an async coroutine to completion in a fresh event loop."""

    return asyncio.run(coro)


@contextmanager
def patched_env(values: dict[str, str | None]) -> Iterator[None]:
    """Temporarily set/clear env vars; restore on exit.

    Pass ``None`` as the value to unset a variable for the duration of
    the ``with`` block.
    """

    previous: dict[str, str | None] = {}
    for key, value in values.items():
        previous[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, prior in previous.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior


# ---------------------------------------------------------------------------
# Discord channel / message stand-ins
# ---------------------------------------------------------------------------


class FakeParent:
    def __init__(self, parent_id: int | None, parent_name: str | None) -> None:
        self.id = parent_id
        self.name = parent_name


class FakeChannel:
    def __init__(
        self,
        *,
        channel_id: int,
        name: str | None,
        parent_id: int | None = None,
        parent_name: str | None = None,
    ) -> None:
        self.id = channel_id
        self.name = name
        if parent_id is None and parent_name is None:
            self.parent = None
            self.parent_id = None
        else:
            self.parent = FakeParent(parent_id, parent_name)
            self.parent_id = parent_id


class FakeAuthor:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeMessage:
    def __init__(
        self,
        *,
        content: str,
        channel: FakeChannel,
        author_id: int = 4242,
    ) -> None:
        self.content = content
        self.channel = channel
        self.author = FakeAuthor(author_id)
        self.mentions: list[Any] = []


class FakeMessageWithAttachments(FakeMessage):
    def __init__(
        self,
        *,
        content: str,
        channel: FakeChannel,
        attachments: Iterable[Any] | None = None,
        author_id: int = 4242,
    ) -> None:
        super().__init__(content=content, channel=channel, author_id=author_id)
        self.attachments = list(attachments or [])


# ---------------------------------------------------------------------------
# Workflow session / intake stand-ins
# ---------------------------------------------------------------------------


@dataclass
class FakeSession:
    session_id: str
    task_type: str
    executor_role: str | None = "tech-lead"
    executor_runner: str | None = "claude-code"


@dataclass
class FakePlan:
    role_sequence: tuple[str, ...] = ("tech-lead", "backend-engineer")


@dataclass
class FakeIntakeResult:
    session: FakeSession
    plan: FakePlan
    message: str


def extract_prompt(*, message: object, bot_user: object) -> str:
    """Default ``extract_prompt`` for router tests — just stringify content."""

    return str(getattr(message, "content", "") or "")


# ---------------------------------------------------------------------------
# Workflow cache isolation
# ---------------------------------------------------------------------------


def isolate_cache_for_test(test: unittest.TestCase) -> None:
    """Pin ``YULE_CACHE_DB_PATH`` at a per-test temp file.

    Why: ``decide_routing`` reads ``list_open_sessions`` from the SQLite
    cache. Without per-test isolation, sessions written by earlier tests
    in the same process would leak into routing similarity scoring and
    flip CREATE expectations to JOIN/ASK — silent failures that only
    surface when the full test suite runs.
    """

    handle = tempfile.NamedTemporaryFile(
        prefix="yule-router-test-", suffix=".sqlite3", delete=False
    )
    handle.close()
    prev = os.environ.get("YULE_CACHE_DB_PATH")
    os.environ["YULE_CACHE_DB_PATH"] = handle.name

    def _restore() -> None:
        if prev is None:
            os.environ.pop("YULE_CACHE_DB_PATH", None)
        else:
            os.environ["YULE_CACHE_DB_PATH"] = prev
        try:
            os.unlink(handle.name)
        except OSError:
            pass

    test.addCleanup(_restore)
