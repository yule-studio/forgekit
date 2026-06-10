"""GitHub sync — pending plan only, never direct push."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.engineering_intelligence.github_sync import (
    build_pending_audit,
    build_pending_git_sync_plan,
)
from yule_engineering.agents.engineering_intelligence.models import (
    EngineeringKnowledgeItem,
    Importance,
    SourceKind,
)


def _it(role: str, topic: str, title: str) -> EngineeringKnowledgeItem:
    return EngineeringKnowledgeItem(
        item_id=f"{role}-{topic}",
        topic_key=topic,
        title=title,
        role=role,
        stack_tags=("x",),
        source_name="src",
        source_url=f"https://example.com/{topic}",
        source_kind=SourceKind.DOCS,
        collected_at="2026-05-08T00:00:00Z",
        importance=Importance.HIGH,
    )


class PlanShapeTests(unittest.TestCase):
    def test_plan_is_pending_and_no_direct_push(self) -> None:
        items = [
            _it("backend-engineer", "spring-6-2", "Spring 6.2"),
            _it("backend-engineer", "pg-17", "PostgreSQL 17"),
        ]
        plan = build_pending_git_sync_plan("backend-engineer", items)
        self.assertEqual(plan.status, "pending")
        self.assertTrue(plan.approval_required)
        self.assertFalse(plan.direct_push_to_main)
        self.assertEqual(plan.request_type, "docs_only_sync_plan")

    def test_plan_role_branch_includes_role_and_date(self) -> None:
        items = [_it("frontend-engineer", "react-19", "React 19")]
        plan = build_pending_git_sync_plan(
            "frontend-engineer",
            items,
            today="2026-05-08",
        )
        self.assertIn("frontend-engineer", plan.target_branch)
        self.assertIn("2026-05-08", plan.target_branch)

    def test_files_present_for_each_item(self) -> None:
        items = [
            _it("ai-engineer", "rag-eval", "RAG eval"),
            _it("ai-engineer", "vec-db", "Vector DB"),
        ]
        plan = build_pending_git_sync_plan("ai-engineer", items, today="2026-05-08")
        self.assertEqual(len(plan.files), 2)
        topics = {f.topic_key for f in plan.files}
        self.assertEqual(topics, {"rag-eval", "vec-db"})
        for f in plan.files:
            self.assertIn(f.topic_key, f.proposed_path)
            self.assertIn("ai-engineer", f.proposed_path)

    def test_proposed_path_uses_engineering_knowledge_layout(self) -> None:
        items = [_it("qa-engineer", "playwright-1-50", "Playwright 1.50")]
        plan = build_pending_git_sync_plan(
            "qa-engineer",
            items,
            today="2026-05-08",
            layout="yule-agent-vault",
        )
        f = plan.files[0]
        self.assertTrue(f.proposed_path.startswith("05-engineering/knowledge/"))


class ProtectedBaseGuardTests(unittest.TestCase):
    def test_main_base_downgraded_and_recorded(self) -> None:
        plan = build_pending_git_sync_plan(
            "backend-engineer",
            [_it("backend-engineer", "x", "X")],
            target_base_branch="main",
        )
        self.assertEqual(plan.target_base_branch, "unset-safe-base")
        self.assertTrue(
            any("protected_base_branch_rejected" in r for r in plan.rejected_reasons)
        )

    def test_release_base_downgraded(self) -> None:
        plan = build_pending_git_sync_plan(
            "backend-engineer",
            [_it("backend-engineer", "x", "X")],
            target_base_branch="release",
        )
        self.assertEqual(plan.target_base_branch, "unset-safe-base")

    def test_refs_heads_master_downgraded(self) -> None:
        plan = build_pending_git_sync_plan(
            "backend-engineer",
            [_it("backend-engineer", "x", "X")],
            target_base_branch="refs/heads/master",
        )
        self.assertEqual(plan.target_base_branch, "unset-safe-base")

    def test_safe_base_passes_through(self) -> None:
        plan = build_pending_git_sync_plan(
            "backend-engineer",
            [_it("backend-engineer", "x", "X")],
            target_base_branch="develop",
        )
        self.assertEqual(plan.target_base_branch, "develop")
        self.assertEqual(plan.rejected_reasons, ())


class AuditTests(unittest.TestCase):
    def test_audit_states_no_push_happened(self) -> None:
        plan = build_pending_git_sync_plan(
            "devops-engineer",
            [_it("devops-engineer", "k8s-1-31", "k8s 1.31")],
        )
        audit = build_pending_audit(plan)
        self.assertEqual(audit["action"], "engineering_knowledge_github_sync")
        self.assertEqual(audit["outcome"], "plan_pending_no_push")
        self.assertTrue(audit["approval_required"])
        self.assertFalse(audit["direct_push_to_main"])
        self.assertEqual(audit["role"], "devops-engineer")
        self.assertIn("docs_only_sync_plan", audit["summary"])


if __name__ == "__main__":
    unittest.main()
