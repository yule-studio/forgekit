"""G2 — forge governance ledger PERSIST + VIEW operator path (through the REAL router).

`/resolve apply <요청>` persists a forge receipt to the append-only ledger;
`/resolve <요청>` (preview) does NOT; `/resolve ledger` renders the durable log. Honest:
a risky/blocked plan never persists a fake success. The ledger lives under a tmp
``FORGEKIT_HOME`` so the suite never touches the operator's real state dir.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.router import ConsoleContext, route
from forgekit_runtime.forge import forge_receipt_ledger_path, read_forge_receipts

# representative requests: a plain ask resolves safe→executed; a deploy/secret/schema ask
# is classified destructive→blocked (verified once in the bridge sanity check).
_SAFE_REQUEST = "Spring Boot JWT refresh token 구현"
_BLOCKED_REQUEST = "프로덕션 DB 스키마 마이그레이션 배포 및 secret 교체"


class ForgeLedgerPersistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        self.env = {"FORGEKIT_HOME": str(self.home)}
        # repo_root is only used by status loaders we don't exercise here.
        self.ctx = ConsoleContext(repo_root=self.home, env=self.env)

    def _run(self, raw: str):
        return route(parse_input(raw), self.ctx)

    def _ledger(self):
        return read_forge_receipts(env=self.env)

    # ── apply → persist ────────────────────────────────────────────────────
    def test_apply_persists_receipt_to_ledger(self) -> None:
        self.assertEqual(self._ledger(), [], "ledger should start empty")
        result = self._run(f"/resolve apply {_SAFE_REQUEST}")
        self.assertEqual(result.kind, "info", f"apply should confirm, got {result}")
        # confirm line names the ledger path
        joined = "\n".join(result.lines)
        self.assertIn("ledger", joined)
        self.assertIn("영속", joined)
        # read back: exactly one authorized/executed receipt
        entries = self._ledger()
        self.assertEqual(len(entries), 1, entries)
        receipt = entries[0]["receipt"]
        self.assertEqual(receipt["request"], _SAFE_REQUEST)
        self.assertTrue(receipt["authorized"])
        self.assertEqual(receipt["outcome"], "executed")
        self.assertTrue(forge_receipt_ledger_path(env=self.env).exists())

    # ── preview → NO persist (regression) ──────────────────────────────────
    def test_preview_does_not_persist(self) -> None:
        result = self._run(f"/resolve {_SAFE_REQUEST}")
        self.assertEqual(result.kind, "info")
        # the preview still shows the governance verdict inline
        self.assertIn("governance", "\n".join(result.lines))
        # but NOTHING was written to the durable ledger
        self.assertEqual(self._ledger(), [], "preview must not persist")
        self.assertFalse(forge_receipt_ledger_path(env=self.env).exists())

    # ── ledger view ────────────────────────────────────────────────────────
    def test_ledger_view_empty_is_honest(self) -> None:
        result = self._run("/resolve ledger")
        self.assertEqual(result.kind, "info")
        joined = "\n".join(result.lines)
        self.assertIn("기록된 receipt 없음", joined)

    def test_ledger_view_renders_appended_receipts(self) -> None:
        self._run(f"/resolve apply {_SAFE_REQUEST}")
        result = self._run("/resolve ledger")
        self.assertEqual(result.kind, "info")
        joined = "\n".join(result.lines)
        self.assertIn("최근 1건", joined)
        self.assertIn(_SAFE_REQUEST[:20], joined)
        self.assertIn("인가", joined)

    # ── honest: blocked plan never persists a fake success ─────────────────
    def test_blocked_plan_does_not_persist(self) -> None:
        result = self._run(f"/resolve apply {_BLOCKED_REQUEST}")
        self.assertEqual(result.kind, "error", f"blocked plan must error, got {result}")
        joined = "\n".join(result.lines)
        self.assertIn("미인가", joined)
        # nothing entered the durable log
        self.assertEqual(self._ledger(), [], "blocked plan must not persist")

    # ── apply with no request is honest, no persist ────────────────────────
    def test_apply_without_request_is_honest(self) -> None:
        result = self._run("/resolve apply")
        joined = "\n".join(result.lines)
        self.assertIn("요청을 입력", joined)
        self.assertEqual(self._ledger(), [])


if __name__ == "__main__":
    unittest.main()
