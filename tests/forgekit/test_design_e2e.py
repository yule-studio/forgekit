"""Design program end-to-end (design WT6) — restricted source → packet → discomfort
→ improvement packet → restricted vault note, all honest about blocked access. Pure.
"""

from __future__ import annotations

import json
import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import design as DS
from forgekit_console.design import discomfort as DC
from forgekit_console.design import packet as PK


class DesignEndToEndTests(unittest.TestCase):
    def test_full_design_flow_blocked_but_honest(self) -> None:
        # 1) restricted source registered, design-only, honest access state
        source = DS.register_design_backup()
        self.assertEqual(source.visibility, "restricted")
        self.assertFalse(source.role_allowed("pm"))
        self.assertTrue(source.role_allowed("ux-ui-designer"))

        # 2) packet — blocked source → honest scaffold (no fabricated design data)
        pkt = PK.build_reference_packet(source)
        if source.access_state != "ok":
            self.assertIn("design_source_blocked", pkt.note)
            self.assertEqual(pkt.screen_list, ())

        # 3) non-design role reads a projection, never raw
        fe_view = PK.project_for("fe", pkt).to_dict()
        self.assertIn("access_state", fe_view)
        self.assertNotIn("raw_source_path", fe_view)   # projection excludes the raw path

        # 4) UX discomfort → routed improvement packet
        d = DC.analyze_discomfort("버튼 간격이 좁아 누르기 불편", affected_flow="결제")
        kind, ipacket = DC.promote_to_packet(d)
        self.assertEqual(kind, DC.PACKET_FRONTEND_IMPL)
        self.assertTrue(ipacket.user_discomfort)

        # 5) restricted vault note — metadata only, raw not embedded
        note = DS.build_restricted_design_note(
            design_source_id=source.source_id, source_path=source.source_path,
            access_state=source.access_state, allowed_roles=source.allowed_roles,
            created_at="2026-06-18")
        self.assertIn("visibility: restricted", note)
        self.assertIn("raw 자산 아님", note)

    def test_evidence_serialisable(self) -> None:
        source = DS.register_design_backup()
        pkt = PK.build_reference_packet(source)
        s = json.dumps({"source": source.to_dict(), "packet": pkt.to_dict()}, ensure_ascii=False)
        self.assertIn("restricted", s)
        self.assertIn(source.access_state, s)


if __name__ == "__main__":
    unittest.main()
