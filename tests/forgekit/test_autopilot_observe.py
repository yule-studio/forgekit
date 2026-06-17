"""repo-autopilot WT3 — observe/classify + discomfort → improvement packets.

Proves: observe gathers repo-local + discovery + UI discomfort into findings with
discomfort framing, UI discomfort is honestly flagged figma_not_connected (no fake
read), and findings become user-value improvement packets. Pure/offline → CI.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import autopilot as AP
from forgekit_console.autopilot import observe as OB


class ObserveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "apps").mkdir()
        (self.tmp / "apps" / "x.py").write_text("# TODO a\n# TODO b\n# FIXME c\n", encoding="utf-8")

    def test_gathers_multiple_signal_sources(self) -> None:
        findings = OB.observe_repo(
            "forgekit", self.tmp,
            discovery_signals=["콘솔 도움말 부족"],
            ui_discomfort=["버튼 간격이 좁아 누르기 불편"])
        kinds = {f.kind for f in findings}
        self.assertIn("docs", kinds)         # repo-local TODO
        self.assertIn("discomfort", kinds)   # discovery + UI
        self.assertTrue(all(f.repo == "forgekit" for f in findings))

    def test_ui_discomfort_is_figma_not_connected_honest(self) -> None:
        findings = OB.observe_repo("forgekit", self.tmp,
                                   ui_discomfort=["spacing 불편"])
        ui = [f for f in findings if "spacing" in f.finding][0]
        self.assertIn("figma_not_connected", ui.evidence)  # honest, no fake read

    def test_ui_reference_connected_changes_evidence(self) -> None:
        ref = OB.UIReferenceState(OB.REF_CONNECTED, "live figma")
        findings = OB.observe_repo("forgekit", self.tmp, ui_discomfort=["spacing"],
                                   ui_reference=ref)
        ui = [f for f in findings if "spacing" in f.finding][0]
        self.assertIn("reference 비교", ui.evidence)

    def test_findings_become_user_value_packets(self) -> None:
        findings = OB.observe_repo("forgekit", self.tmp, ui_discomfort=["불편"])
        packets = OB.to_improvement_packets(findings)
        self.assertTrue(packets)
        self.assertTrue(all(p.user_discomfort for p in packets))
        self.assertTrue(all(p.source_origin.startswith("autopilot:") for p in packets))


if __name__ == "__main__":
    unittest.main()
