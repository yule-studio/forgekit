"""Discovery sweep loop — free-first collectors → idea pipeline → operator digest.

Proves the wiring the lane adds: one sweep collects from LIVE free-first sources
(planned seams stay honestly empty, never faked), runs idea-discovery, and frames it
as an operator digest answering "왜 올라왔는지 / 다음에 무엇을 물어볼지". A brief
promotes to a PM handoff (proposal only) and persists as a retrieval-friendly
authored vault note (real color/css path). The `/discovery` surface is exercised via
the pure router with an ISOLATED context (no real env / no real vault). Offline +
deterministic → bare CI (a fake fetcher makes the network collectors return []).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import discovery as D
from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.router import ConsoleContext, route


def _empty_fetcher(_url: str) -> str:
    # network collectors parse this → no items (honest empty), so the sweep is
    # deterministic and offline (only repo-local + operator signals contribute).
    return "{}"


class DiscoverySweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "apps").mkdir()
        (self.tmp / "apps" / "m.py").write_text(
            "# TODO a\n# TODO b\n# FIXME c\n", encoding="utf-8")

    # --- core sweep -----------------------------------------------------------
    def test_sweep_wires_free_first_collectors_to_briefs(self) -> None:
        sw = D.run_discovery_sweep(
            self.tmp, fetcher=_empty_fetcher,
            extra_signals=["AI 메모 트렌드 급상승", "forgekit 콘솔 자체 개선 필요"])
        # free-first live ordering: repo-local is the no-cost first source
        self.assertEqual(sw.digest.live_sources[0], "repo-local")
        # planned seams are listed but contributed nothing (no fake live)
        self.assertEqual(set(sw.digest.planned_sources), {"youtube", "instagram", "google"})
        self.assertTrue(sw.briefs)                              # repo-local gap → brief
        self.assertGreaterEqual(sw.digest.gap_count, 1)
        # the self-improve signal splits out (not turned into a product brief)
        self.assertEqual(sw.digest.self_improve_count, 1)

    def test_digest_explains_why_and_next_questions(self) -> None:
        sw = D.run_discovery_sweep(self.tmp, fetcher=_empty_fetcher)
        self.assertTrue(sw.digest.entries)
        top = sw.digest.entries[0]
        self.assertIn("score", top["why"])                     # WHY it surfaced
        self.assertTrue(top["next_questions"])                 # what to ask next
        # the digest renders a planned-source honesty note (never fake-live)
        joined = "\n".join(sw.digest.lines())
        self.assertIn("fake-live 아님", joined)
        self.assertIn("물어볼 것", joined)

    def test_next_questions_deterministic_and_targeted(self) -> None:
        sw = D.run_discovery_sweep(self.tmp, fetcher=_empty_fetcher)
        qs = D.next_questions_for(sw.top_brief)
        self.assertEqual(qs, D.next_questions_for(sw.top_brief))   # deterministic
        self.assertTrue(any("target_user" in q for q in qs))      # missing → asked

    def test_planned_sources_never_fake(self) -> None:
        from forgekit_console.sources import default_registry

        reg = default_registry(self.tmp, fetcher=_empty_fetcher)
        self.assertTrue(all(c.collect() == [] for c in reg.planned()))

    # --- knowledge plane: authored vault note ---------------------------------
    def test_authored_note_carries_real_color_css(self) -> None:
        sw = D.run_discovery_sweep(self.tmp, fetcher=_empty_fetcher)
        note = D.brief_to_authored_note(sw.top_brief, created_at="2026-06-22")
        # real Obsidian color/css path (cssclasses + agent_color + typed callout)
        self.assertIn("cssclasses: [fk-user-research]", note)
        self.assertIn('agent_color: "#c084fc"', note)
        self.assertIn("> [!fk-user-research]", note)
        # retrieval-friendly: tags + the standard note sections (not hollow)
        self.assertIn("tags: [forgekit, discovery, idea-brief]", note)
        for section in ("## 핵심 요약", "## 문제 · 근거", "## 차별화 가설",
                        "## 다음 실험", "## 참고"):
            self.assertIn(section, note)

    def test_css_snippet_includes_author_color(self) -> None:
        from nexus.vault.authorship import vault_css_snippet

        snippet = vault_css_snippet()
        self.assertIn(".fk-user-research", snippet)
        self.assertIn("#c084fc", snippet)

    def test_persist_brief_writes_under_inbox_and_is_honest(self) -> None:
        sw = D.run_discovery_sweep(self.tmp, fetcher=_empty_fetcher)
        vault = self.tmp / "vault"
        path = D.persist_brief(sw.top_brief, vault, created_at="2026-06-22")
        self.assertIsNotNone(path)
        self.assertIn("00-inbox/discovery", str(path))          # raw intake, honest
        self.assertTrue(path.exists())
        # no vault root → honest None (never a fake write)
        self.assertIsNone(D.persist_brief(sw.top_brief, None))

    # --- promotion ------------------------------------------------------------
    def test_promote_brief_to_pm_handoff(self) -> None:
        sw = D.run_discovery_sweep(self.tmp, fetcher=_empty_fetcher)
        ho = D.promote_brief(sw.top_brief)
        self.assertEqual(ho.trace[-1].phase, "tech-lead")       # PM→gateway→tech-lead

    # --- /discovery surface (pure router, isolated ctx) -----------------------
    def _ctx(self) -> ConsoleContext:
        # isolated: empty env/config so /discovery save is honestly "not connected"
        return ConsoleContext(repo_root=self.tmp, env={}, config={})

    def test_router_discovery_digest(self) -> None:
        res = route(parse_input("/discovery"), self._ctx())
        self.assertEqual(res.title, "discovery")
        self.assertIn("operator digest", "\n".join(res.lines))

    def test_router_promote(self) -> None:
        res = route(parse_input("/discovery promote 1"), self._ctx())
        self.assertEqual(res.title, "discovery promote")
        self.assertIn("tech-lead", "\n".join(res.lines))

    def test_router_save_not_connected_is_honest(self) -> None:
        res = route(parse_input("/discovery save 1"), self._ctx())
        self.assertEqual(res.kind, "error")                     # no vault → honest error
        self.assertIn("미연결", "\n".join(res.lines))


if __name__ == "__main__":
    unittest.main()
