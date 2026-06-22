"""Specialist work order ENFORCEMENT — the materialized handoff packet.

The lane proves the chain is real/replay-able; this proves the specialist receives a
**full design briefing**, not a thin 'go build it'. The goal: reduce 'design 없이 바로 구현'
by refusing to start a specialist off an order that lacks the design context.

- **the briefing carries the design context** — goal, proposed stack + WHY, the REJECTED
  options (derived from the stack comparison), coding conventions, design system, API/infra
  notes, scope, test strategy, acceptance;
- **a thin order is not startable** — ``can_specialist_start`` is stronger than
  ``can_engineer_start``: a valid signed decision + handoff whose materialized briefing is
  missing design context cannot start (anti 'just build it');
- **rejected options are real** — every non-recommended stack option appears with its cons
  as 'why not', so the loser isn't silently dropped;
- **the work order is persisted + replay-able** — ``record_lane_artifacts(briefing=...)``
  enriches the handoff event payload so ``/handoff`` can replay the full packet;
- **integration_notes (API/infra) flow through** end to end.

Hermetic: a tmp FORGEKIT_HOME isolates the log; identities via the registry SSoT.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _rel in (
    "packages/forgekit-runtime/src",
    "packages/forgekit-config/src",
    "packages/forgekit-provider/src",
    "packages/forgekit-contracts/src",
    "packages/forgekit-goal/src",
    "packages/nexus/src",
    "apps/forgekit-console/src",
):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_runtime import decision_lane as D


def _brief():
    return D.PMBrief(topic="실시간 알림", problem="운영자가 실패를 늦게 안다",
                     user_value="실패 즉시 통지", acceptance_criteria=("30초내 통지",),
                     success_metrics=("MTTA<1m",))


def _meeting():
    parts = (D.ParticipantPosition("tech-lead", "support", "SSE 가자"),
             D.ParticipantPosition("be", "conditional", "백프레셔 조건", concerns=("재연결",)))
    return D.MeetingRecord(meeting_id="m1", topic="전송", agenda=("전송",),
                           participants=parts, decisions=("SSE 채택",))


def _stack():
    return D.StackComparison(
        decision_topic="전송", recommended="SSE",
        options=(D.StackOption("SSE", summary="server-sent events", pros=("단순",), cons=("단방향",)),
                 D.StackOption("WebSocket", pros=("양방향",), cons=("인프라 복잡", "비용"))),
        rationale="단방향 알림은 SSE 로 충분", tradeoffs=("양방향 포기",))


def _run(**kw):
    return D.run_lane(_brief(), _meeting(), _stack(), design_system="forgekit-ds",
                      coding_convention="ruff+black", executor_role="be",
                      scope=("notify/sse.py",), test_strategy="unit+integration", **kw)


# --- briefing composition ----------------------------------------------------


class BriefingCompositionTests(unittest.TestCase):
    def test_briefing_carries_full_design_context(self):
        res = _run(integration_notes=("GET /events SSE", "Redis pub/sub"))
        b = res.briefing
        self.assertEqual(D.validate_specialist_briefing(b), ())
        self.assertEqual(b.proposed_stack, "SSE")
        self.assertIn("→", b.goal)                            # problem → user_value
        self.assertEqual(b.coding_conventions, "ruff+black")
        self.assertEqual(b.design_system, "forgekit-ds")
        self.assertEqual(b.integration_notes, ("GET /events SSE", "Redis pub/sub"))
        self.assertIn("30초내 통지", b.acceptance_criteria)

    def test_rejected_options_derived_with_reasons(self):
        b = _run().briefing
        names = {r.name for r in b.rejected_options}
        self.assertEqual(names, {"WebSocket"})                # everything not recommended
        ws = next(r for r in b.rejected_options if r.name == "WebSocket")
        self.assertIn("인프라 복잡", ws.why_not)               # cons carried as 'why not'

    def test_work_order_lines_render(self):
        joined = "\n".join(_run(integration_notes=("GET /events",)).briefing.lines())
        for token in ("목표:", "제안 스택: SSE", "선택 이유:", "✗ 탈락: WebSocket",
                      "코딩 컨벤션:", "디자인 시스템:", "API/infra: GET /events",
                      "✓ acceptance:"):
            self.assertIn(token, joined)


# --- anti-thin-order gate ----------------------------------------------------


class SpecialistGateTests(unittest.TestCase):
    def test_full_lane_specialist_may_start(self):
        res = _run()
        self.assertTrue(res.engineer_may_start)
        self.assertTrue(D.can_specialist_start(_brief(), res.decision, res.handoff))

    def test_thin_briefing_blocks_start(self):
        # a signed decision + valid handoff, but strip the design context off the briefing →
        # can_engineer_start is True, can_specialist_start is False (the stronger gate).
        res = _run()
        self.assertTrue(D.can_engineer_start(res.decision, res.handoff))
        thin = replace(res.briefing, proposed_stack="", stack_rationale="",
                       rejected_options=(), coding_conventions="", design_system="")
        self.assertTrue(D.validate_specialist_briefing(thin))   # rejected as thin

    def test_briefing_validator_flags_each_missing_piece(self):
        res = _run()
        for field in ("goal", "proposed_stack", "stack_rationale", "coding_conventions",
                      "design_system", "test_strategy"):
            bad = replace(res.briefing, **{field: ""})
            self.assertTrue(D.validate_specialist_briefing(bad), f"{field} 누락이 통과됨")
        self.assertTrue(D.validate_specialist_briefing(replace(res.briefing, rejected_options=())))
        self.assertTrue(D.validate_specialist_briefing(replace(res.briefing, scope=())))
        self.assertTrue(D.validate_specialist_briefing(replace(res.briefing, acceptance_criteria=())))


# --- persistence + replay ----------------------------------------------------


class BriefingPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp()
        self.env = {"FORGEKIT_HOME": self._home}

    def test_briefing_enriches_handoff_payload(self):
        res = _run(integration_notes=("GET /events",))
        D.record_lane_artifacts("s1", brief=_brief(), meeting=_meeting(),
                                decision=res.decision, handoff=res.handoff,
                                briefing=res.briefing, env=self.env)
        events = D.replay_governance_log("s1", env=self.env)
        ho = next(e for e in events if e.kind == D.KIND_HANDOFF)
        self.assertTrue(ho.valid)
        self.assertEqual(ho.payload.get("proposed_stack"), "SSE")
        self.assertTrue(ho.payload.get("rejected_options"))
        self.assertEqual(ho.payload.get("integration_notes"), ["GET /events"])

    def test_thin_briefing_recorded_invalid(self):
        res = _run()
        thin = replace(res.briefing, proposed_stack="", rejected_options=())
        recs = D.record_lane_artifacts("s2", decision=res.decision, handoff=res.handoff,
                                       briefing=thin, env=self.env)
        ho = next(r for r in recs if r.kind == D.KIND_HANDOFF)
        self.assertFalse(ho.valid)                            # thin order logged invalid

    def test_trail_surfaces_stack_and_rejected(self):
        res = _run()
        D.record_lane_artifacts("s3", brief=_brief(), meeting=_meeting(),
                                decision=res.decision, handoff=res.handoff,
                                briefing=res.briefing, env=self.env)
        events = D.replay_governance_log("s3", env=self.env)
        trail = "\n".join(D.decision_trail_from_log(events))
        self.assertIn("stack=SSE", trail)
        self.assertIn("탈락", trail)


# --- console /handoff surface ------------------------------------------------


class HandoffSurfaceTests(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp()

    def _route(self, line):
        import os
        os.environ["FORGEKIT_HOME"] = self._home
        from forgekit_console.commands.parser import parse_input
        from forgekit_console.commands.router import build_default_context, route
        return route(parse_input(line), build_default_context(Path(".")))

    def test_handoff_renders_work_order(self):
        res = _run(integration_notes=("GET /events",))
        D.record_lane_artifacts("sh", brief=_brief(), meeting=_meeting(),
                                decision=res.decision, handoff=res.handoff,
                                briefing=res.briefing, env={"FORGEKIT_HOME": self._home})
        joined = "\n".join(self._route("/handoff sh").lines)
        self.assertIn("제안 스택: SSE", joined)
        self.assertIn("✗ 탈락: WebSocket", joined)
        self.assertIn("API/infra: GET /events", joined)

    def test_handoff_usage_without_session(self):
        self.assertIn("work order", "\n".join(self._route("/handoff").lines))

    def test_handoff_no_record_is_honest(self):
        joined = "\n".join(self._route("/handoff nope").lines)
        self.assertIn("work order 없음", joined)


if __name__ == "__main__":
    unittest.main()
