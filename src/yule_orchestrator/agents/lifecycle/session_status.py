"""Read-only diagnostic over a :class:`WorkflowSession`.

The Discord operator runs ``yule discord up`` and then has no obvious way
to ask "where is each session stuck?" without re-running CLI commands.
Phase E adds a single pure-Python place where we *detect / report /
propose* — never auto-write, never auto-commit.

This module deliberately:

- Imports nothing from the Discord runtime so it can be unit-tested
  without Discord/network present.
- Reads only the persisted ``WorkflowSession`` shape (state +
  ``extra``). It never touches the cache directly.
- Produces a structured :class:`SessionStatusReport` plus a short
  Korean-language summary the gateway / supervisor CLI can print.

Detected states map 1:1 to the operator-facing complaints in Phase E:

- ``research_pack`` 있음 but open-call 없음
- open-call 있음 but role_turn 없음
- role_turn 있음 but synthesis 없음
- synthesis 있음 but Obsidian proposal 없음
- pending Obsidian approval
- Obsidian write failed

The :class:`SessionStatusSignal` carries severity (``info`` / ``stale``
/ ``blocked`` / ``failed``) plus a short proposal so callers can render
"감지된 다음 단계" without re-deriving the rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Signal codes — stable identifiers for the detected session states.
# ---------------------------------------------------------------------------

RESEARCH_PACK_MISSING = "research_pack_missing"
OPEN_CALL_MISSING = "open_call_missing"
OPEN_CALL_FAILED = "open_call_failed"
FORUM_PUBLISH_FAILED = "forum_publish_failed"
ROLE_TURN_MISSING = "role_turn_missing"
SYNTHESIS_MISSING = "synthesis_missing"
OBSIDIAN_PROPOSAL_MISSING = "obsidian_proposal_missing"
OBSIDIAN_PENDING_APPROVAL = "obsidian_pending_approval"
OBSIDIAN_WRITE_FAILED = "obsidian_write_failed"
RESEARCH_LOOP_ERROR = "research_loop_error"
SESSION_CLOSED = "session_closed"
CODING_PROPOSAL_PENDING = "coding_proposal_pending"
CODING_JOB_READY = "coding_job_ready"


# Severity ordering — used by callers that want to surface the "worst"
# signal first. ``failed`` outranks ``blocked`` outranks ``stale``
# outranks ``info``. The renderer below is severity-stable.
_SEVERITY_ORDER = {"failed": 0, "blocked": 1, "stale": 2, "info": 3}


@dataclass(frozen=True)
class SessionStatusSignal:
    """One detected state for a workflow session.

    ``code`` is a stable id (the constants above) so tests / supervisor
    CLI can match without depending on the Korean label phrasing.

    ``propose`` is intentionally a *suggestion*, not an action. Phase E
    is detect/report/propose only — auto-execute is a future phase.
    """

    code: str
    severity: str
    title: str
    detail: Optional[str] = None
    propose: Optional[str] = None


@dataclass(frozen=True)
class SessionStatusReport:
    """Structured snapshot of a workflow session for the diagnostic layer."""

    session_id: Optional[str]
    state: Optional[str]
    task_type: Optional[str]
    prompt: Optional[str]
    has_research_pack: bool
    forum_thread_id: Optional[int]
    forum_thread_url: Optional[str]
    forum_publish_error: Optional[str]
    forum_comment_mode: Optional[str]
    forum_kickoff_posted: Optional[bool]
    forum_kickoff_error: Optional[str]
    role_sequence: Tuple[str, ...]
    played_roles: Tuple[str, ...]
    has_synthesis: bool
    write_requested: bool
    write_blocked_reason: Optional[str]
    obsidian_proposal_present: bool
    obsidian_write_error: Optional[str]
    research_loop_error: Optional[str]
    last_progress_note: Optional[str]
    coding_proposal_present: bool = False
    coding_job_status: Optional[str] = None
    coding_executor_role: Optional[str] = None
    coding_write_scope: Tuple[str, ...] = ()
    # P0-H stage 2 status surface — gateway 가 session.extra 에 박아 둔
    # work_mode / topology / scope / github_target / repo_contract 결과를
    # 그대로 노출. 모두 optional default 라 기존 caller 무회귀.
    repository: Optional[str] = None  # "owner/repo"
    work_mode: Optional[str] = None
    topology: Optional[str] = None
    scope_mode: Optional[str] = None
    branch_name: Optional[str] = None
    pull_request_number: Optional[int] = None
    repo_contract_detected: bool = False
    repo_contract_summary: Optional[str] = None
    obsidian_mirror_path: Optional[str] = None
    # P0-I stage 3 — enforcement-layer surface fields. 모두 optional.
    tracking_status: Optional[str] = None  # ok / needs_issue / needs_branch / ...
    tracking_blocked: bool = False
    tracking_summary: Optional[str] = None  # status_summary_line text
    growth_ledger_summary: Optional[str] = None
    growth_promotion_candidate_count: int = 0
    vault_push_audit_count: int = 0
    vault_push_not_configured_reason: Optional[str] = None
    pr_slice_primary: Optional[str] = None
    pr_size_warning: bool = False
    signals: Tuple[SessionStatusSignal, ...] = field(default_factory=tuple)

    def has_signal(self, code: str) -> bool:
        return any(signal.code == code for signal in self.signals)

    def signal(self, code: str) -> Optional[SessionStatusSignal]:
        for signal in self.signals:
            if signal.code == code:
                return signal
        return None

    def primary_signal(self) -> Optional[SessionStatusSignal]:
        """Return the highest-severity actionable signal.

        ``info`` signals are skipped — they are "everything fine, just
        nothing to do yet" markers the renderer still surfaces but a
        diagnostic primary should not lead with them.
        """

        candidates = tuple(s for s in self.signals if s.severity != "info")
        if not candidates:
            return None
        return min(candidates, key=lambda s: _SEVERITY_ORDER.get(s.severity, 99))


# ---------------------------------------------------------------------------
# Diagnose
# ---------------------------------------------------------------------------


def diagnose_session(session: Optional[Any]) -> SessionStatusReport:
    """Build a :class:`SessionStatusReport` for *session*.

    A ``None`` session is allowed so callers can pass the result of
    :func:`find_latest_open_session` straight in. The returned report
    has no signals — the renderer treats it as "no session matched".
    """

    if session is None:
        return SessionStatusReport(
            session_id=None,
            state=None,
            task_type=None,
            prompt=None,
            has_research_pack=False,
            forum_thread_id=None,
            forum_thread_url=None,
            forum_publish_error=None,
            forum_comment_mode=None,
            forum_kickoff_posted=None,
            forum_kickoff_error=None,
            role_sequence=(),
            played_roles=(),
            has_synthesis=False,
            write_requested=False,
            write_blocked_reason=None,
            obsidian_proposal_present=False,
            obsidian_write_error=None,
            research_loop_error=None,
            last_progress_note=None,
            signals=(),
        )

    extra = dict(getattr(session, "extra", {}) or {})

    research_pack = extra.get("research_pack")
    has_research_pack = bool(research_pack)

    forum_thread_id = _coerce_int(
        extra.get("research_forum_thread_id") or extra.get("forum_thread_id")
    )
    forum_thread_url = _coerce_str(
        extra.get("forum_thread_url") or extra.get("research_forum_thread_url")
    )
    forum_publish_error = _coerce_str(
        extra.get("forum_publish_error") or extra.get("research_forum_error")
    )
    forum_comment_mode = _coerce_str(extra.get("forum_comment_mode"))
    forum_kickoff_posted = extra.get("forum_kickoff_posted")
    if forum_kickoff_posted is not None:
        forum_kickoff_posted = bool(forum_kickoff_posted)
    forum_kickoff_error = _coerce_str(extra.get("forum_kickoff_error"))

    role_sequence = tuple(
        str(role) for role in (getattr(session, "role_sequence", ()) or ())
    )
    played_roles = _extract_played_roles(extra)
    has_synthesis = bool(extra.get("research_synthesis")) or bool(
        extra.get("research_synthesis_text")
    )

    write_requested = bool(getattr(session, "write_requested", False))
    write_blocked_reason = _coerce_str(getattr(session, "write_blocked_reason", None))

    obsidian_proposal_present = _detect_obsidian_proposal(extra)
    obsidian_write_error = _coerce_str(
        extra.get("obsidian_write_error") or extra.get("obsidian_export_error")
    )

    research_loop_error = _extract_research_loop_error(extra)

    progress_notes = tuple(getattr(session, "progress_notes", ()) or ())
    last_progress_note = str(progress_notes[-1]) if progress_notes else None

    coding_proposal_payload = extra.get("coding_proposal")
    coding_job_payload = extra.get("coding_job")
    coding_proposal_present = isinstance(coding_proposal_payload, Mapping) and bool(
        coding_proposal_payload
    )
    coding_job_status: Optional[str] = None
    coding_executor_role: Optional[str] = None
    coding_write_scope: Tuple[str, ...] = ()
    if isinstance(coding_job_payload, Mapping) and coding_job_payload:
        coding_job_status = _coerce_str(coding_job_payload.get("status"))
        coding_executor_role = _coerce_str(coding_job_payload.get("executor_role"))
        raw_scope = coding_job_payload.get("write_scope") or ()
        if isinstance(raw_scope, (list, tuple)):
            coding_write_scope = tuple(str(item) for item in raw_scope if item)
    elif coding_proposal_present and isinstance(coding_proposal_payload, Mapping):
        coding_job_status = "pending-approval"
        coding_executor_role = _coerce_str(
            coding_proposal_payload.get("executor_role")
        )
        raw_scope = coding_proposal_payload.get("write_scope") or ()
        if isinstance(raw_scope, (list, tuple)):
            coding_write_scope = tuple(str(item) for item in raw_scope if item)

    # P0-H stage 2 — surface gateway-decided fields from session.extra.
    github_target_payload = extra.get("github_target")
    repository: Optional[str] = None
    pull_request_number: Optional[int] = None
    branch_name: Optional[str] = None
    if isinstance(github_target_payload, Mapping) and github_target_payload:
        owner = _coerce_str(github_target_payload.get("owner"))
        repo = _coerce_str(github_target_payload.get("repo"))
        if owner and repo:
            repository = f"{owner}/{repo}"
        kind = _coerce_str(github_target_payload.get("kind"))
        if kind == "pull_request":
            pull_request_number = _coerce_int(github_target_payload.get("number"))
        branch_name = _coerce_str(github_target_payload.get("branch_or_sha"))
    # explicit branch override stored separately wins over tree/blob derive.
    explicit_branch = _coerce_str(extra.get("branch_name"))
    if explicit_branch:
        branch_name = explicit_branch
    # explicit PR number override (e.g. work item not pasted via URL).
    explicit_pr = _coerce_int(extra.get("pull_request_number"))
    if explicit_pr is not None:
        pull_request_number = explicit_pr

    work_mode = _coerce_str(extra.get("work_mode"))
    topology = _coerce_str(extra.get("topology"))
    scope_mode = _coerce_str(extra.get("scope"))

    repo_contract_payload = extra.get("repo_contract")
    repo_contract_detected = False
    repo_contract_summary: Optional[str] = None
    if isinstance(repo_contract_payload, Mapping) and repo_contract_payload:
        repo_contract_detected = not bool(repo_contract_payload.get("fallback"))
        # Prefer the precomputed summary stored alongside the contract
        # so the renderer doesn't need to import the helper.
        repo_contract_summary = _coerce_str(
            extra.get("repo_contract_summary")
        ) or _coerce_str(repo_contract_payload.get("summary_line"))

    obsidian_mirror_path = _coerce_str(extra.get("obsidian_mirror_path"))

    # P0-I stage 3 — enforcement surface from session.extra.
    tracking_payload = extra.get("tracking_validation")
    tracking_status: Optional[str] = None
    tracking_blocked = False
    tracking_summary: Optional[str] = None
    if isinstance(tracking_payload, Mapping) and tracking_payload:
        tracking_status = _coerce_str(tracking_payload.get("status"))
        tracking_blocked = bool(tracking_payload.get("blocked"))
        # Rebuild a one-line summary cheaply without importing the
        # validator module (caller may not need it loaded).
        missing = tracking_payload.get("missing_links") or ()
        allowed_ex = bool(tracking_payload.get("allowed_via_contract_exception"))
        if tracking_status == "ok":
            tracking_summary = "✅ tracking chain complete"
        elif tracking_status == "standalone_no_target":
            tracking_summary = "ℹ️ tracking chain: GitHub target 없음 (research/discussion only)"
        else:
            flag = "⚠️" if tracking_blocked else "ℹ️"
            missing_text = (
                ", ".join(str(m) for m in missing) if missing else "unknown"
            )
            suffix = " (RepoContract 예외 적용)" if allowed_ex else ""
            tracking_summary = (
                f"{flag} tracking chain: missing {missing_text}{suffix}"
            )

    growth_ledger = extra.get("growth_ledger")
    growth_ledger_summary: Optional[str] = None
    growth_promotion_candidate_count = 0
    if isinstance(growth_ledger, list) and growth_ledger:
        # Cheap inline counts; full helper imported only when present.
        try:
            from .growth_ledger import summarize_for_status

            growth_ledger_summary = summarize_for_status(extra)
        except Exception:  # noqa: BLE001
            growth_ledger_summary = f"🌱 growth ledger: {len(growth_ledger)} events"
        candidates = extra.get("growth_promotion_candidates") or ()
        growth_promotion_candidate_count = len(candidates) if isinstance(candidates, list) else 0

    vault_push_audit = extra.get("vault_push_audit")
    vault_push_audit_count = (
        len(vault_push_audit) if isinstance(vault_push_audit, list) else 0
    )
    vault_push_not_configured_reason = _coerce_str(
        extra.get("vault_push_not_configured_reason")
    )

    pr_slice_payload = extra.get("pr_slice_classification")
    pr_slice_primary: Optional[str] = None
    pr_size_warning = False
    if isinstance(pr_slice_payload, Mapping) and pr_slice_payload:
        pr_slice_primary = _coerce_str(pr_slice_payload.get("primary_slice"))
        pr_size_warning = bool(pr_slice_payload.get("size_warning"))

    state_value = getattr(session, "state", None)
    state_label = getattr(state_value, "value", state_value)

    signals = _detect_signals(
        state=state_label,
        has_research_pack=has_research_pack,
        forum_thread_id=forum_thread_id,
        forum_publish_error=forum_publish_error,
        forum_comment_mode=forum_comment_mode,
        forum_kickoff_posted=forum_kickoff_posted,
        forum_kickoff_error=forum_kickoff_error,
        played_roles=played_roles,
        role_sequence=role_sequence,
        has_synthesis=has_synthesis,
        write_requested=write_requested,
        write_blocked_reason=write_blocked_reason,
        obsidian_proposal_present=obsidian_proposal_present,
        obsidian_write_error=obsidian_write_error,
        research_loop_error=research_loop_error,
        coding_proposal_present=coding_proposal_present,
        coding_job_status=coding_job_status,
        coding_executor_role=coding_executor_role,
    )
    # C3 — append council-specific signals so the operator can see why a
    # session has not advanced to synthesis. Import inline so this 854-
    # line module does not pull council deps when the path is unused.
    try:
        from .council_status_signals import collect_council_signals

        council_signals = collect_council_signals(extra, intake_completed=True)
        if council_signals:
            signals = (*signals, *council_signals)
    except Exception:  # noqa: BLE001 — best-effort, never block diagnose
        pass

    return SessionStatusReport(
        session_id=_coerce_str(getattr(session, "session_id", None)),
        state=_coerce_str(state_label),
        task_type=_coerce_str(getattr(session, "task_type", None)),
        prompt=_coerce_str(getattr(session, "prompt", None)),
        has_research_pack=has_research_pack,
        forum_thread_id=forum_thread_id,
        forum_thread_url=forum_thread_url,
        forum_publish_error=forum_publish_error,
        forum_comment_mode=forum_comment_mode,
        forum_kickoff_posted=forum_kickoff_posted,
        forum_kickoff_error=forum_kickoff_error,
        role_sequence=role_sequence,
        played_roles=played_roles,
        has_synthesis=has_synthesis,
        write_requested=write_requested,
        write_blocked_reason=write_blocked_reason,
        obsidian_proposal_present=obsidian_proposal_present,
        obsidian_write_error=obsidian_write_error,
        research_loop_error=research_loop_error,
        last_progress_note=last_progress_note,
        coding_proposal_present=coding_proposal_present,
        coding_job_status=coding_job_status,
        coding_executor_role=coding_executor_role,
        coding_write_scope=coding_write_scope,
        repository=repository,
        work_mode=work_mode,
        topology=topology,
        scope_mode=scope_mode,
        branch_name=branch_name,
        pull_request_number=pull_request_number,
        repo_contract_detected=repo_contract_detected,
        repo_contract_summary=repo_contract_summary,
        obsidian_mirror_path=obsidian_mirror_path,
        tracking_status=tracking_status,
        tracking_blocked=tracking_blocked,
        tracking_summary=tracking_summary,
        growth_ledger_summary=growth_ledger_summary,
        growth_promotion_candidate_count=growth_promotion_candidate_count,
        vault_push_audit_count=vault_push_audit_count,
        vault_push_not_configured_reason=vault_push_not_configured_reason,
        pr_slice_primary=pr_slice_primary,
        pr_size_warning=pr_size_warning,
        signals=signals,
    )


def _detect_signals(
    *,
    state: Optional[str],
    has_research_pack: bool,
    forum_thread_id: Optional[int],
    forum_publish_error: Optional[str],
    forum_comment_mode: Optional[str],
    forum_kickoff_posted: Optional[bool],
    forum_kickoff_error: Optional[str],
    played_roles: Sequence[str],
    role_sequence: Sequence[str],
    has_synthesis: bool,
    write_requested: bool,
    write_blocked_reason: Optional[str],
    obsidian_proposal_present: bool,
    obsidian_write_error: Optional[str],
    research_loop_error: Optional[str],
    coding_proposal_present: bool = False,
    coding_job_status: Optional[str] = None,
    coding_executor_role: Optional[str] = None,
) -> Tuple[SessionStatusSignal, ...]:
    """Walk the session shape and emit signals in pipeline order."""

    signals: list[SessionStatusSignal] = []

    if state in {"completed", "rejected"}:
        signals.append(
            SessionStatusSignal(
                code=SESSION_CLOSED,
                severity="info",
                title=f"세션 종료 상태({state})",
                propose="추가 작업이 필요하면 새 세션을 시작하세요.",
            )
        )
        return tuple(signals)

    # 1) research_pack 단계 진단
    if not has_research_pack:
        signals.append(
            SessionStatusSignal(
                code=RESEARCH_PACK_MISSING,
                severity="info",
                title="research_pack 미수집",
                detail="아직 1차 자료가 모이지 않은 단계입니다.",
                propose=(
                    "intake 메시지에 자료/링크를 보강하거나 collector 실행 결과를 확인하세요."
                ),
            )
        )

    # 2) forum publish / open-call 단계
    if has_research_pack and forum_thread_id is None and forum_publish_error:
        signals.append(
            SessionStatusSignal(
                code=FORUM_PUBLISH_FAILED,
                severity="failed",
                title="운영-리서치 forum 게시 실패",
                detail=forum_publish_error,
                propose="forum publisher 로그/권한을 확인하고 다시 시도하세요.",
            )
        )
    elif (
        has_research_pack
        and forum_thread_id is None
        and not forum_publish_error
    ):
        signals.append(
            SessionStatusSignal(
                code=OPEN_CALL_MISSING,
                severity="stale",
                title="research_pack 있음 · open-call 미게시",
                detail="자료는 모였지만 운영-리서치 thread / open-call 단계가 아직 실행되지 않았습니다.",
                propose=(
                    "publisher 단계가 호출됐는지 / starter 메시지가 4000자를 넘었는지 점검하세요."
                ),
            )
        )

    # member-bots 모드에서 open-call directive 자체가 실패한 경우
    if forum_comment_mode == "member-bots":
        if forum_kickoff_posted is False:
            signals.append(
                SessionStatusSignal(
                    code=OPEN_CALL_FAILED,
                    severity="failed",
                    title="open-call directive 게시 실패",
                    detail=forum_kickoff_error or "원인 미확인",
                    propose=(
                        "gateway 봇 권한과 forum_kickoff 재시도를 점검하세요. (auto-retry 없음)"
                    ),
                )
            )

    # 3) role_turn 단계 — open-call(또는 forum thread)은 있는데 멤버 turn이 없을 때
    open_call_active = (
        forum_thread_id is not None
        and (
            forum_comment_mode != "member-bots"
            or forum_kickoff_posted is True
        )
    )
    if open_call_active and not played_roles:
        signals.append(
            SessionStatusSignal(
                code=ROLE_TURN_MISSING,
                severity="stale",
                title="open-call 게시됨 · 멤버 봇 turn 없음",
                detail=(
                    "운영-리서치 forum은 열렸지만 어떤 역할도 아직 응답하지 않았습니다."
                ),
                propose=(
                    "멤버 봇들이 살아 있는지(`yule discord up` 인벤토리), "
                    "그리고 토큰이 채워졌는지 점검하세요."
                ),
            )
        )

    # 4) synthesis 단계 — role_turn은 있는데 synthesis가 없을 때
    if played_roles and not has_synthesis:
        signals.append(
            SessionStatusSignal(
                code=SYNTHESIS_MISSING,
                severity="stale",
                title="role turn 있음 · tech-lead synthesis 없음",
                detail=(
                    f"{len(played_roles)}개 역할이 응답했지만 synthesis 단계가 아직 기록되지 않았습니다."
                ),
                propose="research_loop synthesize 단계가 호출됐는지 / 오류 로그가 있는지 확인하세요.",
            )
        )

    # 5) Obsidian 단계 — synthesis는 있는데 proposal이 없을 때
    if has_synthesis and not obsidian_proposal_present and not obsidian_write_error:
        signals.append(
            SessionStatusSignal(
                code=OBSIDIAN_PROPOSAL_MISSING,
                severity="stale",
                title="synthesis 있음 · Obsidian proposal 없음",
                detail="vault 내보내기 제안이 아직 만들어지지 않았습니다.",
                propose=(
                    "운영자가 `yule obsidian sync --session <id> --dry-run` 으로 미리 확인하세요."
                ),
            )
        )

    # 6) Pending Obsidian approval — workflow가 이미 막아둔 상태
    if write_requested and write_blocked_reason:
        signals.append(
            SessionStatusSignal(
                code=OBSIDIAN_PENDING_APPROVAL,
                severity="blocked",
                title="Obsidian write 승인 대기",
                detail=write_blocked_reason,
                propose=(
                    "검토 후 `yule engineer approve --session <id>` 로 승인하거나 reject 하세요."
                ),
            )
        )

    # 7) Obsidian write failed — 가장 최근 실패가 extra에 남아 있을 때
    if obsidian_write_error:
        signals.append(
            SessionStatusSignal(
                code=OBSIDIAN_WRITE_FAILED,
                severity="failed",
                title="Obsidian write 실패",
                detail=obsidian_write_error,
                propose=(
                    "vault 경로/권한과 git working tree 상태를 점검한 뒤 sync를 다시 시도하세요."
                ),
            )
        )

    # 7b) Coding authorization 단계 — proposal pending이면 사용자 승인 대기.
    if coding_proposal_present and coding_job_status in {None, "pending-approval"}:
        signals.append(
            SessionStatusSignal(
                code=CODING_PROPOSAL_PENDING,
                severity="blocked",
                title="코딩 권한 제안 승인 대기",
                detail=(
                    f"executor 후보: `{coding_executor_role or 'unknown'}` — "
                    "사용자 승인 phrase가 도착해야 coding job이 ready로 전환됩니다."
                ),
                propose=(
                    "Discord에서 `수정 승인` / `이대로 구현 진행` / `구현 시작` 중 하나로 답하거나, "
                    "권한이 잘못 잡혔으면 `코딩 권한 제안`으로 다시 요청하세요."
                ),
            )
        )
    elif coding_job_status == "ready":
        signals.append(
            SessionStatusSignal(
                code=CODING_JOB_READY,
                severity="info",
                title="코딩 권한 승인 완료",
                detail=(
                    f"executor: `{coding_executor_role or 'unknown'}` — "
                    "executor에게 안전한 prompt가 만들어진 상태입니다."
                ),
                propose=(
                    "executor가 계획을 보여주고 사용자 추가 승인을 받은 뒤에만 실제 코드 변경을 진행하세요."
                ),
            )
        )

    # 8) research-loop hook 자체가 에러를 보고한 경우
    if research_loop_error:
        signals.append(
            SessionStatusSignal(
                code=RESEARCH_LOOP_ERROR,
                severity="failed",
                title="research loop 보고 오류",
                detail=research_loop_error,
                propose="bot 로그에서 마지막 publish/synthesize 에러를 확인하세요.",
            )
        )

    return tuple(signals)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_diagnostic_summary(report: SessionStatusReport) -> str:
    """Multi-line Korean summary aimed at the Discord status response.

    Lines mirror :func:`format_status_diagnostic_response` so both call
    sites stay consistent without copy-pasting the rule logic. When no
    signals fire we still print the basic state header.
    """

    if report.session_id is None:
        return (
            "현재 채널/스레드에 매칭되는 열린 engineering-agent 세션이 보이지 않아요.\n"
            "확인하려는 작업의 session id를 알려 주시거나, "
            "이어갈 thread 안에서 다시 말씀해 주세요."
        )

    lines: list[str] = ["현재 engineering-agent 세션 상태를 확인했어요.", ""]
    lines.append(f"- 세션: `{report.session_id}`")
    lines.append(f"- 상태: {report.state or 'unknown'}")
    lines.append(f"- 종류: {report.task_type or 'unknown'}")
    if report.coding_job_status:
        executor = report.coding_executor_role or "unknown"
        lines.append(
            f"- coding_job: {report.coding_job_status} (executor=`{executor}`)"
        )
        if report.coding_write_scope:
            scope_preview = ", ".join(report.coding_write_scope[:3])
            if len(report.coding_write_scope) > 3:
                scope_preview += " 외"
            lines.append(f"  · write_scope: {scope_preview}")

    actionable = tuple(s for s in report.signals if s.severity != "info")
    if actionable:
        lines.append("")
        lines.append("감지된 다음 단계:")
        for signal in actionable:
            tag = _severity_tag(signal.severity)
            lines.append(f"- {tag} {signal.title}")
            if signal.detail:
                lines.append(f"  · 상세: {_one_line(signal.detail)}")
            if signal.propose:
                lines.append(f"  · 제안: {_one_line(signal.propose)}")

    if report.last_progress_note:
        short = _one_line(report.last_progress_note)
        if len(short) > 160:
            short = short[:157] + "..."
        lines.append("")
        lines.append(f"마지막 진행 노트: {short}")

    return "\n".join(lines)


def render_member_bot_summary(report: SessionStatusReport) -> str:
    """Short summary tuned to "멤버 봇들은 뭐 하고 있어?" questions.

    The forum thread is where the *actual* role comments land; this
    summary points the operator there instead of duplicating their
    content. It also calls out the open-call directive state explicitly
    so a missing kickoff is surfaced even when the rest of the pipeline
    looks healthy.
    """

    if report.session_id is None:
        return (
            "현재 매칭되는 세션이 없어 멤버 봇 활동을 확인할 수 없어요. "
            "session id를 알려 주시거나 해당 thread에서 다시 호출해 주세요."
        )

    lines: list[str] = [f"멤버 봇 진행 상태 (`{report.session_id}`):"]

    if report.forum_thread_id is None:
        if report.forum_publish_error:
            lines.append(
                f"- 운영-리서치 forum 미게시 — 게시 실패: {_one_line(report.forum_publish_error)}"
            )
        else:
            lines.append("- 운영-리서치 forum이 아직 열리지 않아 멤버 봇이 호출되지 않았어요.")
        return "\n".join(lines)

    mode = report.forum_comment_mode or "(미기록)"
    lines.append(f"- 댓글 모드: {mode}")

    if report.forum_comment_mode == "member-bots":
        if report.forum_kickoff_posted is True:
            lines.append("- open-call directive: 게시 완료")
        elif report.forum_kickoff_posted is False:
            reason = report.forum_kickoff_error or "원인 미확인"
            lines.append(f"- open-call directive: 게시 실패 — {_one_line(reason)}")
        else:
            lines.append("- open-call directive: 상태 미기록")

    if report.played_roles:
        lines.append(
            f"- 응답한 역할({len(report.played_roles)}): {', '.join(report.played_roles)}"
        )
    else:
        lines.append("- 아직 응답한 멤버 봇이 없어요.")

    if report.has_synthesis:
        lines.append("- tech-lead synthesis: 기록됨")
    else:
        lines.append("- tech-lead synthesis: 아직 없음")

    lines.append("- 후속 댓글은 운영-리서치 thread에서 직접 확인해 주세요.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_played_roles(extra: Mapping[str, Any]) -> Tuple[str, ...]:
    """Pull ``team_conversation.played_roles`` defensively from *extra*.

    Older sessions stored the list under a flat ``played_roles`` key.
    Both shapes are tolerated so the diagnostic stays useful across
    cache versions.
    """

    block = extra.get("team_conversation")
    if isinstance(block, Mapping):
        played = block.get("played_roles")
        if played:
            return tuple(str(role) for role in played)
    flat = extra.get("played_roles")
    if flat:
        return tuple(str(role) for role in flat)
    return ()


def _detect_obsidian_proposal(extra: Mapping[str, Any]) -> bool:
    """Heuristic check for whether an Obsidian export proposal exists."""

    for key in (
        "obsidian_export",
        "obsidian_proposal",
        "obsidian_write_result",
        "obsidian_path",
    ):
        if extra.get(key):
            return True
    return False


def _extract_research_loop_error(extra: Mapping[str, Any]) -> Optional[str]:
    report = extra.get("research_loop_report")
    if report is None:
        return None
    if isinstance(report, Mapping):
        error = report.get("error")
    else:
        error = getattr(report, "error", None)
    return _coerce_str(error)


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


_SEVERITY_TAGS = {
    "failed": "[FAILED]",
    "blocked": "[BLOCKED]",
    "stale": "[STALE]",
    "info": "[INFO]",
}


def _severity_tag(severity: str) -> str:
    return _SEVERITY_TAGS.get(severity, f"[{severity.upper()}]")


__all__ = [
    "RESEARCH_PACK_MISSING",
    "OPEN_CALL_MISSING",
    "OPEN_CALL_FAILED",
    "FORUM_PUBLISH_FAILED",
    "ROLE_TURN_MISSING",
    "SYNTHESIS_MISSING",
    "OBSIDIAN_PROPOSAL_MISSING",
    "OBSIDIAN_PENDING_APPROVAL",
    "OBSIDIAN_WRITE_FAILED",
    "RESEARCH_LOOP_ERROR",
    "SESSION_CLOSED",
    "SessionStatusSignal",
    "SessionStatusReport",
    "diagnose_session",
    "render_diagnostic_summary",
    "render_member_bot_summary",
]
