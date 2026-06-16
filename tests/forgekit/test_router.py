"""forgekit console router (pure, fake loaders)."""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.router import ConsoleContext, route
from forgekit_console.models import (
    KIND_AGENT_MODE,
    KIND_CLEAR,
    KIND_ERROR,
    KIND_HELP,
    KIND_INFO,
    KIND_QUIT,
    Alert,
    StatusSection,
    StatusSummary,
)


def _ctx(**overrides) -> ConsoleContext:
    op = StatusSummary(
        title="operator dashboard",
        sections=(StatusSection("provider runtime", ("live runs: 1 / 2",)),),
        alerts=(Alert("warn", "1 proposal waiting"),),
        next_actions=("respond in #승인-대기",),
    )
    base = dict(
        repo_root=Path("/tmp/repo"),
        load_operator=lambda: op,
        load_runtime=lambda: StatusSummary(title="runtime status", sections=(StatusSection("runtime status", ("ok",)),)),
        load_doctor=lambda: StatusSummary(title="doctor", sections=(StatusSection("doctor", ("ok",)),)),
    )
    base.update(overrides)
    return ConsoleContext(**base)


def _route(line: str, ctx: ConsoleContext):
    return route(parse_input(line), ctx)


class RouterTests(unittest.TestCase):
    def test_help_opens_overlay_kind(self) -> None:
        r = _route("/help", _ctx())
        self.assertEqual(r.kind, KIND_HELP)
        # lines kept as a text fallback for non-TUI consumers
        joined = "\n".join(r.lines)
        self.assertIn("/status", joined)
        self.assertIn("/quit", joined)

    def test_agents_lists_agents(self) -> None:
        r = _route("/agents", _ctx())
        joined = "\n".join(r.lines)
        self.assertIn("Engineering", joined)
        self.assertIn("Security", joined)

    def test_status_uses_operator_loader(self) -> None:
        called = []
        ctx = _ctx(load_operator=lambda: (called.append(1) or StatusSummary(title="operator dashboard")))
        r = _route("/status", ctx)
        self.assertEqual(r.kind, KIND_INFO)
        self.assertEqual(r.title, "operator dashboard")
        self.assertEqual(len(called), 1)

    def test_harness_also_uses_operator_loader(self) -> None:
        r = _route("/harness", _ctx())
        self.assertEqual(r.title, "operator dashboard")

    def test_runtime_and_doctor_loaders(self) -> None:
        self.assertEqual(_route("/runtime", _ctx()).title, "runtime status")
        self.assertEqual(_route("/doctor", _ctx()).title, "doctor")

    def test_unavailable_summary_is_error_kind(self) -> None:
        ctx = _ctx(load_runtime=lambda: StatusSummary(title="runtime status", available=False, error="no db"))
        r = _route("/runtime", ctx)
        self.assertEqual(r.kind, KIND_ERROR)

    def test_agent_enter_stub(self) -> None:
        r = _route("/pm-agent", _ctx())
        self.assertEqual(r.kind, KIND_AGENT_MODE)
        self.assertEqual(r.title, "agent:product-agent")
        self.assertIn("stub", "\n".join(r.lines))

    def test_ops_observer_includes_alerts(self) -> None:
        r = _route("/ops-observer", _ctx())
        self.assertEqual(r.kind, KIND_AGENT_MODE)
        self.assertIn("1 proposal waiting", "\n".join(r.lines))

    def test_unknown_command(self) -> None:
        r = _route("/nope", _ctx())
        self.assertEqual(r.kind, KIND_ERROR)
        self.assertIn("unknown command", r.title)

    def test_free_text_not_wired(self) -> None:
        r = _route("just chatting", _ctx())
        self.assertEqual(r.kind, KIND_INFO)
        self.assertIn("아직 연결되지 않았습니다", "\n".join(r.lines))

    def test_quit_and_clear_kinds(self) -> None:
        self.assertEqual(_route("/quit", _ctx()).kind, KIND_QUIT)
        self.assertEqual(_route("/clear", _ctx()).kind, KIND_CLEAR)

    def test_layout_kind(self) -> None:
        from forgekit_console.models import KIND_LAYOUT

        self.assertEqual(_route("/layout", _ctx()).kind, KIND_LAYOUT)

    def test_exit_is_quit_alias(self) -> None:
        self.assertEqual(_route("/exit", _ctx()).kind, KIND_QUIT)
        self.assertEqual(_route("/quit", _ctx()).kind, KIND_QUIT)


if __name__ == "__main__":
    unittest.main()
