"""In-console approve / deny over goals (operator cockpit, wave-2 gw3).

The last operator-cockpit parity gap: a goal the runtime parked in
``awaiting_approval`` (a risky/restricted packet needs the operator) must be
decidable IN the console. Proven via the REAL router + goal store (tmp
FORGEKIT_HOME), not mocks:

- ``/goal awaiting`` lists awaiting_approval goals + their linked packets;
- ``/goal approve <id> [note]`` → legal ``awaiting_approval -> active`` + an
  append-only ``decision`` evidence record;
- ``/goal deny <id> [note]`` → legal ``awaiting_approval -> blocked`` + evidence;
- approve is HONEST about execution: the GW4-B bridge is now connected, but a
  goal with NO linked/resolvable packet has nothing to execute → the bridge
  returns an honest "실행 불가/대기" and writes NO ``execution`` evidence (never a
  fake "executed");
- missing id / non-awaiting goal are surfaced as errors (no silent no-op).

Surface stays thin (render / CRUD / legal transition) — it owns no goal logic.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401  (inserts console + package srcs on sys.path)

from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.router import ConsoleContext, route
from forgekit_contracts.models import KIND_ERROR, KIND_INFO
from forgekit_goal import Goal, GoalStatus, GoalStore, transitions


def _route(raw: str, env):
    return route(parse_input(raw), ConsoleContext(repo_root=Path("."), env=env))


class GoalApprovalTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.env = {"FORGEKIT_HOME": self._tmp.name}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _awaiting(self, title: str, *packets: str) -> str:
        """Persist a goal in awaiting_approval (active→awaiting) with linked packets."""
        g = Goal.create(title)
        g = transitions.apply(g, GoalStatus.ACTIVE)
        g = transitions.apply(g, GoalStatus.AWAITING_APPROVAL)
        for p in packets:
            g = g.link_packet(p)
        GoalStore(env=self.env).save(g)
        return g.id

    def _get(self, gid: str) -> Goal:
        g = GoalStore(env=self.env).get(gid)
        assert g is not None
        return g

    # --- awaiting list ------------------------------------------------------
    def test_awaiting_empty_is_honest(self) -> None:
        res = _route("/goal awaiting", self.env)
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("승인 대기 goal 없음", res.lines[0])

    def test_awaiting_lists_goal_and_linked_packets(self) -> None:
        gid = self._awaiting("배포 파이프라인", "packet-deploy-1", "packet-deploy-2")
        res = _route("/goal awaiting", self.env)
        self.assertEqual(res.kind, KIND_INFO)
        blob = "\n".join(res.lines)
        self.assertIn(gid, blob)
        self.assertIn("packet-deploy-1", blob)         # linked packets surfaced
        self.assertIn(f"/goal approve {gid}", blob)     # the action hint
        self.assertIn(f"/goal deny {gid}", blob)

    # --- approve ------------------------------------------------------------
    def test_approve_transitions_to_active_and_records_decision(self) -> None:
        gid = self._awaiting("승인 대상")
        res = _route(f"/goal approve {gid} 운영자 확인", self.env)
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("active", res.lines[0])
        g = self._get(gid)
        self.assertEqual(g.status, GoalStatus.ACTIVE)            # legal transition persisted
        decisions = [e for e in g.evidence if e.kind == "decision"]
        self.assertEqual(len(decisions), 1)                     # append-only decision evidence
        self.assertIn("승인", decisions[0].summary)
        self.assertIn("운영자 확인", decisions[0].summary)       # the operator note is kept

    def test_approve_with_no_packet_is_honest_no_fake_execution(self) -> None:
        # GW4-B bridge is connected, but this goal has NO linked packet → nothing to
        # execute. The bridge must report that honestly — never a fake "executed".
        gid = self._awaiting("실행 대기 확인")
        res = _route(f"/goal approve {gid}", self.env)
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("active", res.lines[0])                  # legal transition still happens
        self.assertNotIn("safe·게이트 통과", res.lines[0])       # NOT a fake "executed"
        self.assertNotIn("실행 인가됨", res.lines[0])            # NOT authorized either
        g = self._get(gid)
        # the surface records the bridge note (honest string), but it must NOT claim a
        # real authorized execution — no "safe·게이트 통과" / "인가" execution record.
        exec_records = [e for e in g.evidence if e.kind == "execution"]
        for e in exec_records:
            self.assertNotIn("safe·게이트 통과", e.summary)
            self.assertNotIn("실행 인가", e.summary)

    def test_approve_missing_goal_is_error(self) -> None:
        res = _route("/goal approve goal-nope", self.env)
        self.assertEqual(res.kind, KIND_ERROR)
        self.assertIn("없음", res.lines[0])

    def test_approve_non_awaiting_goal_rejected(self) -> None:
        # a plain draft goal is not awaiting approval → cannot be approved
        g = Goal.create("그냥 초안")
        GoalStore(env=self.env).save(g)
        res = _route(f"/goal approve {g.id}", self.env)
        self.assertEqual(res.kind, KIND_ERROR)
        self.assertIn("승인 대기 아님", res.lines[0])
        self.assertEqual(self._get(g.id).status, GoalStatus.DRAFT)   # unchanged

    # --- deny ---------------------------------------------------------------
    def test_deny_transitions_to_blocked_and_records_decision(self) -> None:
        gid = self._awaiting("거부 대상")
        res = _route(f"/goal deny {gid} 범위 밖", self.env)
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("blocked", res.lines[0])
        g = self._get(gid)
        self.assertEqual(g.status, GoalStatus.BLOCKED)
        decisions = [e for e in g.evidence if e.kind == "decision"]
        self.assertEqual(len(decisions), 1)
        self.assertIn("거부", decisions[0].summary)
        self.assertIn("범위 밖", decisions[0].summary)

    def test_deny_non_awaiting_goal_rejected(self) -> None:
        g = Goal.create("초안 거부 불가")
        GoalStore(env=self.env).save(g)
        res = _route(f"/goal deny {g.id}", self.env)
        self.assertEqual(res.kind, KIND_ERROR)
        self.assertIn("승인 대기 아님", res.lines[0])

    # --- registry / usage ---------------------------------------------------
    def test_usage_lists_approve_deny(self) -> None:
        res = _route("/goal frobnicate", self.env)   # unknown sub → usage
        blob = "\n".join(res.lines)
        self.assertIn("approve", blob)
        self.assertIn("deny", blob)
        self.assertIn("awaiting", blob)


if __name__ == "__main__":
    unittest.main()
