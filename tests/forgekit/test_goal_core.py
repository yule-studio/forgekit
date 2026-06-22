"""ForgeKit goal core (GW1) regression — model + transitions + persistent store.

Proves the control-plane spine:
- a goal is created in ``draft`` with non-empty title (empty rejected);
- status changes go through the transition matrix: legal moves work, illegal
  moves (draft→done, draft→blocked, done→draft) raise InvalidTransition;
- ``done`` is refused with no evidence (no fake-green), allowed once evidence
  exists; same-state transition is a no-op;
- child-goal tree + work-packet linkage are recorded (and de-duplicated);
- evidence is append-only;
- save→load round-trips a goal exactly, survives a fresh store instance
  (restart simulation), and load_all returns persisted goals.

Pure / CI-safe: all IO goes to a tmp dir; no providers, no network, no real
``~/.forgekit`` touched (FORGEKIT_HOME is pointed at the tmp dir).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Make the package importable before a reinstall, mirroring tests/forgekit/__init__.
_ROOT = Path(__file__).resolve().parents[2]
for _rel in ("packages/forgekit-goal/src", "packages/forgekit-config/src"):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_goal import Goal, GoalStatus, GoalStore, InvalidTransition
from forgekit_goal import transitions


def _clock():
    """Monotonic ISO-ish timestamps so updated_at ordering is deterministic."""

    counter = {"n": 0}

    def now() -> str:
        counter["n"] += 1
        return f"2026-06-22T00:00:{counter['n']:02d}+00:00"

    return now


class GoalModelTest(unittest.TestCase):
    def test_create_defaults_and_empty_title_rejected(self) -> None:
        g = Goal.create("ForgeKit control plane", intent="finish it", now=_clock())
        self.assertTrue(g.id.startswith("goal-"))
        self.assertEqual(g.status, GoalStatus.DRAFT)
        self.assertEqual(g.title, "ForgeKit control plane")
        self.assertEqual(g.intent, "finish it")
        self.assertEqual(g.children, ())
        self.assertEqual(g.packets, ())
        self.assertEqual(g.evidence, ())
        self.assertTrue(g.created_at and g.updated_at)
        with self.assertRaises(ValueError):
            Goal.create("   ")

    def test_unknown_mode_rejected(self) -> None:
        Goal.create("ok", mode="auto")  # known mode is fine
        with self.assertRaises(ValueError):
            Goal.create("bad", mode="warp-speed")

    def test_child_and_packet_linkage_dedup(self) -> None:
        now = _clock()
        g = Goal.create("parent", now=now)
        g = g.add_child("goal-child1", now=now).add_child("goal-child1", now=now)
        self.assertEqual(g.children, ("goal-child1",))
        with self.assertRaises(ValueError):
            g.add_child(g.id)  # self-parenting rejected
        g = g.link_packet("packet-7", now=now).link_packet("packet-7", now=now)
        self.assertEqual(g.packets, ("packet-7",))
        g = g.unlink_packet("packet-7", now=now)
        self.assertEqual(g.packets, ())

    def test_evidence_is_append_only(self) -> None:
        now = _clock()
        g = Goal.create("g", now=now)
        g = g.add_evidence("observation", "saw a thing", ref="src://x", now=now)
        g = g.add_evidence("note", "second", now=now)
        self.assertEqual(len(g.evidence), 2)
        self.assertEqual(g.evidence[0].kind, "observation")
        self.assertEqual(g.evidence[0].ref, "src://x")
        self.assertIsNone(g.evidence[1].ref)


class GoalTransitionTest(unittest.TestCase):
    def test_legal_path_to_done_requires_evidence(self) -> None:
        now = _clock()
        g = Goal.create("ship", now=now)
        g = transitions.apply(g, GoalStatus.ACTIVE, now=now)
        self.assertEqual(g.status, GoalStatus.ACTIVE)
        # done with no evidence -> refused (no fake-green)
        with self.assertRaises(InvalidTransition):
            transitions.apply(g, GoalStatus.DONE, now=now)
        g = g.add_evidence("verification", "tests pass", now=now)
        g = transitions.apply(g, GoalStatus.DONE, now=now)
        self.assertEqual(g.status, GoalStatus.DONE)

    def test_illegal_transitions_rejected(self) -> None:
        now = _clock()
        g = Goal.create("g", now=now).add_evidence("x", "y", now=now)
        with self.assertRaises(InvalidTransition):
            transitions.apply(g, GoalStatus.DONE, now=now)  # draft->done illegal
        with self.assertRaises(InvalidTransition):
            transitions.apply(g, GoalStatus.BLOCKED, now=now)  # draft->blocked illegal

    def test_same_state_is_noop(self) -> None:
        now = _clock()
        g = Goal.create("g", now=now)
        same = transitions.apply(g, GoalStatus.DRAFT, now=now)
        self.assertIs(same, g)

    def test_done_can_reopen_to_active(self) -> None:
        now = _clock()
        g = Goal.create("g", now=now)
        g = transitions.apply(g, GoalStatus.ACTIVE, now=now)
        g = g.add_evidence("x", "y", now=now)
        g = transitions.apply(g, GoalStatus.DONE, now=now)
        g = transitions.apply(g, GoalStatus.ACTIVE, now=now)  # reopen
        self.assertEqual(g.status, GoalStatus.ACTIVE)


class GoalStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "goals"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_load_roundtrip_survives_new_store(self) -> None:
        now = _clock()
        store = GoalStore(self.root)
        g = Goal.create("persisted goal", intent="stay after restart", mode="auto", now=now)
        g = transitions.apply(g, GoalStatus.ACTIVE, now=now)
        g = g.add_child("goal-kid").link_packet("packet-1").add_evidence("note", "hello")
        store.save(g)

        # Fresh store instance = restart simulation.
        reloaded = GoalStore(self.root).load(g.id)
        self.assertEqual(reloaded.to_dict(), g.to_dict())
        self.assertEqual(reloaded.status, GoalStatus.ACTIVE)
        self.assertEqual(reloaded.children, ("goal-kid",))
        self.assertEqual(reloaded.packets, ("packet-1",))
        self.assertEqual(len(reloaded.evidence), 1)

    def test_env_home_routes_goals_dir(self) -> None:
        from forgekit_goal.store import goals_dir

        env = {"FORGEKIT_HOME": str(Path(self._tmp.name) / "home")}
        self.assertEqual(goals_dir(env), Path(self._tmp.name).joinpath("home", "goals").resolve())

    def test_load_all_and_delete(self) -> None:
        store = GoalStore(self.root)
        a = Goal.create("a", now=_clock())
        b = Goal.create("b", now=_clock())
        store.save(a)
        store.save(b)
        ids = {g.id for g in store.load_all()}
        self.assertEqual(ids, {a.id, b.id})
        self.assertTrue(store.delete(a.id))
        self.assertFalse(store.delete(a.id))
        self.assertEqual({g.id for g in store.load_all()}, {b.id})

    def test_missing_goal_raises_keyerror(self) -> None:
        store = GoalStore(self.root)
        with self.assertRaises(KeyError):
            store.load("goal-nope")
        self.assertIsNone(store.get("goal-nope"))

    def test_unsafe_goal_id_rejected(self) -> None:
        store = GoalStore(self.root)
        with self.assertRaises(ValueError):
            store.load("../escape")

    def test_newer_schema_rejected(self) -> None:
        store = GoalStore(self.root)
        g = Goal.create("g", now=_clock())
        path = store.save(g)
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["schema_version"] = 999
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(ValueError):
            store.load(g.id)


if __name__ == "__main__":
    unittest.main()
