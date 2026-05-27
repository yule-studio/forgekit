"""Issue #185 — compact→vault deterministic core.

Verifies:
  * protected regions (head / tail / decision / synthesis / focus) survive
    folding; middle turns become one-line placeholders with audit back-refs;
  * token estimate + saved-tokens accounting;
  * the vault note uses the canonical filename convention (no date prefix)
    and carries the required frontmatter + sections;
  * write_compaction_note lands the file at the expected vault path and never
    commits (working-tree only);
  * the WorkflowSession adapter is defensive.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.harness.context_compaction import (
    CompactionTurn,
    build_compaction_summary,
    compaction_enabled,
    compaction_note_filename,
    from_workflow_session,
    render_compaction_note,
    write_compaction_note,
)
from yule_orchestrator.agents.obsidian.filename_convention import validate_filename


def _turns() -> list[CompactionTurn]:
    return [
        CompactionTurn(0, "user", "prompt", "원문 요청: 로그인 기능 구현"),
        CompactionTurn(1, "tech-lead", "take", "A" * 200, audit_id="aud-1"),
        CompactionTurn(2, "backend", "take", "B" * 200, audit_id="aud-2"),
        CompactionTurn(3, "tech-lead", "decision", "결정: JWT 사용"),
        CompactionTurn(4, "qa", "take", "C" * 200, audit_id="aud-3"),
        CompactionTurn(5, "frontend", "take", "D" * 200, audit_id="aud-4"),
        CompactionTurn(6, "devops", "take", "E" * 200, audit_id="aud-5"),
        CompactionTurn(7, "tech-lead", "synthesis", "합의: 1차 스코프 확정"),
    ]


class BuildSummaryTests(unittest.TestCase):
    def test_protected_regions_survive(self) -> None:
        s = build_compaction_summary(_turns(), session_id="sess1", head_keep=1, tail_keep=1)
        kept_kinds = {t.kind for t in s.kept}
        self.assertIn("prompt", kept_kinds)
        self.assertIn("decision", kept_kinds)
        self.assertIn("synthesis", kept_kinds)
        # the 200-char middle takes (index 2, 4, 5) should be folded
        self.assertTrue(s.folded)
        self.assertTrue(any("audit_id=aud-2" in p for p in s.folded))

    def test_placeholder_shape(self) -> None:
        s = build_compaction_summary(_turns(), session_id="sess1", head_keep=1, tail_keep=1)
        placeholder = next(p for p in s.folded if "aud-2" in p)
        self.assertIn("[take@backend]", placeholder)
        self.assertIn("생략된 본문 200자", placeholder)
        self.assertIn("…", placeholder)  # truncated to <=80 chars

    def test_token_accounting(self) -> None:
        s = build_compaction_summary(_turns(), session_id="sess1", head_keep=1, tail_keep=1)
        self.assertGreater(s.pre_tokens, s.post_tokens)
        self.assertEqual(s.saved_tokens, s.pre_tokens - s.post_tokens)

    def test_focus_keeps_matching_turn(self) -> None:
        turns = [
            CompactionTurn(0, "user", "prompt", "p"),
            CompactionTurn(1, "a", "take", "x" * 200, audit_id="z1"),
            CompactionTurn(2, "b", "take", "특이사항 캐시 무효화 " + "y" * 200, audit_id="z2"),
            CompactionTurn(3, "c", "take", "w" * 200, audit_id="z3"),
            CompactionTurn(4, "d", "take", "v" * 200, audit_id="z4"),
        ]
        s = build_compaction_summary(
            turns, session_id="s", focus="캐시 무효화", head_keep=1, tail_keep=1
        )
        kept_audit = {t.audit_id for t in s.kept}
        self.assertIn("z2", kept_audit)  # focus match preserved


class FilenameTests(unittest.TestCase):
    def test_no_date_prefix_canonical(self) -> None:
        name = compaction_note_filename("abc123def456")
        self.assertEqual(name, "task-log-compact-abc123def456.md")
        self.assertTrue(validate_filename(name).valid)

    def test_issue_suffix(self) -> None:
        name = compaction_note_filename("sess1", issue=185)
        self.assertEqual(name, "task-log-compact-sess1-issue-185.md")
        self.assertTrue(validate_filename(name).valid)


class RenderAndWriteTests(unittest.TestCase):
    def test_render_has_required_frontmatter_and_sections(self) -> None:
        s = build_compaction_summary(_turns(), session_id="sess1")
        md = render_compaction_note(
            s, project="yule-studio-agent", original_prompt="원문 요청 본문"
        )
        self.assertIn("kind: task-log", md)
        self.assertIn("home_hub: 10-projects/yule-studio-agent", md)
        self.assertIn("## 핵심 요약", md)
        self.assertIn("## 접힌 turn", md)
        self.assertIn("원문 요청 본문", md)  # prompt mirror

    def test_write_lands_at_expected_path_and_no_commit(self) -> None:
        s = build_compaction_summary(_turns(), session_id="sess1")
        with tempfile.TemporaryDirectory() as tmp:
            note = write_compaction_note(
                s,
                vault_root=Path(tmp),
                project="yule-studio-agent",
                created_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
            )
            self.assertEqual(
                note.relative_path,
                "10-projects/yule-studio-agent/task-logs/task-log-compact-sess1.md",
            )
            self.assertFalse(note.committed)
            self.assertTrue((Path(tmp) / note.relative_path).is_file())


class SessionAdapterTests(unittest.TestCase):
    def test_defensive_adapter(self) -> None:
        class _FakeSession:
            prompt = "구현 요청"
            progress_notes = ["진행 1", "  ", "진행 2"]
            summary = "최종 합의"
            extra = {
                "agent_ops_audit": [
                    {"action": "research", "summary": "자료 수집 완료", "entry_id": "e1"},
                    {"not": "a-summary"},
                ]
            }

        turns = from_workflow_session(_FakeSession())
        kinds = [t.kind for t in turns]
        self.assertEqual(kinds[0], "prompt")
        self.assertIn("synthesis", kinds)
        self.assertTrue(any(t.audit_id == "e1" for t in turns))

    def test_partial_session_does_not_raise(self) -> None:
        class _Empty:
            pass

        self.assertEqual(from_workflow_session(_Empty()), ())


class FlagTests(unittest.TestCase):
    def test_flag_default_off(self) -> None:
        import os

        os.environ.pop("YULE_COMPACT_TO_VAULT_ENABLED", None)
        self.assertFalse(compaction_enabled())


if __name__ == "__main__":
    unittest.main()
