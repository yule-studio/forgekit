"""UI reference seam (WT5) — live/scaffold/missing honest, no fake figma read.

Proves: figma is NOT connected in this stage (honest figma_not_connected, no fake
read), an operator note is a usable scaffold, and UI discomfort becomes a reference-
aware packet either way. Pure → CI.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import uiref as U


class ReferenceStateTests(unittest.TestCase):
    def test_figma_not_connected_by_default(self) -> None:
        ref = U.figma_reference()
        self.assertEqual(ref.state, U.STATE_MISSING)
        self.assertEqual(ref.kind, U.FIGMA_NOT_CONNECTED)
        self.assertFalse(ref.usable)              # not usable — no fake read
        self.assertEqual(ref.frames, ())

    def test_figma_live_when_connected(self) -> None:
        ref = U.figma_reference(connected=True, frames=("Home", "Detail"))
        self.assertEqual(ref.state, U.STATE_LIVE)
        self.assertTrue(ref.usable)
        self.assertIn("Home", ref.frames)

    def test_operator_note_is_scaffold(self) -> None:
        ref = U.operator_note_reference("버튼 간격 12px, 카드 그림자 약하게")
        self.assertEqual(ref.state, U.STATE_SCAFFOLD)
        self.assertTrue(ref.usable)

    def test_connect_runbook_documents_no_fake_read(self) -> None:
        md = U.figma_connect_runbook()
        self.assertIn("MCP", md)
        self.assertIn(".fig 를 텍스트로 읽는 척하지 않", md)


class PacketTests(unittest.TestCase):
    def test_packet_reference_aware_when_usable(self) -> None:
        ref = U.figma_reference(connected=True, frames=("Home",))
        pkt = U.ui_discomfort_to_packet("버튼 간격이 좁다", ref)
        self.assertEqual(pkt.affected_area, "ui")
        self.assertIn("reference", pkt.why_it_matters)

    def test_packet_honest_when_missing(self) -> None:
        pkt = U.ui_discomfort_to_packet("버튼 간격이 좁다")  # no reference → missing
        self.assertIn("figma_not_connected", pkt.why_it_matters)
        self.assertEqual(pkt.source_origin, "ui-reference")


if __name__ == "__main__":
    unittest.main()
