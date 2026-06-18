"""Nexus read foundation (Hephaistos PR1) — honest read path, no fake-read.

Proves: refs resolve to connected/missing/blocked/restricted/not_connected; markdown
normalize is bounded (not a raw dump); an unreadable status never fabricates content;
restricted sources give projection-only to non-allowed roles; and the resolver seam
(read_plan_sources) reads a plan's declared refs.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.hephaistos import models, nexus_read as nx, resolver


def _ref(path, kind=models.NEXUS_AREA):
    return models.NexusSourceRef(kind, path)


class ResolveRefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def test_not_connected_when_no_root(self) -> None:
        r = nx.resolve_ref(_ref("20-areas/x"), None)
        self.assertEqual(r.status, models.SRC_NOT_CONNECTED)

    def test_missing_when_path_absent(self) -> None:
        r = nx.resolve_ref(_ref("20-areas/absent"), self.root)
        self.assertEqual(r.status, models.SRC_MISSING)

    def test_exists_when_present(self) -> None:
        (self.root / "20-areas").mkdir(parents=True)
        (self.root / "20-areas/x.md").write_text("# T\n- a\n", encoding="utf-8")
        r = nx.resolve_ref(_ref("20-areas/x.md"), self.root)
        self.assertEqual(r.status, models.SRC_EXISTS)

    def test_restricted_detected(self) -> None:
        (self.root / "70-restricted").mkdir(parents=True)
        (self.root / "70-restricted/secret.md").write_text("# s\n", encoding="utf-8")
        r = nx.resolve_ref(_ref("70-restricted/secret.md"), self.root)
        self.assertEqual(r.status, models.SRC_RESTRICTED)
        self.assertTrue(r.restricted)


class NormalizeTests(unittest.TestCase):
    def test_bounded_extraction(self) -> None:
        md = ("# JWT 가이드\n\n요약 문장.\n\n## 규칙\n- refresh token 필수 회전\n"
              "- secret 하드코딩 금지\n\n```java\nString x = jwt();\n```\n"
              "에러: bean cycle 주의\n" + ("긴내용 " * 500))
        nm = nx.normalize_markdown(md)
        self.assertEqual(nm["title"], "JWT 가이드")
        self.assertTrue(nm["key_points"])
        self.assertTrue(any("회전" in p for p in nm["rules"]))
        self.assertTrue(nm["snippets"] and "jwt()" in nm["snippets"][0])
        self.assertTrue(nm["troubleshooting_signals"])
        self.assertLessEqual(len(nm["summary"]), 500)            # bounded, no raw dump
        self.assertLessEqual(len(nm["snippets"][0]), 300)


class NoFakeReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def test_not_connected_yields_no_doc_content(self) -> None:
        res = nx.read_refs([_ref("20-areas/x")], None)
        self.assertTrue(res.not_connected)
        self.assertEqual(res.resolved_docs, ())             # nothing fabricated
        self.assertEqual(res.read_mode, nx.READ_NONE)

    def test_missing_is_honest(self) -> None:
        res = nx.read_refs([_ref("20-areas/absent")], self.root)
        self.assertEqual(len(res.missing_refs), 1)
        self.assertEqual(res.resolved_docs, ())

    def test_restricted_projection_only_for_other_role(self) -> None:
        (self.root / "70-restricted").mkdir(parents=True)
        (self.root / "70-restricted/d.md").write_text("# secret design\n- 비밀 내용\n", encoding="utf-8")
        res = nx.read_refs([_ref("70-restricted/d.md")], self.root, role="backend-engineer")
        self.assertEqual(len(res.restricted_refs), 1)
        doc = res.resolved_docs[0]
        self.assertEqual(doc.read_mode, nx.READ_PROJECTION)
        self.assertNotIn("비밀 내용", doc.summary)            # raw body NOT exposed
        self.assertEqual(doc.key_points, ())

    def test_restricted_raw_for_allowed_role(self) -> None:
        (self.root / "70-restricted").mkdir(parents=True)
        (self.root / "70-restricted/d.md").write_text("# design\n- 핵심 포인트\n", encoding="utf-8")
        res = nx.read_refs([_ref("70-restricted/d.md")], self.root, role="design-lead")
        self.assertEqual(res.resolved_docs[0].read_mode, nx.READ_RAW)
        self.assertTrue(res.resolved_docs[0].key_points)      # allowed role gets content


class ResolverSeamTests(unittest.TestCase):
    def test_read_plan_sources_reads_declared_refs(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        plan = resolver.resolve("Spring Boot JWT refresh token")
        # not connected → honest not_connected, no docs
        nc = nx.read_plan_sources(plan, env={}, config={})
        self.assertTrue(nc.not_connected)
        # create one of the plan's declared ref paths → it reads
        ref0 = plan.nexus_refs[0].ref
        target = root / ref0
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# area\n- 규칙 필수\n", encoding="utf-8")
        res = nx.read_plan_sources(plan, env={"FORGEKIT_NEXUS_ROOT": str(root)}, config={})
        self.assertFalse(res.not_connected)
        self.assertTrue(res.resolved_docs or res.missing_refs)   # seam actually read/located


if __name__ == "__main__":
    unittest.main()
