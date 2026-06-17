"""Restricted design source gate (design WT1) — design-only, honest blocked, no fake-read.

Proves: the desktop Figma backup is registered restricted (design roles only, read-only,
not publishable), a non-design role is refused (projection only), a TCC-blocked path is
``design_source_blocked`` (not fabricated), and there's a runbook. Pure → CI.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import design as DS
from forgekit_console.design import source as SRC


class SourceContractTests(unittest.TestCase):
    def test_registered_restricted_design_only_readonly(self) -> None:
        s = DS.register_design_backup()
        self.assertEqual(s.source_path, SRC.RAW_DESIGN_BACKUP_PATH)
        self.assertEqual(s.visibility, SRC.VISIBILITY_RESTRICTED)
        self.assertEqual(s.ingest_mode, SRC.INGEST_READ_ONLY)
        self.assertFalse(s.publishable)
        for r in ("ux-ui-designer", "design-systems-designer", "illustration-brand-designer"):
            self.assertIn(r, s.allowed_roles)
        self.assertNotIn("pm", s.allowed_roles)
        self.assertNotIn("fe", s.allowed_roles)

    def test_non_design_role_refused_uses_projection(self) -> None:
        s = DS.register_design_backup()
        for role in ("pm", "fe", "qa", "tech-lead", "be"):
            allowed, reason = DS.access_request(s, role)
            self.assertFalse(allowed)
            self.assertIn("projection", reason)

    def test_probe_states_honest(self) -> None:
        # missing path → missing
        self.assertEqual(SRC.probe_access("/no/such/forgekit-design-xyz"), SRC.ACCESS_MISSING)
        # a readable temp dir → ok
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        self.assertEqual(SRC.probe_access(str(tmp)), SRC.ACCESS_OK)

    def test_blocked_source_design_role_still_refused_honestly(self) -> None:
        # simulate a TCC-blocked source state
        s = SRC.RestrictedDesignSource("figma-backup", SRC.RAW_DESIGN_BACKUP_PATH,
                                       access_state=SRC.ACCESS_BLOCKED)
        allowed, reason = DS.access_request(s, "ux-ui-designer")
        self.assertFalse(allowed)                     # design role, but blocked
        self.assertIn("design_source_blocked", reason)  # honest, not fake-read

    def test_runbook_present_and_no_repo_copy(self) -> None:
        md = DS.access_runbook()
        self.assertIn("design_source_blocked", md)
        self.assertIn("복사 금지", md)
        # the module exposes NO function that reads .fig contents (structural)
        self.assertFalse(hasattr(SRC, "read_fig"))


if __name__ == "__main__":
    unittest.main()
