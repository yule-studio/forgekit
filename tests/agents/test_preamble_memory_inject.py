"""F14 commit 5 — preamble + memory injection 회귀."""

from __future__ import annotations

import unittest
from typing import Iterable
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.preamble import build_default_preamble, inject_memory_summary


class _FakeMemoryShard:
    def __init__(self, kind="mistake", source="ledger", content="boom"):
        self.kind = kind
        self.source = source
        self.content = content
        self.related_issue = 73
        self.related_pr = None
        self.hash = f"h-{content}"
        self.created_at = "2026-05-12T00:00:00+00:00"
        self.topic_tags: tuple = ()
        self.blocker_level = None
        self.signature = None


class _FakeSource:
    def __init__(self, shards: Iterable):
        self._shards = tuple(shards)

    def query(self, filter):
        return list(self._shards)


class _FakeMemory:
    def __init__(self, shards):
        self.sources = (_FakeSource(shards),)


from yule_orchestrator.agents.memory.long_term_memory import RequestContext as _RealRequestContext


def _make_request():
    return _RealRequestContext(
        role="backend-engineer",
        topic_tags=("api",),
        issue=None,
        pr=None,
    )


class InjectMemorySummaryTests(unittest.TestCase):
    def test_no_request_no_memory_returns_plain_render(self) -> None:
        p = build_default_preamble()
        out = inject_memory_summary(p)
        self.assertNotIn("LONG-TERM MEMORY", out)

    def test_with_request_and_memory_appends_section(self) -> None:
        p = build_default_preamble()
        mem = _FakeMemory([_FakeMemoryShard(content="prev mistake X")])
        req = _make_request()
        # build_memory_pack 의 env 체크 우회
        with patch(
            "yule_orchestrator.agents.memory.long_term_memory.long_term_memory_enabled",
            return_value=True,
        ):
            out = inject_memory_summary(p, request_context=req, long_term_memory=mem)
        self.assertIn("LONG-TERM MEMORY", out)
        self.assertIn("prev mistake X", out)

    def test_empty_pack_appends_nothing(self) -> None:
        p = build_default_preamble()
        mem = _FakeMemory([])
        req = _make_request()
        with patch(
            "yule_orchestrator.agents.memory.long_term_memory.long_term_memory_enabled",
            return_value=True,
        ):
            out = inject_memory_summary(p, request_context=req, long_term_memory=mem)
        self.assertNotIn("LONG-TERM MEMORY", out)

    def test_env_off_no_injection(self) -> None:
        p = build_default_preamble()
        mem = _FakeMemory([_FakeMemoryShard()])
        req = _make_request()
        with patch(
            "yule_orchestrator.agents.memory.long_term_memory.long_term_memory_enabled",
            return_value=False,
        ):
            out = inject_memory_summary(p, request_context=req, long_term_memory=mem)
        self.assertNotIn("LONG-TERM MEMORY", out)


if __name__ == "__main__":
    unittest.main()
