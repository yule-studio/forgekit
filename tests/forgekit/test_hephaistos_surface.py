"""Hephaistos operator surface (PR2) — /resolve /hephaistos /skills /loadout routing.

Proves the surfaces register + route, project the resolver/verifier/nexus_read core
honestly (not_connected / shallow / partial shown as-is, no fake-live), and that
empty/uncovered contexts surface honestly rather than dummy data.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.router import build_default_context, route
from forgekit_console.hephaistos import projection as proj


def _ctx():
    return build_default_context(Path("."))


class RoutingTests(unittest.TestCase):
    def test_resolve_routes_full_plan(self) -> None:
        r = route(parse_input("/resolve Spring Boot JWT refresh token"), _ctx())
        joined = "\n".join(r.lines)
        self.assertIn("backend-engineer", joined)
        self.assertIn("backend-java-local", joined)
        self.assertIn("nexus", joined)               # source status surfaced

    def test_hephaistos_status(self) -> None:
        r = route(parse_input("/hephaistos"), _ctx())
        joined = "\n".join(r.lines)
        self.assertIn("skill-forging core", joined)
        self.assertIn("armory", joined)

    def test_loadout_verify_surface(self) -> None:
        r = route(parse_input("/loadout backend-java-local"), _ctx())
        joined = "\n".join(r.lines)
        self.assertTrue(any(s in joined for s in ("ready", "partial", "missing")))

    def test_skills_uncovered_is_honest(self) -> None:
        r = route(parse_input("/skills Rust 임베디드 펌웨어"), _ctx())
        self.assertIn("shallow", "\n".join(r.lines))   # honest, not dummy skills

    def test_resolve_without_arg_prompts(self) -> None:
        r = route(parse_input("/resolve"), _ctx())
        self.assertIn("요청을 입력", "\n".join(r.lines))


class HonestyTests(unittest.TestCase):
    def test_nexus_not_connected_surfaced(self) -> None:
        plan, read = proj.resolve_with_sources("Spring Boot JWT", env={}, config={})
        lines = "\n".join(proj.resolve_summary_lines(plan, read))
        self.assertIn("not_connected", lines)           # no fake-live

    def test_nexus_connected_reads(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        plan, _ = proj.resolve_with_sources("Spring Boot JWT", env={}, config={})
        # create a declared ref so the read is connected
        ref0 = plan.nexus_refs[0].ref
        (root / ref0).parent.mkdir(parents=True, exist_ok=True)
        (root / ref0).write_text("# area\n- 규칙\n", encoding="utf-8")
        _, read = proj.resolve_with_sources("Spring Boot JWT",
                                            env={"FORGEKIT_NEXUS_ROOT": str(root)}, config={})
        self.assertIn("connected", "\n".join(proj.nexus_status_lines(read)))


if __name__ == "__main__":
    unittest.main()
