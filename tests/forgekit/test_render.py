"""forgekit console render helpers — help sections, mode badge, palette (pure)."""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.commands.registry import load_agents, load_commands
from forgekit_console.models import MODE_OPERATOR, agent_mode
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


if __name__ == "__main__":
    unittest.main()
