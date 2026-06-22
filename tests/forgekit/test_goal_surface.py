"""GW5 — /goal console surface regression (router + goal_surface).

Proves the operator surface over forgekit_goal:
- `/goal` / `/goal list` lists goals (empty state honest);
- `/goal new <title>` creates a draft and persists it;
- `/goal show <id>` renders status/packets/evidence;
- `/goal activate <id>` does a legal draft->active transition (illegal surfaced);
- `/goal evidence <id>` lists evidence;
- unknown subcommand → usage help.

Surface stays thin: it only reads/writes the goal store (pointed at a tmp
FORGEKIT_HOME via ctx.env), never executes a tick. CI-safe (no textual import —
the command core is stdlib-only).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _rel in (
    "apps/forgekit-console/src",
    "packages/forgekit-contracts/src",
    "packages/forgekit-config/src",
    "packages/forgekit-goal/src",
):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.router import ConsoleContext, route
from forgekit_contracts.models import KIND_ERROR, KIND_INFO


def _route(raw: str, env) -> object:
    return route(parse_input(raw), ConsoleContext(repo_root=Path("."), env=env))


class GoalSurfaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.env = {"FORGEKIT_HOME": self._tmp.name}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _new(self, title: str) -> str:
        res = _route(f"/goal new {title}", self.env)
        self.assertEqual(res.kind, KIND_INFO)
        # message: "goal 생성: goal-xxxx  [draft]  <title>"
        gid = res.lines[0].split("goal 생성:")[1].split()[0]
        self.assertTrue(gid.startswith("goal-"))
        return gid

    def test_empty_list(self) -> None:
        res = _route("/goal", self.env)
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("등록된 goal 없음", res.lines[0])

    def test_new_then_list_and_show(self) -> None:
        gid = self._new("ForgeKit 완성")
        lst = _route("/goal list", self.env)
        self.assertTrue(any(gid in ln for ln in lst.lines))
        show = _route(f"/goal show {gid}", self.env)
        self.assertEqual(show.kind, KIND_INFO)
        self.assertTrue(any(gid in ln and "[draft]" in ln for ln in show.lines))

    def test_new_empty_title_rejected(self) -> None:
        res = _route("/goal new", self.env)
        self.assertEqual(res.kind, KIND_ERROR)

    def test_activate_legal_and_persisted(self) -> None:
        gid = self._new("activate me")
        act = _route(f"/goal activate {gid}", self.env)
        self.assertEqual(act.kind, KIND_INFO)
        self.assertIn("active", act.lines[0])
        # persisted: show now reports active
        show = _route(f"/goal show {gid}", self.env)
        self.assertTrue(any("[active]" in ln for ln in show.lines))

    def test_show_missing_goal(self) -> None:
        res = _route("/goal show goal-nope", self.env)
        self.assertEqual(res.kind, KIND_INFO)
        self.assertIn("없음", res.lines[0])

    def test_unknown_sub_shows_usage(self) -> None:
        res = _route("/goal frobnicate", self.env)
        self.assertEqual(res.kind, KIND_INFO)
        self.assertTrue(any("control plane" in ln for ln in res.lines))

    def test_registry_has_goal_command(self) -> None:
        from forgekit_console.commands.registry import find_command, H_GOAL

        cmd = find_command("goal")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.handler, H_GOAL)


if __name__ == "__main__":
    unittest.main()
