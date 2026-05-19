"""Discord 업무접수 → GitHub WorkOS bridge — G4 adapter.

Responsibilities:

  1. Read a session that just landed through the existing engineering
     channel intake (`engineering_channel_router`) and decide whether
     the user's request needs a GitHub work order. The decision is the
     **AND** of three upstream signals so the adapter never decides
     "needs coding" on its own:

       * ``session.extra['lifecycle_mode'] == 'implementation'`` —
         already populated by ``_persist_lifecycle_mode`` in the
         engineering channel router (which delegates to
         :func:`agents.coding.authorization.recommend_authorization`).
         When this is ``research_only`` we leave the session alone so
         existing research-log / Obsidian flows keep their seat.
       * :func:`agents.job_queue.github_work_order.detect_coding_intent`
         — a *positive* phrase bank (PR / 이슈 / 버그 수정 / 구현 /
         리팩터 / GitHub Actions / 테스트 추가 …). Mere
         ``lifecycle_mode == implementation`` is not enough — we want
         a deliberate user phrase before queuing an approval card.
       * No active duplicate already in flight — dedup keys on
         ``(session_id, source_message_id)`` via the approval queue's
         existing ``ApprovalWorker.find_active`` and on
         ``(session_id, proposal_id)`` via
         :func:`find_active_work_order` for the post-approval row.

  2. Compose a :class:`GitHubWorkOrderProposal` carrying every field
     the approval card and the post-approval dispatcher need
     (source ids, request summary, selected/excluded roles, intent
     evidence, dry-run flag).

  3. Wrap the proposal in an :class:`ApprovalRequest` with
     ``approval_kind = APPROVAL_KIND_ENGINEERING_WRITE`` and hand it to
     :class:`ApprovalWorker.run_one` (or just ``enqueue`` for tests
     that don't want to drive the consumer side). The approval card
     posts to ``#승인-대기`` via the existing channel resolver — no
     new Discord channels are required.

  4. After a user "승인" reply lands, :func:`handle_github_work_approval_reply`
     converts the matched approval row into a :class:`GitHubWorkOrder`
     and enqueues it on the github_work_order queue (still **dry-run
     by default**). Only the executor consumer (G3 work stream) ever
     calls GitHub.

  5. Existing Obsidian / research-log flows are untouched —
     :func:`should_route_to_github_workos` returns False whenever the
     session signal points at obsidian-write or research-log, so the
     adapter is a no-op for those code paths.

Pure-Python: every Discord side effect (post the card, send a reply,
post a status line) is injected. Tests drive the adapter without a
real bot.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional, Sequence, Tuple, Union

from ...agents.job_queue.approval_reply import find_replyable_approval
from ...agents.job_queue.approval_worker import (
    APPROVAL_KIND_ENGINEERING_WRITE,
    ApprovalRequest,
    ApprovalWorker,
)
from ...agents.git.repo_contract import RepoContract
from ...agents.github_workos.issue_auto_create import (
    AUDIT_TEMPLATE_FALLBACK,
    IssueAutoCreateOutcome,
    build_issue_auto_create_plan,
)
from ...agents.job_queue.github_work_order import (
    APPROVAL_KIND_GITHUB_WORK_ORDER,
    GitHubWorkOrder,
    GitHubWorkOrderDispatchOutcome,
    GitHubWorkOrderProposal,
    SKIPPED_AWAITING_APPROVAL,
    SKIPPED_DUPLICATE,
    detect_coding_intent,
    dispatch_github_work_order,
)
from ...agents.job_queue.store import JobQueue


# ---------------------------------------------------------------------------
# Public outcomes
# ---------------------------------------------------------------------------


# Skipped reason constants surfaced via :class:`GitHubWorkApprovalOutcome`.
SKIPPED_RESEARCH_ONLY: str = "research_only"
SKIPPED_NO_CODING_INTENT: str = "no_coding_intent"
SKIPPED_OBSIDIAN_INTENT: str = "obsidian_intent"
SKIPPED_DUPLICATE_APPROVAL: str = "duplicate_approval_in_flight"


@dataclass(frozen=True)
class GitHubWorkApprovalOutcome:
    """Result of :func:`enqueue_github_work_approval`.

    ``proposal`` is None when the adapter decided to no-op (research
    only / no coding signal / obsidian intent). ``approval_job_id`` is
    the queue row backing the approval card. ``approval_post_outcome``
    forwards the underlying :class:`ApprovalJobOutcome` so the caller
    can surface the posted Discord message id without re-running the
    approval consumer side.
    """

    proposal: Optional[GitHubWorkOrderProposal]
    approval_job_id: Optional[str] = None
    approval_post_outcome: Optional[Any] = None
    skipped_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Routing decision
# ---------------------------------------------------------------------------


# Phrases that indicate the user wants the request to land on the
# Obsidian save flow rather than the GitHub WorkOS one. The adapter
# stays out of those sessions so the existing approval / writer chain
# from M10a / M10b keeps owning that surface end-to-end.
_OBSIDIAN_INTENT_PHRASES: Tuple[str, ...] = (
    "obsidian에 정리",
    "obsidian에 저장",
    "옵시디언에 정리",
    "옵시디언에 저장",
    "토의 기록 obsidian에",
    "토의 기록 옵시디언에",
    "vault에 저장",
    "vault 에 저장",
    "knowledge note 저장",
    "knowledge note 만들",
    "knowledge-note 저장",
    "save to obsidian",
    "save to vault",
)


_NORMALIZE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub(" ", (text or "").lower()).strip()


def detect_obsidian_intent(text: str) -> bool:
    """Detect whether *text* is an Obsidian save request the existing
    M10 flow already owns. Used as a no-route guard before the GitHub
    coding-intent detector runs.
    """

    norm = _normalize(text)
    if not norm:
        return False
    return any(phrase in norm for phrase in _OBSIDIAN_INTENT_PHRASES)


def should_route_to_github_workos(
    *,
    session: Any,
    request_text: str,
    approved_continuation: bool = False,
) -> Tuple[bool, str, Optional[str]]:
    """Return ``(eligible, skipped_reason, kind_hint)``.

    ``eligible`` is True iff the adapter should proceed to build a
    proposal. ``skipped_reason`` is one of the ``SKIPPED_*`` constants
    or ``""`` when eligible. ``kind_hint`` is the lifecycle_mode for
    debugging.

    P1-Z3 — *approved_continuation* True 면 fresh intake 가 아니라
    이미 approved 된 세션의 work_order continuation 경로.  intake 단계
    에서 ``lifecycle_mode`` / ``github_target`` / ``coding_handoff_packet``
    이 이미 stamp 됐고, decide_post_approval_action 이 false-positive
    가드까지 통과시킨 상태.  따라서 prompt phrase 기반 coding-intent
    재판정은 **건너뛴다** — structured signals 가 더 강한 source of truth.

    그러나 ``lifecycle_mode == "research_only"`` 와 obsidian intent 는
    어떤 경우든 reject — operator 명시 의도 / domain mismatch 보호.
    """

    if detect_obsidian_intent(request_text):
        return False, SKIPPED_OBSIDIAN_INTENT, None

    extra = getattr(session, "extra", None)
    if not isinstance(extra, Mapping):
        extra = {}

    lifecycle = str(extra.get("lifecycle_mode") or "").strip().lower()
    if lifecycle == "research_only":
        return False, SKIPPED_RESEARCH_ONLY, lifecycle

    if approved_continuation:
        # P1-Z3 — structured signals 신뢰.  prompt phrase 가 약해도
        # decide_post_approval_action 이 이미 lifecycle + target + packet
        # / proposal / anchor / terminal / repo 모든 가드 통과 검증함.
        return True, "", lifecycle or None

    intent = detect_coding_intent(request_text)
    if intent.research_only:
        return False, SKIPPED_RESEARCH_ONLY, "research_only"
    if not intent.coding_required:
        return False, SKIPPED_NO_CODING_INTENT, lifecycle or None
    return True, "", lifecycle or None


# ---------------------------------------------------------------------------
# Proposal builder
# ---------------------------------------------------------------------------


_DEFAULT_TECH_LEAD: str = "tech-lead"

# When session.extra carries no role context we still need a sensible
# default so the approval card has a "검토자" line. Tech-lead is the
# canonical reviewer per CLAUDE.md ("단일 executor + tech-lead 합의").
_FALLBACK_REVIEWERS: Tuple[str, ...] = (_DEFAULT_TECH_LEAD,)


def build_github_work_order_proposal(
    *,
    session: Any,
    request_text: str,
    source_channel_id: Optional[int] = None,
    source_thread_id: Optional[int] = None,
    source_message_id: Optional[int] = None,
    requested_by: str = "",
    repo: Optional[str] = None,
    base_branch: Optional[str] = None,
    proposal_id: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
    summary_max_chars: int = 280,
    repo_contract: Optional[RepoContract] = None,
    issue_template_loader: Optional[Callable[[str], Optional[str]]] = None,
    existing_issue_number: Optional[int] = None,
    approved_continuation: bool = False,
) -> Optional[GitHubWorkOrderProposal]:
    """Compose a :class:`GitHubWorkOrderProposal` for *session* / *request_text*,
    or ``None`` when the request should not flow through the GitHub
    WorkOS bridge (research-only, obsidian save, no coding intent).

    Selected roles are pulled from ``session.extra``:

      * ``coding_proposal.executor_role`` / ``coding_proposal.review_roles`` /
        ``coding_proposal.participant_roles`` — when the engineering
        channel router already ran :func:`recommend_authorization` and
        stashed the result.
      * ``active_research_roles`` — the role-selection layer's pick.
      * ``excluded_research_roles`` — never appears in selected_roles
        regardless of upstream choice.

    The merge keeps tech-lead in the reviewer slot, drops duplicates,
    and preserves first-seen order so the approval card has a stable
    list across re-runs.
    """

    eligible, skipped_reason, _hint = should_route_to_github_workos(
        session=session,
        request_text=request_text,
        approved_continuation=approved_continuation,
    )
    if not eligible:
        return None

    intent = detect_coding_intent(request_text)
    # P1-Z3 — approved continuation 경로에서 prompt 가 약해 intent.coding_required
    # 가 False 라도 (decide_post_approval_action 이 이미 lifecycle/target/packet
    # 가드 통과시킨 상태) proposal 의 ``intent_actions`` 가 빈 튜플로 떨어져
    # operator surface 가 "어떤 작업인지 모름" 으로 보일 수 있다.  최소 한 줄의
    # generic action label 을 부여해 audit 줄이 비어있지 않도록 한다.
    if approved_continuation and not intent.actions:
        from yule_orchestrator.agents.job_queue.github_work_order import CodingIntent

        intent = CodingIntent(
            coding_required=True,
            matched=intent.matched or ("approved_continuation",),
            actions=("코드 변경 (approved continuation)",),
        )
    extras_payload: dict[str, Any] = dict(extra or {})

    selected, excluded = _resolve_roles_from_session(session)
    summary = _short_request_summary(request_text, max_chars=summary_max_chars)

    # P0-S — Issue-less bootstrap. repo_contract 가 주어지면 그 자리에서
    # issue auto-create plan 을 빌드해 proposal payload 에 stamp. existing
    # issue 번호가 명시되면 plan 은 None — worker 가 existing anchor 로
    # 전환. session.extra 가 이미 anchor 를 들고 있어도 동일하게 인식한다
    # (이전 PR 후속 호출에서 한 번 stamp 된 경우).
    #
    # P0-U `missing_plan_or_issue` 회귀 fix — 이전엔 caller 가 repo_contract
    # 를 안 넘기면 plan 이 영원히 None 으로 남아 executor 가
    # `github_work_order_missing_plan_or_issue` 로 떨어졌다. 이제는 *repo
    # 만이라도* 주어지면 minimal RepoContract (owner/repo) 를 즉석에서
    # 구성해 `build_default_issue_body` fallback 으로 plan 을 *반드시*
    # 만든다. 같은 issue 가 다시 발생하지 않게 caller 측 RepoContract 주입
    # 누락이 더 이상 silent failure 가 되지 않는다.
    issue_plan_payload: Optional[Mapping[str, Any]] = None
    resolved_existing = _coerce_existing_issue(existing_issue_number, session)
    effective_repo_contract = repo_contract
    if effective_repo_contract is None and not resolved_existing:
        effective_repo_contract = _minimal_repo_contract_from_repo(repo)
    if effective_repo_contract is not None and not resolved_existing:
        try:
            outcome = build_issue_auto_create_plan(
                repo_contract=effective_repo_contract,
                request_summary=summary,
                template_loader=issue_template_loader,
                session_id=str(getattr(session, "session_id", "") or ""),
            )
        except Exception:  # noqa: BLE001 - never block the proposal on plan failure
            outcome = None
        if outcome is not None and outcome.plan is not None:
            issue_plan_payload = outcome.plan.to_dict()
            # audit 흔적을 extras_payload 에도 남김 — operator 가 카드를
            # 볼 때 어떤 audit_reason 으로 plan 이 만들어졌는지 확인 가능
            extras_payload.setdefault(
                "issue_auto_create_audit_reason", outcome.audit_reason
            )
            if outcome.plan.needs_operator_decision:
                extras_payload.setdefault("issue_auto_create_needs_decision", True)
            if repo_contract is None:
                # caller 가 본격 contract 를 안 넘긴 경로 — operator 가
                # "왜 fallback 으로 떨어졌는지" 한 줄에 보이도록 audit 에
                # 별도 marker.
                extras_payload.setdefault(
                    "issue_auto_create_contract_source", "minimal_repo_string"
                )

    pid = (proposal_id or "").strip() or _new_proposal_id()
    return GitHubWorkOrderProposal(
        proposal_id=pid,
        session_id=str(getattr(session, "session_id", "") or ""),
        source_channel_id=source_channel_id,
        source_thread_id=source_thread_id,
        source_message_id=source_message_id,
        request_summary=summary,
        coding_required=True,
        selected_roles=selected,
        excluded_roles=excluded,
        intent_actions=intent.actions,
        intent_evidence=intent.matched,
        approval_kind=APPROVAL_KIND_GITHUB_WORK_ORDER,
        approval_level="L3_HUMAN_APPROVAL",
        repo=repo,
        base_branch=base_branch,
        requested_by=requested_by,
        dry_run_default=True,
        extra=extras_payload,
        created_at=_utc_now_iso(),
        issue_auto_create_plan=issue_plan_payload,
        existing_issue_number=resolved_existing,
    )


def _minimal_repo_contract_from_repo(repo: Optional[str]) -> Optional[RepoContract]:
    """`owner/name` 문자열로부터 최소 :class:`RepoContract` 생성.

    template / contributing / pr_template 등은 모두 비어있는 fallback —
    `build_issue_auto_create_plan` 이 후보 template 없음을 보고 자동으로
    `build_default_issue_body` 경로 (deterministic 4 섹션 fallback) 로
    떨어진다. 그래서 plan 이 *반드시* 생성된다.

    Returns ``None`` 만 owner/name 둘 다 추출 가능할 때 (`"yule-studio/
    naver-search-clone"` 같은 형태). 호출자가 빈 문자열 / None / 잘못된
    형식을 줘도 None 만 반환 — proposal 측에서 자동 skip.
    """

    if not repo:
        return None
    text = str(repo).strip()
    if not text or "/" not in text:
        return None
    owner, _, name = text.partition("/")
    owner = owner.strip()
    name = name.strip().rstrip(".git")
    if not owner or not name:
        return None
    return RepoContract(
        owner=owner,
        repo=name,
        fallback=True,
        failure_mode="no_repo_contract_supplied_by_caller",
        backend=None,
    )


def _coerce_existing_issue(
    explicit: Optional[int], session: Any
) -> Optional[int]:
    """Caller-provided existing issue > prompt anchor > session anchor > None.

    Priority (P1-U):

      1. ``explicit`` (slash option / caller-injected) — strongest signal.
      2. ``session.extra["existing_issue_number"]`` — prompt URL / `#N`
         / `issue N` anchor extracted by ``prepare_coding_session_context``.
      3. ``session.extra["github_work_order_issue"].issue_number`` — anchor
         stamped by a previous run of :class:`GitHubWorkOrderWorker` after
         a successful auto-create.

    옛 wiring 은 (2) 가 없어서 사용자가 prompt 에 issue URL / `#5` 를
    명시해도 work_order 가 auto-create 로 새 issue 를 생성했다.  본
    helper 가 (2) 우선순위로 잡아 reuse 강제.
    """

    if explicit is not None:
        try:
            value = int(explicit)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    extra = getattr(session, "extra", None)
    if not isinstance(extra, Mapping):
        return None

    # P1-U — prompt URL / 텍스트 anchor 우선
    prompt_anchor = extra.get("existing_issue_number")
    if prompt_anchor is not None:
        try:
            value = int(prompt_anchor)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value

    # 옛 anchor (이미 auto-create 거친 세션의 anchor)
    anchor = extra.get("github_work_order_issue")
    if not isinstance(anchor, Mapping):
        return None
    try:
        value = int(anchor.get("issue_number") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _resolve_roles_from_session(
    session: Any,
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Return ``(selected_roles, excluded_roles)`` honoring the
    upstream role selection + coding proposal hints stamped on
    ``session.extra``."""

    extra: Mapping[str, Any] = {}
    raw_extra = getattr(session, "extra", None)
    if isinstance(raw_extra, Mapping):
        extra = raw_extra

    excluded_raw = extra.get("excluded_research_roles") or ()
    excluded: list[str] = []
    for role in excluded_raw if isinstance(excluded_raw, (list, tuple)) else ():
        text = str(role or "").strip()
        if text and text not in excluded:
            excluded.append(text)

    selected: list[str] = []

    proposal = extra.get("coding_proposal")
    if isinstance(proposal, Mapping):
        executor = str(proposal.get("executor_role") or "").strip()
        if executor and executor not in selected:
            selected.append(executor)
        for key in ("review_roles", "participant_roles"):
            for role in proposal.get(key) or ():
                text = str(role or "").strip()
                if text and text not in selected:
                    selected.append(text)

    for role in extra.get("active_research_roles") or ():
        text = str(role or "").strip()
        if text and text not in selected:
            selected.append(text)

    # tech-lead always reviews per the engineering CLAUDE.md contract;
    # ensure it's at the front of the list (reorder if already present
    # downstream, insert if missing). The approval card renders the
    # reviewer first so the operator sees who blesses the work.
    if _DEFAULT_TECH_LEAD in selected:
        selected = [_DEFAULT_TECH_LEAD] + [
            role for role in selected if role != _DEFAULT_TECH_LEAD
        ]
    else:
        selected.insert(0, _DEFAULT_TECH_LEAD)

    if not selected:
        selected = list(_FALLBACK_REVIEWERS)

    final_selected = tuple(role for role in selected if role not in excluded)
    return final_selected, tuple(excluded)


