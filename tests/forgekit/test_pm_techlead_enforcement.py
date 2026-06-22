"""PM / Tech-Lead lane → runtime execution enforcement regression.

Proves the governance teeth bind at execution time:
- a SAFE action with the full chain (gateway + tech-lead signoff + handoff) is
  authorized and its commit trailers validate;
- a DESTRUCTIVE action (deploy/secret) is NEVER auto-authorized, even with a signoff;
- a RISKY action needs a real operator approval (matching the decision) — absent or
  mismatched → blocked;
- **scope creep**: an action whose class exceeds the signed level is blocked
  (re-signoff), so a "safe" signoff can't smuggle a deploy;
- the chain is mandatory: no gateway routing / no signoff / non-engineer executor →
  blocked, and ``assert_executable`` raises;
- commits must carry the REAL approval metadata — a missing/forged trailer is rejected
  (no fake approval on the work path);
- ``bridge_to_autopilot`` only yields ``can_execute`` for a SAFE/L2 signoff.

Hermetic + pure: identities resolved through the registry SSoT; no env credentials
needed (GitHub-App trailer reflects honest ``missing`` status under an empty env).
"""

from __future__ import annotations

import sys
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

from forgekit_runtime import decision_lane as L


def _lane(risk_class: str = "safe", executor: str = "be") -> L.LaneResult:
    brief = L.PMBrief(topic="알림 채널", problem="늦은 인지", user_value="MTTA 단축",
                      acceptance_criteria=("실패당 1알림",), success_metrics=("MTTA 50%↓",))
    meeting = L.MeetingRecord(
        meeting_id="m-enf", topic="스택", agenda=("비교",),
        participants=(L.ParticipantPosition("tech-lead", "support", "웹훅"),
                      L.ParticipantPosition("be", "conditional", "rate-limit", concerns=("스팸",))),
        decisions=("채택",))
    stack = L.StackComparison(
        decision_topic="전달", recommended="a",
        options=(L.StackOption("a", pros=("단순",), cons=("재시도",)),
                 L.StackOption("b", pros=("관리형",), cons=("비용",))),
        rationale="단순성", tradeoffs=("재시도 직접",))
    return L.run_lane(brief, meeting, stack, design_system="tokens v2",
                      coding_convention="ruff+black", executor_role=executor,
                      scope=("docs/x.md",), test_strategy="unit", risk_class=risk_class)


class ClassifyTests(unittest.TestCase):
    def test_classes(self) -> None:
        self.assertEqual(L.classify_action(L.ActionRequest(kind="docs"))[0], L.SAFE)
        self.assertEqual(L.classify_action(L.ActionRequest(kind="deploy"))[0], L.DESTRUCTIVE)
        self.assertEqual(L.classify_action(L.ActionRequest(kind="secret"))[0], L.DESTRUCTIVE)
        # unknown kind is NOT auto-safe
        self.assertEqual(L.classify_action(L.ActionRequest(kind="mystery"))[0], L.RISKY)
        self.assertEqual(L.classify_action(L.ActionRequest(summary="auth rewrite", risk_flag="risky"))[0], L.RISKY)


class AuthorizeSafeTests(unittest.TestCase):
    def test_full_chain_authorizes_safe(self) -> None:
        r = _lane("safe")
        v = L.authorize_execution(r.decision, r.handoff, L.ActionRequest(kind="docs"),
                                  routing=r.routing)
        self.assertTrue(v.allowed)
        self.assertEqual(v.action_class, L.SAFE)
        self.assertIn("gateway", v.satisfied)
        self.assertIn("tech-lead", v.satisfied)
        self.assertIn("internal-safe", v.satisfied)
        self.assertIn(f"decision={r.decision.decision_id}", v.approval_metadata)

    def test_safe_commit_trailers_validate(self) -> None:
        r = _lane("safe")
        v = L.authorize_execution(r.decision, r.handoff, L.ActionRequest(kind="docs"),
                                  routing=r.routing, )
        trailers = L.execution_commit_trailers(v, env={})
        msg = "📝 docs 보강\n\n변경 이유\n- x\n\n주요 변경 사항\n- y\n\n비고\n- 없음\n\n" + "\n".join(trailers)
        self.assertEqual(L.validate_execution_trailers(msg, v), ())
        self.assertIn(f"Forgekit-Agent: {v.executor_id}", msg)
        self.assertIn(f"Forgekit-Approval: {v.approval_metadata}", msg)


