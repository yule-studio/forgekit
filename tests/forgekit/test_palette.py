"""forgekit console palette + autocomplete state machine (pure)."""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.commands import palette as P
from forgekit_console.commands.registry import load_commands

_CMDS = load_commands()


class RefilterTests(unittest.TestCase):
    def test_non_slash_is_closed(self) -> None:
        self.assertFalse(P.refilter("hello", _CMDS).is_open)

    def test_bare_slash_lists_all(self) -> None:
        s = P.refilter("/", _CMDS)
        self.assertTrue(s.is_open)
        self.assertEqual(len(s.matches), len(_CMDS))
        self.assertEqual(s.index, -1)

    def test_prefix_filters(self) -> None:
        s = P.refilter("/hel", _CMDS)
        self.assertEqual([c.name for c in s.matches], ["help"])

    def test_prefix_filters_multiple(self) -> None:
        # /he now matches both help and hephaistos (the skill-forge surface)
        s = P.refilter("/he", _CMDS)
        self.assertEqual([c.name for c in s.matches], ["help", "hephaistos"])

    def test_substring_fallback_when_no_prefix_match(self) -> None:
        # a meaningful word that no command STARTS with previously dead-ended to an empty
        # palette; it now reaches the command that CONTAINS it (prefix-first, then fallback).
        self.assertIn("self-improve", {c.name for c in P.refilter("/improve", _CMDS).matches})
        self.assertIn("red-blue", {c.name for c in P.refilter("/blue", _CMDS).matches})
        self.assertIn("ops-observer", {c.name for c in P.refilter("/observer", _CMDS).matches})

    def test_prefix_wins_over_substring(self) -> None:
        # when a prefix match exists, the fallback must NOT widen the set (no regression):
        # `/p` stays exactly the p-prefixed commands even though many names contain 'p'.
        names = [c.name for c in P.refilter("/p", _CMDS).matches]
        self.assertTrue(all(n.startswith("p") for n in names))
        self.assertNotIn("copy", names)        # contains 'p' but is NOT surfaced (prefix wins)
        self.assertNotIn("self-improve", names)

    def test_substring_fallback_still_empty_for_nonsense(self) -> None:
        self.assertEqual(P.refilter("/zzz", _CMDS).matches, ())


class CycleTests(unittest.TestCase):
    def test_tab_from_empty_selects_first(self) -> None:
        s = P.cycle(P.refilter("/p", _CMDS), 1)
        self.assertEqual(P.selected(s).name, "paste")   # /p → paste, provider, pm-agent, planning-agent

    def test_tab_cycles_forward_and_wraps(self) -> None:
        s = P.refilter("/p", _CMDS)  # paste, provider, pm-agent, planning-agent
        s = P.cycle(s, 1)
        self.assertEqual(P.selected(s).name, "paste")
        s = P.cycle(s, 1)
        self.assertEqual(P.selected(s).name, "provider")
        s = P.cycle(s, 1)
        self.assertEqual(P.selected(s).name, "pm-agent")
        s = P.cycle(s, 1)
        self.assertEqual(P.selected(s).name, "planning-agent")
        s = P.cycle(s, 1)  # wrap
        self.assertEqual(P.selected(s).name, "paste")

    def test_shift_tab_from_empty_selects_last(self) -> None:
        s = P.cycle(P.refilter("/p", _CMDS), -1)
        self.assertEqual(P.selected(s).name, "planning-agent")

    def test_cycle_on_closed_is_noop(self) -> None:
        self.assertEqual(P.cycle(P.CLOSED, 1), P.CLOSED)


class CompletionTests(unittest.TestCase):
    def test_tab_completes_he_to_help(self) -> None:
        s = P.cycle(P.refilter("/he", _CMDS), 1)
        self.assertEqual(P.completion_text(s), "/help ")

    def test_completion_uses_first_when_no_selection(self) -> None:
        s = P.refilter("/he", _CMDS)  # index -1
        self.assertEqual(P.completion_text(s), "/help ")

    def test_no_matches_no_completion(self) -> None:
        s = P.refilter("/zzz", _CMDS)
        self.assertIsNone(P.completion_text(s))


class CloseTests(unittest.TestCase):
    def test_close(self) -> None:
        self.assertFalse(P.close().is_open)


if __name__ == "__main__":
    unittest.main()
