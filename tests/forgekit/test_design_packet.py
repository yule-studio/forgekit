"""DesignReferencePacket + projections (design WT3) — raw stays restricted.

Proves: a packet built from a blocked source is honest scaffolding (no fake design
data), the raw path is metadata only (no raw content embedded), and non-design roles
get a projection subset — never the raw source. Pure → CI.
"""

from __future__ import annotations

import json
import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import design as DS
from forgekit_console.design import packet as PK


class PacketTests(unittest.TestCase):
    def test_blocked_source_yields_honest_scaffold(self) -> None:
        src = DS.register_design_backup()           # live → blocked (TCC)
        p = PK.build_reference_packet(src)
        self.assertEqual(p.access_state, src.access_state)
        self.assertFalse(p.publishable)
        if src.access_state != "ok":
            self.assertIn("design_source_blocked", p.note)   # honest, no fake data
            self.assertEqual(p.screen_list, ())              # nothing fabricated
            self.assertTrue(p.open_questions)

    def test_raw_path_is_metadata_not_content(self) -> None:
        src = DS.register_design_backup()
        p = PK.build_reference_packet(src)
        d = p.to_dict()
        # the path is referenced as metadata; no raw .fig payload field exists
        self.assertEqual(d["raw_source_path"], src.source_path)
        self.assertNotIn("raw_bytes", d)
        self.assertNotIn("fig_content", d)


class ProjectionTests(unittest.TestCase):
    def _packet(self):
        return PK.DesignReferencePacket(
            "figma-backup", access_state="ok",
            screen_list=("Home", "Detail"), component_inventory=("Button", "Card"),
            spacing_scale=("4", "8", "12"), color_tokens=("brand", "bg"),
            implementation_notes=("8px grid",), ux_risks=("작은 터치 타깃",),
            do_not_change=("로고 비율",), interaction_notes=("탭 전환",),
            open_questions=("다크모드?",))

    def test_non_design_roles_get_projection_subset(self) -> None:
        p = self._packet()
        fe = PK.project_for("fe", p).to_dict()
        self.assertIn("component_inventory", fe)
        self.assertIn("implementation_notes", fe)
        # FE projection does NOT expose ux_risks/open_questions (PM's view)
        self.assertNotIn("open_questions", fe)
        pm = PK.project_for("pm", p).to_dict()
        self.assertIn("ux_risks", pm)
        self.assertNotIn("component_inventory", pm)   # PM doesn't need the inventory

    def test_each_design_specialist_has_a_projection(self) -> None:
        p = self._packet()
        self.assertIn("layout_rules", PK.project_for("ux-ui-designer", p).to_dict())
        self.assertIn("typography_rules", PK.project_for("design-systems-designer", p).to_dict())
        self.assertIn("color_tokens", PK.project_for("illustration-brand-designer", p).to_dict())

    def test_projection_carries_access_state(self) -> None:
        p = self._packet()
        for role in ("fe", "pm", "qa", "ux-ui-designer"):
            self.assertIn("access_state", PK.project_for(role, p).to_dict())


if __name__ == "__main__":
    unittest.main()