class BlockTests(unittest.TestCase):
    def test_destructive_never_auto(self) -> None:
        r = _lane("safe")
        v = L.authorize_execution(r.decision, r.handoff, L.ActionRequest(kind="deploy"),
                                  routing=r.routing)
        self.assertFalse(v.allowed)
        self.assertEqual(v.action_class, L.DESTRUCTIVE)

    def test_risky_needs_operator(self) -> None:
        r = _lane("risky")  # signed at L3
        req = L.ActionRequest(kind="gap", summary="auth 권한 변경", risk_flag="risky")
        # without operator → blocked
        v0 = L.authorize_execution(r.decision, r.handoff, req, routing=r.routing)
        self.assertFalse(v0.allowed)
        # mismatched operator ref → blocked
        bad = L.OperatorApproval(approver="operator", decision_ref="WRONG", approved=True)
        v1 = L.authorize_execution(r.decision, r.handoff, req, routing=r.routing, operator_approval=bad)
        self.assertFalse(v1.allowed)
        # real operator approval → allowed
        ok = L.OperatorApproval(approver="operator", decision_ref=r.decision.decision_id, approved=True)
        v2 = L.authorize_execution(r.decision, r.handoff, req, routing=r.routing, operator_approval=ok)
        self.assertTrue(v2.allowed)
        self.assertIn("operator", v2.satisfied)
        self.assertIn("operator=operator", v2.approval_metadata)

    def test_scope_creep_blocked(self) -> None:
        # signed safe, but the actual action is risky → exceeds signed level
        r = _lane("safe")
        ok = L.OperatorApproval(approver="operator", decision_ref=r.decision.decision_id, approved=True)
        v = L.authorize_execution(r.decision, r.handoff,
                                  L.ActionRequest(kind="gap", summary="auth rewrite", risk_flag="risky"),
                                  routing=r.routing, operator_approval=ok)
        self.assertFalse(v.allowed)
        self.assertTrue(any("서명 범위" in x for x in v.blocking_reasons))

    def test_no_gateway_blocked(self) -> None:
        r = _lane("safe")
        v = L.authorize_execution(r.decision, r.handoff, L.ActionRequest(kind="docs"), routing=None)
        self.assertFalse(v.allowed)
        self.assertTrue(any("gateway" in x for x in v.blocking_reasons))

    def test_no_signoff_blocked(self) -> None:
        r = _lane("blocked")  # BLOCKED decision, no handoff
        v = L.authorize_execution(r.decision, None, L.ActionRequest(kind="docs"), routing=r.routing)
        self.assertFalse(v.allowed)

    def test_non_engineer_executor_blocked(self) -> None:
        r = _lane("safe", executor="tech-lead")  # decider can't be the executor
        self.assertFalse(r.engineer_may_start)
        v = L.authorize_execution(r.decision, r.handoff, L.ActionRequest(kind="docs"), routing=r.routing)
        self.assertFalse(v.allowed)


class AssertAndBridgeTests(unittest.TestCase):
    def test_assert_executable_raises_on_block(self) -> None:
        r = _lane("safe")
        with self.assertRaises(L.ExecutionBlocked):
            L.assert_executable(r.decision, r.handoff, L.ActionRequest(kind="deploy"), routing=r.routing)

    def test_assert_executable_returns_verdict_on_allow(self) -> None:
        r = _lane("safe")
        v = L.assert_executable(r.decision, r.handoff, L.ActionRequest(kind="docs"), routing=r.routing)
        self.assertTrue(v.allowed)

    def test_blocked_verdict_emits_no_trailers(self) -> None:
        r = _lane("safe")
        v = L.authorize_execution(r.decision, r.handoff, L.ActionRequest(kind="deploy"), routing=r.routing)
        self.assertEqual(L.execution_commit_trailers(v, env={}), ())
        # and a commit claiming approval is rejected for a blocked verdict
        self.assertTrue(L.validate_execution_trailers("📝 x\n\nForgekit-Approval: fake", v))

    def test_missing_trailer_rejected(self) -> None:
        r = _lane("safe")
        v = L.authorize_execution(r.decision, r.handoff, L.ActionRequest(kind="docs"), routing=r.routing)
        # a commit without the approval metadata is rejected (no fake/absent approval)
        self.assertTrue(L.validate_execution_trailers("📝 docs\n\n변경 이유\n- x", v))

    def test_bridge_can_execute_only_for_safe(self) -> None:
        safe = L.bridge_to_autopilot(_lane("safe").decision)
        risky = L.bridge_to_autopilot(_lane("risky").decision)
        self.assertTrue(safe.can_execute)
        self.assertFalse(risky.can_execute)


if __name__ == "__main__":
    unittest.main()
