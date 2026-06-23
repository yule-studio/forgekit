"""Discovery adoption-efficiency review + GeekNews collector.

Proves the wave's spine: a collected candidate is classified (6 classes), run through an
8-field efficiency review that DEFAULTS to collect-first (no fake adoption), emits a REAL
3-axis consult, and only reaches adopt-now via an explicit operator decision — which then
bridges to the armory intake gate where *adopted* (a validated catalog spec) stays distinct
from *equipped* (catalog registration). Also covers the new free GeekNews radar source.

Network-free + deterministic: empty collector queries + fake fetcher + isolated state.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import discovery as D
from forgekit_console.discovery import models as M
from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.router import ConsoleContext, route


def _empty_fetcher(_url: str) -> str:
    return "{}"


_OFFLINE_CFG = {"discovery": {"hackernews_query": "", "subreddits": [], "github_query": "",
                              "geeknews": False}}


def _rmtree(p: Path) -> None:
    __import__("shutil").rmtree(p, ignore_errors=True)


_FULL_CONTRACT = {
    "kind": "tool", "category": "devtools",
    "summary": "콘솔 상태를 한눈에 보여주는 TUI 대시보드 도구",
    "signals": ("tui", "dashboard"), "when_to_use": ("운영 상태 모니터링",),
    "unsafe_boundary": ("프로덕션 자동 변경 금지",),
    "capability_note": "터미널에서 런타임 상태를 시각화하는 능력",
    "install_requirements": ("pip install x",), "commands": ("x --status",),
    "verification": ("x --version",),
}


class DiscoveryAdoptionTests(unittest.TestCase):
    # --- classification -------------------------------------------------------
    def test_classification_six_classes(self) -> None:
        cases = {
            "새 오픈소스 CLI 도구": D.CLASS_TOOL,
            "경쟁사 X 가 신기능": D.CLASS_COMPETITOR,
            "이 라이선스는 상용 제약이 있음": D.CLASS_RISK,
            "github.com 예제 구현 패턴": D.CLASS_IMPL_REF,
            "노트 동기화 아이디어 feature": D.CLASS_IDEA,
            "그냥 관측된 무언가": D.CLASS_SIGNAL_ONLY,
        }
        for text, expected in cases.items():
            self.assertEqual(D.classify_candidate(text), expected, text)

    # --- efficiency review defaults (no fake adoption) ------------------------
    def _brief(self, title="TUI 대시보드 도구", problem="콘솔 상태 도구가 필요"):
        return M.IdeaBrief(title=title, problem=problem,
                           differentiation=M.DifferentiationHypothesis("더 단순한 TUI"), score=3.0)

    def test_review_defaults_collect_first_never_adopt(self) -> None:
        r = D.build_adoption_review(self._brief())
        self.assertEqual(r.disposition, D.COLLECT_FIRST)
        self.assertFalse(r.reviewed)
        # all 8 fields populated (not hollow)
        for f in (r.current_pain, r.expected_benefit, r.overlap, r.operational_cost,
                  r.maintenance_risk, r.provider_runtime_fit,
                  r.governance_security_impact, r.disposition_rationale):
            self.assertTrue(f.strip())

    def test_risk_candidate_holds(self) -> None:
        r = D.build_adoption_review(self._brief(title="유료 API 비용 제약", problem="비용 제약 추적"))
        self.assertEqual(r.classification, D.CLASS_RISK)
        self.assertEqual(r.disposition, D.HOLD)

    def test_overlap_with_existing_capability_holds(self) -> None:
        r = D.build_adoption_review(self._brief(title="provider routing 도구",
                                                problem="provider routing 도구 필요"),
                                    existing_signals=("provider routing",))
        self.assertEqual(r.disposition, D.HOLD)
        self.assertIn("겹침", r.overlap)

    def test_consult_is_real_3_axis(self) -> None:
        from forgekit_runtime.decision_lane import ConsultNote, validate_consult

        r = D.build_adoption_review(self._brief())
        c = r.consult
        self.assertIn("product-manager", c["to_roles"])
        self.assertIn("tech-lead", c["to_roles"])
        self.assertEqual(len(c["to_roles"]), 3)            # PM + tech-lead + specialist
        note = ConsultNote(consult_id=c["consult_id"], topic=c["topic"], by_role=c["by_role"],
                           to_roles=tuple(c["to_roles"]), question=c["question"])
        self.assertEqual(validate_consult(note), ())       # a real, valid consult

    # --- resolve + armory bridge (adopted ≠ equipped) -------------------------
    def test_bridge_none_until_adopt_now(self) -> None:
        r = D.build_adoption_review(self._brief())
        self.assertIsNone(D.adoption_to_armory_candidate(r, contract=_FULL_CONTRACT))

    def test_resolve_adopt_then_bridge_adopted_not_equipped(self) -> None:
        from armory import catalog

        catalog.clear_overlay()
        r = D.build_adoption_review(self._brief())
        decided = D.resolve_review(r, adopt=True, note="3축 검토 통과")
        self.assertEqual(decided.disposition, D.ADOPT_NOW)
        self.assertTrue(decided.reviewed)
        result = D.adoption_to_armory_candidate(decided, contract=_FULL_CONTRACT)
        self.assertTrue(result.accepted)                   # adopted = validated spec
        self.assertIsNotNone(result.spec)
        # adopted ≠ equipped: the bridge does NOT register it into the catalog overlay
        self.assertNotIn(result.spec.id, {s.id for s in catalog.promoted_skills()})

    def test_bridge_rejects_incomplete_contract(self) -> None:
        r = D.build_adoption_review(self._brief())
        decided = D.resolve_review(r, adopt=True)
        result = D.adoption_to_armory_candidate(decided, contract={})  # raw idea, no contract
        self.assertFalse(result.accepted)                  # honest: not adopted, lists gaps
        self.assertTrue(result.reasons)

    def test_risk_cannot_be_adopted(self) -> None:
        r = D.build_adoption_review(self._brief(title="보안 취약점 제약", problem="취약점 추적"))
        decided = D.resolve_review(r, adopt=True, note="x")
        self.assertEqual(decided.disposition, D.HOLD)      # risk never flips to adopt-now

    # --- persistence (vault evidence note) ------------------------------------
    def test_persist_review_writes_authored_note(self) -> None:
        vault = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rmtree(vault))
        r = D.build_adoption_review(self._brief())
        path = D.persist_adoption_review(r, vault)
        self.assertIsNotNone(path)
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("도입 효율 검토 (8축)", text)
        self.assertIn("adoption-review", text)             # kind/tag persisted
        self.assertIn(D.COLLECT_FIRST, text)               # disposition persisted
        self.assertIn("agent_author:", text)               # authorship persisted

    def test_persist_review_no_vault_none(self) -> None:
        self.assertIsNone(D.persist_adoption_review(D.build_adoption_review(self._brief()), None))

    # --- GeekNews collector ---------------------------------------------------
    def test_geeknews_collector_parses_rss(self) -> None:
        from nexus.sources import geeknews_collector

        rss = ("<?xml version='1.0'?><rss><channel>"
               "<item><title>GeekNews 글 A</title><link>https://news.hada.io/topic?id=1</link></item>"
               "</channel></rss>")
        items = geeknews_collector(fetcher=lambda _u: rss).collect(limit=5)
        self.assertEqual([i.source_id for i in items], ["geeknews"])
        self.assertEqual(items[0].title, "GeekNews 글 A")

    def test_geeknews_in_default_registry_toggle(self) -> None:
        from nexus.sources import default_registry, registry_from_config

        on = [c.spec.id for c in default_registry(".", fetcher=_empty_fetcher).live()]
        self.assertIn("geeknews", on)
        off = [c.spec.id for c in registry_from_config(
            ".", {"discovery": {"geeknews": False}}, fetcher=_empty_fetcher).live()]
        self.assertNotIn("geeknews", off)

    # --- router surfaces ------------------------------------------------------
    def _ctx(self, tmp, home, *, config=None):
        cfg = dict(_OFFLINE_CFG)
        if config:
            cfg.update(config)
        return ConsoleContext(repo_root=tmp, env={"FORGEKIT_HOME": str(home)}, config=cfg)

    def _seed(self):
        tmp = Path(tempfile.mkdtemp()); home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rmtree(tmp)); self.addCleanup(lambda: _rmtree(home))
        (tmp / "apps").mkdir()
        (tmp / "apps" / "m.py").write_text("# TODO a\n# TODO b\n# FIXME c\n", encoding="utf-8")
        return tmp, home

    def test_router_review_is_collect_first(self) -> None:
        tmp, home = self._seed()
        ctx = self._ctx(tmp, home)
        route(parse_input("/discovery"), ctx)
        res = route(parse_input("/discovery review 1"), ctx)
        joined = "\n".join(res.lines)
        self.assertIn("도입 효율 검토", joined)
        self.assertIn("collect-first", joined)
        self.assertIn("product-manager", joined)           # 3-axis consult surfaced

    def test_router_adopt_raw_idea_is_honest(self) -> None:
        tmp, home = self._seed()
        ctx = self._ctx(tmp, home)
        route(parse_input("/discovery"), ctx)
        res = route(parse_input("/discovery adopt 1"), ctx)
        joined = "\n".join(res.lines)
        self.assertIn("adopt-now 결정 기록", joined)
        self.assertIn("계약 미완성", joined)                  # raw idea → not equipped, honest gaps


if __name__ == "__main__":
    unittest.main()
