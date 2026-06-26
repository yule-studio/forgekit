"""Install/activation safety lane — external tool/skill/plugin activation under the gate.

Proves the activation path is bound to the org governance backbone (not a side door):

- the lifecycle state model separates 추천됨(collected/curated/armory-registered) from
  설치됨(enabled) from 실행됨(executed): ``derive_readiness_state`` never returns an active
  state, and the transition map has NO edge into enabled/executed that skips approval;
- ``classify_activation`` derives safe/risky/blocked from the candidate's supply-chain
  FACTS (install-required / global-write / external source / unknown safety) — STRICTEST
  wins — not from the action verb (executing a vetted, present, safe armory tool is safe);
- ``activate`` runs the candidate through the SAME real gate forge/self-improvement use
  (run_internal_chain → authorize_runtime_execution) and issues an authorized,
  trailer-stamped :class:`ActivationReceipt` ONLY for a cleared activation;
- a risky install is blocked WITHOUT an operator approval and enabled WITH one; a
  destructive candidate is blocked even with one; neither blocked path carries trailers;
- the receipt validator + ledger refuse fakes ("fake 'installed' 금지"): an enabled/
  executed outcome requires a real authorization;
- the append-only ledger persists every verdict (the runtime-loop 흔적) and ``latest_states``
  folds it into the runtime's activation memory.

Hermetic + pure: resolves from the catalog; the ledger writes under an isolated
FORGEKIT_HOME tempdir (never the real ~/.forgekit).
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
    "packages/hephaistos/src",
    "packages/armory/src",
    "packages/nexus/src",
    "apps/forgekit-console/src",
):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_runtime import activation as ACT
from forgekit_runtime.activation import states as S
from forgekit_runtime.decision_lane import OperatorApproval

_OP = OperatorApproval(approver="operator", decision_ref="", approved=True)


def _cand(**kw):
    base = dict(id="ripgrep", kind="tool", source="armory", present=True,
                armory_registered=True, curated=True, safety="safe", why="코드 검색")
    base.update(kw)
    return ACT.ActivationCandidate(**base)


class StateModelTests(unittest.TestCase):
    def test_readiness_never_returns_active_state(self):
        # every facts permutation derives a pre-gate state, never enabled/executed/blocked.
        for present in (True, False):
            for install in (True, False):
                for gw in (True, False):
                    for safety in ("safe", "risky", ""):
                        for curated in (True, False):
                            for armory in (True, False):
                                st = ACT.derive_readiness_state(_cand(
                                    present=present, needs_install=install, global_write=gw,
                                    safety=safety, curated=curated, armory_registered=armory))
                                self.assertNotIn(st, S.OUTCOME_STATES)
                                self.assertIn(st, S.ALL_STATES)

    def test_recommendation_track_is_not_installed(self):
        # 추천됨 track: collected/curated/armory-registered all mean NOT active.
        self.assertEqual(ACT.derive_readiness_state(_cand(curated=False)), S.ST_COLLECTED)
        self.assertEqual(
            ACT.derive_readiness_state(_cand(armory_registered=False)), S.ST_CURATED)
        for st in S.RECOMMENDATION_STATES:
            self.assertNotIn(st, S.ACTIVE_STATES)

    def test_attachable_vs_install_required_vs_approval(self):
        # present + vetted + safe + no global write → attachable (the no-friction path).
        self.assertEqual(ACT.derive_readiness_state(_cand()), S.ST_ATTACHABLE)
        # missing → install-required (when otherwise safe).
        self.assertEqual(
            ACT.derive_readiness_state(_cand(present=False, needs_install=True)),
            S.ST_INSTALL_REQUIRED)
        # global write / unknown safety → approval-needed.
        self.assertEqual(
            ACT.derive_readiness_state(_cand(global_write=True)), S.ST_APPROVAL_NEEDED)
        self.assertEqual(
            ACT.derive_readiness_state(_cand(safety="")), S.ST_APPROVAL_NEEDED)

    def test_no_transition_into_active_state_skips_approval(self):
        # the only edges into enabled are from attachable or approval-needed.
        for frm in S.ALL_STATES:
            if S.can_transition(frm, S.ST_ENABLED):
                self.assertIn(frm, (S.ST_ATTACHABLE, S.ST_APPROVAL_NEEDED))
            # executed only follows enabled.
            if S.can_transition(frm, S.ST_EXECUTED):
                self.assertEqual(frm, S.ST_ENABLED)
        # install-required can NEVER jump straight to enabled (must pass approval).
        self.assertFalse(S.can_transition(S.ST_INSTALL_REQUIRED, S.ST_ENABLED))
        self.assertTrue(S.can_transition(S.ST_INSTALL_REQUIRED, S.ST_APPROVAL_NEEDED))

    def test_terminal_states_have_no_exit(self):
        for st in S.TERMINAL_STATES:
            self.assertEqual(S.next_states(st), ())


class ClassifyTests(unittest.TestCase):
    def test_safe_only_for_present_vetted_safe_tool(self):
        c = ACT.classify_activation(_cand(), ACT.ACT_ATTACH)
        self.assertEqual(c.disposition, ACT.SAFE)
        self.assertFalse(c.needs_approval)
        self.assertEqual(c.supply_chain_flags, ())

    def test_execute_of_vetted_tool_is_safe_not_verb_bumped(self):
        # risk is in the facts, not the verb: executing a vetted safe tool stays safe.
        self.assertEqual(
            ACT.classify_activation(_cand(), ACT.ACT_EXECUTE).disposition, ACT.SAFE)

    def test_install_required_is_risky(self):
        c = ACT.classify_activation(_cand(present=False, needs_install=True), ACT.ACT_INSTALL)
        self.assertEqual(c.disposition, ACT.RISKY)
        self.assertIn("install_required", c.supply_chain_flags)

    def test_external_source_and_unknown_safety_are_risky(self):
        c = ACT.classify_activation(_cand(source="external", safety=""), ACT.ACT_EXECUTE)
        self.assertEqual(c.disposition, ACT.RISKY)
        self.assertIn("external_source", c.supply_chain_flags)
        self.assertIn("unknown_safety", c.supply_chain_flags)

    def test_global_write_is_risky(self):
        c = ACT.classify_activation(_cand(global_write=True), ACT.ACT_ENABLE)
        self.assertEqual(c.disposition, ACT.RISKY)
        self.assertIn("global_write", c.supply_chain_flags)

    def test_destructive_wording_is_blocked(self):
        c = ACT.classify_activation(
            _cand(id="deploy-secret-tool", why="production deploy secret"), ACT.ACT_ENABLE)
        self.assertEqual(c.disposition, ACT.BLOCKED)

    def test_unknown_action_is_blocked(self):
        self.assertEqual(
            ACT.classify_activation(_cand(), "yolo").disposition, ACT.BLOCKED)


class BridgeTests(unittest.TestCase):
    def test_safe_attach_authorizes_and_stamps_trailers(self):
        r = ACT.activate(_cand(), ACT.ACT_ATTACH)
        self.assertTrue(r.authorized)
        self.assertEqual(r.outcome, ACT.OUTCOME_ENABLED)
        self.assertEqual(r.to_state, S.ST_ENABLED)
        self.assertTrue(r.commit_trailers)
        self.assertTrue(r.approval_metadata)
        self.assertEqual(ACT.validate_activation_receipt(r), ())

    def test_execute_action_yields_executed_outcome(self):
        r = ACT.activate(_cand(), ACT.ACT_EXECUTE)
        self.assertTrue(r.authorized)
        self.assertEqual(r.outcome, ACT.OUTCOME_EXECUTED)
        self.assertEqual(r.to_state, S.ST_EXECUTED)

    def test_install_blocked_without_operator_approval(self):
        r = ACT.activate(_cand(source="external", present=False, needs_install=True),
                         ACT.ACT_INSTALL)
        self.assertFalse(r.authorized)
        self.assertEqual(r.outcome, ACT.OUTCOME_BLOCKED)
        self.assertEqual(r.to_state, S.ST_BLOCKED)
        self.assertEqual(r.commit_trailers, ())          # no fake approval on a blocked path
        self.assertTrue(r.blocking_reasons)
        # supply-chain reasons are surfaced for the audit.
        self.assertTrue(any("공급망" in x or "설치" in x for x in r.blocking_reasons))

    def test_install_enabled_with_operator_approval(self):
        r = ACT.activate(_cand(source="external", present=False, needs_install=True),
                         ACT.ACT_INSTALL, operator_approval=_OP)
        self.assertTrue(r.authorized)
        self.assertEqual(r.outcome, ACT.OUTCOME_ENABLED)
        self.assertTrue(r.commit_trailers)
        self.assertEqual(ACT.validate_activation_receipt(r), ())

    def test_destructive_blocked_even_with_operator_approval(self):
        r = ACT.activate(_cand(id="deploy-secret", why="production deploy secret"),
                         ACT.ACT_ENABLE, operator_approval=_OP)
        self.assertFalse(r.authorized)
        self.assertEqual(r.disposition, ACT.BLOCKED)
        self.assertEqual(r.commit_trailers, ())

    def test_empty_candidate_is_awaiting_not_blocked(self):
        r = ACT.activate(ACT.ActivationCandidate(id=""), ACT.ACT_ATTACH)
        self.assertEqual(r.outcome, ACT.OUTCOME_AWAITING)
        self.assertFalse(r.authorized)

    def test_executor_is_registry_known(self):
        from forgekit_config.identity.registry import is_known
        r = ACT.activate(_cand(), ACT.ACT_ATTACH)
        self.assertTrue(is_known(r.executor))


class AntiFakeReceiptTests(unittest.TestCase):
    def test_enabled_requires_authorization(self):
        fake = ACT.ActivationReceipt(candidate_id="x", action="install",
                                     outcome=ACT.OUTCOME_ENABLED, authorized=False)
        v = ACT.validate_activation_receipt(fake)
        self.assertTrue(any("fake 'installed'" in x or "fake 설치" in x for x in v))

    def test_authorized_without_trailers_is_fake(self):
        fake = ACT.ActivationReceipt(
            candidate_id="x", action="attach", outcome=ACT.OUTCOME_ENABLED,
            authorized=True, approval_metadata="decision=..;level=..", executor="devops-engineer")
        self.assertTrue(ACT.validate_activation_receipt(fake))   # no trailers

    def test_blocked_with_trailers_is_fake(self):
        fake = ACT.ActivationReceipt(
            candidate_id="x", action="install", outcome=ACT.OUTCOME_BLOCKED,
            authorized=False, commit_trailers=("Forgekit-Approval: x",),
            blocking_reasons=("risky",))
        self.assertTrue(ACT.validate_activation_receipt(fake))

    def test_unknown_executor_is_fake(self):
        fake = ACT.ActivationReceipt(
            candidate_id="x", action="attach", outcome=ACT.OUTCOME_ENABLED, authorized=True,
            approval_metadata="m", commit_trailers=("t",), executor="nobody-9000")
        self.assertTrue(ACT.validate_activation_receipt(fake))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.env = {"FORGEKIT_HOME": self._dir}

    def test_persist_records_granted_and_blocked(self):
        ACT.activate(_cand(), ACT.ACT_ATTACH, env=self.env, persist=True)
        ACT.activate(_cand(id="cli", source="external", present=False, needs_install=True,
                           why="편의"), ACT.ACT_INSTALL, env=self.env, persist=True)
        entries = ACT.read_activation_receipts(env=self.env)
        self.assertEqual(len(entries), 2)

    def test_latest_states_folds_log(self):
        ACT.activate(_cand(), ACT.ACT_ATTACH, env=self.env, persist=True)
        ACT.activate(_cand(id="cli", source="external", present=False, needs_install=True,
                           why="편의"), ACT.ACT_INSTALL, env=self.env, persist=True)
        states = ACT.latest_states(self.env)
        self.assertEqual(states.get("ripgrep"), S.ST_ENABLED)
        self.assertEqual(states.get("cli"), S.ST_BLOCKED)

    def test_ledger_refuses_fake(self):
        from forgekit_runtime.activation.ledger import (
            FakeActivationRefused, record_activation_receipt)
        fake = ACT.ActivationReceipt(candidate_id="x", action="install",
                                     outcome=ACT.OUTCOME_ENABLED, authorized=False)
        with self.assertRaises(FakeActivationRefused):
            record_activation_receipt(fake, env=self.env)

    def test_ledger_lines_empty_is_honest(self):
        lines = ACT.activation_ledger_lines(env=self.env)
        self.assertEqual(len(lines), 1)
        self.assertIn("없음", lines[0])

    def test_ledger_lines_carry_why(self):
        ACT.activate(_cand(why="검색 속도"), ACT.ACT_ATTACH, env=self.env, persist=True)
        lines = ACT.activation_ledger_lines(env=self.env)
        self.assertTrue(any("검색 속도" in x for x in lines))


if __name__ == "__main__":
    unittest.main()
