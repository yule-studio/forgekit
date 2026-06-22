"""Governance artifact ENFORCEMENT — payload persistence + consult + decision trail.

The lane already proves preconditions are enforced/replay-able (test_lane_readiness). This
proves the *artifacts themselves are durable and attributable* — the operator's must-verify
items for "회의한 척 / 승인한 척" being impossible AND "누가 무엇을 결정했는지" traceable:

- **consult is a real, typed artifact** — a consult with no consultee or no question is
  rejected (anti-fake); a real one records ``valid=True`` and is NON-gating (it never makes
  an unready lane look executable);
- **the decision content is persisted, not just its existence** — the replayed log carries
  each artifact's payload, so design-system / coding-convention / stack / tradeoffs /
  acceptance survive (you can audit *what* was decided, not only *that* it was);
- **payload is evidence, never a gate** — readiness keys on the validator ``valid`` flag, so
  a rich payload on an INVALID decision still cannot fake a ready lane;
- **the decision trail surfaces 'who decided what'** — ``decision_trail_from_log`` renders
  actor → kind → decision facts, marking validator-rejected artifacts with ``✗``;
- **backward compatible** — an old log line with no ``payload`` key replays cleanly.

Hermetic: a tmp FORGEKIT_HOME isolates the log; identities via the registry SSoT.
"""

from __future__ import annotations

import json
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
                     acceptance_criteria=("a1", "a2"), success_metrics=("m",))


def _meeting():
    parts = (D.ParticipantPosition("tech-lead", "support", "ok"),
             D.ParticipantPosition("be", "oppose", "음", concerns=("c",)))
    return D.MeetingRecord(meeting_id="m1", topic="t", agenda=("a",),
                           participants=parts, decisions=("go",))


def _stack():
    return D.StackComparison(
        decision_topic="t", recommended="SSE",
        options=(D.StackOption("SSE", pros=("간단",), cons=("단방향",)),
                 D.StackOption("WS", pros=("양방향",), cons=("복잡",))),
        rationale="r", tradeoffs=("양방향 포기",))


def _decision():
    return D.tech_lead_decide(_brief(), _meeting(), _stack(),
                              design_system="forgekit-ds", coding_convention="ruff+black",
                              rationale="알림은 단방향이라 SSE")


# --- consult artifact (anti-fake, non-gating) --------------------------------


class ConsultArtifactTests(unittest.TestCase):
    def test_real_consult_passes(self):
        note = D.ConsultNote(consult_id="c1", topic="스택", by_role="tech-lead",
                             to_roles=("be", "fe"), question="SSE vs WS?")
        self.assertEqual(D.validate_consult(note), ())

    def test_consult_without_consultee_or_question_is_fake(self):
        no_to = D.ConsultNote(consult_id="c", topic="t", by_role="tech-lead",
                              to_roles=(), question="q")
        no_q = D.ConsultNote(consult_id="c", topic="t", by_role="tech-lead",
                             to_roles=("be",), question="")
        self.assertTrue(D.validate_consult(no_to))
        self.assertTrue(D.validate_consult(no_q))

    def test_consult_unknown_role_rejected(self):
        note = D.ConsultNote(consult_id="c", topic="t", by_role="not-a-role",
                             to_roles=("be",), question="q")
        self.assertTrue(D.validate_consult(note))


# --- payload persistence + non-gating consult --------------------------------


class PayloadPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp()
        self.env = {"FORGEKIT_HOME": self._home}

    def _record_full(self, session):
        brief, meeting, stack = _brief(), _meeting(), _stack()
        decision = _decision()
        handoff = D.handoff_to_engineer(decision, "be", scope=("notify.py",),
                                        test_strategy="unit",
                                        acceptance_criteria=brief.acceptance_criteria)
        gateway = D.gateway_review(brief, meeting)
        consult = D.ConsultNote(consult_id="c1", topic="스택", by_role="tech-lead",
                                to_roles=("be",), question="SSE vs WS?")
        return D.record_lane_artifacts(
            session, brief=brief, consult=consult, gateway=gateway, meeting=meeting,
            decision=decision, handoff=handoff, env=self.env)

    def test_decision_content_survives_replay(self):
        self._record_full("s-decision")
        events = D.replay_governance_log("s-decision", env=self.env)
        dec = next(e for e in events if e.kind == D.KIND_DECISION)
        # what was decided is durable — not just that a decision happened
        self.assertEqual(dec.payload.get("design_system"), "forgekit-ds")
        self.assertEqual(dec.payload.get("coding_convention"), "ruff+black")
        self.assertEqual(dec.payload.get("stack_decision", {}).get("recommended"), "SSE")
        self.assertTrue(dec.payload.get("tradeoffs"))

    def test_brief_and_handoff_payload_survive(self):
        self._record_full("s-payload")
        events = D.replay_governance_log("s-payload", env=self.env)
        brief = next(e for e in events if e.kind == D.KIND_BRIEF)
        handoff = next(e for e in events if e.kind == D.KIND_HANDOFF)
        self.assertEqual(brief.payload.get("acceptance_criteria"), ["a1", "a2"])
        self.assertEqual(handoff.payload.get("scope"), ["notify.py"])

    def test_consult_recorded_and_non_gating(self):
        # a fake consult is recorded valid=False but the lane is STILL executable —
        # consult never gates, and a real consult is valid=True.
        brief, meeting, decision = _brief(), _meeting(), _decision()
        handoff = D.handoff_to_engineer(decision, "be", scope=("x",), test_strategy="t",
                                        acceptance_criteria=brief.acceptance_criteria)
        fake = D.ConsultNote(consult_id="bad", topic="t", by_role="tech-lead",
                             to_roles=(), question="")
        recs = D.record_lane_artifacts("s-consult", brief=brief, consult=fake,
                                       meeting=meeting, decision=decision, handoff=handoff,
                                       env=self.env)
        consult_ev = next(r for r in recs if r.kind == D.KIND_CONSULT)
        self.assertFalse(consult_ev.valid)
        events = D.replay_governance_log("s-consult", env=self.env)
        self.assertTrue(D.readiness_from_log(events).executable)

    def test_multiple_consults_each_recorded(self):
        c1 = D.ConsultNote(consult_id="c1", topic="t", by_role="tech-lead",
                           to_roles=("be",), question="q1")
        c2 = D.ConsultNote(consult_id="c2", topic="t", by_role="tech-lead",
                           to_roles=("fe",), question="q2")
        recs = D.record_lane_artifacts("s-multi", consult=(c1, c2), env=self.env)
        consults = [r for r in recs if r.kind == D.KIND_CONSULT]
        self.assertEqual(len(consults), 2)
        self.assertTrue(all(r.valid for r in consults))


