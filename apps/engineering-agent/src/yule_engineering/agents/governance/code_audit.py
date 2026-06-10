"""Orchestrator-wide code audit — large file / mixed responsibility /
dead wiring / recovery gap detection. P0-T enforcement.

본 모듈은 advisory 가 아니라 **hard enforcement** 의 SSoT.

- `audit_orchestrator_file_sizes` — `apps/engineering-agent/src/yule_engineering/` + 추출된
  `apps/*/src` 전수 LOC 검사 + 분류 (split_now / split_soon / exception / safe)
- `detect_mixed_responsibilities` — 한 파일에 책임 signal 3종 이상이면
  분리 후보로 분류
- `detect_missing_worker_wiring` — JOB_TYPE_* 상수가 inventory 의
  `_KIND_TO_JOB_TYPE` 에 매핑돼 있는지 검사. queue row 는 enqueue 되는데
  consumer 가 없는 wiring miss 회귀 차단
- `detect_retryable_without_recovery` — `failed_retryable` 로 떨어지는
  error reason 중 startup-recovery hook 이 없는 케이스 검출

호출 측 (caller-driven gate):
- `coding_executor_worker.process_job` preflight — 후속 wiring
- `governance smoke test` — silent regression 방지
- `self_improvement_seed_detectors` — 위반을 signal 로 노출 → 운영-리서치 +
  Obsidian troubleshooting note + worktree 제안

설계 원칙:
- pure: SQLite I/O 없음. 파일 시스템만 read. caller 가 결과 객체를 보고
  enforcement 결정
- exception 은 명시적 이유 dict — silent allow 금지
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Thresholds (CLAUDE.md / CODE_LAYOUT.md 와 동기화)
# ---------------------------------------------------------------------------


WARN_LOC: int = 700
SPLIT_LOC: int = 1000


VERDICT_SAFE: str = "safe"
VERDICT_WARN: str = "warn"
VERDICT_SPLIT_NOW: str = "split_now"
VERDICT_SPLIT_SOON: str = "split_soon"
VERDICT_EXCEPTION: str = "exception"
VERDICT_SPLIT_PENDING: str = "split_pending_deadline"


# ---------------------------------------------------------------------------
# Allowlist — 명시 exception 만 통과. 신규 항목 추가 시 반드시 이유 명시.
# ---------------------------------------------------------------------------


FILE_SIZE_ALLOWLIST: Mapping[str, str] = {
    # In-flight P0-Q discord 분해 진행 중 — discord 는 apps/discord-gateway 로
    # 이전됨 (yule_discord). 옛 경로는 compat shim 만 남음.
    "apps/discord-gateway/src/yule_discord/bot/_legacy.py": (
        "P0-Q 분해 진행 중 — 의미 그룹 추출 후 점진 제거 (`bot/scheduling.py`, `bot/channels.py` 등)"
    ),
    "apps/engineering-agent/src/yule_engineering/agents/engineering_team_runtime/_legacy.py": (
        "P0-Q 분해 진행 중 — engineering_team_runtime 패키지화 후 점진 제거"
        " (decouple: discord → agents 이동, 순환 제거)"
    ),
    # Registry / data table — 분기 로직 없음, 선언만.
    "apps/engineering-agent/src/yule_engineering/agents/engineering_intelligence/source_registry.py": (
        "큰 registry — provider 메타데이터만, 분기 로직 없음"
    ),
}


# ---------------------------------------------------------------------------
# SPLIT_NOW pending list — 1000+ LOC + 책임 ≥ 2 위반이지만 본 PR / 후속
# PR 에서 분리 작업이 진행 중인 파일. 반드시 ``deadline`` (ISO 날짜) +
# ``owner`` + ``axes`` 명시. deadline 지나면 audit fail.
#
# 본 목록은 FILE_SIZE_ALLOWLIST 와 달리 verdict 가 ``split_pending`` 으로
# 별도 bucket 에 surface. operator 가 "무한 예외" 와 "데드라인 있는
# in-flight" 를 시각적으로 구분.
# ---------------------------------------------------------------------------


SPLIT_NOW_PENDING: Mapping[str, Dict[str, str]] = {
    # ``runtime/run_service.py`` 는 본 PR 에서 heartbeats, discord_runner,
    # work_order_executor_runner 로 추출 완료 (1387 → 980 LOC, split_now 해소).
    "apps/engineering-agent/src/yule_engineering/runtime/status.py": {
        "deadline": "2026-06-21",
        "owner": "codwithyc",
        "axes": "builder, renderer, operator_actions, journal",
    },
    "apps/engineering-agent/src/yule_engineering/agents/research/collector.py": {
        "deadline": "2026-05-31",
        "owner": "codwithyc",
        "axes": "provider adapter modules",
    },
    "apps/engineering-agent/src/yule_engineering/agents/deliberation.py": {
        "deadline": "2026-06-07",
        "owner": "codwithyc",
        "axes": "synthesis, open_research",
    },
    "apps/engineering-agent/src/yule_engineering/agents/job_queue/forum_obsidian_handoff.py": {
        "deadline": "2026-06-21",
        "owner": "codwithyc",
        "axes": "intake, routing, persistence",
    },
    "apps/engineering-agent/src/yule_engineering/agents/obsidian/export.py": {
        "deadline": "2026-06-14",
        "owner": "codwithyc",
        "axes": "writer, renderer",
    },
    "apps/discord-gateway/src/yule_discord/commands/__init__.py": {
        "deadline": "2026-06-14",
        "owner": "codwithyc",
        "axes": "command group split",
    },
    "apps/engineering-agent/src/yule_engineering/agents/engineering_conversation/research_bootstrap.py": {
        "deadline": "2026-06-14",
        "owner": "codwithyc",
        "axes": "bootstrap, runtime, formatting",
    },
    "apps/engineering-agent/src/yule_engineering/agents/job_queue/coding_executor_live.py": {
        "deadline": "2026-06-14",
        "owner": "codwithyc",
        "axes": "live runner, formatting",
    },
    "apps/discord-gateway/src/yule_discord/engineering_channel_router/main.py": {
        "deadline": "2026-06-14",
        "owner": "codwithyc",
        "axes": "router, formatting",
    },
    "apps/engineering-agent/src/yule_engineering/cli/github_workos.py": {
        "deadline": "2026-06-14",
        "owner": "codwithyc",
        "axes": "subcommand per module",
    },
    "apps/engineering-agent/src/yule_engineering/agents/job_queue/obsidian_writer_worker.py": {
        "deadline": "2026-06-14",
        "owner": "codwithyc",
        "axes": "worker loop, persistence, formatting",
    },
    "apps/engineering-agent/src/yule_engineering/agents/job_queue/coding_executor_worker.py": {
        "deadline": "2026-06-14",
        "owner": "codwithyc",
        "axes": "process_job pipeline, reason classification, progress stamping",
    },
    "apps/engineering-agent/src/yule_engineering/runtime/coding_executor_runner.py": {
        "deadline": "2026-06-21",
        "owner": "codwithyc",
        "axes": "background loops (producer / target_repo / pr_merge_continuation) + executor builders (live merge + approval enqueuer + next slice dispatcher) + recovery sweeps",
    },
}


# ---------------------------------------------------------------------------
# Responsibility heuristic
# ---------------------------------------------------------------------------


# 한 파일에서 동시에 보이면 책임 signal 로 분류되는 keyword. 3 종 이상 +
# 1000 LOC 초과면 hard fail.
_RESPONSIBILITY_SIGNALS: Mapping[str, Tuple[str, ...]] = {
    "intake": ("intake", "_run_engineer_intake", "Orchestrator.intake"),
    "intent_classification": (
        "detect_coding_intent",
        "classify(",
        "_suggest_task_type",
        "is_research_only",
    ),
    "routing": (
        "route_engineering_message",
        "route_approval_channel_message",
        "_pick_filters_for",
        "Dispatcher.dispatch",
    ),
    "state_persistence": (
        "save_session",
        "update_session",
        "save_json_cache",
        "load_json_cache",
        "JobQueue(",
        "HeartbeatStore(",
    ),
    "formatting": (
        "render_runtime_status",
        "render_authorization_message",
        "format_intake_message",
        "_render_outcome_message",
        "render_approval_request",
    ),
    "external_integration": (
        "LiveGithubAppClient",
        "build_production_post_fn",
        "discord.Client",
        "discord.LoginFailure",
        "build_engineering_gateway_bot",
    ),
    "runtime_orchestration": (
        "run_supervisor_watch_loop",
        "run_worker_loop",
        "_run_async",
        "asyncio.Event",
        "run_until_shutdown",
        "_install_signal_handlers",
    ),
    "github_workflow": (
        "GithubWriter",
        "create_draft_pull_request",
        "create_branch_ref",
        "derive_branch_name",
    ),
    "discord_runtime": (
        "build_engineering_gateway_bot",
        "run_member_bot_until_shutdown",
        "DISCORD_MEMBER_BOT",
        "DISCORD_GATEWAY",
    ),
    "self_improvement": (
        "self_improvement_detect",
        "self_improvement_dispatch",
        "build_self_improvement",
    ),
    "recovery_orchestration": (
        "requeue_retryable",
        "requeue_no_repo_failures",
        "FAILED_RETRYABLE",
        "_recover_repo_for_work_order",
    ),
    "status_rendering": (
        "render_runtime_status",
        "build_runtime_status",
        "summarize_operator_actions",
        "build_compact_status_summary",
    ),
}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileSizeRow:
    path: str
    loc: int
    verdict: str
    reason: str = ""
    responsibilities: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FileSizeAudit:
    """`audit_orchestrator_file_sizes` 결과.

    - ``violations``: split_now (1000+ + 책임 ≥ 2 + allowlist 없음 +
      pending 없음) 들. hard fail 대상.
    - ``warnings``: split_soon / warn — operator 가 PR 본문에 이유 명시
      필요.
    - ``allowed_exceptions``: 영구 allowlist 통과 항목.
    - ``split_pending``: deadline 있는 in-flight split 항목. deadline
      당일까지 통과, 지나면 ``violations`` 로 escalate.
    - ``stale_allowlist``: allowlist 에는 있지만 더 이상 위반 없는 항목
      (cleanup 후보).
    """

    rows: Tuple[FileSizeRow, ...]
    violations: Tuple[FileSizeRow, ...] = ()
    warnings: Tuple[FileSizeRow, ...] = ()
    allowed_exceptions: Tuple[FileSizeRow, ...] = ()
    split_pending: Tuple[FileSizeRow, ...] = ()
    stale_allowlist: Tuple[str, ...] = ()

    def is_blocking(self) -> bool:
        return bool(self.violations)


def _classify_row(
    *,
    rel_path: str,
    loc: int,
    responsibilities: Sequence[str],
    today: date,
) -> Tuple[str, str]:
    """Decide (verdict, reason) for one file.

    Order:
    1) 명시 allowlist → ``exception``.
    2) loc < WARN_LOC → ``safe``.
    3) loc ≥ SPLIT_LOC + responsibilities ≥ 2:
       - SPLIT_NOW_PENDING 등록 & deadline 미경과 → ``split_pending``.
       - 그 외 → ``split_now`` (HARD FAIL).
    4) loc ≥ SPLIT_LOC + responsibilities < 2 → ``split_soon``.
    5) WARN_LOC ≤ loc < SPLIT_LOC → ``warn``.
    """

    if rel_path in FILE_SIZE_ALLOWLIST:
        return VERDICT_EXCEPTION, FILE_SIZE_ALLOWLIST[rel_path]
    if loc < WARN_LOC:
        return VERDICT_SAFE, ""
    if loc >= SPLIT_LOC:
        if len(responsibilities) >= 2:
            pending = SPLIT_NOW_PENDING.get(rel_path)
            if pending is not None:
                deadline_iso = str(pending.get("deadline", "")).strip()
                if deadline_iso:
                    try:
                        deadline_d = date.fromisoformat(deadline_iso)
                    except ValueError:
                        deadline_d = today  # 잘못된 데드라인 → 즉시 fail
                else:
                    deadline_d = today
                if deadline_d >= today:
                    axes = str(pending.get("axes", "")).strip()
                    owner = str(pending.get("owner", "")).strip()
                    return (
                        VERDICT_SPLIT_PENDING,
                        f"{loc} LOC + {len(responsibilities)} responsibilities — "
                        f"deadline {deadline_iso} ({owner}) — split axes: {axes}",
                    )
            return (
                VERDICT_SPLIT_NOW,
                f"{loc} LOC + {len(responsibilities)} responsibilities "
                f"({', '.join(responsibilities[:5])})",
            )
        return (
            VERDICT_SPLIT_SOON,
            f"{loc} LOC ≥ {SPLIT_LOC} — split recommended",
        )
    return (
        VERDICT_WARN,
        f"{loc} LOC — between {WARN_LOC} and {SPLIT_LOC}",
    )


def detect_mixed_responsibilities(*, text: str) -> Tuple[str, ...]:
    """File 본문에 동시에 보이는 책임 signal 들 (deduped, sorted)."""

    hits: List[str] = []
    for resp, keywords in _RESPONSIBILITY_SIGNALS.items():
        for kw in keywords:
            if kw in text:
                hits.append(resp)
                break
    return tuple(sorted(set(hits)))


DEFAULT_PACKAGE_ROOTS: Tuple[str, ...] = (
    "apps/engineering-agent/src/yule_engineering",
    # 모놀리스에서 추출된 app 패키지 — 큰 파일이 ``apps/*/src`` 로 이동해도
    # LOC / responsibility audit 와 allowlist 검증이 계속 적용되도록 스캔 대상에
    # 포함한다 (planning-agent / discord-gateway 등).
    "apps/planning-agent/src",
    "apps/discord-gateway/src",
)


def audit_orchestrator_file_sizes(
    *,
    repo_root: Path,
    package_root: Union[str, Sequence[str]] = DEFAULT_PACKAGE_ROOTS,
    skip_dirs: Sequence[str] = (".venv", "__pycache__"),
    today: Optional[date] = None,
) -> FileSizeAudit:
    """`<repo>/<package_root>` 전수 LOC + responsibility audit.

    ``package_root`` 는 단일 경로(str) 또는 경로 목록(Sequence[str]). 모놀리스
    ``apps/engineering-agent/src/yule_engineering`` 와 추출된 ``apps/*/src`` 를 함께 스캔해 파일이
    이동해도 audit 가 끊기지 않도록 한다.

    Args:
        today: deadline 비교 기준 날짜. None 이면 ``date.today()``.

    Returns:
        FileSizeAudit — rows (전체) + violations / warnings / exceptions
        / split_pending / stale_allowlist 분류. caller 가
        ``is_blocking()`` 으로 fail 결정.
    """

    today = today or date.today()
    roots = (package_root,) if isinstance(package_root, str) else tuple(package_root)
    rows: List[FileSizeRow] = []
    seen_rel: set = set()
    scanned_any = False
    for root in roots:
        base = Path(repo_root) / root
        if not base.is_dir():
            continue
        scanned_any = True
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(repo_root).as_posix()
            if rel in seen_rel:
                continue
            seen_rel.add(rel)
            if any(part in skip_dirs for part in path.parts):
                continue
            try:
                with path.open("r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:  # noqa: BLE001
                continue
            loc = text.count("\n") + (0 if text.endswith("\n") else 1)
            responsibilities = detect_mixed_responsibilities(text=text)
            verdict, reason = _classify_row(
                rel_path=rel,
                loc=loc,
                responsibilities=responsibilities,
                today=today,
            )
            rows.append(
                FileSizeRow(
                    path=rel,
                    loc=loc,
                    verdict=verdict,
                    reason=reason,
                    responsibilities=responsibilities,
                )
            )

    # 스캔된 root 가 하나도 없으면 (예: 존재하지 않는 repo_root) allowlist
    # 검증을 적용하지 않고 빈 audit 을 돌려준다 — 전수 스캔이 일어나지 않은
    # 것을 "모든 allowlist 가 stale" 로 오해하지 않도록.
    if not scanned_any:
        return FileSizeAudit(rows=())

    violations = tuple(r for r in rows if r.verdict == VERDICT_SPLIT_NOW)
    warnings = tuple(
        r for r in rows if r.verdict in (VERDICT_SPLIT_SOON, VERDICT_WARN)
    )
    exceptions = tuple(r for r in rows if r.verdict == VERDICT_EXCEPTION)
    pending = tuple(r for r in rows if r.verdict == VERDICT_SPLIT_PENDING)

    seen_paths = {r.path for r in rows}
    stale = tuple(
        sorted(
            p
            for p in FILE_SIZE_ALLOWLIST
            if p not in seen_paths
            or _row_loc(seen_paths, rows, p) < WARN_LOC
        )
    )
    return FileSizeAudit(
        rows=tuple(rows),
        violations=violations,
        warnings=warnings,
        allowed_exceptions=exceptions,
        split_pending=pending,
        stale_allowlist=stale,
    )


def _row_loc(seen_paths: set, rows: Sequence[FileSizeRow], rel_path: str) -> int:
    if rel_path not in seen_paths:
        return 0
    for row in rows:
        if row.path == rel_path:
            return row.loc
    return 0


# ---------------------------------------------------------------------------
# Missing worker wiring detector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MissingWiringReport:
    unmapped_job_types: Tuple[str, ...]
    mapped_job_types: Tuple[str, ...]

    def is_blocking(self) -> bool:
        return bool(self.unmapped_job_types)


def detect_missing_worker_wiring(
    *,
    declared_job_types: Iterable[str],
    kind_to_job_type: Mapping[Any, Optional[str]],
) -> MissingWiringReport:
    """JOB_TYPE_* 상수 중 ServiceKind 매핑이 없는 케이스 검출.

    queue row 가 enqueue 되는데 consumer 가 없는 wiring miss 회귀 차단.
    *kind_to_job_type* 의 None 매핑 (supervisor / gateway / member_bot) 은
    queue consumer 가 아니라 제외.
    """

    declared = {str(t).strip() for t in declared_job_types if str(t).strip()}
    mapped = {
        str(v).strip()
        for v in kind_to_job_type.values()
        if v is not None and str(v).strip()
    }
    missing = tuple(sorted(declared - mapped))
    return MissingWiringReport(
        unmapped_job_types=missing,
        mapped_job_types=tuple(sorted(mapped)),
    )


# ---------------------------------------------------------------------------
# Retryable-without-recovery detector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryGapReport:
    uncovered_reasons: Tuple[str, ...]
    covered_reasons: Tuple[str, ...]

    def is_blocking(self) -> bool:
        return bool(self.uncovered_reasons)


def detect_retryable_without_recovery(
    *,
    declared_retryable_reasons: Iterable[str],
    registered_recovery_reasons: Iterable[str],
    known_transient: Iterable[str] = (),
) -> RecoveryGapReport:
    """`failed_retryable` 로 떨어지는 error reason 중 startup-recovery
    hook 이 등록되지 않은 케이스 검출.

    *known_transient* — 의도적으로 backoff 만 두는 transient (예:
    Discord 429 / 5xx). 본 집합은 fail 로 보지 않음.
    """

    declared = {str(r).strip() for r in declared_retryable_reasons if str(r).strip()}
    covered = {str(r).strip() for r in registered_recovery_reasons if str(r).strip()}
    transient = {str(r).strip() for r in known_transient if str(r).strip()}
    uncovered = tuple(sorted(declared - covered - transient))
    return RecoveryGapReport(
        uncovered_reasons=uncovered,
        covered_reasons=tuple(sorted(covered)),
    )


# ---------------------------------------------------------------------------
# Rendering helper for operator surfaces
# ---------------------------------------------------------------------------


def render_audit_summary(audit: FileSizeAudit) -> str:
    """Operator-visible audit summary (한국어 + LOC + responsibilities)."""

    lines: list[str] = []
    if audit.violations:
        lines.append(
            f"🚫 split_now 위반 {len(audit.violations)} 건 — 본 PR 에서 분리 필수:"
        )
        for row in audit.violations:
            lines.append(
                f"  - {row.path} ({row.loc} LOC): {row.reason}"
            )
    if audit.split_pending:
        lines.append(
            f"⏳ split_pending {len(audit.split_pending)} 건 — deadline 내 분리 약속:"
        )
        for row in audit.split_pending:
            lines.append(f"  - {row.path} ({row.loc} LOC) — {row.reason}")
    if audit.warnings:
        lines.append(
            f"⚠️  split_soon / warn {len(audit.warnings)} 건 — PR 본문에 사유 명시:"
        )
        for row in audit.warnings[:10]:
            lines.append(f"  - {row.path} ({row.loc} LOC)")
        if len(audit.warnings) > 10:
            lines.append(f"  ... 외 {len(audit.warnings) - 10}건")
    if audit.allowed_exceptions:
        lines.append(
            f"✅ allowlist {len(audit.allowed_exceptions)} 건 (명시 예외):"
        )
        for row in audit.allowed_exceptions:
            lines.append(f"  - {row.path} ({row.loc} LOC) — {row.reason}")
    if audit.stale_allowlist:
        lines.append(
            f"🧹 stale allowlist {len(audit.stale_allowlist)} 건 — 더 이상 필요 없음, 제거 요망:"
        )
        for path in audit.stale_allowlist:
            lines.append(f"  - {path}")
    if not lines:
        lines.append("✅ orchestrator 파일 크기 audit 통과 (위반 0)")
    return "\n".join(lines)


__all__ = (
    "FILE_SIZE_ALLOWLIST",
    "FileSizeAudit",
    "FileSizeRow",
    "MissingWiringReport",
    "RecoveryGapReport",
    "SPLIT_LOC",
    "SPLIT_NOW_PENDING",
    "VERDICT_EXCEPTION",
    "VERDICT_SAFE",
    "VERDICT_SPLIT_NOW",
    "VERDICT_SPLIT_PENDING",
    "VERDICT_SPLIT_SOON",
    "VERDICT_WARN",
    "WARN_LOC",
    "audit_orchestrator_file_sizes",
    "detect_mixed_responsibilities",
    "detect_missing_worker_wiring",
    "detect_retryable_without_recovery",
    "render_audit_summary",
)
