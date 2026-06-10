"""Memory unifier governance regression (F10 / #101).

Pins the hard rails defined in issue #101 so a future refactor that
breaks one trips a clearly named test:

  1. ``YULE_LONG_TERM_MEMORY_ENABLED`` defaults to OFF — calling
     :func:`build_memory_pack` without the env returns an empty
     :class:`MemoryPack`.
  2. Source adapters are read-only — they expose only ``query`` (no
     ``write`` / ``insert`` / ``upsert`` / ``delete`` / ``mutate``).
  3. A BLOCK-level mistake shard always lands at ``shards[0]``, even
     when its recency / topic / role signals are 0.
  4. ContextPack carries the ``memory_pack`` field and ``to_payload``
     emits it (None or full payload) — downstream payload renderers
     never silently drop the channel.
  5. Adapter exceptions never propagate to caller — a broken adapter
     yields an empty contribution, not a worker crash.
  6. PasteGuard module remains importable + functional — F10 must
     not regress F1's outbound rails.
  7. MistakeLedger module remains importable + functional — F10
     must not regress F2's durable mistake ledger.
  8. ``memory`` package exposes every public surface in ``__all__``
     so import drift is caught early.
"""

from __future__ import annotations

import os
import unittest
from typing import Iterable

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.decision.context_pack import (
    ContextPack,
    build_context_pack,
)
from yule_learning.mistake_ledger import (
    BlockerLevel,
    MistakeLedger,
)
from yule_engineering.agents.memory import (
    ENV_LONG_TERM_MEMORY_ENABLED,
    AuditSource,
    DecisionSource,
    LongTermMemory,
    MemoryFilter,
    MemoryPack,
    MemoryShard,
    MistakeLedgerSource,
    ObsidianVaultSource,
    RequestContext,
    SessionExtraSource,
    ShardKind,
    build_memory_pack,
)
from yule_security.paste_guard import (
    OutboundChannel,
    guard_outbound,
)


READ_ONLY_FORBIDDEN_METHODS = (
    "write",
    "insert",
    "upsert",
    "delete",
    "mutate",
    "drop",
    "create_table",
    "save",
)


class _AlwaysRaisingSource:
    """Sentinel source that fails every call — used in isolation test."""

    kind = ShardKind.AUDIT

    def query(self, filter: MemoryFilter) -> Iterable[MemoryShard]:
        raise RuntimeError("intentional sentinel failure")


class MemoryUnifierGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_env = os.environ.get(ENV_LONG_TERM_MEMORY_ENABLED)

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop(ENV_LONG_TERM_MEMORY_ENABLED, None)
        else:
            os.environ[ENV_LONG_TERM_MEMORY_ENABLED] = self._prev_env

    # 1
    def test_env_off_by_default_returns_empty_pack(self) -> None:
        os.environ.pop(ENV_LONG_TERM_MEMORY_ENABLED, None)
        ltm = LongTermMemory([])
        pack = build_memory_pack(
            long_term_memory=ltm,
            request_context=RequestContext(),
        )
        self.assertIsInstance(pack, MemoryPack)
        self.assertEqual(pack.shards, ())

    # 2
    def test_all_source_adapters_are_read_only(self) -> None:
        ledger = MistakeLedger(database_path=":memory:")
        try:
            adapters = [
                ObsidianVaultSource("/nonexistent-vault"),
                SessionExtraSource([]),
                MistakeLedgerSource(ledger),
                DecisionSource([]),
                AuditSource([]),
            ]
            for adapter in adapters:
                public_methods = {
                    name
                    for name in dir(adapter)
                    if not name.startswith("_")
                }
                for forbidden in READ_ONLY_FORBIDDEN_METHODS:
                    self.assertNotIn(
                        forbidden,
                        public_methods,
                        f"{type(adapter).__name__} exposes mutating "
                        f"method {forbidden!r}",
                    )
                self.assertIn("query", public_methods)
        finally:
            ledger.close()

    # 3
    def test_block_mistake_always_pinned_top(self) -> None:
        os.environ[ENV_LONG_TERM_MEMORY_ENABLED] = "true"
        block_shard = MemoryShard(
            kind=ShardKind.MISTAKE,
            source="mistake-ledger:ai-engineer",
            content="[force_push] never bypass protected branch",
            created_at="2000-01-01T00:00:00+00:00",
            topic_tags=(),
            blocker_level="BLOCK",
        )
        fresh_note = MemoryShard(
            kind=ShardKind.OBSIDIAN_NOTE,
            source="obsidian-vault:notes/relevant.md",
            content="relevant",
            created_at="2026-05-11T00:00:00+00:00",
            topic_tags=("topic",),
        )

        class _Src:
            kind = ShardKind.OBSIDIAN_NOTE

            def query(self, filter: MemoryFilter):
                return [fresh_note, block_shard]

        ltm = LongTermMemory([_Src()])
        pack = build_memory_pack(
            long_term_memory=ltm,
            request_context=RequestContext(topic_tags=("topic",)),
        )
        self.assertGreater(len(pack.shards), 0)
        self.assertEqual(pack.shards[0].kind, ShardKind.MISTAKE)

    # 4
    def test_context_pack_carries_memory_pack(self) -> None:
        os.environ[ENV_LONG_TERM_MEMORY_ENABLED] = "true"
        ltm = LongTermMemory([])
        pack = build_memory_pack(
            long_term_memory=ltm,
            request_context=RequestContext(),
        )
        ctx_pack = build_context_pack(prompt="hello", memory_pack=pack)
        self.assertIsInstance(ctx_pack, ContextPack)
        self.assertEqual(ctx_pack.memory_pack, pack)
        payload = ctx_pack.to_payload()
        self.assertIn("memory_pack", payload)

        # And None survives round-trip.
        ctx_pack_none = build_context_pack(prompt="hello")
        self.assertIsNone(ctx_pack_none.memory_pack)
        self.assertIsNone(ctx_pack_none.to_payload()["memory_pack"])

    # 5
    def test_adapter_exception_isolated(self) -> None:
        os.environ[ENV_LONG_TERM_MEMORY_ENABLED] = "true"
        ltm = LongTermMemory([_AlwaysRaisingSource()])
        pack = build_memory_pack(
            long_term_memory=ltm,
            request_context=RequestContext(),
        )
        # No exception escaped. Empty pack is acceptable; the rail is
        # "caller never sees the adapter fault".
        self.assertEqual(pack.shards, ())

    # 6
    def test_paste_guard_still_importable_and_functional(self) -> None:
        verdict = guard_outbound(
            channel=OutboundChannel.LLM, payload="hello world"
        )
        self.assertFalse(verdict.blocked)
        self.assertEqual(verdict.redacted, "hello world")

    # 7
    def test_mistake_ledger_still_functional(self) -> None:
        ledger = MistakeLedger(database_path=":memory:")
        try:
            record = ledger.record_mistake(
                role="ai-engineer",
                pattern="ci",
                signature="ci test failed",
                blocker_level=BlockerLevel.WARNING,
            )
            self.assertEqual(record.role, "ai-engineer")
            self.assertEqual(record.occurrences, 1)
        finally:
            ledger.close()

    # 8
    def test_public_surface_explicit(self) -> None:
        import yule_engineering.agents.memory as memory_pkg

        expected = {
            "AuditSource",
            "DecisionSource",
            "LongTermMemory",
            "MemoryFilter",
            "MemoryPack",
            "MemoryShard",
            "MemorySource",
            "MistakeLedgerSource",
            "ObsidianVaultSource",
            "RelevanceSelector",
            "RequestContext",
            "SessionExtraSource",
            "ShardKind",
            "TopicIndex",
            "build_memory_pack",
        }
        actual = set(memory_pkg.__all__)
        missing = expected - actual
        self.assertEqual(missing, set(), f"missing public exports: {missing}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