# --- payload is evidence, never a gate ---------------------------------------


class PayloadIsEvidenceNotGateTests(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp()
        self.env = {"FORGEKIT_HOME": self._home}

    def test_rich_payload_on_invalid_decision_does_not_fake_ready(self):
        # an UNSIGNED (escalated) decision carries a full payload, yet readiness must
        # refuse to call the lane ready — readiness keys on `valid`, not on the payload.
        brief, meeting = _brief(), _meeting()
        bad = D.tech_lead_decide(brief, meeting, _stack(), design_system="ds",
                                 coding_convention="cc", rationale="",  # empty → not signed
                                 risk_class="blocked")
        D.record_lane_artifacts("s-evid", brief=brief, meeting=meeting, decision=bad,
                                env=self.env)
        events = D.replay_governance_log("s-evid", env=self.env)
        dec = next(e for e in events if e.kind == D.KIND_DECISION)
        self.assertTrue(dec.payload)              # payload preserved as evidence
        self.assertFalse(dec.valid)               # but validator rejected it
        self.assertFalse(D.readiness_from_log(events).executable)


# --- decision trail surface --------------------------------------------------


class DecisionTrailTests(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp()
        self.env = {"FORGEKIT_HOME": self._home}

    def test_trail_surfaces_design_decision_facts(self):
        brief, meeting, decision = _brief(), _meeting(), _decision()
        handoff = D.handoff_to_engineer(decision, "be", scope=("x",), test_strategy="t",
                                        acceptance_criteria=brief.acceptance_criteria)
        D.record_lane_artifacts("s-trail", brief=brief, meeting=meeting, decision=decision,
                                handoff=handoff, env=self.env)
        events = D.replay_governance_log("s-trail", env=self.env)
        trail = "\n".join(D.decision_trail_from_log(events))
        self.assertIn("design=forgekit-ds", trail)
        self.assertIn("stack=SSE", trail)
        self.assertIn("approval=", trail)
        self.assertIn("executor=be", trail)

    def test_trail_marks_invalid_artifacts(self):
        fake = D.ConsultNote(consult_id="bad", topic="t", by_role="tech-lead",
                             to_roles=(), question="")
        D.record_lane_artifacts("s-mark", consult=fake, env=self.env)
        events = D.replay_governance_log("s-mark", env=self.env)
        trail = D.decision_trail_from_log(events)
        self.assertTrue(any("✗" in ln and "consult" in ln for ln in trail))

    def test_empty_log_empty_trail(self):
        self.assertEqual(D.decision_trail_from_log(()), ())


# --- backward compatibility --------------------------------------------------


class BackwardCompatTests(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp()
        self.env = {"FORGEKIT_HOME": self._home}

    def test_old_log_line_without_payload_replays(self):
        # simulate a pre-payload log line (no 'payload' key) written to disk.
        path = D.governance_log_path("s-old", env=self.env)
        path.parent.mkdir(parents=True, exist_ok=True)
        legacy = {"session_id": "s-old", "kind": D.KIND_BRIEF, "actor": "product-manager",
                  "summary": "PM brief: 알림", "valid": True, "ref": "알림"}
        path.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
        events = D.replay_governance_log("s-old", env=self.env)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload, {})              # tolerant default
        # trail still renders (no payload facts, but no crash)
        self.assertTrue(D.decision_trail_from_log(events))


if __name__ == "__main__":
    unittest.main()
