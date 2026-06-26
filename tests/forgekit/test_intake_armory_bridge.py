"""Intake → Armory bridge — the two candidate models are a pipeline, not duplicates.

Proves the console bridge maps a discovery ``ExternalCandidate`` onto the EXISTING
``armory.ArmoryCandidate`` (no parallel model), and that a freshly-discovered
candidate is honestly REJECTED by ``armory.promote_candidate`` until a curator fills
the selection contract — then accepted. Pure / offline.
"""

from __future__ import annotations

import dataclasses
import unittest

from tests.forgekit import _SRC  # noqa: F401  (puts console + package src on path)

from forgekit_console.intake_bridge import propose_to_armory, to_armory_candidate
from nexus.intake import candidate as K
from armory.models import KIND_MCP, KIND_PLUGIN, KIND_SKILL, KIND_TOOL


def _cand(**over) -> K.ExternalCandidate:
    base = dict(
        name="ponytail", source="github", repo_url="https://github.com/example/ponytail",
        provider_affinity=K.AFFINITY_NEUTRAL, capability_class=K.CAP_CODE_REVIEW,
        install_shape=K.SHAPE_CLI, trust_risk=K.RISK_LOW,
        maintenance_signal=K.MAINT_ACTIVE, license="MIT", why_it_matters="simplicity review")
    base.update(over)
    return K.ExternalCandidate(**base)


class BridgeMappingTests(unittest.TestCase):
    def test_install_shape_maps_to_armory_kind(self) -> None:
        self.assertEqual(to_armory_candidate(_cand(install_shape=K.SHAPE_SKILL)).kind, KIND_SKILL)
        self.assertEqual(to_armory_candidate(_cand(install_shape=K.SHAPE_PLUGIN)).kind, KIND_PLUGIN)
        self.assertEqual(to_armory_candidate(_cand(install_shape=K.SHAPE_HOOK)).kind, KIND_PLUGIN)
        self.assertEqual(to_armory_candidate(_cand(install_shape=K.SHAPE_MCP)).kind, KIND_MCP)
        self.assertEqual(to_armory_candidate(_cand(install_shape=K.SHAPE_CLI)).kind, KIND_TOOL)
        self.assertEqual(to_armory_candidate(_cand(install_shape=K.SHAPE_LIB)).kind, KIND_TOOL)

    def test_provenance_carried(self) -> None:
        ac = to_armory_candidate(_cand())
        self.assertEqual(ac.source, "discovery-intake")
        self.assertEqual(ac.source_ref, "https://github.com/example/ponytail")
        self.assertEqual(ac.summary, "simplicity review")
        self.assertEqual(ac.capability_note, K.CAP_CODE_REVIEW)

    def test_selection_contract_left_empty(self) -> None:
        # discovery cannot fabricate the catalog contract — it stays empty (honest)
        ac = to_armory_candidate(_cand())
        self.assertEqual(ac.signals, ())
        self.assertEqual(ac.when_to_use, ())
        self.assertEqual(ac.unsafe_boundary, ())


class BridgeGateTests(unittest.TestCase):
    def test_fresh_candidate_rejected_until_curated(self) -> None:
        result = propose_to_armory(_cand())
        self.assertFalse(result.accepted)
        joined = " ".join(result.reasons)
        self.assertIn("signals", joined)
        self.assertIn("when_to_use", joined)
        self.assertIn("unsafe_boundary", joined)

    def test_enriched_candidate_promotes_into_catalog(self) -> None:
        # operator/curator fills the selection contract on the bridged draft → accepted.
        draft = to_armory_candidate(_cand(install_shape=K.SHAPE_SKILL))  # skill: no attach needed
        enriched = dataclasses.replace(
            draft,
            signals=("simplify", "over-engineering", "review"),
            when_to_use=("새 모듈/계층 추가 전 단순성 검토",),
            unsafe_boundary=("자동 설치/실행 금지 — 검토 전용",),
            verification=("ponytail --check",),
        )
        from armory import promote_candidate
        promoted = promote_candidate(enriched)
        self.assertTrue(promoted.accepted, promoted.reasons)
        self.assertEqual(promoted.spec.kind, KIND_SKILL)
        # provenance from discovery intake is preserved in the evidence trail
        self.assertTrue(any("discovery-intake" in e for e in promoted.evidence))


if __name__ == "__main__":
    unittest.main()