def _short_request_summary(text: str, *, max_chars: int) -> str:
    cleaned = (text or "").strip().replace("\r", " ").replace("\n", " ")
    cleaned = _NORMALIZE_RE.sub(" ", cleaned)
    if len(cleaned) <= max_chars:
        return cleaned
    head = cleaned[:max_chars]
    pivot = head.rfind(" ")
    if pivot >= max_chars // 2:
        head = head[:pivot]
    return head.rstrip(" ,.;:") + "…"


# ---------------------------------------------------------------------------
# Approval enqueue (pre-approval)
# ---------------------------------------------------------------------------


def _approval_request_from_proposal(
    proposal: GitHubWorkOrderProposal,
    *,
    created_by: str,
) -> ApprovalRequest:
    title = "GitHub 작업 시작 승인"
    summary = proposal.request_summary or "(요약 미포함)"
    role_list = (
        ", ".join(proposal.selected_roles) if proposal.selected_roles else "tech-lead"
    )
    action_label = (
        ", ".join(proposal.intent_actions) if proposal.intent_actions else "코드 변경"
    )
    requested_action = (
        f"{action_label} (역할: {role_list}) — 승인 시 dry-run github_work_order "
        f"가 큐에 적재됩니다."
    )

    extra = {
        "github_work_order_proposal": dict(proposal.to_payload()),
        "intent_actions": list(proposal.intent_actions),
        "intent_evidence": list(proposal.intent_evidence),
        "selected_roles": list(proposal.selected_roles),
        "excluded_roles": list(proposal.excluded_roles),
        "approval_level": proposal.approval_level,
        "proposal_id": proposal.proposal_id,
        "dry_run_default": proposal.dry_run_default,
    }

    return ApprovalRequest(
        session_id=proposal.session_id,
        approval_kind=APPROVAL_KIND_ENGINEERING_WRITE,
        title=title,
        summary=summary,
        requested_action=requested_action,
        created_by=created_by or proposal.requested_by or "engineering-agent",
        source_channel_id=proposal.source_channel_id,
        source_thread_id=proposal.source_thread_id,
        source_message_id=proposal.source_message_id,
        extra=extra,
    )


