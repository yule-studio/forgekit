"""Design role split (design WT2) — 3 specialists + lead, non-overlapping, FE boundary.

Proves: the four design roles exist with distinct, non-overlapping ownership, the
design-lead orchestrates, and none of them owns frontend implementation. Pure → CI.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.design import roles as R


class RoleSplitTests(unittest.TestCase):
    def test_four_roles_present(self) -> None:
        for rid in (R.ROLE_UX_UI, R.ROLE_DESIGN_SYSTEMS, R.ROLE_ILLUSTRATION_BRAND,
                    R.ROLE_DESIGN_LEAD):
            self.assertIn(rid, R.DESIGN_ROLES)

    def test_ownership_does_not_overlap(self) -> None:
        self.assertEqual(R.owns_overlap(), ())   # responsibilities don't collide

    def test_no_design_role_owns_frontend_impl(self) -> None:
        for r in R.DESIGN_ROLES.values():
            self.assertNotIn("frontend-구현", r.owns)
            # FE implementation is explicitly disclaimed where relevant
        self.assertIn("frontend", R.FE_BOUNDARY)

    def test_specialists_distinct_domains(self) -> None:
        ux = R.role(R.ROLE_UX_UI)
        ds = R.role(R.ROLE_DESIGN_SYSTEMS)
        ib = R.role(R.ROLE_ILLUSTRATION_BRAND)
        self.assertIn("flow", ux.owns)
        self.assertIn("tokens", ds.owns)
        self.assertIn("brand", ib.owns)
        # cross-claims are disclaimed
        self.assertIn("component-library", ux.not_owns)
        self.assertIn("brand-illustration", ds.not_owns)

    def test_design_lead_orchestrates(self) -> None:
        lead = R.role(R.ROLE_DESIGN_LEAD)
        self.assertIn("synthesis", lead.owns)
        self.assertTrue(any("핸드오프" in x for x in lead.responsibilities))


if __name__ == "__main__":
    unittest.main()
