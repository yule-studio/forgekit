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

    # Live /compact canary: deterministic estimate + (when --live) live
    # compact_boundary capture, with estimate-vs-live drift. --live forces the
    # canary on for this run; graceful fallback to estimate otherwise.
    from ..agents.harness.compact_canary import default_compact_fn, run_compact_canary

    turns = from_workflow_session(session)
    note, receipt, report = run_compact_canary(
        turns,
        session_id=session_id,
        vault_root=Path(vault_path),
        project=project,
        focus=focus,
        issue=issue,
        original_prompt=getattr(session, "prompt", None),
        compact_fn=default_compact_fn() if live else None,
        enabled=True if live else None,
    )
    if json_output:
        out = receipt.to_dict()
        out["canary"] = report.to_dict()
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"compaction status: {receipt.status}")
        if note is not None:
            print(f"task-log note: {note.relative_path} (committed={receipt.committed})")
        print(report.render().rstrip())
        for w in report.warnings:
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


def run_harness_bench_command(
    repo_root: Path,
    *,
    slug: str,
    date: Optional[str],
    out: Optional[str],
    json_output: bool,
) -> int:
    """Run the token-efficiency benchmark and write the evidence package.

    Writes baseline/after/delta (JSON + MD), a receipt, and a vault-friendly
    summary under ``runs/token-efficiency/<date>-<slug>/``.
    """

    from ..agents.harness import token_benchmark as tb

    stamp = date or _today_iso()
    out_dir = Path(out) if out else (repo_root / "runs" / "token-efficiency" / f"{stamp}-{slug}")
    out_dir.mkdir(parents=True, exist_ok=True)

    generated_at = f"{stamp}T00:00:00Z"
    baseline = tb.run_benchmark("baseline", slug=slug, generated_at=generated_at)
    after = tb.run_benchmark("after", slug=slug, generated_at=generated_at)
    delta = tb.compute_delta(baseline, after)

    (out_dir / "baseline.json").write_text(
        json.dumps(baseline.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "baseline.md").write_text(tb.render_report_markdown(baseline), encoding="utf-8")
    (out_dir / "after.json").write_text(
        json.dumps(after.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "after.md").write_text(tb.render_report_markdown(after), encoding="utf-8")
    (out_dir / "delta.json").write_text(
        json.dumps(delta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "delta.md").write_text(tb.render_delta_markdown(delta), encoding="utf-8")
    (out_dir / "receipt.md").write_text(_bench_receipt_md(delta, stamp, slug), encoding="utf-8")
    (out_dir / "summary.md").write_text(_bench_summary_md(delta, stamp, slug), encoding="utf-8")

    rel = out_dir.relative_to(repo_root) if out_dir.is_relative_to(repo_root) else out_dir
    if json_output:
        print(json.dumps({"out_dir": str(rel), "delta": delta}, ensure_ascii=False, indent=2))
    else:
        print(tb.render_delta_markdown(delta))
        print(f"evidence written: {rel}/  (baseline, after, delta, receipt, summary)")
    return 0


def _today_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).date().isoformat()


def _bench_receipt_md(delta: dict, stamp: str, slug: str) -> str:
    t = delta["totals"]
    lines = [
        f"# Benchmark receipt — {stamp} {slug}",
        "",
        f"- estimator: {delta['estimator']}",
        f"- selected_runner: deterministic (no live LLM — token estimates only)",
        f"- total input tokens: {t['input_tokens_before']} → {t['input_tokens_after']} "
        f"(−{t['input_tokens_saved']}, −{t['input_reduction_pct']}%)",
        "- feature flags exercised: YULE_RUNNER_INPUT_COMPACTION_ENABLED, "
        "YULE_RETRIEVAL_BOOST_ENABLED (default off; benchmark applies transforms directly)",
        "- protected regions preserved: recent K decisions + kind=decision/synthesis never folded",
        "",
        "## Per-scenario",
        "| scenario | before | after | saved | reduction |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for r in delta["scenarios"]:
        lines.append(
            f"| {r['scenario']} | {r['input_tokens_before']} | {r['input_tokens_after']} | "
            f"{r['input_tokens_saved']} | −{r['input_reduction_pct']}% |"
        )
    return "\n".join(lines).rstrip() + "\n"


def _bench_summary_md(delta: dict, stamp: str, slug: str) -> str:
    """Vault-friendly summary: 문제 → 가설 → 개입 → 측정 → 결과 → 해석."""

    t = delta["totals"]
    rows = "\n".join(
        f"| {r['scenario']} | {r['input_tokens_before']} | {r['input_tokens_after']} | "
        f"−{r['input_reduction_pct']}% |"
        for r in delta["scenarios"]
    )
    return f"""---
title: 토큰 효율 코어 — baseline vs after ({stamp})
kind: task-log
status: draft
created_at: {stamp}
tags: [token-efficiency, benchmark, task-log]
related: []
home_hub: 10-projects/yule-studio-agent
---

# 토큰 효율 코어 — {slug} ({stamp})

## 문제
거버넌스/감사/메모리는 강하지만 토큰 효율은 미완성이었다: context render가 무겁고,
role-runner 입력(previous_decisions/source_context)이 길이에 비례해 커지며, retrieval이
저장 중심이라 재사용 boost가 live wiring되지 않았고, before/after 증거 surface가 없었다.

## 가설
(1) policy를 매번 full-text로 싣지 말고 digest pointer로 넘기면 context 토큰이 준다.
(2) 오래된 role take는 protected 영역만 남기고 접으면 dispatch 입력이 준다.
(3) decision/canonical/reusable boost로 재정렬하고 본문 대신 reference(snippet)로 넘기면
retrieval 토큰이 준다.

## 개입
- `token_budget`: 일관 추정기(chars/4) + policy digest bundle + previous_decisions
  compaction(protected 보존) + source reference-mode.
- `retrieval_boost`: memory-policy 4절 boost 재정렬 + reference-mode (`fetch_role_context`
  flag-gated 결선, indexer가 canonical/reusable/status를 extra로 투영).
- `standalone_runners`: role-runner 입력 조립 hot path에 compaction 결선(flag-gated).

## 측정
동일 추정기(chars/4)로 baseline(현행)과 after(최적화)를 deterministic 합성 시나리오로 측정.

| scenario | before | after | reduction |
| --- | ---: | ---: | ---: |
{rows}

**총 input tokens: {t['input_tokens_before']} → {t['input_tokens_after']} (−{t['input_tokens_saved']}, −{t['input_reduction_pct']}%)**

## 결과
입력 토큰이 시나리오별로 줄었고, 총합 −{t['input_reduction_pct']}%. saved_tokens는
receipt/benchmark JSON에 기록되어 재현 가능하다.

## 해석
절감은 "모델 교체"가 아니라 "덜 읽고(digest)·자동으로 접고(compaction)·요약 reference로
넘기기(boost+reference)"에서 나왔다. 안전/승인/보안 hard rail, 원문 prompt/decision/
synthesis/audit는 손대지 않았고(보존), 모든 live 결선은 flag-gated 기본 off다.

## 거버넌스/품질 리스크
- digest/compaction은 진단/runner-fed surface에만 적용; 영속 저장 원문은 full 유지.
- compaction은 protected 영역(recent K + decision/synthesis)을 절대 접지 않는다.
- boost는 read-side만 변경(새 write/자동 memory write 없음). vault 기록 전 clear/cleanup 금지 유지.

## 다음 최적화 후보
- live `/compact` 토큰을 estimate 대신 compact_boundary로 채우기.
- role별 minimal policy bundle 선택(작업 intent 기반)으로 context 추가 절감.
- retrieval pool 캐시 + topic-aware recall 결선.

> Obsidian sync: 이 파일을 vault `10-projects/yule-studio-agent/task-logs/`로 복사.
> orphan/broken link 없을 때만 commit (vault governance 준수).
"""


def run_harness_insights_command(
    repo_root: Path,
    *,
    runs_dir: Optional[str],
    receipts: Optional[str] = None,
    session: Optional[str] = None,
    agent_id: str = "engineering-agent",
    json_output: bool,
) -> int:
    """Print cumulative token-efficiency insights + (optional) LLM-usage roll-up.

    Token savings come from the benchmark ``delta.json`` evidence; the LLM
    minimization usage (rule-resolved / llm-used / live-LLM-avoided) comes from
    execution receipts (``--receipts <json>`` file or a live ``--session``).
    """

    from ..agents.harness.insights import (
        aggregate_receipts,
        render_markdown,
        render_usage_markdown,
        scan_token_efficiency_evidence,
    )

    target = Path(runs_dir) if runs_dir else (repo_root / "runs" / "token-efficiency")
    insights = scan_token_efficiency_evidence(target)

    receipt_dicts = _load_receipt_dicts(repo_root, agent_id, receipts=receipts, session=session)
    usage = aggregate_receipts(receipt_dicts) if receipt_dicts is not None else None

    if json_output:
        out = {"token_efficiency": insights.to_dict()}
        if usage is not None:
            out["llm_usage"] = usage
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(render_markdown(insights))
    if insights.runs == 0:
        print(f"(no delta.json under {target} — run `yule harness bench` first)", file=sys.stderr)
    if usage is not None:
        print()
        print(render_usage_markdown(usage))
    return 0


def _load_receipt_dicts(repo_root, agent_id, *, receipts, session):
    """Load execution-receipt dicts from a JSON file or a live session, or None."""

    if receipts:
        try:
            data = json.loads(Path(receipts).read_text(encoding="utf-8"))
            return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
        except (OSError, ValueError) as exc:
            print(f"warning: could not read --receipts: {exc}", file=sys.stderr)
            return []
    if session:
        from ..agents import Dispatcher, WorkflowOrchestrator, build_participants_pool

        pool = build_participants_pool(repo_root, agent_id)
        sess = WorkflowOrchestrator(Dispatcher(pool)).get(session)
        if sess is None:
            print(f"warning: session {session} not found", file=sys.stderr)
            return []
        extra = getattr(sess, "extra", None) or {}
        bucket = extra.get("execution_receipts") if isinstance(extra, dict) else None
        return [d for d in bucket if isinstance(d, dict)] if isinstance(bucket, list) else []
    return None


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
    "run_harness_bench_command",
    "run_harness_insights_command",
)
