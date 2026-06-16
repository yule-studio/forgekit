"""forgekit console render helpers — help sections, mode badge, palette (pure)."""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.commands.registry import load_agents, load_commands
from forgekit_console.models import (
    MODE_OPERATOR,
    Alert,
    StatusSection,
    StatusSummary,
    agent_mode,
)
from forgekit_console.tui import render


class HelpSectionTests(unittest.TestCase):
    def test_four_tabs(self) -> None:
        secs = render.help_sections(load_commands(), load_agents())
        self.assertEqual([s.title for s in secs], ["Help", "General", "Commands", "Agents"])

    def test_commands_tab_lists_slash(self) -> None:
        secs = render.help_sections(load_commands(), load_agents())
        commands_tab = next(s for s in secs if s.title == "Commands")
        joined = "\n".join(commands_tab.lines)
        self.assertIn("/status", joined)
        self.assertIn("/quit", joined)

    def test_agents_tab_shows_entry_commands(self) -> None:
        secs = render.help_sections(load_commands(), load_agents())
        agents_tab = next(s for s in secs if s.title == "Agents")
        joined = "\n".join(agents_tab.lines)
        self.assertIn("/pm-agent", joined)
        self.assertIn("/ops-observer", joined)


class ModeBadgeTests(unittest.TestCase):
    def test_operator(self) -> None:
        self.assertIn("OPERATOR", render.mode_badge(MODE_OPERATOR))

    def test_agent_badge_uses_label(self) -> None:
        badge = render.mode_badge(agent_mode("product-agent"), load_agents())
        self.assertIn("AGENT", badge)
        self.assertIn("Product", badge)

    def test_palette_badge(self) -> None:
        self.assertIn("PALETTE", render.mode_badge("palette"))


class PalettePanelTests(unittest.TestCase):
    def test_selected_row_highlighted(self) -> None:
        cmds = load_commands()[:3]
        lines = render.palette_panel_lines(cmds, selected=1)
        self.assertIn("reverse", lines[1])
        self.assertNotIn("reverse", lines[0])

    def test_empty_matches_message(self) -> None:
        lines = render.palette_panel_lines([], selected=-1)
        self.assertEqual(len(lines), 1)
        self.assertIn("없습니다", lines[0])


class StatusPillTests(unittest.TestCase):
    def _summary(self, **over):
        base = dict(
            title="operator dashboard",
            sections=(StatusSection("provider", ("live runs: 1 / 2",)),),
            alerts=(),
        )
        base.update(over)
        return StatusSummary(**base)

    def test_one_line_with_green_dot_when_healthy(self) -> None:
        pill = render.status_pill(self._summary())
        self.assertIn("green", pill)
        self.assertIn("provider", pill)
        self.assertNotIn("\n", pill)

    def test_warn_dot(self) -> None:
        pill = render.status_pill(self._summary(alerts=(Alert("warn", "x"),)))
        self.assertIn("yellow", pill)

    def test_unavailable(self) -> None:
        pill = render.status_pill(StatusSummary(title="x", available=False, error="no db"))
        self.assertIn("unavailable", pill)


class HintLineTests(unittest.TestCase):
    def test_default(self) -> None:
        h = render.hint_line()
        self.assertIn("palette", h)
        self.assertNotIn("\n", h)

    def test_palette_open(self) -> None:
        self.assertIn("순환", render.hint_line(palette_open=True))

    def test_help_open(self) -> None:
        self.assertIn("닫기", render.hint_line(help_open=True))

    def test_agent_shows_operator_exit(self) -> None:
        self.assertIn("operator", render.hint_line(in_agent=True))


class ModePillTests(unittest.TestCase):
    def test_operator(self) -> None:
        self.assertIn("operator", render.mode_pill(MODE_OPERATOR))

    def test_agent_uses_label(self) -> None:
        self.assertIn("Product", render.mode_pill(agent_mode("product-agent"), load_agents()))

    def test_palette(self) -> None:
        self.assertIn("palette", render.mode_pill("palette"))


class HelpDocumentTests(unittest.TestCase):
    def _secs(self):
        return render.help_sections(load_commands(), load_agents())

    def test_tab_order_and_default_is_general(self) -> None:
        titles = [s.title for s in self._secs()]
        self.assertEqual(titles, ["Help", "General", "Commands", "Agents"])
        self.assertEqual(self._secs()[render.default_help_tab(self._secs())].title, "General")

    def test_document_shows_tab_strip_and_active_body_only(self) -> None:
        secs = self._secs()
        general = render.default_help_tab(secs)
        joined = "\n".join(render.help_document(secs, general))
        self.assertIn("forgekit help", joined)
        self.assertIn("Esc", joined)
        # all four tab labels appear in the strip
        for title in ("Help", "General", "Commands", "Agents"):
            self.assertIn(title, joined)
        # active = General → its body (단축키) shows, Commands body (/quit list) does not
        self.assertIn("단축키", joined)
        self.assertNotIn("/quit", joined)

    def test_commands_tab_lists_exit_alias(self) -> None:
        secs = self._secs()
        cmd_idx = next(i for i, s in enumerate(secs) if s.title == "Commands")
        joined = "\n".join(render.help_document(secs, cmd_idx))
        self.assertIn("/exit", joined)
        self.assertIn("/quit", joined)


class IntroTests(unittest.TestCase):
    def test_intro_has_avatar_left_and_brand_right(self) -> None:
        from forgekit_console.tui import intro

        avatar = ("[rgb(200,200,205)]▀▀[/]", "[rgb(200,200,205)]▄▄[/]")
        lines = intro.intro_lines(repo="/repo", version="0.1.0", avatar_lines=avatar)
        joined = "\n".join(lines)
        self.assertIn("forgekit", joined)
        self.assertIn("v0.1.0", joined)
        self.assertIn("/repo", joined)
        self.assertIn("operator", joined)  # profile
        # avatar markup is on the left of the first row
        self.assertTrue(lines[0].startswith("[rgb(200,200,205)]▀▀"))

    def test_intro_uses_baked_avatar_by_default(self) -> None:
        from forgekit_console.tui import intro

        lines = intro.intro_lines(repo="/r", version="0.1.0")
        self.assertTrue(lines)
        self.assertIn("forgekit", "\n".join(lines))


class IssueLineTests(unittest.TestCase):
    def _summary(self, **over):
        base = dict(title="op", sections=(StatusSection("provider", ("ok",)),), alerts=())
        base.update(over)
        return StatusSummary(**base)

    def test_quiet_when_no_alerts(self) -> None:
        self.assertIn("ready", render.issue_line(self._summary()))

    def test_counts_issues_and_points_to_doctor(self) -> None:
        line = render.issue_line(self._summary(alerts=(Alert("warn", "settings missing"),)))
        self.assertIn("1 issue", line)
        self.assertIn("/doctor", line)

    def test_unavailable(self) -> None:
        self.assertIn("unavailable", render.issue_line(StatusSummary(title="x", available=False)))


if __name__ == "__main__":
    unittest.main()
