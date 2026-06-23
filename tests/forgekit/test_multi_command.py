"""Multi-command submit — close the "하나만 인식" gap.

A submit buffer that is a stack of `/...` lines runs EACH command in order (not just
the first token of the whole buffer). Pure `split_command_lines` tests + a pilot test
that a two-command submit appends two command blocks to the copy/transcript store.
Free text and single commands are byte-for-byte untouched.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.commands.parser import split_command_lines

_TEXTUAL = importlib.util.find_spec("textual") is not None


class SplitCommandLinesTests(unittest.TestCase):
    def test_single_command_untouched(self) -> None:
        self.assertEqual(split_command_lines("/goal list"), ("/goal list",))

    def test_free_text_untouched(self) -> None:
        self.assertEqual(split_command_lines("hello world"), ("hello world",))

    def test_multiline_free_text_not_split(self) -> None:
        # a paragraph whose lines are not all slash commands is one free-text submit.
        raw = "first line\n/looks-like-cmd but is prose"
        self.assertEqual(split_command_lines(raw), (raw,))

    def test_multiline_free_text_with_no_slash_lines(self) -> None:
        raw = "line one\nline two\nline three"
        self.assertEqual(split_command_lines(raw), (raw,))

    def test_stack_of_commands_splits(self) -> None:
        out = split_command_lines("/goal show 3\n/goal awaiting")
        self.assertEqual(out, ("/goal show 3", "/goal awaiting"))

    def test_blank_lines_between_commands_dropped(self) -> None:
        out = split_command_lines("/whoami\n\n/status\n")
        self.assertEqual(out, ("/whoami", "/status"))

    def test_three_commands_preserve_order(self) -> None:
        out = split_command_lines("/a\n/b 1 2\n/c")
        self.assertEqual(out, ("/a", "/b 1 2", "/c"))

    def test_one_slash_line_among_prose_not_split(self) -> None:
        # the conservative rule: ALL non-empty lines must be slash lines to split.
        raw = "/goal list\nplease also explain"
        self.assertEqual(split_command_lines(raw), (raw,))

    def test_empty_is_passthrough(self) -> None:
        self.assertEqual(split_command_lines(""), ("",))


@unittest.skipUnless(_TEXTUAL, "textual not installed")
class MultiCommandPilotTests(unittest.IsolatedAsyncioTestCase):
    def _app(self):
        from forgekit_console.commands.registry import load_agents, load_commands
        from forgekit_console.commands.router import ConsoleContext
        from forgekit_console.tui.app import ForgekitConsoleApp
        ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
        return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx)

    async def test_two_command_submit_runs_both(self) -> None:
        app = self._app()
        async with app.run_test(size=(90, 28)) as pilot:
            await pilot.pause()
            app.query_one("#prompt").value = "/whoami\n/whoami"
            await pilot.press("enter")
            await pilot.pause()
            users = [b for b in app._store.blocks if b.role == "user"]
            # both commands recorded — not collapsed into one garbled command.
            self.assertEqual(sum(1 for b in users if b.text.strip() == "/whoami"), 2)

    async def test_single_command_still_one(self) -> None:
        app = self._app()
        async with app.run_test(size=(90, 28)) as pilot:
            await pilot.pause()
            app.query_one("#prompt").value = "/whoami"
            await pilot.press("enter")
            await pilot.pause()
            users = [b for b in app._store.blocks if b.role == "user"]
            self.assertEqual(sum(1 for b in users if b.text.strip() == "/whoami"), 1)


if __name__ == "__main__":
    unittest.main()
