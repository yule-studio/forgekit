"""PM intake → gateway → tech-lead handoff (WT2) — incl. the BKURS scenario.

Proves the path actually closes: a vague ask becomes a real ProductIntentPacket
(implied features found), the gateway forwards it, tech-lead splits it into per-role
tasks, and no-permission areas (infra/deploy) are surfaced as BLOCKED with a runbook
hint — not faked as done. Pure (the product-intake engine is importable in CI).
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import handoff as h


class IntakeTests(unittest.TestCase):
    def test_media_upload_ask_finds_implied_features(self) -> None:
        packet = h.intake_packet("영상 업로드 기능 만들어줘")
        # the engine should detect implied features the ask never mentioned
        implied = [getattr(g, "name", "") for g in getattr(packet, "implied_features", ())]
        self.assertTrue(implied, "implied features 보강이 비어 있음")
        # processing/failure/thumbnail are the media_upload family's implied set
        joined = " ".join(implied)
        self.assertTrue(any(k in joined for k in ("processing", "failure", "thumbnail")))


class TechLeadSplitTests(unittest.TestCase):
    def test_upload_splits_across_roles_with_blocked_infra(self) -> None:
        ho = h.run_handoff("영상 업로드 기능을 운영까지 완성해줘")
        roles = ho.split.roles()
        for r in ("be", "fe", "qa", "tech-lead"):
            self.assertIn(r, roles, f"{r} task 누락")
        # an infra/storage area must be BLOCKED (no execution permission), with a runbook
        self.assertTrue(ho.has_blocked)
        blocked = ho.split.blocked
        self.assertTrue(all(t.needs_approval for t in blocked))
        self.assertTrue(all(t.runbook_hint for t in blocked))

    def test_trace_records_authorship_and_phases(self) -> None:
        ho = h.run_handoff("관리자 CRUD 페이지 추가")
        phases = [t.phase for t in ho.trace]
        self.assertEqual(phases, [h.PHASE_INTAKE, h.PHASE_GATEWAY, h.PHASE_TECH_LEAD])
        # the trace names who handed off to whom
        self.assertEqual(ho.trace[0].author, "product-agent")
        self.assertEqual(ho.trace[0].handoff_to, "gateway")
        self.assertEqual(ho.trace[-1].handoff_to, "engineers")


class BkursScenarioTests(unittest.TestCase):
    """The required end-to-end scenario is reproducible + serialisable to evidence."""

    def test_bkurs_request_produces_structured_handoff(self) -> None:
        ask = "bkurs-fe와 bkurs-be를 완성해줘. 디자인, 간격, 운영도 부족한 것 같아."
        ho = h.run_handoff(ask, project="bkurs")
        d = ho.to_dict()
        # structured, not a passthrough: a packet + a multi-role split + trace
        self.assertEqual(d["project"], "bkurs")
        self.assertIn("packet", d)
        self.assertGreaterEqual(len(d["split"]["tasks"]), 2)
        self.assertEqual(len(d["trace"]), 3)
        # "운영" signal → a blocked deploy/infra area with operator approval needed
        self.assertTrue(d["has_blocked"])
        self.assertTrue(any(t["needs_approval"] for t in d["split"]["tasks"]))

    def test_handoff_is_json_serialisable(self) -> None:
        import json

        ho = h.run_handoff("결제 기능 추가하고 배포까지", project="bkurs")
        # round-trips through JSON (evidence on disk)
        s = json.dumps(ho.to_dict(), ensure_ascii=False)
        self.assertIn("bkurs", s)
        self.assertIn("blocked", s)


if __name__ == "__main__":
    unittest.main()
