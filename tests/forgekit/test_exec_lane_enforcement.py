"""ForgeKit governance execution lane — decision-lane enforcement on the REAL autopilot
execution path (orchestrator run loop).

Proves the lane bites where work actually happens:
- ``authorize_runtime_execution`` applies safe/risky/destructive classification + the
  approval chain to the autopilot finding-chain decision (not just the design lane);
- the orchestrator, given a ``make_runtime_authorizer`` guard, executes an authorized
  SAFE finding AND binds its approval metadata onto the executed record;
- **defense-in-depth**: a finding whose TEXT looks safe but whose KIND is forbidden
  (``deploy``) is passed by the chain's text-only gate (``can_execute=True``) yet BLOCKED
  in-loop by the lane (re-classified destructive) — it never mutates;
- a non-engineering executor slot is refused;
- WITHOUT a guard the orchestrator behaves exactly as before (legacy unchanged) — no
  approval key, same executed count.

Hermetic: a real BoundedMutator writes into a tmp dir (no repo writes); identities via
the registry SSoT.
"""

from __future__ import annotations

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

from forgekit_runtime import decision_lane as L
from forgekit_runtime.autopilot.artifacts import RepoFinding
from forgekit_runtime.autopilot.chain import run_internal_chain
from forgekit_runtime.autopilot.orchestrator import PHASE_AUTHORIZE, AutopilotOrchestrator
from forgekit_runtime.autopilot.runner import BoundedMutator


def _orch(authorizer=None) -> AutopilotOrchestrator:
    return AutopilotOrchestrator(mutator=BoundedMutator(repo_root=tempfile.mkdtemp()),
                                 execution_authorizer=authorizer)


def _decision(finding: RepoFinding, risk_class: str = ""):
    _, _, decision, _ = run_internal_chain(finding, risk_class=risk_class)
    return decision


class RuntimeAuthorizeTests(unittest.TestCase):
    def test_safe_authorized_with_metadata(self) -> None:
        f = RepoFinding("forgekit", "docs 보강", kind="docs")
        v = L.authorize_runtime_execution(_decision(f), L.ActionRequest(kind="docs", summary="docs 보강"),
                                          executor_role="be")
        self.assertTrue(v.allowed)
        self.assertEqual(v.action_class, L.SAFE)
        self.assertIn("level=L2_internal_approve", v.approval_metadata)
        self.assertIn("signoff=tech-lead", v.approval_metadata)

    def test_forbidden_kind_blocked_even_if_text_safe(self) -> None:
        # chain's text-only gate would pass this; the lane re-classifies by KIND
        f = RepoFinding("forgekit", "update config values", kind="deploy")
        from forgekit_runtime.autopilot.chain import can_specialist_execute
        self.assertTrue(can_specialist_execute(_decision(f)))  # chain says safe
        v = L.authorize_runtime_execution(_decision(f), L.ActionRequest(kind="deploy", summary="update config values"),
                                          executor_role="be")
        self.assertFalse(v.allowed)
        self.assertEqual(v.action_class, L.DESTRUCTIVE)

    def test_risky_needs_operator(self) -> None:
        f = RepoFinding("forgekit", "auth 대규모 rewrite", kind="gap")
        req = L.ActionRequest(kind="gap", summary="auth 대규모 rewrite", risk_flag="risky")
        d = _decision(f, "risky")
        self.assertFalse(L.authorize_runtime_execution(d, req, executor_role="be").allowed)
        ok = L.OperatorApproval(approver="operator", decision_ref="x", approved=True)
        self.assertTrue(L.authorize_runtime_execution(d, req, executor_role="be", operator_approval=ok).allowed)

    def test_non_engineer_executor_refused(self) -> None:
        f = RepoFinding("forgekit", "docs", kind="docs")
        for role in ("gateway", "pm", "product-manager", "bogus-role"):
            v = L.authorize_runtime_execution(_decision(f), L.ActionRequest(kind="docs"), executor_role=role)
            self.assertFalse(v.allowed, role)
        # tech-lead IS allowed on the finding path (chain routes docs→tech-lead)
        self.assertTrue(L.authorize_runtime_execution(_decision(f), L.ActionRequest(kind="docs"),
                                                      executor_role="tech-lead").allowed)

    def test_no_gateway_blocked(self) -> None:
        f = RepoFinding("forgekit", "docs", kind="docs")
        v = L.authorize_runtime_execution(_decision(f), L.ActionRequest(kind="docs"),
                                          executor_role="be", gateway_ok=False)
        self.assertFalse(v.allowed)


class OrchestratorInLoopTests(unittest.TestCase):
    def test_authorized_safe_executes_and_binds_metadata(self) -> None:
        orch = _orch(L.make_runtime_authorizer())
        f = RepoFinding("forgekit", "lint 정리", kind="lint")  # → be, safe
        res = orch.run_cycle("forgekit", [f])
        self.assertIn(PHASE_AUTHORIZE, res.steps)
        self.assertEqual(len(res.executed), 1)
        self.assertIn("approval", res.executed[0])
        self.assertIn("signoff=tech-lead", res.executed[0]["approval"])

    def test_defense_in_depth_blocks_forbidden_kind_in_loop(self) -> None:
        orch = _orch(L.make_runtime_authorizer())
        f = RepoFinding("forgekit", "update config values", kind="deploy")
        res = orch.run_cycle("forgekit", [f])
        self.assertEqual(res.executed, [])
        blocked = [p for p in res.proposed if p.get("blocked_by") == "lane-enforcement"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["action_class"], L.DESTRUCTIVE)

    def test_legacy_without_authorizer_unchanged(self) -> None:
        orch = _orch(authorizer=None)
        f = RepoFinding("forgekit", "lint 정리", kind="lint")
        res = orch.run_cycle("forgekit", [f])
        self.assertNotIn(PHASE_AUTHORIZE, res.steps)
        self.assertEqual(len(res.executed), 1)
        self.assertNotIn("approval", res.executed[0])

    def test_executed_metadata_round_trips_to_commit_trailers(self) -> None:
        # the approval metadata bound in-loop is the same a commit would carry
        f = RepoFinding("forgekit", "docs 보강", kind="docs")
        v = L.authorize_runtime_execution(_decision(f), L.ActionRequest(kind="docs", summary="docs 보강"),
                                          executor_role="be")
        trailers = L.execution_commit_trailers(v, env={})
        msg = "📝 docs\n\n변경 이유\n- x\n\n주요 변경 사항\n- y\n\n비고\n- 없음\n\n" + "\n".join(trailers)
        self.assertEqual(L.validate_execution_trailers(msg, v), ())


if __name__ == "__main__":
    unittest.main()
