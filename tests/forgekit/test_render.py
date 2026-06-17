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
        from forgekit_console.tui import theme

        pill = render.status_pill(self._summary())
        self.assertIn(theme.SUCCESS, pill)  # brand success (green) dot
        self.assertIn("provider", pill)
        self.assertNotIn("\n", pill)

    def test_warn_dot(self) -> None:
        from forgekit_console.tui import theme

        pill = render.status_pill(self._summary(alerts=(Alert("warn", "x"),)))
        self.assertIn(theme.WARNING, pill)  # brand warning (amber) dot

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


class HelpPanelDocumentTests(unittest.TestCase):
    """Help is its own VIEW document for the active tab (not a transcript block)."""

    def _secs(self):
        return render.help_sections(load_commands(), load_agents())

    def test_tab_order_and_default_is_general(self) -> None:
        titles = [s.title for s in self._secs()]
        self.assertEqual(titles, ["Help", "General", "Commands", "Agents"])
        self.assertEqual(self._secs()[render.default_help_tab(self._secs())].title, "General")

    def test_document_shows_tab_strip_and_active_body_only(self) -> None:
        secs = self._secs()
        general = render.default_help_tab(secs)
        joined = "\n".join(render.help_panel_document(secs, general))
        # the header is the cyan→magenta wordmark + "help" (brand mark, not a
        # plain "forgekit help" literal)
        self.assertIn("forge", joined)
        self.assertIn("help", joined)
        self.assertIn("Esc", joined)
        # all four tab labels appear in the strip
        for title in ("Help", "General", "Commands", "Agents"):
            self.assertIn(title, joined)
        # active = General → its body (단축키) shows, Commands body (/quit list) does not
        self.assertIn("단축키", joined)
        self.assertNotIn("/quit", joined)

    def test_document_has_esc_close_hint(self) -> None:
        # The help view (composer hidden in help mode) gives the Esc/Tab guidance.
        secs = self._secs()
        joined = "\n".join(render.help_panel_document(secs, render.default_help_tab(secs)))
        self.assertIn("Esc", joined)
        self.assertIn("Tab", joined)
        self.assertNotIn("입력창", joined)  # no stale "input stays open" note

    def test_commands_tab_lists_exit_alias(self) -> None:
        secs = self._secs()
        cmd_idx = next(i for i, s in enumerate(secs) if s.title == "Commands")
        joined = "\n".join(render.help_panel_document(secs, cmd_idx))
        self.assertIn("/exit", joined)
        self.assertIn("/quit", joined)

    def test_switching_tab_does_not_accumulate(self) -> None:
        # Each tab renders its OWN document — General body absent on Commands tab.
        secs = self._secs()
        general = render.default_help_tab(secs)
        cmd_idx = next(i for i, s in enumerate(secs) if s.title == "Commands")
        gen_doc = "\n".join(render.help_panel_document(secs, general))
        cmd_doc = "\n".join(render.help_panel_document(secs, cmd_idx))
        self.assertIn("단축키", gen_doc)
        self.assertNotIn("단축키", cmd_doc)


class IntroMetaTests(unittest.TestCase):
    def test_meta_has_brand_version_provider_profile_repo(self) -> None:
        lines = render.intro_meta_lines(
            repo="/repo", version="0.1.0", profile="operator", provider="claude"
        )
        joined = "\n".join(lines)
        # brand is the cyan→magenta wordmark ("forge" + "kit" split spans)
        self.assertIn("forge", joined)
        self.assertIn("kit", joined)
        self.assertIn("v0.1.0", joined)
        self.assertIn("/repo", joined)
        self.assertIn("operator", joined)  # profile
        self.assertIn("claude", joined)    # provider

    def test_meta_defaults(self) -> None:
        lines = render.intro_meta_lines(repo="/r", version="0.1.0")
        self.assertTrue(lines)
        self.assertIn("forge", "\n".join(lines))


class _Diag:
    """Minimal RendererDiagnostics-shaped stub for the debug-line builder."""

    def __init__(self, *, av_backend, av_policy, br_backend, br_policy,
                 cap, lib_ok, lib_backend="none", lib_reason=""):
        self.avatar_backend = av_backend
        self.avatar_policy = av_policy
        self.brand_backend = br_backend
        self.brand_policy = br_policy
        self.capability_reason = cap
        self.lib_ok = lib_ok
        self.lib_backend = lib_backend
        self.lib_reason = lib_reason


class RendererDebugLineTests(unittest.TestCase):
    def test_true_raster_shows_policy(self) -> None:
        line = render.renderer_debug_line(
            _Diag(av_backend="sixel", av_policy="true-raster",
                  br_backend="sixel", br_policy="true-raster",
                  cap="iterm2 inline images", lib_ok=True, lib_backend="sixel")
        )
        self.assertIn("avatar=sixel (true-raster)", line)
        self.assertIn("brand=sixel (true-raster)", line)
        self.assertIn("cap=iterm2 inline images", line)
        self.assertIn("lib=ok:sixel", line)

    def test_managed_fallback_not_called_raster(self) -> None:
        # importable but resolved to a cell fallback → managed-fallback, NOT raster
        line = render.renderer_debug_line(
            _Diag(av_backend="avatar-mark", av_policy="managed-fallback",
                  br_backend="brand-text", br_policy="managed-fallback",
                  cap="term_program=vscode", lib_ok=True, lib_backend="halfcell")
        )
        self.assertIn("avatar=avatar-mark (managed-fallback)", line)
        self.assertIn("brand=brand-text (managed-fallback)", line)
        self.assertIn("cap=term_program=vscode", line)
        self.assertIn("lib=ok:halfcell", line)  # importable, but halfcell ≠ raster
        self.assertNotIn("true-raster", line)

    def test_lib_import_failure_shown(self) -> None:
        line = render.renderer_debug_line(
            _Diag(av_backend="avatar-mark", av_policy="managed-fallback",
                  br_backend="brand-text", br_policy="managed-fallback",
                  cap="term_program=vscode", lib_ok=False, lib_backend="none",
                  lib_reason="ImportError: cannot import name 'NoneType'")
        )
        self.assertIn("lib=✗", line)
        self.assertIn("ImportError", line)

    def test_long_lib_reason_truncated(self) -> None:
        line = render.renderer_debug_line(
            _Diag(av_backend="text-mark", av_policy="hard-fallback",
                  br_backend="brand-text", br_policy="managed-fallback",
                  cap="x", lib_ok=False, lib_backend="none", lib_reason="X" * 200)
        )
        self.assertIn("…", line)
        self.assertLess(len(line), 220)


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
