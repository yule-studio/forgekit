"""Vault authorship (WT5) — author/role/handoff/phase metadata + real color strategy.

Proves a note records WHO wrote it and the handoff phase, that the colour strategy is
Obsidian-real (cssclasses + a generated snippet + typed callout — not a fake "colour
the text"), and that a WT2 handoff becomes an authored note. Pure → bare CI.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import vault
from forgekit_console.vault import authorship as A


class AuthorshipMetaTests(unittest.TestCase):
    def test_each_agent_has_distinct_identity(self) -> None:
        css = {i.cssclass for i in A.AGENT_IDENTITIES.values()}
        colors = {i.color for i in A.AGENT_IDENTITIES.values()}
        self.assertEqual(len(css), len(A.AGENT_IDENTITIES))      # unique css classes
        self.assertGreaterEqual(len(colors), 6)                  # visually distinct
        for key in ("product-agent", "tech-lead", "fe", "be", "devops", "qa", "security"):
            self.assertIn(key, A.AGENT_IDENTITIES)

    def test_frontmatter_carries_authorship_and_handoff(self) -> None:
        note = vault.build_authored_note(
            "product-agent", "영상 업로드 패킷", "본문",
            handoff_from="operator", handoff_to="gateway", phase="intake",
            source_flow="pm-intake", created_at="2026-06-17",
        )
        for token in ("agent_author: product-agent", "agent_role: Product (PM)",
                      "handoff_from: operator", "handoff_to: gateway",
                      "phase: intake", "source_flow: pm-intake",
                      "cssclasses: [fk-pm]", "created_at: 2026-06-17"):
            self.assertIn(token, note, token)
        # typed callout marker (themable) names the author + phase
        self.assertIn("> [!fk-pm] Product (PM)", note)
        self.assertIn("phase: intake", note)

    def test_color_strategy_is_obsidian_real_not_fake(self) -> None:
        snippet = A.vault_css_snippet()
        # the colour comes from a cssclass snippet the user adds — real Obsidian
        self.assertIn(".fk-pm", snippet)
        self.assertIn("data-callout", snippet)   # callout theming too
        self.assertIn(A.AGENT_IDENTITIES["product-agent"].color, snippet)


class HandoffNoteTests(unittest.TestCase):
    def test_handoff_becomes_authored_note(self) -> None:
        from forgekit_console.handoff import run_handoff

        ho = run_handoff("영상 업로드 기능을 운영까지 완성해줘", project="bkurs")
        note = vault.note_from_handoff(ho, created_at="2026-06-17")
        self.assertIn("agent_author: tech-lead", note)
        self.assertIn("phase: tech-lead", note)
        self.assertIn("source_flow: pm-intake-handoff", note)
        self.assertIn("handoff trace", note)
        self.assertIn("BLOCKED", note)  # blocked area shown honestly in the note

    def test_write_note_round_trips(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        note = vault.build_authored_note("be", "백엔드 작업", "구현 메모", created_at="2026-06-17")
        path = vault.write_note(note, tmp, "10-projects/bkurs/be-note.md")
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        self.assertIn("agent_author: be", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
