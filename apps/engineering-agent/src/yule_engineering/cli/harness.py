"""CLI surface for the harness enforcement layer (issue #185 follow-up).

  * ``yule harness receipt`` — print the execution receipt (loaded docs /
    policies / agent / role / granted skills / blocked-or-missing / runner /
    warnings / compaction / cleanup status). Item D's debug surface.
  * ``yule harness compact`` — run compact→vault for a session and print the
    compaction receipt. Working-tree write only (never commits). Item F.
  * ``yule harness cleanup`` — allowlist cleanup; dry-run by default, execute
    requires ``--execute --yes``. Prints the cleanup receipt. Item G.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from yule_core.context_loader import load_agent_context

from ..agents.harness import (
    assess_security_review,
    build_execution_receipt,
    load_grant_table,
    run_cleanup,
    run_compaction_to_vault,
)
from ..agents.harness.context_compaction import from_workflow_session
from ..agents.harness.grant_enforcement import GrantVerdict, evaluate_skill


def run_harness_receipt_command(
    repo_root: Path,
    agent_id: str,
    *,
    role: Optional[str],
    runner: Optional[str],
    capabilities: Sequence[str],
    change_paths: Sequence[str] = (),
    change_summary: Optional[str] = None,
    json_output: bool,
) -> int:
    loaded = load_agent_context(repo_root=repo_root, agent_id=agent_id, role_id=role)
    table = load_grant_table()
    security = None
    if change_paths or change_summary:
        security = assess_security_review(
            {"paths": list(change_paths), "summary": change_summary or ""}
        )
    receipt = build_execution_receipt(
        loaded,
        table,
        selected_runner=runner,
        requested_capabilities=list(capabilities),
        security=security,
    )
    if json_output:
        print(json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(receipt.render())
    return 0


def run_harness_compact_command(
    repo_root: Path,
    agent_id: str,
    *,
    session_id: str,
    vault_path: str,
    project: str,
    focus: Optional[str],
    issue: Optional[int],
    role: Optional[str] = None,
    live: bool = False,
    json_output: bool,
) -> int:
    # Grant enforcement (hot path): the actor must be granted compact-to-vault.
    table = load_grant_table()
    actor = f"{agent_id}/{role}" if role else agent_id
    decision = evaluate_skill(table, actor, "compact-to-vault")
    if decision.verdict is GrantVerdict.BLOCK:
        print(f"blocked: {decision.surface()}", file=sys.stderr)
        return 1
    if decision.verdict is GrantVerdict.ADVISORY:
        print(f"advisory: {decision.surface()}", file=sys.stderr)

    # Resolve the session through the engineering orchestrator (read-only get).
    from ..agents import Dispatcher, WorkflowOrchestrator, build_participants_pool

    pool = build_participants_pool(repo_root, agent_id)
    orchestrator = WorkflowOrchestrator(Dispatcher(pool))
    session = orchestrator.get(session_id)
    if session is None:
        raise ValueError(f"session {session_id} not found")

    # Optional live /compact token capture (graceful fallback + warning).
    compact_boundary = None
    if live:
        try:
            from ..agents.runners.claude_code import ClaudeCodeRunner

            compact_boundary = ClaudeCodeRunner().compact(focus=focus)
        except Exception as exc:  # noqa: BLE001 - live capture is best-effort
            print(f"warning: live /compact capture failed: {exc}", file=sys.stderr)

    turns = from_workflow_session(session)
    note, receipt = run_compaction_to_vault(
        turns,
        session_id=session_id,
        vault_root=Path(vault_path),
        project=project,
        focus=focus,
        issue=issue,
        original_prompt=getattr(session, "prompt", None),
        compact_boundary=compact_boundary,
    )
    if json_output:
        print(json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"compaction status: {receipt.status}")
        if note is not None:
            print(f"task-log note: {note.relative_path} (committed={receipt.committed})")
        print(
            f"pre_tokens={receipt.pre_tokens} post_tokens={receipt.post_tokens} "
            f"saved={receipt.saved_tokens} token_source={receipt.token_source}"
        )
        for w in receipt.warnings:
            print(f"warning: {w}", file=sys.stderr)
    return 0


def run_harness_security_command(
    repo_root: Path,
    *,
    paths: Sequence[str],
    summary: Optional[str],
    json_output: bool,
) -> int:
    decision = assess_security_review({"paths": list(paths), "summary": summary or ""})
    if json_output:
        print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(decision.surface())
    for reason in decision.reasons:
        print(f"  - {reason}")
    # Non-zero exit when review is required so CI / gates can act on it.
    return 2 if decision.required else 0


def run_harness_cleanup_command(
    repo_root: Path,
    *,
    root: Optional[str],
    execute: bool,
    yes: bool,
    json_output: bool,
) -> int:
    scan_root = Path(root) if root else _default_cleanup_root(repo_root)
    receipt = run_cleanup(scan_root, execute=execute, confirm=yes)
    if json_output:
        print(json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(f"cleanup status: {receipt.status}  root={receipt.root}")
    print(f"scanned={receipt.scanned_count} reclaimable_bytes={receipt.reclaimable_bytes}")
    print(
        f"deletable={len(receipt.deletable)} deleted={receipt.deleted_count} "
        f"protected={len(receipt.protected)} approval_needed={len(receipt.approval_needed)}"
    )
    if receipt.approval_needed:
        print("approval-needed:")
        for e in receipt.approval_needed:
            print(f"  - {e.rel_path} ({e.reason})")
    if not receipt.executed and receipt.deletable:
        print("(dry-run — re-run with --execute --yes to reclaim)")
    for w in receipt.warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 0


def _default_cleanup_root(repo_root: Path) -> Path:
    """Default scan root: the local cache dir if present, else repo root.

    ``.cache`` holds the regeneratable harness/runtime scratch; the operator
    workflow ``*.sqlite3`` inside it is PRESERVED by the allowlist regardless.
    """

    env = os.environ.get("YULE_CACHE_DIR")
    if env:
        return Path(env)
    cache = repo_root / ".cache"
    return cache if cache.exists() else repo_root


__all__ = (
    "run_harness_receipt_command",
    "run_harness_compact_command",
    "run_harness_cleanup_command",
    "run_harness_security_command",
)
