"""Harness bridge — registry SSoT projected onto Claude Code / Codex (issue #185).

This package holds the *bridge* between Yule's runtime-agnostic markdown
registry (``agents/<agent>/{skills,commands,hooks}/*.md`` + the grant
SSoT) and the harness-native artifacts the runners actually invoke
(``.claude/`` for Claude Code, ``.agents/`` for Codex, plugin bundles).

Design rule (revises ECC foundation Appendix A.3): the markdown registry
and :mod:`slash_command_grants` JSON stay the single source of truth.
Harness directories are *generated* by ``scripts/sync_harness_skills.py``
and must not be hand-edited.

Surfaces:

  * :mod:`slash_command_grants` — load + validate the per-department
    slash-command / skill grant table. Pure-Python, deterministic, no
    side effects on import.
  * :mod:`grant_enforcement` — runtime ALLOW/ADVISORY/BLOCK verdicts over the
    grant table (item C).
  * :mod:`context_compaction` — deterministic compact→vault core.
  * :mod:`compaction_protocol` — checkpoints, compaction receipt, /clear guard
    (item F).
  * :mod:`cleanup` — allowlist-based artifact cleanup with dry-run/execute
    (item G).
  * :mod:`execution_receipt` — the per-run execution proof binding the above
    (item D).
"""

from __future__ import annotations

from .slash_command_grants import (
    BuiltinCommand,
    CommandGrant,
    CustomSkill,
    DepartmentGrants,
    EffectiveGrants,
    GrantTable,
    GrantValidationError,
    RoleOverride,
    SkillGrant,
    default_grants_path,
    load_grant_table,
)
from .grant_enforcement import (
    CapabilityKind,
    GrantDecision,
    GrantVerdict,
    evaluate_capability,
    evaluate_command,
    evaluate_skill,
)
from .compaction_protocol import (
    Checkpoint,
    ClearBlockedError,
    ClearDecision,
    CompactionCandidate,
    CompactionReceipt,
    compaction_candidates,
    evaluate_clear,
    require_clear_allowed,
    run_compaction_to_vault,
)
from .cleanup import (
    CleanupEntry,
    CleanupReceipt,
    Classification,
    classify,
    run_cleanup,
    scan,
)
from .execution_receipt import ExecutionReceipt, build_execution_receipt
from .security_gate import (
    SecurityReviewDecision,
    assess_security_review,
    security_review_required,
)
from .token_budget import (
    build_policy_bundle,
    compact_decisions,
    estimate_tokens,
    reference_sources,
)
from .retrieval_boost import boost_for, rerank, to_references
from .mcp_registry import (
    McpServer,
    McpValidationError,
    load_mcp_servers,
    validate_mcp_server,
)
from .compact_canary import (
    CompactCanaryReport,
    canary_enabled,
    default_compact_fn,
    run_compact_canary,
)
from .policy_bundle import (
    PolicySelection,
    SelectedPolicyBundle,
    build_selected_policy_bundle,
    select_policy_documents,
)
from .insights import (
    TokenEfficiencyInsights,
    aggregate_receipts,
    scan_token_efficiency_evidence,
)
from .llm_minimization import (
    ResolutionDecision,
    resolve as resolve_llm_minimization,
    resolve_from_metadata as resolve_llm_minimization_from_metadata,
)
from . import token_benchmark
from .hot_path import (
    build_capability_block_gate,
    dispatch_receipt,
    evaluate_input_capabilities,
    requested_capabilities,
)

__all__ = (
    "BuiltinCommand",
    "CommandGrant",
    "CustomSkill",
    "DepartmentGrants",
    "EffectiveGrants",
    "GrantTable",
    "GrantValidationError",
    "RoleOverride",
    "SkillGrant",
    "default_grants_path",
    "load_grant_table",
    # grant enforcement (C)
    "CapabilityKind",
    "GrantDecision",
    "GrantVerdict",
    "evaluate_capability",
    "evaluate_command",
    "evaluate_skill",
    # compaction protocol (F)
    "Checkpoint",
    "ClearBlockedError",
    "ClearDecision",
    "CompactionCandidate",
    "CompactionReceipt",
    "compaction_candidates",
    "evaluate_clear",
    "require_clear_allowed",
    "run_compaction_to_vault",
    # cleanup (G)
    "CleanupEntry",
    "CleanupReceipt",
    "Classification",
    "classify",
    "run_cleanup",
    "scan",
    # execution receipt (D)
    "ExecutionReceipt",
    "build_execution_receipt",
    # hot-path seam (gate + receipt wiring)
    "build_capability_block_gate",
    "dispatch_receipt",
    "evaluate_input_capabilities",
    "requested_capabilities",
    # security auto-dispatch gate (C)
    "SecurityReviewDecision",
    "assess_security_review",
    "security_review_required",
    # token-efficiency core
    "estimate_tokens",
    "build_policy_bundle",
    "compact_decisions",
    "reference_sources",
    "boost_for",
    "rerank",
    "to_references",
    "token_benchmark",
    # MCP server registry (vendor-neutral SSoT)
    "McpServer",
    "McpValidationError",
    "load_mcp_servers",
    "validate_mcp_server",
    # live /compact canary
    "CompactCanaryReport",
    "canary_enabled",
    "default_compact_fn",
    "run_compact_canary",
    # minimal policy bundle selector
    "PolicySelection",
    "SelectedPolicyBundle",
    "build_selected_policy_bundle",
    "select_policy_documents",
    # token-efficiency insights
    "TokenEfficiencyInsights",
    "aggregate_receipts",
    "scan_token_efficiency_evidence",
    # rule-first LLM minimization
    "ResolutionDecision",
    "resolve_llm_minimization",
    "resolve_llm_minimization_from_metadata",
)