async def enqueue_github_work_approval(
    *,
    session: Any,
    request_text: str,
    approval_worker: ApprovalWorker,
    source_channel_id: Optional[int] = None,
    source_thread_id: Optional[int] = None,
    source_message_id: Optional[int] = None,
    requested_by: str = "",
    repo: Optional[str] = None,
    base_branch: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
    proposal_id: Optional[str] = None,
    drive_consumer: bool = True,
    now: Optional[float] = None,
    repo_contract: Optional[RepoContract] = None,
    issue_template_loader: Optional[Callable[[str], Optional[str]]] = None,
    existing_issue_number: Optional[int] = None,
) -> GitHubWorkApprovalOutcome:
    """Build a proposal + enqueue (and optionally post) the approval card.

    Returns :class:`GitHubWorkApprovalOutcome` describing what the
    adapter actually did. The function never calls GitHub. The two
    side-effects it can perform are:

      1. Insert a row into the approval_post queue (idempotent —
         duplicate (session, kind, message) returns
         ``skipped_reason="duplicate_approval_in_flight"``).
      2. (Only when *drive_consumer* is True, the production default)
         immediately drive ``ApprovalWorker.run_one`` so the card
         posts to ``#승인-대기`` in the same call. Tests that want
         to inspect the queue without posting set this to False.
    """

    proposal = build_github_work_order_proposal(
        session=session,
        request_text=request_text,
        source_channel_id=source_channel_id,
        source_thread_id=source_thread_id,
        source_message_id=source_message_id,
        requested_by=requested_by,
        repo=repo,
        base_branch=base_branch,
        proposal_id=proposal_id,
        extra=extra,
        repo_contract=repo_contract,
        issue_template_loader=issue_template_loader,
        existing_issue_number=existing_issue_number,
    )
    if proposal is None:
        eligible, skipped_reason, _ = should_route_to_github_workos(
            session=session,
            request_text=request_text,
        )
        return GitHubWorkApprovalOutcome(
            proposal=None,
            skipped_reason=skipped_reason or SKIPPED_NO_CODING_INTENT,
        )

    # Stronger dedup than the queue's ``find_active`` alone: also skip
    # when an approval card has already POSTED (state=SAVED) for the
    # same (session, source_message_id) — operators must not see two
    # cards for the same intake.
    queue = approval_worker._queue  # noqa: SLF001 - intentional reuse
    already_posted = find_replyable_approval(
        queue=queue,
        session_id=proposal.session_id,
        approval_kind=APPROVAL_KIND_ENGINEERING_WRITE,
        source_message_id=proposal.source_message_id,
        source_thread_id=proposal.source_thread_id,
    )
    if already_posted is not None:
        return GitHubWorkApprovalOutcome(
            proposal=proposal,
            approval_job_id=already_posted.job_id,
            approval_post_outcome=None,
            skipped_reason=SKIPPED_DUPLICATE_APPROVAL,
        )

    request = _approval_request_from_proposal(
        proposal,
        created_by=requested_by,
    )

    if drive_consumer:
        outcome = await approval_worker.run_one(request, now=now)
        approval_job_id = (
            outcome.job.job_id if getattr(outcome, "job", None) else None
        )
        # ApprovalWorker.run_one flags duplicates with
        # ``skipped_reason="duplicate_in_flight"``. Surface the same
        # information using the adapter's vocabulary so callers don't
        # have to know two skipped-reason namespaces.
        skipped = None
        if getattr(outcome, "skipped_reason", None) == "duplicate_in_flight":
            skipped = SKIPPED_DUPLICATE_APPROVAL
        elif getattr(outcome, "skipped_reason", None):
            skipped = outcome.skipped_reason
        return GitHubWorkApprovalOutcome(
            proposal=proposal,
            approval_job_id=approval_job_id,
            approval_post_outcome=outcome,
            skipped_reason=skipped,
        )

    job, created = approval_worker.enqueue(request, now=now)
    return GitHubWorkApprovalOutcome(
        proposal=proposal,
        approval_job_id=job.job_id if job is not None else None,
        approval_post_outcome=None,
        skipped_reason=None if created else SKIPPED_DUPLICATE_APPROVAL,
    )


