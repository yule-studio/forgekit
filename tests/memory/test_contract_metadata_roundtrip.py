"""Agent-contract frontmatter keys round-trip through the memory index.

Task 2 wiring: a note stamped with the agent invocation contract
(agent / role / obsidian_lane / color_token / write_owner) is indexed so
those keys are searchable/filterable metadata. Retrieval stays
metadata-driven; color_token is a passive field, never a ranking key.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_memory import MEMORY_DB_ENV, open_memory_index, reindex_paths
from yule_memory.indexer import _document_from_markdown_file
from yule_memory.models import SOURCE_OBSIDIAN
from yule_memory.search import search

from yule_engineering.agents.governance.agent_contract_registry import contract_for
from yule_engineering.agents.governance import note_frontmatter as nf
from yule_engineering.agents.obsidian import agent_note_frontmatter as anf


def _contract_note(role: str, *, kind: str, body: str) -> str:
    fm = anf.build_agent_note_frontmatter(
        role,
        title="Upload pipeline 결정",
        kind=kind,
        topic="upload",
        tags=["backend"],
    )
    return nf.render_frontmatter(fm) + f"\n# Upload pipeline 결정\n\n{body}\n"


class ContractMetadataExtractionTests(unittest.TestCase):
    def test_contract_keys_projected_into_extra(self) -> None:
        contract = contract_for("backend-engineer")
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "n.md"
            md.write_text(
                _contract_note("backend-engineer", kind="decision", body="본문"),
                encoding="utf-8",
            )
            doc = _document_from_markdown_file(
                md_path=md, source_kind=SOURCE_OBSIDIAN, base_dir=Path(tmp)
            )
            self.assertIsNotNone(doc)
            # role is a first-class column; sourced from the singular `role` key.
            self.assertEqual(doc.role, contract.role_id)
            # identity keys round-trip through extra.
            self.assertEqual(doc.extra.get("agent"), contract.agent_id)
            self.assertEqual(doc.extra.get("obsidian_lane"), contract.obsidian_write_target)
            self.assertEqual(doc.extra.get("color_token"), contract.color_token)
            self.assertEqual(doc.extra.get("write_owner"), contract.role_id)


class ContractMetadataSearchRoundtripTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev = os.environ.get(MEMORY_DB_ENV)
        os.environ[MEMORY_DB_ENV] = str(Path(self._tmp.name) / "memory.sqlite3")
        vault = Path(self._tmp.name) / "vault"
        path = vault / "Decisions" / "upload.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _contract_note("backend-engineer", kind="decision", body="업로드 파이프라인"),
            encoding="utf-8",
        )
        with open_memory_index() as index:
            reindex_paths(
                paths=[vault], source_kind=SOURCE_OBSIDIAN, index=index, base_dir=vault
            )

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop(MEMORY_DB_ENV, None)
        else:
            os.environ[MEMORY_DB_ENV] = self._prev

    def test_role_filterable_and_extra_survives_search(self) -> None:
        contract = contract_for("backend-engineer")
        hits = search("업로드 파이프라인", role=contract.role_id, limit=5)
        self.assertTrue(hits)
        doc = hits[0].document
        self.assertEqual(doc.role, contract.role_id)
        self.assertEqual(doc.extra.get("agent"), contract.agent_id)
        self.assertEqual(doc.extra.get("color_token"), contract.color_token)
        self.assertEqual(doc.extra.get("obsidian_lane"), contract.obsidian_write_target)

    def test_wrong_role_filter_excludes(self) -> None:
        hits = search("업로드 파이프라인", role="frontend-engineer", limit=5)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
