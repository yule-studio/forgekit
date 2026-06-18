"""BKURS end-to-end (WT6) — the full operator scenario closes across WT1–WT5.

"bkurs-fe와 bkurs-be를 완성해줘. 디자인, 간격, 운영도 부족한 것 같아." drives:
  PM intake → packet → gateway → tech-lead split (WT2)
  → bounded runtime loop observes the gaps, can't deploy → runbook + wait (WT3)
  → operator notification (inbox, action-oriented) (WT4)
  → authored vault note with handoff/phase metadata (WT5)
and every step leaves consistent evidence — no fake execution. Pure → bare CI.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.handoff import run_handoff
from forgekit_console.runtime import loop as L
from forgekit_console.notify import NotificationEvent, EVENT_ACCESS_REQUIRED
from forgekit_console.notify.service import NotificationService
from forgekit_console import vault

_ASK = "bkurs-fe와 bkurs-be를 완성해줘. 디자인, 간격, 운영도 부족한 것 같아."


class BkursEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_full_chain_closes_with_consistent_evidence(self) -> None:
        # 1) PM intake → gateway → tech-lead split (WT2)
        ho = run_handoff(_ASK, project="bkurs")
        self.assertTrue(ho.has_blocked)               # 운영 → 배포/인프라 BLOCKED
        self.assertEqual(ho.trace[-1].phase, "tech-lead")

        # 2) bounded runtime loop observes the gaps → runbook + wait, NO execution (WT3)
        findings = [
            L.Finding("bkurs-fe", "디자인/간격 보강 필요", category=L.CAT_DESIGN),
            L.Finding("bkurs-be", "운영 배포 apply 권한 필요", category=L.CAT_INFRA, privileged=True),
        ]
        result = L.BoundedRuntimeLoop(autonomy=L.AUTONOMY_BOUNDED, max_iterations=10).run(findings)
        self.assertTrue(result.waiting)
        self.assertGreaterEqual(result.blocked_count, 1)   # runbook produced
        self.assertNotIn("execute", {s.phase for s in result.steps})  # no exec phase

        # 3) operator notification — action-oriented, recorded to inbox (WT4)
        svc = NotificationService(inbox_path=self.tmp / "inbox.json",
                                  dispatcher=lambda t, b: (False, "none"))
        out = svc.notify(NotificationEvent(
            EVENT_ACCESS_REQUIRED, "bkurs always-on: 승인 필요",
            why=f"권한 없는 영역 {result.blocked_count}개에서 멈춤", action="runbook 확인 후 승인",
            options=("승인", "거부"), source="always-on"))
        self.assertTrue(out.inbox_written)
        self.assertEqual(out.request_type, "ACCESS")

        # 4) authored vault note — who/role/phase metadata (WT5)
        note = vault.note_from_handoff(ho, created_at="2026-06-17")
        self.assertIn("agent_author: tech-lead", note)
        self.assertIn("phase: tech-lead", note)
        self.assertIn("BLOCKED", note)

        # cross-evidence consistency: the blocked area is named the same everywhere
        inbox = json.loads((self.tmp / "inbox.json").read_text(encoding="utf-8"))
        self.assertEqual(inbox[-1]["event_type"], "ACCESS_REQUIRED")
        self.assertTrue(inbox[-1]["needs_operator"])
        self.assertTrue(any(t.state == "blocked" for t in ho.split.tasks))

    def test_no_fake_execution_anywhere(self) -> None:
        """The privileged path NEVER reports success — only runbook + wait."""
        result = L.BoundedRuntimeLoop().run(
            [L.Finding("bkurs", "IAM 권한 부여 + 배포", category=L.CAT_INFRA, privileged=True)]
        )
        self.assertTrue(result.waiting)
        self.assertEqual(len(result.handoffs), 0)      # privileged → not packetized for exec
        self.assertGreaterEqual(result.blocked_count, 1)
        # the result dict is honest: waiting + halted bounded, no "done/executed"
        d = result.to_dict()
        self.assertTrue(d["waiting"])
        self.assertNotIn("executed", d)


if __name__ == "__main__":
    unittest.main()
