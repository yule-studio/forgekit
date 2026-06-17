"""Restricted design vault note (design WT5) — index/packet only, no raw dump.

Proves: the note carries restricted frontmatter (visibility/publish:false/design_source_id
/author/role/handoff/cssclasses/allowed_roles), references the raw path WITHOUT
embedding raw content, and states the raw-stays-private principle. Pure → CI.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.design import build_restricted_design_note
from forgekit_console.design import source as SRC


class RestrictedNoteTests(unittest.TestCase):
    def _note(self):
        return build_restricted_design_note(
            design_source_id="figma-backup",
            source_path=SRC.RAW_DESIGN_BACKUP_PATH,
            access_state="blocked",
            allowed_roles=SRC.DESIGN_ROLES,
            author_role="design-lead",
            packet_links=("[[design-ref-packet-home]]",),
            created_at="2026-06-18",
        )

    def test_frontmatter_is_restricted(self) -> None:
        note = self._note()
        for token in ("visibility: restricted", "publish: false",
                      "design_source_id: figma-backup", "source_flow: design-reference",
                      "agent_author: design-lead", "handoff_to: tech-lead"):
            self.assertIn(token, note, token)
        self.assertIn("allowed_roles: [ux-ui-designer", note)   # design roles listed

    def test_references_raw_path_without_embedding_content(self) -> None:
        note = self._note()
        self.assertIn(SRC.RAW_DESIGN_BACKUP_PATH, note)   # path referenced
        self.assertIn("raw 자산 아님", note)                # explicit
        self.assertIn("싣지 않음", note)                    # raw not dumped
        # no raw payload markers
        self.assertNotIn("fig_content", note)

    def test_uses_authorship_cssclass(self) -> None:
        from forgekit_console.vault.authorship import identity_for

        note = self._note()
        ident = identity_for("design-lead")
        self.assertIn(f"cssclasses: [{ident.cssclass}]", note)
        self.assertIn(f"[!{ident.callout}]", note)


if __name__ == "__main__":
    unittest.main()
