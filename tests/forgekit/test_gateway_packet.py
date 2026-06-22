"""Gateway intake packet (approve / reject / request-more-info) + tech-lead request-info.

Closes the org-flow gap: the gateway must be able to hand off an explicit verdict packet,
and the tech-lead must be able to ask for more info (not only approve/reject). Proves:

- ``gateway_review`` yields APPROVE (real brief+meeting), REQUEST_INFO (fixable gaps, with
  the concrete missing items), or REJECT (policy_block, with a reason);
- ``validate_gateway_packet`` is anti-fake — an approve carrying an info request or reject
  reason is rejected, a request-more-info with no items is rejected, a reject with no reason
  is rejected;
- ``tech_lead_request_more_info`` produces a NEEDS_INFO decision that is NOT executable
  (``can_engineer_start`` stays False — a specialist cannot start off a request-more-info);
- the gateway verdict is recorded in the replay-able decision log (approve → valid, a
  reject/request-info → valid=False, i.e. not an advancing verdict).

Hermetic + pure: a tmp FORGEKIT_HOME isolates the log.
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _rel in (
    "packages/forgekit-runtime/src",
    "packages/forgekit-config/src",
    "packages/forgekit-provider/src",
    "packages/forgekit-contracts/src",
    "packages/forgekit-goal/src",
    "packages/nexus/src",
):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_runtime import decision_lane as D


def _brief():
    return D.PMBrief(topic="알림", problem="p", user_value="v",
                     acceptance_criteria=("a",), success_metrics=("m",))


def _meeting():
    return D.MeetingRecord(meeting_id="m1", topic="t", agenda=("a",),
                           participants=(D.ParticipantPosition("tech-lead", "support", "ok"),
                                         D.ParticipantPosition("be", "oppose", "반대", concerns=("c",))),
                           decisions=("go",))


class GatewayVerdictTests(unittest.TestCase):
    def test_approve_forwards(self) -> None:
        g = D.gateway_review(_brief(), _meeting())
        self.assertEqual(g.verdict, D.GATEWAY_APPROVE)
        self.assertTrue(g.forwarded)
        self.assertEqual(D.validate_gateway_packet(g), ())

    def test_request_info_lists_gaps(self) -> None:
        g = D.gateway_review(D.PMBrief(topic="t", problem="p", user_value="v"), None)
        self.assertEqual(g.verdict, D.GATEWAY_REQUEST_INFO)
        self.assertFalse(g.forwarded)
        self.assertTrue(g.info_requested)                    # concrete missing items
        self.assertEqual(D.validate_gateway_packet(g), ())

    def test_reject_carries_reason(self) -> None:
        g = D.gateway_review(_brief(), _meeting(), policy_block="범위 밖 — 별도 분기")
        self.assertEqual(g.verdict, D.GATEWAY_REJECT)
        self.assertFalse(g.forwarded)
        self.assertTrue(g.reject_reason)
        self.assertEqual(D.validate_gateway_packet(g), ())


class GatewayAntiFakeTests(unittest.TestCase):
    def test_approve_with_info_rejected(self) -> None:
        g = dataclasses.replace(D.gateway_review(_brief(), _meeting()), info_requested=("x",))
        self.assertTrue(D.validate_gateway_packet(g))

    def test_request_info_without_items_rejected(self) -> None:
        g = D.GatewayPacket(topic="t", verdict=D.GATEWAY_REQUEST_INFO)
        self.assertTrue(D.validate_gateway_packet(g))

    def test_reject_without_reason_rejected(self) -> None:
        g = D.GatewayPacket(topic="t", verdict=D.GATEWAY_REJECT)
        self.assertTrue(any("사유" in x for x in D.validate_gateway_packet(g)))

    def test_unknown_verdict_rejected(self) -> None:
        self.assertTrue(D.validate_gateway_packet(D.GatewayPacket(topic="t", verdict="bogus")))


class TechLeadRequestInfoTests(unittest.TestCase):
    def test_needs_info_not_executable(self) -> None:
        d = D.tech_lead_request_more_info(_brief(), _meeting(),
                                          info_requested=("stack 비교", "디자인시스템"))
        self.assertEqual(d.status, D.NEEDS_INFO)
        self.assertEqual(d.conditions, ("stack 비교", "디자인시스템"))
        self.assertFalse(D.can_engineer_start(d, None))

    def test_needs_info_blocks_readiness(self) -> None:
        d = D.tech_lead_request_more_info(_brief(), _meeting(), info_requested=("x",))
        r = D.assess_lane_readiness(brief=_brief(), meeting=_meeting(), decision=d)
        self.assertFalse(r.executable)
        self.assertEqual(r.stage, D.STAGE_DECISION_PENDING)


class GatewayLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = {"FORGEKIT_HOME": tempfile.mkdtemp()}

    def test_approve_recorded_valid(self) -> None:
        g = D.gateway_review(_brief(), _meeting())
        D.record_lane_artifacts("s1", brief=_brief(), gateway=g, meeting=_meeting(), env=self.env)
        evs = D.replay_governance_log("s1", env=self.env)
        gw = [e for e in evs if e.kind == D.KIND_GATEWAY][0]
        self.assertTrue(gw.valid)
        self.assertIn("approve", gw.summary)

    def test_reject_recorded_not_valid(self) -> None:
        g = D.gateway_review(_brief(), _meeting(), policy_block="범위 밖")
        D.record_lane_artifacts("s2", brief=_brief(), gateway=g, env=self.env)
        gw = [e for e in D.replay_governance_log("s2", env=self.env) if e.kind == D.KIND_GATEWAY][0]
        self.assertFalse(gw.valid)                           # reject is real but not advancing


if __name__ == "__main__":
    unittest.main()
