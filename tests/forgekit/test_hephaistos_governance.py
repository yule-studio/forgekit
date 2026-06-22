"""Hephaistos governance completion — forge plan → real execution gate → receipt.

Proves the forging core is bound to the org governance backbone (not a separate path):
- ``classify_forge_plan`` derives safe/risky/destructive from packet level + weapon
  safety + goal wording (forbidden-scope is a guardrail, NOT a destructive signal);
- ``forge_execute`` runs a SAFE plan through the SAME real gate the self-improvement
  bridge uses (run_internal_chain → authorize_runtime_execution → validate_execution) and
  issues an authorized, trailer-stamped :class:`ForgeExecutionReceipt`;
- destructive wording / a risky (or unknown) weapon → blocked receipt, NO trailers, never
  "executed";
- the receipt validator rejects fakes: authorized-without-trailers, blocked-with-trailers,
  executed-without-authorization;
- the executor on the receipt is the forge's selected specialist, a registry identity.

Hermetic + pure: forge resolves from the Armory catalog; no network, no file writes.
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
    "packages/hephaistos/src",
    "packages/armory/src",
    "packages/nexus/src",
    "apps/forgekit-console/src",
):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_runtime import forge as F
from forgekit_runtime.decision_lane import OperatorApproval
from hephaistos import resolve

_SAFE_REQUEST = "java spring boot 백엔드 REST API 추가"


class ClassifyTests(unittest.TestCase):
    def test_plain_backend_request_is_safe(self) -> None:
        c = F.classify_forge_plan(resolve(_SAFE_REQUEST), weapon_safety=lambda w: "safe")
        self.assertEqual(c.action_class, F.SAFE)
        self.assertEqual(c.approval_level, "L2_internal_approve")

    def test_destructive_wording_bumps_to_destructive(self) -> None:
        c = F.classify_forge_plan(resolve("프로덕션 배포 deploy 및 secret 회전"),
                                  weapon_safety=lambda w: "safe")
        self.assertEqual(c.action_class, F.DESTRUCTIVE)

    def test_risky_weapon_bumps_to_risky(self) -> None:
        c = F.classify_forge_plan(resolve("docker 컨테이너 구성"),
                                  weapon_safety=lambda w: "risky" if w == "docker" else "safe")
        self.assertEqual(c.action_class, F.RISKY)
        self.assertIn("docker", c.risky_weapons)

    def test_unknown_weapon_is_not_auto_safe(self) -> None:
        c = F.classify_forge_plan(resolve(_SAFE_REQUEST), weapon_safety=lambda w: None)
        self.assertEqual(c.action_class, F.RISKY)
        self.assertTrue(c.unknown_weapons)


class ReceiptValidatorTests(unittest.TestCase):
    def _base(self, **kw):
        d = dict(request="x", selected_agent="backend-engineer", action_class="safe",
                 authorized=True, outcome=F.OUTCOME_EXECUTED,
                 approval_metadata="decision=x;level=L2;signoff=tech-lead",
                 commit_trailers=("Forgekit-Agent: backend-engineer",))
        d.update(kw)
        return F.ForgeExecutionReceipt(**d)

    def test_real_authorized_receipt_passes(self) -> None:
        self.assertEqual(F.validate_forge_receipt(self._base()), ())

    def test_authorized_without_trailers_rejected(self) -> None:
        self.assertTrue(F.validate_forge_receipt(self._base(commit_trailers=())))

    def test_authorized_without_metadata_rejected(self) -> None:
        self.assertTrue(F.validate_forge_receipt(self._base(approval_metadata="")))

    def test_blocked_with_trailers_rejected(self) -> None:
        r = self._base(authorized=False, outcome=F.OUTCOME_BLOCKED,
                       blocking_reasons=("x",))  # but still carries trailers
        self.assertTrue(any("fake approval" in x for x in F.validate_forge_receipt(r)))

    def test_executed_without_authorization_rejected(self) -> None:
        r = self._base(authorized=False, outcome=F.OUTCOME_EXECUTED,
                       commit_trailers=(), blocking_reasons=("x",))
        self.assertTrue(F.validate_forge_receipt(r))

    def test_unknown_executor_rejected(self) -> None:
        self.assertTrue(F.validate_forge_receipt(self._base(selected_agent="not-a-role")))


class ForgeExecuteTests(unittest.TestCase):
    def test_safe_forge_authorized_and_stamped(self) -> None:
        r = F.forge_execute(_SAFE_REQUEST, weapon_safety=lambda w: "safe")
        self.assertTrue(r.authorized)
        self.assertEqual(r.outcome, F.OUTCOME_EXECUTED)
        self.assertEqual(r.action_class, F.SAFE)
        self.assertTrue(r.commit_trailers)
        self.assertIn("signoff=tech-lead", r.approval_metadata)
        self.assertEqual(r.selected_agent, "backend-engineer")
        self.assertEqual(F.validate_forge_receipt(r), ())
        # the chain actually ran PM→gateway→tech-lead
        self.assertEqual(len(r.chain_trace), 3)

    def test_destructive_forge_blocked_no_trailers(self) -> None:
        r = F.forge_execute("프로덕션 배포 deploy 및 secret 회전", weapon_safety=lambda w: "safe")
        self.assertFalse(r.authorized)
        self.assertEqual(r.outcome, F.OUTCOME_BLOCKED)
        self.assertEqual(r.action_class, F.DESTRUCTIVE)
        self.assertEqual(r.commit_trailers, ())
        self.assertTrue(r.blocking_reasons)
        self.assertEqual(F.validate_forge_receipt(r), ())

    def test_risky_weapon_forge_blocked(self) -> None:
        r = F.forge_execute("docker 컨테이너 구성",
                            weapon_safety=lambda w: "risky" if w == "docker" else "safe")
        self.assertFalse(r.authorized)
        self.assertEqual(r.action_class, F.RISKY)
        self.assertIn("docker", r.risky_weapons)
        self.assertEqual(r.commit_trailers, ())
        self.assertEqual(F.validate_forge_receipt(r), ())

    def test_risky_with_operator_still_chain_gated(self) -> None:
        # operator approval satisfies decision-lane, but the autopilot chain only auto-runs
        # safe — so a risky forge stays blocked (honest, not fake-executed)
        op = OperatorApproval(approver="operator", decision_ref="x", approved=True)
        r = F.forge_execute("docker 구성", weapon_safety=lambda w: "risky" if w == "docker" else "safe",
                            operator_approval=op)
        self.assertFalse(r.authorized)
        self.assertEqual(F.validate_forge_receipt(r), ())

    def test_empty_request_awaiting(self) -> None:
        r = F.forge_execute("")
        self.assertEqual(r.outcome, F.OUTCOME_AWAITING)
        self.assertFalse(r.authorized)
        self.assertEqual(F.validate_forge_receipt(r), ())


class LedgerTests(unittest.TestCase):
    """The forge governance decision log accumulates (영속) and refuses fakes."""

    def _env(self):
        import tempfile
        return {"FORGEKIT_HOME": tempfile.mkdtemp()}

    def test_persist_and_read_back(self) -> None:
        env = self._env()
        F.forge_execute(_SAFE_REQUEST, weapon_safety=lambda w: "safe",
                        env=env, persist=True, recorded_at="2026-06-22")
        F.forge_execute("프로덕션 배포 deploy secret", env=env, persist=True, recorded_at="2026-06-22")
        entries = F.read_forge_receipts(env=env)
        self.assertEqual(len(entries), 2)                      # append-only accumulation
        self.assertEqual(entries[0]["receipt"]["outcome"], F.OUTCOME_EXECUTED)
        self.assertEqual(entries[1]["receipt"]["outcome"], F.OUTCOME_BLOCKED)
        self.assertEqual(entries[0]["recorded_at"], "2026-06-22")

    def test_preview_does_not_persist(self) -> None:
        env = self._env()
        F.forge_execute(_SAFE_REQUEST, weapon_safety=lambda w: "safe", env=env)  # persist=False
        self.assertEqual(F.read_forge_receipts(env=env), [])

    def test_fake_receipt_refused_at_persistence(self) -> None:
        env = self._env()
        fake = F.ForgeExecutionReceipt(request="x", authorized=True, outcome=F.OUTCOME_EXECUTED,
                                       approval_metadata="", selected_agent="backend-engineer")
        with self.assertRaises(F.FakeReceiptRefused):
            F.record_forge_receipt(fake, env=env)
        self.assertEqual(F.read_forge_receipts(env=env), [])   # nothing persisted


class ResolveSurfaceTests(unittest.TestCase):
    """The governance verdict is reachable at runtime — /resolve surfaces it."""

    def test_resolve_surfaces_governance_receipt(self) -> None:
        from pathlib import Path as _P

        from forgekit_console.commands.parser import parse_input
        from forgekit_console.commands.router import build_default_context, route

        r = route(parse_input("/resolve java spring boot REST API"), build_default_context(_P(".")))
        joined = "\n".join(r.lines)
        self.assertIn("governance", joined)            # the section is rendered
        self.assertIn("forge execution receipt", joined)
        self.assertIn("class", joined)                 # safe/risky/destructive surfaced


if __name__ == "__main__":
    unittest.main()
