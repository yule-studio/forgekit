"""External intake lane (nexus.intake) — schema · extract · curation gate.

Proves the free-first external skill/plugin/tool intake lane closes honestly:
- ``SourceItem`` → ``ExternalCandidate`` heuristic classification (shape/affinity/cap),
- dedupe by fingerprint (richest/freshest wins) + source allowlist,
- the curation gate's promote / raw / blocked decisions and their reasons,
- planned sources (YouTube/IG/Google/Figma/GeekNews) NEVER fake live data,
- an end-to-end ponytail-like packet: 1 promote + 1 raw + 1 blocked.
Pure stdlib → bare CI (network collectors driven by an injected fetcher).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401  (ensures package src on path)

from nexus import intake as I
from nexus.intake import candidate as K
from nexus.intake import collect as CO
from nexus.intake import curate as CU
from nexus.intake import extract as EX
from nexus.sources.contract import SourceItem


class SchemaTests(unittest.TestCase):
    def test_fingerprint_normalizes_url(self) -> None:
        a = K.ExternalCandidate(name="x", repo_url="https://github.com/A/B/")
        b = K.ExternalCandidate(name="x", repo_url="http://www.github.com/a/b")
        self.assertEqual(a.fingerprint, "github.com/a/b")
        self.assertEqual(a.fingerprint, b.fingerprint)

    def test_fingerprint_falls_back_to_source_name(self) -> None:
        c = K.ExternalCandidate(name="No URL Tool", source="reddit")
        self.assertEqual(c.fingerprint, "reddit:no url tool")

    def test_has_min_metadata_floor(self) -> None:
        thin = K.ExternalCandidate(name="x", repo_url="u")  # cap unknown, no why
        self.assertFalse(thin.has_min_metadata)
        full = K.ExternalCandidate(name="x", repo_url="u",
                                   capability_class=K.CAP_RETRIEVAL, why_it_matters="z")
        self.assertTrue(full.has_min_metadata)


class ClassifyTests(unittest.TestCase):
    def test_install_shape_cues(self) -> None:
        self.assertEqual(EX.classify_install_shape("an mcp server for x"), K.SHAPE_MCP)
        self.assertEqual(EX.classify_install_shape("a claude skill recipe"), K.SHAPE_SKILL)
        self.assertEqual(EX.classify_install_shape("vscode plugin"), K.SHAPE_PLUGIN)
        self.assertEqual(EX.classify_install_shape("a handy cli tool"), K.SHAPE_CLI)
        self.assertEqual(EX.classify_install_shape("ollama local llm"), K.SHAPE_BACKEND)
        self.assertEqual(EX.classify_install_shape("just a python library"), K.SHAPE_LIB)

    def test_backend_cue_wins_over_generic(self) -> None:
        # backend must be detected even when 'tool' words are present (allowlist gate)
        self.assertEqual(EX.classify_install_shape("ollama backend runner cli"), K.SHAPE_BACKEND)

    def test_provider_affinity(self) -> None:
        self.assertEqual(EX.classify_provider_affinity("for claude code"), K.AFFINITY_CLAUDE)
        self.assertEqual(EX.classify_provider_affinity("openai codex tool"), K.AFFINITY_CODEX)
        self.assertEqual(EX.classify_provider_affinity("google gemini helper"), K.AFFINITY_GEMINI)
        self.assertEqual(EX.classify_provider_affinity("a generic tool"), K.AFFINITY_NEUTRAL)

    def test_capability(self) -> None:
        self.assertEqual(EX.classify_capability("rag retrieval index"), K.CAP_RETRIEVAL)
        self.assertEqual(EX.classify_capability("code review and lint"), K.CAP_CODE_REVIEW)
        self.assertEqual(EX.classify_capability("nothing matches"), K.CAP_UNKNOWN)


class ExtractTests(unittest.TestCase):
    def test_candidate_from_item_keeps_unverifiable_unknown(self) -> None:
        it = SourceItem("github", "claude mcp server", url="https://github.com/a/b",
                        summary="retrieval rag", kind="repo", score=12.0)
        c = EX.candidate_from_item(it)
        self.assertEqual(c.install_shape, K.SHAPE_MCP)
        self.assertEqual(c.provider_affinity, K.AFFINITY_CLAUDE)
        self.assertEqual(c.capability_class, K.CAP_RETRIEVAL)
        # we never fabricate these from a bare signal:
        self.assertEqual(c.license, K.LICENSE_UNKNOWN)
        self.assertEqual(c.maintenance_signal, K.MAINT_UNKNOWN)
        self.assertEqual(c.trust_risk, K.RISK_UNKNOWN)

    def test_dedupe_keeps_highest_score(self) -> None:
        a = K.ExternalCandidate(name="x", source="github", repo_url="https://github.com/a/b", score=5)
        b = K.ExternalCandidate(name="x", source="github", repo_url="https://github.com/a/b", score=9)
        out = EX.dedupe([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].score, 9)

    def test_dedupe_prefers_enriched_record_on_tie(self) -> None:
        thin = K.ExternalCandidate(name="x", source="github",
                                   repo_url="https://github.com/a/b", score=5)
        rich = K.ExternalCandidate(name="x", source="github",
                                   repo_url="https://github.com/a/b", score=5,
                                   capability_class=K.CAP_RETRIEVAL, why_it_matters="z")
        out = EX.dedupe([thin, rich])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].has_min_metadata)

    def test_source_allowlist_filters(self) -> None:
        good = K.ExternalCandidate(name="a", source="github", repo_url="u1")
        bad = K.ExternalCandidate(name="b", source="youtube", repo_url="u2")
        out = EX.apply_source_allowlist([good, bad])
        self.assertEqual([c.source for c in out], ["github"])


class CurationGateTests(unittest.TestCase):
    def _full(self, **over):
        base = dict(name="ponytail", source="github", repo_url="https://github.com/x/ponytail",
                    provider_affinity=K.AFFINITY_NEUTRAL, capability_class=K.CAP_CODE_REVIEW,
                    install_shape=K.SHAPE_CLI, trust_risk=K.RISK_LOW,
                    maintenance_signal=K.MAINT_ACTIVE, license="MIT",
                    why_it_matters="simplicity review")
        base.update(over)
        return K.ExternalCandidate(**base)

    def test_promote_when_fully_vetted(self) -> None:
        v = CU.curate(self._full())
        self.assertEqual(v.disposition, K.DISPOSITION_PROMOTE)

    def test_raw_when_license_unknown(self) -> None:
        v = CU.curate(self._full(license=K.LICENSE_UNKNOWN))
        self.assertEqual(v.disposition, K.DISPOSITION_RAW)
        self.assertTrue(any("license" in r for r in v.reasons))

    def test_raw_when_metadata_thin(self) -> None:
        v = CU.curate(self._full(capability_class=K.CAP_UNKNOWN, why_it_matters=""))
        self.assertEqual(v.disposition, K.DISPOSITION_RAW)

    def test_blocked_backend_shape(self) -> None:
        v = CU.curate(self._full(install_shape=K.SHAPE_BACKEND))
        self.assertEqual(v.disposition, K.DISPOSITION_BLOCKED)
        self.assertTrue(any("install_shape" in r for r in v.reasons))

    def test_blocked_high_risk(self) -> None:
        v = CU.curate(self._full(trust_risk=K.RISK_HIGH))
        self.assertEqual(v.disposition, K.DISPOSITION_BLOCKED)

    def test_blocked_archived(self) -> None:
        v = CU.curate(self._full(maintenance_signal=K.MAINT_ARCHIVED))
        self.assertEqual(v.disposition, K.DISPOSITION_BLOCKED)

    def test_blocked_proprietary_license(self) -> None:
        v = CU.curate(self._full(license=K.LICENSE_PROPRIETARY))
        self.assertEqual(v.disposition, K.DISPOSITION_BLOCKED)

    def test_blocked_by_blocklist_fingerprint(self) -> None:
        c = self._full()
        v = CU.curate(c, blocklist_fingerprints=[c.fingerprint])
        self.assertEqual(v.disposition, K.DISPOSITION_BLOCKED)

    def test_packet_groups_and_counts(self) -> None:
        promote = self._full()
        raw = self._full(name="raw", repo_url="https://github.com/x/raw",
                         license=K.LICENSE_UNKNOWN)
        blocked = self._full(name="be", repo_url="https://github.com/x/be",
                             install_shape=K.SHAPE_BACKEND)
        pkt = CU.curate_all([promote, raw, blocked])
        self.assertEqual(pkt.counts,
                         {K.DISPOSITION_PROMOTE: 1, K.DISPOSITION_RAW: 1,
                          K.DISPOSITION_BLOCKED: 1})
        # round-trips to JSON evidence
        self.assertIn("promoted", pkt.to_dict())


class CollectFreeFirstTests(unittest.TestCase):
    def _fetcher(self):
        def fake(url: str) -> str:
            if "api.github.com" in url:
                return json.dumps({"items": [
                    {"full_name": "x/claude-mcp", "html_url": "https://github.com/x/claude-mcp",
                     "description": "an mcp server for claude retrieval", "stargazers_count": 99}]})
            if "hn.algolia.com" in url:
                return json.dumps({"hits": [
                    {"title": "Show HN: a devtools cli", "url": "https://github.com/y/cli",
                     "points": 30}]})
            if "reddit.com" in url:
                return json.dumps({"data": {"children": [
                    {"data": {"title": "best selfhosted tool", "permalink": "/r/x/1", "ups": 10}}]}})
            return "{}"
        return fake

    def test_planned_sources_never_fake_live(self) -> None:
        reg = CO.intake_source_registry(Path("."), fetcher=self._fetcher())
        planned_ids = {c.spec.id for c in reg.planned()}
        self.assertIn("youtube", planned_ids)
        self.assertIn("figma-community", planned_ids)
        self.assertIn("geeknews", planned_ids)
        for c in reg.planned():
            self.assertEqual(c.collect(limit=5), [])

    def test_run_intake_end_to_end_with_fake_fetcher(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "apps").mkdir()
        pkt = CO.run_intake(tmp, fetcher=self._fetcher(), limit_per=5)
        # the github mcp repo got extracted + classified
        names = {v.candidate.name for v in pkt.verdicts}
        self.assertIn("x/claude-mcp", names)
        mcp = next(v for v in pkt.verdicts if v.candidate.name == "x/claude-mcp")
        self.assertEqual(mcp.candidate.install_shape, K.SHAPE_MCP)
        # auto-extracted (license/maint unknown) → cannot auto-promote (honest)
        self.assertNotEqual(mcp.disposition, K.DISPOSITION_PROMOTE)
        # planned sources contributed nothing
        self.assertTrue(pkt.source_status)

    def test_operator_enrich_lifts_signal_to_promote(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "apps").mkdir()
        vetted = K.ExternalCandidate(
            name="x/claude-mcp", source="github", repo_url="https://github.com/x/claude-mcp",
            provider_affinity=K.AFFINITY_CLAUDE, capability_class=K.CAP_RETRIEVAL,
            install_shape=K.SHAPE_MCP, trust_risk=K.RISK_LOW,
            maintenance_signal=K.MAINT_ACTIVE, license="MIT", why_it_matters="retrieval seam",
            score=200)
        pkt = CO.run_intake(tmp, fetcher=self._fetcher(), enrich=[vetted], limit_per=5)
        mcp = next(v for v in pkt.verdicts if v.candidate.name == "x/claude-mcp")
        # the enriched record won the fingerprint merge → promotable
        self.assertEqual(mcp.disposition, K.DISPOSITION_PROMOTE)


class PonytailExampleTests(unittest.TestCase):
    """The ponytail-like end-to-end packet that ships as committed evidence."""

    def _packet(self) -> CU.IntakePacket:
        ponytail = K.ExternalCandidate(
            name="ponytail", source="github",
            repo_url="https://github.com/example/ponytail",
            provider_affinity=K.AFFINITY_NEUTRAL, capability_class=K.CAP_CODE_REVIEW,
            install_shape=K.SHAPE_CLI, trust_risk=K.RISK_LOW,
            maintenance_signal=K.MAINT_ACTIVE, license="MIT",
            why_it_matters="과설계 검토(ponytail lens) — autonomy/execution core 단순성 게이트에 활용",
            score=420)
        thin = K.ExternalCandidate(
            name="some-rag-lib", source="hackernews",
            repo_url="https://github.com/acme/some-rag-lib",
            capability_class=K.CAP_RETRIEVAL, install_shape=K.SHAPE_LIB,
            score=33)  # license/maint/trust unknown, no why → raw
        backend = K.ExternalCandidate(
            name="local-llama-runner", source="github",
            repo_url="https://github.com/acme/local-llama-runner",
            capability_class=K.CAP_ORCHESTRATION, install_shape=K.SHAPE_BACKEND,
            trust_risk=K.RISK_LOW, maintenance_signal=K.MAINT_ACTIVE, license="MIT",
            why_it_matters="local inference", score=88)  # backend → blocked
        return CU.curate_all([ponytail, thin, backend])

    def test_ponytail_promotes_thin_raw_backend_blocked(self) -> None:
        pkt = self._packet()
        self.assertEqual([v.candidate.name for v in pkt.promoted], ["ponytail"])
        self.assertEqual([v.candidate.name for v in pkt.raw], ["some-rag-lib"])
        self.assertEqual([v.candidate.name for v in pkt.blocked], ["local-llama-runner"])

    def test_example_file_matches_code(self) -> None:
        path = (Path(__file__).resolve().parents[2] / "apps" / "forgekit-console" /
                "examples" / "discovery" / "external-intake-packet.json")
        self.assertTrue(path.exists(), "example packet missing — regenerate it")
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, self._packet().to_dict())


if __name__ == "__main__":
    unittest.main()
