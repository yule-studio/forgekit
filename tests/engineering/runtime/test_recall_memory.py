"""Phase 3B — Recall stage with memory_search_fn wired in.

The runtime treats memory hits as a parallel signal: even when there's
no session match the role policy still runs a role-shaped search and
attaches the hits so the gateway can surface citations. Errors in the
memory adapter must never propagate.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.runtime import (
    INTENT_NEW_WORK_REQUEST,
    INTENT_SUMMARIZE_PREVIOUS_WORK,
    RuntimeInput,
    RuntimeIntent,
    RuntimeObservation,
)
from yule_engineering.agents.runtime.recall import make_recall_fn


@dataclass
class FakeSession:
    session_id: str
    prompt: str = ""
    task_type: str = "unknown"
    state: str = "in_progress"
    summary: Optional[str] = None
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    updated_at: Optional[datetime] = None
    extra: Mapping[str, Any] = lambda: {}  # type: ignore[assignment]


def _now(offset_minutes: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)


def _obs(text: str) -> RuntimeObservation:
    return RuntimeObservation(
        role_id="engineering-agent/backend-engineer",
        message_text=text,
        normalized_text=" ".join(text.lower().split()),
    )


def _input(text: str, role: str = "engineering-agent/backend-engineer") -> RuntimeInput:
    return RuntimeInput(role_id=role, message_text=text)


class MemorySearchInjectionTests(unittest.TestCase):
    def test_memory_search_fn_called_with_role_filter(self) -> None:
        captured: dict = {}

        def fake_search(query, **kwargs):
            captured["query"] = query
            captured["kwargs"] = kwargs
            return [{"doc_id": "abc", "title": "queue ack design"}]

        recall = make_recall_fn(
            list_sessions_fn=lambda **_kw: [],
            memory_search_fn=fake_search,
        )
        result = recall(
            _obs("hermes 학습 루프 정리해줘"),
            RuntimeIntent(intent_id=INTENT_SUMMARIZE_PREVIOUS_WORK),
            _input("hermes 학습 루프 정리해줘"),
        )
        self.assertEqual(captured["query"], "hermes 학습 루프 정리해줘")
        # backend-engineer's memory_role_filter is "backend-engineer".
        self.assertEqual(captured["kwargs"]["role"], "backend-engineer")
        # And one memory hit landed on the result.
        self.assertEqual(len(result.memory_hits), 1)
        self.assertEqual(result.memory_hits[0]["doc_id"], "abc")

    def test_memory_search_failure_is_swallowed(self) -> None:
        def explosive(query, **_kw):
            raise RuntimeError("index offline")

        recall = make_recall_fn(
            list_sessions_fn=lambda **_kw: [],
            memory_search_fn=explosive,
        )
        result = recall(
            _obs("hermes 학습 루프 정리해줘"),
            RuntimeIntent(intent_id=INTENT_SUMMARIZE_PREVIOUS_WORK),
            _input("hermes 학습 루프 정리해줘"),
        )
        # No hits, but no exception — recall returned its base result.
        self.assertEqual(result.memory_hits, ())
        self.assertEqual(result.confidence, "low")

    def test_simple_search_fn_without_kwargs_still_works(self) -> None:
        def simple_search(query):
            return [{"doc_id": "x", "title": "fallback"}]

        recall = make_recall_fn(
            list_sessions_fn=lambda **_kw: [],
            memory_search_fn=simple_search,
        )
        result = recall(
            _obs("hermes 학습 루프"),
            RuntimeIntent(intent_id=INTENT_SUMMARIZE_PREVIOUS_WORK),
            _input("hermes 학습 루프"),
        )
        self.assertEqual(len(result.memory_hits), 1)
        self.assertEqual(result.memory_hits[0]["doc_id"], "x")

    def test_gateway_role_passes_no_role_filter(self) -> None:
        captured: dict = {}

        def fake_search(query, **kwargs):
            captured["kwargs"] = kwargs
            return []

        recall = make_recall_fn(
            list_sessions_fn=lambda **_kw: [],
            memory_search_fn=fake_search,
        )
        recall(
            _obs("hermes 학습 루프"),
            RuntimeIntent(intent_id=INTENT_SUMMARIZE_PREVIOUS_WORK),
            _input("hermes 학습 루프", role="gateway"),
        )
        # Gateway policy has memory_role_filter=None, which we forward
        # so the search adapter keeps the role unfiltered.
        self.assertIsNone(captured["kwargs"]["role"])

    def test_empty_message_skips_memory_search(self) -> None:
        called: dict[str, int] = {"n": 0}

        def fake_search(query, **kwargs):
            called["n"] += 1
            return []

        recall = make_recall_fn(
            list_sessions_fn=lambda **_kw: [],
            memory_search_fn=fake_search,
        )
        recall(
            _obs(""),
            RuntimeIntent(intent_id=INTENT_SUMMARIZE_PREVIOUS_WORK),
            _input(""),
        )
        self.assertEqual(called["n"], 0)

    def test_new_work_intent_still_runs_memory_search(self) -> None:
        captured: dict = {}

        def fake_search(query, **kwargs):
            captured["query"] = query
            return [{"doc_id": "ref", "title": "prior reference"}]

        recall = make_recall_fn(
            list_sessions_fn=lambda **_kw: [],
            memory_search_fn=fake_search,
        )
        result = recall(
            _obs("결제 모듈 멱등성 구현해줘"),
            RuntimeIntent(intent_id=INTENT_NEW_WORK_REQUEST),
            _input("결제 모듈 멱등성 구현해줘"),
        )
        # Even on new-work the memory search still gives the runtime
        # something to cite — Decide will choose whether to use it.
        self.assertEqual(captured["query"], "결제 모듈 멱등성 구현해줘")
        self.assertEqual(len(result.memory_hits), 1)


if __name__ == "__main__":
    unittest.main()