# ---------------------------------------------------------------------------
# Post-approval dispatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitHubWorkApprovalReplyOutcome:
    """Result of :func:`handle_github_work_approval_reply`.

    ``work_order`` is set when the adapter built one (regardless of
    whether the queue insertion succeeded or was a duplicate).
    ``dispatched_job_id`` is the github_work_order row's id when the
    queue insert created a fresh row. Failures / no-ops carry a
    ``skipped_reason`` matching the constants in this module or the
    underlying queue helper.
    """

    work_order: Optional[GitHubWorkOrder]
    dispatched_job_id: Optional[str] = None
    skipped_reason: Optional[str] = None


def handle_github_work_approval_reply(
    *,
    queue: JobQueue,
    approval_request: ApprovalRequest,
    approval_id: str,
    approved_by: str,
    approved_at: Optional[str] = None,
    dry_run: Optional[bool] = None,
    now: Optional[float] = None,
) -> GitHubWorkApprovalReplyOutcome:
    """Convert an approved engineering_write card into a queued
    :class:`GitHubWorkOrder`.

    Refuses (returns ``skipped_reason="approval_kind_mismatch"``) when
    the approval row's kind isn't ``engineering_write`` — Obsidian
    approvals stay on their existing converter
    (:func:`agents.job_queue.approval_reply.approval_to_obsidian_write_request`).

    The dry_run flag defaults to ``proposal.dry_run_default`` (True)
    so every dispatched row is non-live. Operators must pass
    ``dry_run=False`` from a different surface to actually call GitHub.
    """

    if approval_request.approval_kind != APPROVAL_KIND_ENGINEERING_WRITE:
        return GitHubWorkApprovalReplyOutcome(
            work_order=None,
            skipped_reason="approval_kind_mismatch",
        )

    raw_proposal = (approval_request.extra or {}).get(
        "github_work_order_proposal"
    )
    if not isinstance(raw_proposal, Mapping):
        return GitHubWorkApprovalReplyOutcome(
            work_order=None,
            skipped_reason="proposal_payload_missing",
        )
    try:
        proposal = GitHubWorkOrderProposal.from_payload(raw_proposal)
    except Exception as exc:  # noqa: BLE001 - non-fatal
        return GitHubWorkApprovalReplyOutcome(
            work_order=None,
            skipped_reason=f"proposal_parse_error:{type(exc).__name__}",
        )

    work_order = GitHubWorkOrder.from_proposal(
        proposal,
        approval_id=approval_id,
        approved_by=approved_by,
        approved_at=approved_at,
        dry_run=dry_run,
    )
    outcome = dispatch_github_work_order(queue, work_order, now=now)

    skipped: Optional[str] = None
    if outcome.skipped_reason == SKIPPED_DUPLICATE:
        skipped = SKIPPED_DUPLICATE
    elif outcome.skipped_reason == SKIPPED_AWAITING_APPROVAL:
        skipped = SKIPPED_AWAITING_APPROVAL
    elif outcome.skipped_reason:
        skipped = outcome.skipped_reason

    dispatched_id = (
        outcome.job.job_id
        if outcome.job is not None and not skipped
        else None
    )
    return GitHubWorkApprovalReplyOutcome(
        work_order=work_order,
        dispatched_job_id=dispatched_id,
        skipped_reason=skipped,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _new_proposal_id() -> str:
    return f"gho-{uuid.uuid4().hex[:16]}"


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


__all__ = (
    "GitHubWorkApprovalOutcome",
    "GitHubWorkApprovalReplyOutcome",
    "SKIPPED_DUPLICATE_APPROVAL",
    "SKIPPED_NO_CODING_INTENT",
    "SKIPPED_OBSIDIAN_INTENT",
    "SKIPPED_RESEARCH_ONLY",
    "build_github_work_order_proposal",
    "detect_obsidian_intent",
    "enqueue_github_work_approval",
    "handle_github_work_approval_reply",
    "should_route_to_github_workos",
)
