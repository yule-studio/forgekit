"""F8 / #99 — Obsidian 파일명 컨벤션 governance regression.

policies/runtime/agents/engineering-agent/issue-pr-conventions.md §4.1
의 hard rail 을 단일 테스트 스위트로 핀. 본 컨벤션 위반은
mistake_ledger signature 와 1:1 매핑된다 (§7).

핀 대상:

  1. `validate_filename` 이 ``<kind>-<topic>[-issue-<n>].md`` 캐논을 통과.
  2. 날짜 prefix 노트는 ``obsidian.filename.date-prefix`` signature 로 거절.
  3. kind 누락은 ``obsidian.filename.kind-missing`` signature 로 거절.
  4. ``vault-mirror/10-projects/yule-studio-agent/`` 내 현존 노트가 모두
     컨벤션을 만족 (regression — 마이그레이션 이후 본 어설션이 영구 가드).
  5. ``vault_auto_push`` 의 env 가 default OFF.
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.obsidian.filename_convention import (
    ALLOWED_KINDS,
    validate_filename,
)
from yule_engineering.agents.obsidian.vault_auto_push import (
    ENV_AUTOPUSH_ENABLED,
    push_vault_if_ready,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
VAULT_PROJECT_DIR = REPO_ROOT / "notes" / "vault-mirror" / "10-projects" / "yule-studio-agent"


class CanonicalShapeTests(unittest.TestCase):
    def test_kind_topic_issue_is_valid(self) -> None:
        verdict = validate_filename("task-log-tech-lead-runtime-loop-issue-73.md")
        self.assertTrue(verdict.valid)
        self.assertEqual(verdict.kind, "task-log")
        self.assertEqual(verdict.topic_slug, "tech-lead-runtime-loop")
        self.assertEqual(verdict.issue, 73)

    def test_kind_topic_without_issue_is_valid(self) -> None:
        verdict = validate_filename("research-ecc-foundation.md")
        self.assertTrue(verdict.valid)
        self.assertEqual(verdict.kind, "research")
        self.assertEqual(verdict.topic_slug, "ecc-foundation")
        self.assertIsNone(verdict.issue)

    def test_all_allowed_kinds_validate(self) -> None:
        for kind in ALLOWED_KINDS:
            with self.subTest(kind=kind):
                v = validate_filename(f"{kind}-topic-x.md")
                self.assertTrue(v.valid, f"kind={kind} failed: {v.reason}")


class MistakeSignatureTests(unittest.TestCase):
    def test_date_prefix_maps_to_date_prefix_signature(self) -> None:
        verdict = validate_filename("2026-05-12_task-log-foo.md")
        self.assertFalse(verdict.valid)
        self.assertEqual(verdict.signature, "obsidian.filename.date-prefix")

    def test_kind_missing_signature(self) -> None:
        verdict = validate_filename("randomtopic.md")
        self.assertFalse(verdict.valid)
        self.assertEqual(verdict.signature, "obsidian.filename.kind-missing")

    def test_non_markdown_signature(self) -> None:
        verdict = validate_filename("task-log-foo.txt")
        self.assertFalse(verdict.valid)
        self.assertEqual(verdict.signature, "obsidian.filename.not-markdown")


class VaultPushDefaultOffTests(unittest.TestCase):
    def test_vault_autopush_default_off_governance(self) -> None:
        # ENV 미지정 = OFF. 본 어설션이 깨지면 hard rail #4 가 무너진 것.
        verdict = push_vault_if_ready(completion_event=type("E", (), {"status": "done", "job_id": "j", "reason": "r"})(), env={})
        self.assertFalse(verdict.performed)
        self.assertIn(ENV_AUTOPUSH_ENABLED, verdict.skipped_reason or "")


class VaultMirrorRegressionTests(unittest.TestCase):
    def test_all_existing_notes_satisfy_convention(self) -> None:
        """마이그레이션 후 vault-mirror 내 모든 노트가 §4.1 캐논을 만족.

        본 테스트가 깨지는 경로는 두 가지:
        (a) 누군가 새 노트를 옛 컨벤션으로 작성 → mistake_ledger
        (b) 마이그레이션 회귀 → PR reject.
        """

        if not VAULT_PROJECT_DIR.exists():
            self.skipTest("vault-mirror not present")
        offenders = []
        for path in VAULT_PROJECT_DIR.rglob("*.md"):
            verdict = validate_filename(path.name)
            if not verdict.valid:
                offenders.append((path.name, verdict.signature))
        self.assertEqual(offenders, [], f"convention offenders: {offenders}")


if __name__ == "__main__":
    unittest.main()
