"""Minimal policy bundle selector (token-eff Phase 3)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.policy_bundle import (
    ALWAYS_INCLUDE,
    build_selected_policy_bundle,
    select_policy_documents,
)

_STEMS = [
    "safety", "context-loading", "testing", "version-control", "workflow",
    "role-profiles", "role-weights-v0", "memory-policy", "recall-policy",
    "context-compression", "dispatcher", "message-protocol",
]


def _docs():
    body = "# 정책\n\n규칙 본문. " + ("상세 " * 200)
    instr = [
        SimpleNamespace(label="entrypoint", path="AGENTS.md", content="# e"),
        SimpleNamespace(label="root_instructions", path="CLAUDE.md", content="# r"),
        SimpleNamespace(label="agent_instructions", path="agents/x/CLAUDE.md", content="# a"),
        SimpleNamespace(label="role_instructions", path="agents/x/qa/CLAUDE.md", content="# role"),
    ]
    pol = [
        SimpleNamespace(label="policy", path=f"policies/runtime/agents/engineering-agent/{s}.md", content=body)
        for s in _STEMS
    ]
    return instr + pol


def _stems_of(docs):
    from pathlib import Path

    return {Path(d.path).stem for d in docs if d.label == "policy"}


class SelectionTests(unittest.TestCase):
    def test_known_task_narrows(self) -> None:
        sel = select_policy_documents(_docs(), task_type="testing")
        kept = _stems_of(sel.selected)
        self.assertEqual(kept, {"safety", "context-loading", "testing"})
        self.assertLess(sel.selected_policies, sel.total_policies)
        self.assertGreater(sel.dropped_policies, 0)

    def test_instruction_layers_always_kept(self) -> None:
        sel = select_policy_documents(_docs(), task_type="testing")
        labels = {d.label for d in sel.selected}
        for layer in ("entrypoint", "root_instructions", "agent_instructions", "role_instructions"):
            self.assertIn(layer, labels)

    def test_always_include_present(self) -> None:
        sel = select_policy_documents(_docs(), task_type="deploy")
        kept = _stems_of(sel.selected)
        self.assertTrue(ALWAYS_INCLUDE <= kept)

    def test_role_adds_policies(self) -> None:
        sel = select_policy_documents(_docs(), role="ai-engineer", task_type="testing")
        kept = _stems_of(sel.selected)
        self.assertIn("memory-policy", kept)  # from ai-engineer role bundle
        self.assertIn("testing", kept)        # from task

    def test_unknown_keeps_all(self) -> None:
        sel = select_policy_documents(_docs(), task_type="totally-unknown-xyz")
        self.assertEqual(sel.selected_policies, sel.total_policies)
        self.assertEqual(sel.dropped_policies, 0)
        self.assertEqual(sel.reason, "no_bundle_match_keep_all")

    def test_intent_path(self) -> None:
        sel = select_policy_documents(_docs(), intent="compress")
        self.assertIn("context-compression", _stems_of(sel.selected))


class BundleTests(unittest.TestCase):
    def test_selected_bundle_is_digest_and_smaller(self) -> None:
        from yule_engineering.agents.harness.token_budget import build_policy_bundle

        docs = _docs()
        all_fed = build_policy_bundle([d for d in docs if d.label == "policy"], mode="digest").fed_tokens
        sb = build_selected_policy_bundle(docs, role="qa-engineer", task_type="testing")
        self.assertEqual(sb.bundle.mode, "digest")
        self.assertLess(sb.bundle.fed_tokens, all_fed)  # fewer policies fed
        self.assertGreater(sb.selection.dropped_policies, 0)
        d = sb.to_dict()
        self.assertIn("selected_policies", d)
        self.assertIn("policy_fed_tokens", d)

    def test_unknown_task_bundle_keeps_all(self) -> None:
        sb = build_selected_policy_bundle(_docs(), task_type="xyz")
        self.assertEqual(sb.selection.selected_policies, sb.selection.total_policies)


class BenchmarkBundleScenarioTests(unittest.TestCase):
    def test_bundle_scenario_reduces(self) -> None:
        from yule_engineering.agents.harness import token_benchmark as tb

        base = tb.run_bundle_scenario("baseline")
        after = tb.run_bundle_scenario("after")
        self.assertEqual(base.scenario, "bundle")
        self.assertLess(after.input_tokens_est, base.input_tokens_est)
        self.assertGreater(after.saved_tokens_by_compaction, 0)
        self.assertIn("bundle", tb.SCENARIOS)


if __name__ == "__main__":
    unittest.main()
