"""GitHub WorkOS bridge — proposal model + queue payload + dedup helpers.

The G4 (Discord 업무접수 → GitHub WorkOS) lifecycle has three queue
artifacts:

  1. :class:`GitHubWorkOrderProposal` — composed during the engineering
     channel intake AFTER the existing :func:`recommend_authorization`
     pipeline determines the request needs implementation work. This
     module **never** infers coding-required by itself; it consumes the
     upstream verdict (lifecycle mode + coding intent score) and bundles
     the metadata an approval card needs to render.
  2. ``approval_post`` — same job type the existing approval flow uses
     (:data:`APPROVAL_KIND_ENGINEERING_WRITE`). The G4 adapter wraps
     each :class:`GitHubWorkOrderProposal` in an :class:`ApprovalRequest`
     and hands it to :class:`ApprovalWorker`. Until the user replies
     "승인" / "이대로 진행" no GitHub write artifact is queued.
  3. :class:`GitHubWorkOrder` — only constructed AFTER the approval
     reply lands. The work order carries the dry-run flag (``True`` by
     default) so the executor consumer can choose to (a) plan branch +
     issue + draft PR offline or (b) actually call GitHub once an
     operator passes ``dry_run=False`` explicitly.

This module is **pure-Python**: no Discord client, no GitHub API calls,
no LLM hooks. The Discord-side wiring lives in
:mod:`yule_orchestrator.discord.integrations.github_workos_adapter`; the executor
service that drains :data:`JOB_TYPE_GITHUB_WORK_ORDER` rows is the
sibling G3 work stream's responsibility — this module exposes the row
shape so G3 can decode it without importing the discord layer.

Coding intent detection (:func:`detect_coding_intent`) is a small
keyword bank the adapter consults as a *positive* signal alongside the
existing ``lifecycle_mode`` (``research_only`` vs ``implementation``)
flag stored on ``session.extra``. The two checks are AND-ed: a
research-only session never raises a GitHub work order, and a session
that didn't trigger the implementation lifecycle also stays out of the
queue. Operators can opt in by typing one of the strong phrases
("PR 올려줘", "이슈 만들어줘", "GitHub Actions 고쳐줘", …).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .approval_worker import APPROVAL_KIND_ENGINEERING_WRITE
from .state_machine import JobState
from .store import Job, JobQueue


JOB_TYPE_GITHUB_WORK_ORDER: str = "github_work_order"
SERVICE_ID_GITHUB_WORK_ORDER: str = "eng-github-work-order"


# Reuse the existing approval kind so the queue/UX surface stays
# coherent — the rendering label (":코드 변경") is already wired in
# ``approval_worker._APPROVAL_KIND_LABELS``.
APPROVAL_KIND_GITHUB_WORK_ORDER: str = APPROVAL_KIND_ENGINEERING_WRITE


# Skipped reasons for :class:`GitHubWorkOrderDispatchOutcome`.
SKIPPED_DUPLICATE: str = "duplicate_in_flight"
SKIPPED_AWAITING_APPROVAL: str = "awaiting_approval"
SKIPPED_DRY_RUN_GUARD: str = "dry_run_guard"


_ACTIVE_STATES: Tuple[JobState, ...] = (
    JobState.QUEUED,
    JobState.ASSIGNED,
    JobState.IN_PROGRESS,
    JobState.WAITING_FOR_ROLE,
    JobState.RESEARCHING,
    JobState.PENDING_APPROVAL,
    JobState.READY_FOR_OBSIDIAN,
)


# ---------------------------------------------------------------------------
# Coding intent detection
# ---------------------------------------------------------------------------


# Positive coding signals — phrases that strongly indicate the user
# wants real code changes / PR / issue / branch work, not just research.
# Match is substring-based after :func:`_normalize`. Each entry's
# trailing whitespace tolerates "PR 올려줘" vs "PR을 올려줘"-style
# inflections without exploding the table.
_CODING_INTENT_PHRASES: Tuple[str, ...] = (
    # PR / issue verbs
    "pr 올려",
    "pr을 올려",
    "pr 만들",
    "pr을 만들",
    "pr 작성",
    "pr 생성",
    "pull request",
    "draft pr",
    "draft pull",
    "이슈로 만들",
    "이슈를 만들",
    "이슈 만들어",
    "issue 만들",
    "issue 생성",
    "github 이슈",
    # Bug fix verbs
    "버그 고쳐",
    "버그고쳐",
    "버그 수정",
    "bug fix",
    "fix this bug",
    "fix the bug",
    # Implementation verbs
    "구현해",
    "구현 해",
    "구현 부탁",
    "코드 작성",
    "코드 짜",
    "코드 짜줘",
    "기능 구현",
    "implement this",
    "implement it",
    # Test additions
    "테스트 추가",
    "테스트 작성",
    "테스트 보강",
    "단위 테스트 추가",
    "유닛 테스트 추가",
    "regression test",
    "add tests",
    "write tests",
    # Refactor verbs
    "리팩터",
    "리팩토",
    "refactor",
    # CI / actions verbs
    "github actions",
    "github action",
    "ci 수정",
    "ci 고쳐",
    "ci 작성",
    "workflow 수정",
    "workflow 고쳐",
    "actions workflow",
    "actions 고쳐",
    "release workflow",
    # Branch / commit verbs
    "branch 만들",
    "branch 생성",
    "feature branch",
    "체크인",
    "commit 만들",
    "git push",
    # Generic write / patch verbs
    "코드 수정",
    "코드 수정해",
    "코드를 수정",
    "코드 패치",
    "패치 작성",
    "patch 작성",
    "패치를 만들",
    "patch 만들",
)


# Negative phrases — when present, force coding_required=False even if
# a positive phrase also fires. Mirrors the research-only bank in
# :mod:`agents.coding.authorization` so a typo'd "리서치만 해줘 + PR
# 올려줘" still surfaces as research_only.
_RESEARCH_ONLY_PHRASES: Tuple[str, ...] = (
    "코드 수정 없이",
    "코드 수정없이",
    "코드 수정은 없",
    "코드 수정하지 말",
    "코드 수정 하지 말",
    "코드 수정 금지",
    "리서치만 해",
    "리서치만 정리",
    "조사만 해",
    "조사만 정리",
    "research only",
    "research-only",
    "no code change",
    "do not write code",
    "don't write code",
)


# Action signature — when one of these phrases lands, the proposal is
# tagged with the matching action label so the approval card / status
# diagnostic can show what kind of GitHub work the user asked for.
_ACTION_SIGNATURES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("github_actions", ("github actions", "github action", "workflow 수정",
                         "workflow 고쳐", "actions workflow",
                         "release workflow", "ci 수정", "ci 고쳐",
                         "ci 작성", "actions 고쳐")),
    ("bug_fix", ("버그 고쳐", "버그고쳐", "버그 수정", "bug fix",
                  "fix this bug", "fix the bug")),
    ("test_add", ("테스트 추가", "테스트 작성", "테스트 보강",
                   "단위 테스트 추가", "유닛 테스트 추가",
                   "regression test", "add tests", "write tests")),
    ("refactor", ("리팩터", "리팩토", "refactor")),
    ("issue_create", ("이슈로 만들", "이슈를 만들", "이슈 만들어",
                       "issue 만들", "issue 생성", "github 이슈")),
    ("pull_request", ("pr 올려", "pr을 올려", "pr 만들", "pr을 만들",
                       "pr 작성", "pr 생성", "pull request",
                       "draft pr", "draft pull")),
    ("implement", ("구현해", "구현 해", "구현 부탁", "코드 작성",
                    "코드 짜", "코드 짜줘", "기능 구현",
                    "implement this", "implement it")),
    ("patch", ("코드 수정", "코드 수정해", "코드를 수정",
                "코드 패치", "패치 작성", "patch 작성",
                "패치를 만들", "patch 만들")),
)


_NORMALIZE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub(" ", (text or "").lower()).strip()


@dataclass(frozen=True)
class CodingIntent:
    """Outcome of :func:`detect_coding_intent`.

    ``coding_required`` is True iff at least one positive phrase fired
    AND no research-only phrase suppressed the result. ``matched`` is
    the list of phrases that triggered the verdict (in first-seen
    order) — useful for the approval card's "왜 코딩이 필요하다고 봤는지"
    line. ``actions`` is the deduped action-signature labels. ``research_only``
    is True iff a research-only phrase fired regardless of positive matches.
    """

    coding_required: bool
    matched: Tuple[str, ...] = ()
    actions: Tuple[str, ...] = ()
    research_only: bool = False


def detect_coding_intent(text: str) -> CodingIntent:
    """Classify *text* into coding-required / research-only / neutral.

    Resolution order:

      1. Empty / whitespace text → neutral (coding_required=False).
      2. Any research-only phrase present → research_only=True,
         coding_required=False (irrespective of positive matches).
      3. Otherwise iterate :data:`_CODING_INTENT_PHRASES`; if at least
         one matches, coding_required=True with the matched phrases +
         action labels exposed.

    The detector intentionally does NOT short-circuit on the first
    positive hit; collecting every match lets the approval card
    surface a richer "evidence" line ("PR 올려줘" + "버그 수정"
    together → action_signature pull_request + bug_fix).
    """

    norm = _normalize(text)
    if not norm:
        return CodingIntent(coding_required=False)

    # 2. research-only veto
    if any(phrase in norm for phrase in _RESEARCH_ONLY_PHRASES):
        return CodingIntent(coding_required=False, research_only=True)

    # 3. positive matches
    matched: list[str] = []
    for phrase in _CODING_INTENT_PHRASES:
        if phrase in norm and phrase not in matched:
            matched.append(phrase)

    if not matched:
        return CodingIntent(coding_required=False)

    actions: list[str] = []
    for label, phrase_set in _ACTION_SIGNATURES:
        for phrase in phrase_set:
            if phrase in norm and label not in actions:
                actions.append(label)
                break

    return CodingIntent(
        coding_required=True,
        matched=tuple(matched),
        actions=tuple(actions),
    )


# ---------------------------------------------------------------------------
# Proposal + work order dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitHubWorkOrderProposal:
    """The pre-approval planning record G4 emits.

    Carries everything the approval card needs to render and
    everything the post-approval :class:`GitHubWorkOrder` needs to
    dispatch. Lives independently of :mod:`agents.coding.authorization`
    so the GitHub wiring can be reasoned about without importing the
    role-recommender layer (the adapter copies the relevant fields
    over from ``CodingAuthorizationProposal`` when the upstream
    pipeline produced one).

    Field meanings:

      * ``proposal_id`` — UUIDv4-shaped string created by the adapter.
        The approval queue dedup uses (session_id, source_message_id)
        directly; ``proposal_id`` is for downstream audit trails.
      * ``coding_required`` — always True for proposals that reach the
        adapter. The dataclass keeps the field so payload round-trips
        retain the verdict for offline replay tooling.
      * ``selected_roles`` — executor + reviewers + research participants
        that survived ``excluded_research_roles`` filtering.
      * ``approval_level`` — ``L3_HUMAN_APPROVAL`` for normal coding
        work, ``L4_STRONG_APPROVAL_OR_FORBIDDEN`` for high-risk verbs
        (push to main, deploy). Phase 1 only emits L3; the field is
        present for forward compatibility.
      * ``intent_evidence`` — phrases that triggered
        :class:`CodingIntent.matched`, plus the action labels.
      * ``request_summary`` — short canonical prompt the approval card
        renders verbatim (≤ 280 chars).
      * ``dry_run_default`` — True. The adapter never flips this; an
        operator who wants live writes must explicitly pass
        ``dry_run=False`` when constructing the
        :class:`GitHubWorkOrder` post-approval.
    """

    proposal_id: str
    session_id: str
    source_channel_id: Optional[int]
    source_thread_id: Optional[int]
    source_message_id: Optional[int]
    request_summary: str
    coding_required: bool = True
    selected_roles: Tuple[str, ...] = ()
    excluded_roles: Tuple[str, ...] = ()
    intent_actions: Tuple[str, ...] = ()
    intent_evidence: Tuple[str, ...] = ()
    approval_kind: str = APPROVAL_KIND_GITHUB_WORK_ORDER
    approval_level: str = "L3_HUMAN_APPROVAL"
    repo: Optional[str] = None
    base_branch: Optional[str] = None
    requested_by: str = ""
    dry_run_default: bool = True
    extra: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "session_id": self.session_id,
            "source_channel_id": self.source_channel_id,
            "source_thread_id": self.source_thread_id,
            "source_message_id": self.source_message_id,
            "request_summary": self.request_summary,
            "coding_required": self.coding_required,
            "selected_roles": list(self.selected_roles),
            "excluded_roles": list(self.excluded_roles),
            "intent_actions": list(self.intent_actions),
            "intent_evidence": list(self.intent_evidence),
            "approval_kind": self.approval_kind,
            "approval_level": self.approval_level,
            "repo": self.repo,
            "base_branch": self.base_branch,
            "requested_by": self.requested_by,
            "dry_run_default": self.dry_run_default,
            "extra": dict(self.extra),
            "created_at": self.created_at,
        }

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, Any]
    ) -> "GitHubWorkOrderProposal":
        return cls(
            proposal_id=str(payload.get("proposal_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            source_channel_id=_coerce_int(payload.get("source_channel_id")),
            source_thread_id=_coerce_int(payload.get("source_thread_id")),
            source_message_id=_coerce_int(payload.get("source_message_id")),
            request_summary=str(payload.get("request_summary") or ""),
            coding_required=bool(payload.get("coding_required", True)),
            selected_roles=tuple(
                str(r) for r in (payload.get("selected_roles") or ())
            ),
            excluded_roles=tuple(
                str(r) for r in (payload.get("excluded_roles") or ())
            ),
            intent_actions=tuple(
                str(a) for a in (payload.get("intent_actions") or ())
            ),
            intent_evidence=tuple(
                str(e) for e in (payload.get("intent_evidence") or ())
            ),
            approval_kind=str(
                payload.get("approval_kind") or APPROVAL_KIND_GITHUB_WORK_ORDER
            ),
            approval_level=str(payload.get("approval_level") or "L3_HUMAN_APPROVAL"),
            repo=_optional_str(payload.get("repo")),
            base_branch=_optional_str(payload.get("base_branch")),
            requested_by=str(payload.get("requested_by") or ""),
            dry_run_default=bool(payload.get("dry_run_default", True)),
            extra=dict(payload.get("extra") or {}),
            created_at=str(payload.get("created_at") or ""),
        )


@dataclass(frozen=True)
class GitHubWorkOrder:
    """Post-approval dispatch payload that lands on the
    ``github_work_order`` queue.

    The G3 (executor) work stream consumes these rows and decides how
    far to take each (issue create / branch / draft PR) — this module
    only owns the typed envelope. ``dry_run`` defaults to True so a
    misconfigured executor never accidentally writes to a live repo.
    Operators flip the flag to False after eyeballing the planned
    actions.
    """

    proposal_id: str
    session_id: str
    approval_id: str
    approved_by: str
    approved_at: str
    request_summary: str
    selected_roles: Tuple[str, ...] = ()
    intent_actions: Tuple[str, ...] = ()
    repo: Optional[str] = None
    base_branch: Optional[str] = None
    dry_run: bool = True
    source_channel_id: Optional[int] = None
    source_thread_id: Optional[int] = None
    source_message_id: Optional[int] = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "session_id": self.session_id,
            "approval_id": self.approval_id,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "request_summary": self.request_summary,
            "selected_roles": list(self.selected_roles),
            "intent_actions": list(self.intent_actions),
            "repo": self.repo,
            "base_branch": self.base_branch,
            "dry_run": self.dry_run,
            "source_channel_id": self.source_channel_id,
            "source_thread_id": self.source_thread_id,
            "source_message_id": self.source_message_id,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "GitHubWorkOrder":
        return cls(
            proposal_id=str(payload.get("proposal_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            approval_id=str(payload.get("approval_id") or ""),
            approved_by=str(payload.get("approved_by") or ""),
            approved_at=str(payload.get("approved_at") or ""),
            request_summary=str(payload.get("request_summary") or ""),
            selected_roles=tuple(
                str(r) for r in (payload.get("selected_roles") or ())
            ),
            intent_actions=tuple(
                str(a) for a in (payload.get("intent_actions") or ())
            ),
            repo=_optional_str(payload.get("repo")),
            base_branch=_optional_str(payload.get("base_branch")),
            dry_run=bool(payload.get("dry_run", True)),
            source_channel_id=_coerce_int(payload.get("source_channel_id")),
            source_thread_id=_coerce_int(payload.get("source_thread_id")),
            source_message_id=_coerce_int(payload.get("source_message_id")),
            extra=dict(payload.get("extra") or {}),
        )

    @classmethod
    def from_proposal(
        cls,
        proposal: GitHubWorkOrderProposal,
        *,
        approval_id: str,
        approved_by: str,
        approved_at: Optional[str] = None,
        dry_run: Optional[bool] = None,
    ) -> "GitHubWorkOrder":
        """Lift a :class:`GitHubWorkOrderProposal` into a dispatchable
        work order. ``dry_run`` defaults to ``proposal.dry_run_default``
        (which is True) — explicit operator opt-in is required to flip
        it. Approval triple is required so the executor side can audit
        every dispatched row back to a real human.
        """

        return cls(
            proposal_id=proposal.proposal_id,
            session_id=proposal.session_id,
            approval_id=str(approval_id or ""),
            approved_by=str(approved_by or ""),
            approved_at=str(
                (approved_at or "").strip() or _utc_now_iso()
            ),
            request_summary=proposal.request_summary,
            selected_roles=tuple(proposal.selected_roles),
            intent_actions=tuple(proposal.intent_actions),
            repo=proposal.repo,
            base_branch=proposal.base_branch,
            dry_run=(
                proposal.dry_run_default if dry_run is None else bool(dry_run)
            ),
            source_channel_id=proposal.source_channel_id,
            source_thread_id=proposal.source_thread_id,
            source_message_id=proposal.source_message_id,
            extra=dict(proposal.extra or {}),
        )


# ---------------------------------------------------------------------------
# Outcomes + queue helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitHubWorkOrderDispatchOutcome:
    """Result of :func:`dispatch_github_work_order`."""

    job: Optional[Job]
    work_order: Optional[GitHubWorkOrder] = None
    skipped_reason: Optional[str] = None


def find_active_work_order(
    queue: JobQueue,
    *,
    session_id: str,
    proposal_id: Optional[str] = None,
    source_message_id: Optional[int] = None,
) -> Optional[Job]:
    """Return any non-terminal ``github_work_order`` job for the
    matching dedup key, or ``None``.

    Match priority (most specific first):
      1. ``proposal_id`` exact match — strongest signal because the
         adapter writes a fresh UUID per proposal.
      2. ``source_message_id`` match — when the operator re-typed the
         same Discord intake.
      3. Same ``session_id`` only — last resort, prevents two work
         orders from racing for one session even if the producer
         forgot to stamp ids.
    """

    if not session_id:
        return None
    candidates: list[Job] = []
    for job in queue.list_for_session(session_id, states=_ACTIVE_STATES):
        if job.job_type != JOB_TYPE_GITHUB_WORK_ORDER:
            continue
        candidates.append(job)
    if not candidates:
        return None

    if proposal_id:
        for job in candidates:
            payload = job.payload or {}
            if str(payload.get("proposal_id") or "") == str(proposal_id):
                return job

    if source_message_id is not None:
        for job in candidates:
            payload = job.payload or {}
            existing = payload.get("source_message_id")
            if existing is None:
                continue
            try:
                if int(existing) == int(source_message_id):
                    return job
            except (TypeError, ValueError):
                continue

    return candidates[0]


def dispatch_github_work_order(
    queue: JobQueue,
    work_order: GitHubWorkOrder,
    *,
    priority: int = 0,
    max_attempts: int = 3,
    now: Optional[float] = None,
) -> GitHubWorkOrderDispatchOutcome:
    """Idempotent enqueue of a GitHubWorkOrder.

    Caller must have an approval triple stamped on *work_order* — the
    helper refuses to enqueue without ``approval_id``. This is a
    process-level guard so the adapter can't accidentally dispatch
    a row before the user replies in #승인-대기. The strict approval
    check is one of the load-bearing tests in the G4 suite.
    """

    if not work_order.session_id:
        raise ValueError("GitHubWorkOrder.session_id is required")
    if not (work_order.approval_id or "").strip():
        return GitHubWorkOrderDispatchOutcome(
            job=None,
            work_order=work_order,
            skipped_reason=SKIPPED_AWAITING_APPROVAL,
        )

    existing = find_active_work_order(
        queue,
        session_id=work_order.session_id,
        proposal_id=work_order.proposal_id,
        source_message_id=work_order.source_message_id,
    )
    if existing is not None:
        return GitHubWorkOrderDispatchOutcome(
            job=existing,
            work_order=work_order,
            skipped_reason=SKIPPED_DUPLICATE,
        )

    job = queue.enqueue(
        session_id=work_order.session_id,
        job_type=JOB_TYPE_GITHUB_WORK_ORDER,
        payload=work_order.to_payload(),
        priority=priority,
        max_attempts=max_attempts,
        now=now,
    )
    return GitHubWorkOrderDispatchOutcome(job=job, work_order=work_order)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


__all__ = (
    "APPROVAL_KIND_GITHUB_WORK_ORDER",
    "JOB_TYPE_GITHUB_WORK_ORDER",
    "SERVICE_ID_GITHUB_WORK_ORDER",
    "SKIPPED_AWAITING_APPROVAL",
    "SKIPPED_DRY_RUN_GUARD",
    "SKIPPED_DUPLICATE",
    "CodingIntent",
    "GitHubWorkOrder",
    "GitHubWorkOrderDispatchOutcome",
    "GitHubWorkOrderProposal",
    "detect_coding_intent",
    "dispatch_github_work_order",
    "find_active_work_order",
)
