"""Role council bootstrap — C2 wiring.

Bootstrap that produces, for one workflow session, the deterministic
*first* council artefacts:

- one :class:`TaskBrief`
- one :class:`RoleWorkOrder` per active role
- one :class:`RoleCouncilResult` per active role (round_index=1) consisting
  of an owner :class:`RoleDraft`, a challenger :class:`RoleDraft`, and a
  reviewer :class:`PeerReviewNote`.

This module deliberately does **not** call any LLM or external
provider. Every draft / peer review is a deterministic *seed* that the
next phase (C3+) can replace with provider-driven content. The goal of
C2 is to wire the lifecycle substage transitions and persistence so the
council loop "actually walks" — provider matrix is C3-and-later scope.

Hard rails enforced here:

- Single-executor rule — bootstrap **never** picks an executor; it only
  produces the brief / work-orders / first-round drafts.
- No public surface dump — only ``public_summary`` (1-line fallback if
  missing) ever leaves the module. The raw draft / peer review payload
  lives in ``session.extra`` for the runtime, not Discord.
- Backward compatibility — every input is optional and every failure
  path is best-effort. A session that already has ``role_councils``
  stamped is treated as already bootstrapped (no-op).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from datetime import datetime

from .council import (
    DEFAULT_COUNCIL_ROUND_CAP,
    DEFAULT_SEATS,
    CouncilConsensusStatus,
    PeerReviewNote,
    RoleCouncilResult,
    RoleDraft,
    RoleWorkOrder,
    SeatRole,
    SUBSTAGE_COUNCIL_ESCALATED,
    SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS,
    SUBSTAGE_COUNCIL_ROUND_COMPLETE,
    SUBSTAGE_PEER_REVIEW_PENDING,
    SUBSTAGE_ROLE_BRIEF_DISTRIBUTED,
    SUBSTAGE_ROLE_DRAFTS_IN_PROGRESS,
    TaskBrief,
    canonical_role,
    ensure_disagreement_summary,
    ensure_public_summary,
    must_escalate_to_tech_lead,
    normalize_roles,
    ready_for_synthesis,
    role_council_result_from_payload,
    role_council_result_to_payload,
    role_councils_from_extra,
    role_councils_to_extra,
    role_work_order_from_payload,
    role_work_order_to_payload,
    short_role,
    synthesis_block_reason,
    task_brief_from_payload,
    task_brief_to_payload,
)
from .lifecycle.council_substage import (
    APPROVAL_PACKET_EXTRA_KEY,  # noqa: F401 — re-export for callers
    COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY,
    COUNCIL_ESCALATION_AGGREGATE_EXTRA_KEY,
    COUNCIL_ESCALATION_EXTRA_KEY,
    EXECUTION_REVIEWS_EXTRA_KEY,  # noqa: F401 — re-export for callers
    LIFECYCLE_SUBSTAGE_EXTRA_KEY,
    RETROSPECTIVE_CANDIDATES_EXTRA_KEY,  # noqa: F401 — re-export for callers
    ROLE_COUNCILS_EXTRA_KEY,
)

TASK_BRIEF_EXTRA_KEY = "task_brief"
ROLE_WORK_ORDERS_EXTRA_KEY = "role_work_orders"
PROVIDER_SEAT_MATRIX_EXTRA_KEY = "council_provider_seat_matrix"
"""``session.extra`` key holding a per-role mapping of seat → provider
candidate list. Stamped at bootstrap so downstream runners (C4+) can read
intent without re-loading the manifest.

The matrix is purely metadata for now — C3 does **not** make outbound
LLM calls. The deterministic fallback inside the bootstrap stays the
single producer of council content."""


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CouncilBootstrapResult:
    """What :func:`bootstrap_council` produced for one round."""

    brief: TaskBrief
    work_orders: Tuple[RoleWorkOrder, ...]
    councils: Tuple[RoleCouncilResult, ...]
    substage: str
    escalated_roles: Tuple[str, ...] = ()
    extras_update: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# Role normalization is delegated to :mod:`agents.council` so the
# council vocabulary owns the SSoT. ``_normalize_role`` / ``_short_role``
# remain as private thin wrappers to keep the existing call sites tight.


def _normalize_role(role: str) -> str:
    return canonical_role(role)


def _short_role(role: str) -> str:
    return short_role(role)


_DEFAULT_REQUIRED_OUTPUTS: Mapping[str, Tuple[str, ...]] = {
    "tech-lead": (
        "task_breakdown",
        "executor_recommendation",
        "approval_proposal",
    ),
    "backend-engineer": (
        "api_contract",
        "data_contract",
        "error_contract",
        "test_handoff",
    ),
    "frontend-engineer": (
        "component_tree",
        "state_strategy",
        "loading_error_empty_states",
        "accessibility_review",
    ),
    "qa-engineer": (
        "acceptance_criteria",
        "regression_scenarios",
        "test_matrix",
    ),
    "ai-engineer": (
        "collector_strategy",
        "prompt_policy",
        "evaluation_criteria",
    ),
    "devops-engineer": (
        "cicd_strategy",
        "rollback_plan",
        "observability_signals",
        "release_checklist",
    ),
    "product-designer": (
        "screen_flow",
        "ux_direction",
        "visual_direction",
    ),
}

_DEFAULT_FORBIDDEN: Tuple[str, ...] = (
    "secret / .env / 운영 자격 증명 접근",
    "production deploy / 자동 push / main 직접 push",
    "사용자 승인 없는 비가역 destructive command",
    "다른 role 의 owned 영역 사전 협의 없이 쓰기",
)

_GENERIC_REQUIRED_OUTPUTS: Tuple[str, ...] = (
    "perspective",
    "evidence",
    "risks",
    "next_actions",
)


def required_outputs_for_role(role: str) -> Tuple[str, ...]:
    short = _short_role(role)
    return _DEFAULT_REQUIRED_OUTPUTS.get(short, _GENERIC_REQUIRED_OUTPUTS)


def build_task_brief(
    *,
    session_id: str,
    title: str,
    purpose: str,
    in_scope: Sequence[str] = (),
    out_of_scope: Sequence[str] = (),
    references: Sequence[str] = (),
    research_pack_ref: Optional[str] = None,
    work_mode: Optional[str] = None,
    revision: int = 1,
) -> TaskBrief:
    """Pure builder — no IO. ``title`` / ``purpose`` should be non-empty;
    caller is expected to derive both from the canonical intake prompt."""

    return TaskBrief(
        brief_id=uuid.uuid4().hex[:12],
        session_id=session_id,
        title=(title or "").strip() or "(제목 미정)",
        purpose=(purpose or "").strip() or (title or "(목적 미정)"),
        in_scope=tuple(s for s in in_scope if s),
        out_of_scope=tuple(s for s in out_of_scope if s),
        references=tuple(r for r in references if r),
        research_pack_ref=research_pack_ref or None,
        work_mode=work_mode or None,
        revision=int(revision),
    )


def build_role_work_orders(
    brief: TaskBrief,
    active_roles: Sequence[str],
    *,
    seats: Sequence[SeatRole] = DEFAULT_SEATS,
    council_round_cap: int = DEFAULT_COUNCIL_ROUND_CAP,
    purpose_overrides: Optional[Mapping[str, str]] = None,
    forbidden_scope_overrides: Optional[Mapping[str, Sequence[str]]] = None,
) -> Tuple[RoleWorkOrder, ...]:
    """Build per-role work orders from a brief.

    Each order gets the role's default required outputs, a forbidden
    scope (uniform across roles unless overridden), and the 3 default
    seats. ``provider`` selection is *not* part of this contract —
    seat × provider is C3-and-later.
    """

    purpose_overrides = dict(purpose_overrides or {})
    forbidden_scope_overrides = dict(forbidden_scope_overrides or {})
    out: list[RoleWorkOrder] = []
    # ``normalize_roles`` collapses short ↔ canonical drift in one shot.
    for role in normalize_roles(active_roles):
        short = _short_role(role)
        purpose = (
            purpose_overrides.get(role)
            or purpose_overrides.get(short)
            or f"{short} 관점에서 본 작업의 council 1차 입장 정리"
        )
        forbidden = forbidden_scope_overrides.get(role) or forbidden_scope_overrides.get(short) or _DEFAULT_FORBIDDEN
        out.append(
            RoleWorkOrder(
                role=role,
                brief_id=brief.brief_id,
                work_order_id=f"wo-{short}-{uuid.uuid4().hex[:8]}",
                purpose=purpose,
                required_outputs=required_outputs_for_role(role),
                forbidden_scope=tuple(forbidden),
                seats=tuple(seats),
                council_round_cap=int(council_round_cap),
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Deterministic 3-seat fan-out
# ---------------------------------------------------------------------------


def _owner_draft(
    *,
    role: str,
    round_index: int,
    work_order: RoleWorkOrder,
    brief: TaskBrief,
    provider: Optional[str] = None,
) -> RoleDraft:
    short = _short_role(role)
    perspective = (
        f"{short} 관점에서 1차 입장: {brief.purpose}"
    )
    evidence: list[str] = []
    if brief.research_pack_ref:
        evidence.append(
            f"research_pack: {brief.research_pack_ref} 의 {short} 우선 source 검토 필요"
        )
    if brief.references:
        evidence.append(f"references: {brief.references[0]}")
    if not evidence:
        evidence.append(f"current context: {brief.title}")
    risks = [
        f"{short} 도메인 hard rail 위반 시 회복 비용 큼 — work_order forbidden_scope 준수",
    ]
    next_actions = [
        f"{out} 의 1차 draft 완성"
        for out in work_order.required_outputs[:2]
    ] or [
        f"{short} required_outputs 채움"
    ]
    return RoleDraft(
        role=role,
        seat=SeatRole.OWNER,
        round_index=int(round_index),
        provider=provider,
        perspective=perspective,
        evidence=tuple(evidence),
        risks=tuple(risks),
        next_actions=tuple(next_actions),
        structured_fields={
            "required_outputs": list(work_order.required_outputs),
        },
    )


def _challenger_draft(
    *,
    role: str,
    round_index: int,
    owner: RoleDraft,
    brief: TaskBrief,
    provider: Optional[str] = None,
) -> RoleDraft:
    short = _short_role(role)
    perspective = (
        f"{short} 안에서의 반대/회의 입장: owner 의 1차 draft 가 놓치기 쉬운 실패 모드 점검"
    )
    evidence = (
        f"owner_draft({owner.draft_id}) — 검증할 가정",
    )
    risks = (
        f"happy-path 편향: 실패/권한/엣지/롤백 시나리오 누락",
        f"단일 provider 결정: 같은 role 안 반대 의견 표면화 필요",
    )
    next_actions = (
        f"실패/권한/엣지 시나리오 점검 후 owner_draft 보강 요청",
    )
    return RoleDraft(
        role=role,
        seat=SeatRole.CHALLENGER,
        round_index=int(round_index),
        provider=provider,
        perspective=perspective,
        evidence=evidence,
        risks=risks,
        next_actions=next_actions,
        structured_fields={
            "challenges_for": owner.draft_id,
            "brief_id": brief.brief_id,
        },
    )


def _peer_review(
    *,
    role: str,
    round_index: int,
    owner: RoleDraft,
    challenger: RoleDraft,
    brief: TaskBrief,
    consensus_status: CouncilConsensusStatus = CouncilConsensusStatus.AGREED,
    conditions: Sequence[str] = (),
    disagreement_summary: Optional[str] = None,
    reviewer_provider: Optional[str] = None,
) -> PeerReviewNote:
    short = _short_role(role)
    agreed = (
        f"owner / challenger 모두 {short} 도메인 hard rail 준수",
        f"required_outputs 1차 합의 — round {round_index}",
    )
    open_qs: tuple[str, ...] = ()
    if consensus_status is CouncilConsensusStatus.NEEDS_ANOTHER_ROUND:
        open_qs = ("challenger 가 제기한 실패 시나리오 미해소 — round +1 필요",)
    elif consensus_status is CouncilConsensusStatus.ESCALATED:
        open_qs = ("2 라운드 cap 도달 — tech-lead 가 결정해야 함",)
    # Disagreement summary hard rail (C3) — never empty when reviewer
    # could not settle. ensure_disagreement_summary leaves AGREED* alone.
    disagreement = ensure_disagreement_summary(
        disagreement_summary,
        role=role,
        round_index=round_index,
        consensus_status=consensus_status,
        open_questions=open_qs,
        risks=tuple(owner.risks) + tuple(challenger.risks),
    )
    public_summary_seed = (
        f"[{short}] round {int(round_index)} — peer review {consensus_status.value}"
    )
    public_summary = ensure_public_summary(
        public_summary_seed,
        role=role,
        round_index=round_index,
        consensus_status=consensus_status,
        risks=owner.risks or challenger.risks,
        next_actions=owner.next_actions,
    )
    return PeerReviewNote(
        role=role,
        round_index=int(round_index),
        reviewer_provider=reviewer_provider,
        owner_draft_id=owner.draft_id,
        challenger_draft_id=challenger.draft_id,
        consensus_status=consensus_status,
        agreed_points=agreed,
        open_questions=open_qs,
        conditions=tuple(conditions),
        disagreement_summary=disagreement,
        public_summary=public_summary,
    )


def build_deterministic_role_council(
    *,
    work_order: RoleWorkOrder,
    brief: TaskBrief,
    round_index: int = 1,
    owner_provider: Optional[str] = None,
    challenger_provider: Optional[str] = None,
    reviewer_provider: Optional[str] = None,
    consensus_status: CouncilConsensusStatus = CouncilConsensusStatus.AGREED,
    conditions: Sequence[str] = (),
    disagreement_summary: Optional[str] = None,
) -> RoleCouncilResult:
    """Assemble owner + challenger drafts + peer review into one round.

    Deterministic — no LLM. Caller can later swap drafts produced from
    live runners; the helper just enforces the 3-seat shape and the
    public_summary fallback.
    """

    owner = _owner_draft(
        role=work_order.role,
        round_index=round_index,
        work_order=work_order,
        brief=brief,
        provider=owner_provider,
    )
    challenger = _challenger_draft(
        role=work_order.role,
        round_index=round_index,
        owner=owner,
        brief=brief,
        provider=challenger_provider,
    )
    peer = _peer_review(
        role=work_order.role,
        round_index=round_index,
        owner=owner,
        challenger=challenger,
        brief=brief,
        consensus_status=consensus_status,
        conditions=conditions,
        disagreement_summary=disagreement_summary,
        reviewer_provider=reviewer_provider,
    )
    public_summary = ensure_public_summary(
        peer.public_summary,
        role=work_order.role,
        round_index=round_index,
        consensus_status=consensus_status,
        risks=owner.risks,
        next_actions=owner.next_actions,
    )
    # Mirror the reviewer-level disagreement guard onto the council
    # result itself so callers reading the RoleCouncilResult only (not
    # the nested PeerReviewNote) still see a non-empty summary on
    # ESCALATED / NEEDS_ANOTHER_ROUND.
    result_disagreement = ensure_disagreement_summary(
        disagreement_summary,
        role=work_order.role,
        round_index=round_index,
        consensus_status=consensus_status,
        open_questions=peer.open_questions,
        risks=tuple(owner.risks) + tuple(challenger.risks),
    )
    return RoleCouncilResult(
        role=work_order.role,
        work_order_id=work_order.work_order_id,
        round_index=int(round_index),
        drafts=(owner, challenger),
        peer_review=peer,
        consensus_status=consensus_status,
        public_summary=public_summary,
        disagreement_summary=result_disagreement,
    )


# ---------------------------------------------------------------------------
# Top-level bootstrap
# ---------------------------------------------------------------------------


def _derive_title(prompt: str) -> str:
    text = (prompt or "").strip()
    if not text:
        return "(제목 미정)"
    first_line = text.splitlines()[0].strip()
    return first_line[:80] or "(제목 미정)"


def _derive_purpose(prompt: str) -> str:
    text = (prompt or "").strip()
    return text[:240] or "(목적 미정)"


def determine_substage(
    councils: Sequence[RoleCouncilResult],
    *,
    council_round_cap: int = DEFAULT_COUNCIL_ROUND_CAP,
) -> str:
    """Pick the *post-round* substage for the supplied councils.

    - All settled → ``council_ready_for_synthesis``.
    - Any role that has hit the round cap without settling → ``council_escalated``.
    - Otherwise (still active) → ``council_round_complete``.
    """

    if not councils:
        return SUBSTAGE_ROLE_BRIEF_DISTRIBUTED
    if ready_for_synthesis(councils):
        return SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS
    # Group by role and look at the round caps.
    by_role: dict[str, list[RoleCouncilResult]] = {}
    for c in councils:
        by_role.setdefault(c.role, []).append(c)
    for history in by_role.values():
        if must_escalate_to_tech_lead(history, council_round_cap=council_round_cap):
            return SUBSTAGE_COUNCIL_ESCALATED
    return SUBSTAGE_COUNCIL_ROUND_COMPLETE


def _escalated_roles(councils: Sequence[RoleCouncilResult]) -> Tuple[str, ...]:
    return tuple(sorted({c.role for c in councils if c.is_escalated}))


def already_bootstrapped(session_extra: Mapping[str, Any]) -> bool:
    """Cheap idempotency check — caller uses this to short-circuit."""

    if not isinstance(session_extra, Mapping):
        return False
    return bool(session_extra.get(ROLE_COUNCILS_EXTRA_KEY)) and bool(
        session_extra.get(TASK_BRIEF_EXTRA_KEY)
    )


# ---------------------------------------------------------------------------
# Provider × seat matrix (metadata only — no live calls)
# ---------------------------------------------------------------------------


_DEFAULT_PROVIDER_ROTATION: Tuple[str, ...] = ("claude", "codex", "gemini")


def _role_manifest_path(role: str) -> Optional[str]:
    """Return the relative manifest path for *role* if known.

    All current engineering roles live under ``agents/engineering-agent/
    <short>/manifest.json``. Unknown roles return None so callers can
    skip them cleanly.
    """

    short = _short_role(role)
    if short in {
        "tech-lead",
        "backend-engineer",
        "frontend-engineer",
        "qa-engineer",
        "ai-engineer",
        "devops-engineer",
        "product-designer",
    }:
        return f"agents/engineering-agent/{short}/manifest.json"
    return None


def _load_role_manifest(
    role: str,
    *,
    repo_root: Optional[str] = None,
) -> Mapping[str, Any]:
    """Best-effort manifest load. Returns ``{}`` on any failure.

    ``repo_root`` is injected from tests; production code passes None and
    we resolve from the package layout (``__file__`` parent walk).
    """

    rel = _role_manifest_path(role)
    if not rel:
        return {}
    import json
    import os
    if repo_root is None:
        # Walk up from this module to find the repo root (4 parents:
        # council_bootstrap.py → agents → yule_orchestrator → src → repo).
        here = os.path.dirname(os.path.abspath(__file__))
        candidate = here
        for _ in range(5):
            candidate = os.path.dirname(candidate)
            if os.path.exists(os.path.join(candidate, "agents")):
                repo_root = candidate
                break
    if not repo_root:
        return {}
    try:
        path = os.path.join(repo_root, rel)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 — partial install / test fixture
        return {}


def build_provider_seat_matrix(
    roles: Iterable[str],
    *,
    seats: Sequence[SeatRole] = DEFAULT_SEATS,
    repo_root: Optional[str] = None,
    manifest_loader: Optional[Any] = None,
    available_providers: Optional[Iterable[str]] = None,
) -> Mapping[str, Mapping[str, list[str]]]:
    """Compute a per-role mapping ``role → {seat → [provider candidates]}``.

    The matrix is **metadata only** — C3 does not call any provider.
    For each role we:

    1. Load the role manifest if available (best-effort).
    2. Read ``preferred_advisors`` (ordered list, e.g. ``["claude",
       "codex", "gemini"]``). Falls back to a stable default rotation
       when the manifest is missing.
    3. Read ``council_role.default_seats`` if present, otherwise use the
       passed-in ``seats``.
    4. Round-robin assign providers to seats so owner / challenger get
       distinct providers when possible (provider × seat orthogonality
       from the design doc).

    C4 — ``available_providers`` injects the runtime-known available
    provider set. When supplied, the per-seat list keeps its
    ``preferred_advisors`` ordering but **prefers available providers
    first**, then falls back to the unavailable ones (clearly labelled
    via :func:`build_provider_availability` so the operator can grep).
    Pass ``None`` to keep the C3 behaviour (no filtering).
    """

    out: dict[str, dict[str, list[str]]] = {}
    loader = manifest_loader or (
        lambda role: _load_role_manifest(role, repo_root=repo_root)
    )
    avail_set: Optional[set[str]] = None
    if available_providers is not None:
        avail_set = {str(p).strip() for p in available_providers if str(p).strip()}
    for role in normalize_roles(roles):
        manifest = loader(role) or {}
        preferred = list(manifest.get("preferred_advisors") or _DEFAULT_PROVIDER_ROTATION)
        if not preferred:
            preferred = list(_DEFAULT_PROVIDER_ROTATION)
        council_role = manifest.get("council_role") or {}
        seat_values = (
            council_role.get("default_seats")
            or [s.value for s in seats]
        )
        # Optionally re-order so available providers come first while
        # preserving original priority within each group.
        if avail_set is not None:
            preferred = [p for p in preferred if p in avail_set] + [
                p for p in preferred if p not in avail_set
            ]
        per_seat: dict[str, list[str]] = {}
        for index, seat_label in enumerate(seat_values):
            seat_value = str(seat_label)
            primary = preferred[index % len(preferred)]
            fallbacks = [p for p in preferred if p != primary]
            per_seat[seat_value] = [primary, *fallbacks]
        out[role] = per_seat
    return out


PROVIDER_AVAILABILITY_EXTRA_KEY = "council_provider_availability"
"""``session.extra`` key — per-role mapping of providers → ``available``
bool, plus per-role lists of unavailable candidates. Stamped at
bootstrap when ``available_providers`` is supplied so the operator sees
*why* a given seat's primary candidate cannot run."""


def build_provider_availability(
    matrix: Mapping[str, Mapping[str, list[str]]],
    *,
    available_providers: Iterable[str],
) -> Mapping[str, Mapping[str, Any]]:
    """For each role report which matrix providers are runtime-available.

    Returns a dict of the shape::

        {
            "engineering-agent/backend-engineer": {
                "available": ["claude"],
                "unavailable": ["codex", "gemini"],
                "by_seat": {
                    "owner": {"primary": "claude", "available": True},
                    "challenger": {"primary": "codex", "available": False},
                    "reviewer": {"primary": "gemini", "available": False},
                },
            },
            ...
        }

    The output is intended for ``session.extra`` audit; callers should
    surface "council 가 사실상 claude-only" warnings from it before
    triggering live runners.
    """

    avail_set = {str(p).strip() for p in available_providers if str(p).strip()}
    out: dict[str, dict[str, Any]] = {}
    for role, by_seat in matrix.items():
        flat_candidates: list[str] = []
        for seat_candidates in by_seat.values():
            for cand in seat_candidates:
                if cand not in flat_candidates:
                    flat_candidates.append(cand)
        available = [c for c in flat_candidates if c in avail_set]
        unavailable = [c for c in flat_candidates if c not in avail_set]
        per_seat: dict[str, dict[str, Any]] = {}
        for seat_label, seat_candidates in by_seat.items():
            primary = seat_candidates[0] if seat_candidates else ""
            per_seat[seat_label] = {
                "primary": primary,
                "available": primary in avail_set,
            }
        out[role] = {
            "available": available,
            "unavailable": unavailable,
            "by_seat": per_seat,
        }
    return out


def _build_escalation_digest(
    councils: Sequence[RoleCouncilResult],
) -> Optional[Mapping[str, Any]]:
    """If any role hit the round cap unsettled, return a 1-line digest.

    The latest-round entry per role is the source of truth — older
    rounds are preserved in ``role_councils`` but the escalation digest
    points at the most recent.
    """

    latest_by_role: dict[str, RoleCouncilResult] = {}
    for c in councils:
        cur = latest_by_role.get(c.role)
        if cur is None or c.round_index >= cur.round_index:
            latest_by_role[c.role] = c
    escalated = [r for r in latest_by_role.values() if r.is_escalated]
    if not escalated:
        return None
    # Pick the highest-round-index escalation as the head — keeps the
    # digest stable when multiple roles escalate in the same tick.
    head = max(escalated, key=lambda r: r.round_index)
    return {
        "role": head.role,
        "round_index": int(head.round_index),
        "reason": "round_cap_reached",
        "disagreement_summary": head.disagreement_summary
        or head.peer_review.disagreement_summary
        or "(사유 미기재)",
        "public_summary": head.public_summary,
        "all_escalated_roles": sorted({r.role for r in escalated}),
        "at": datetime.utcnow().isoformat(),
    }


def bootstrap_council(
    *,
    session_id: str,
    canonical_prompt: str,
    active_roles: Sequence[str],
    work_mode: Optional[str] = None,
    research_pack_ref: Optional[str] = None,
    references: Sequence[str] = (),
    in_scope: Sequence[str] = (),
    out_of_scope: Sequence[str] = (),
    council_round_cap: int = DEFAULT_COUNCIL_ROUND_CAP,
    seats: Sequence[SeatRole] = DEFAULT_SEATS,
    manifest_loader: Optional[Any] = None,
    available_providers: Optional[Iterable[str]] = None,
) -> CouncilBootstrapResult:
    """Produce the round-1 council bundle for one session.

    Deterministic — every active role gets a settled council result so
    the lifecycle substage lands on ``council_ready_for_synthesis``. C3
    adds the provider × seat matrix stamp (metadata only, no live
    calls). C4+ will swap in live-runner results.
    """

    brief = build_task_brief(
        session_id=session_id,
        title=_derive_title(canonical_prompt),
        purpose=_derive_purpose(canonical_prompt),
        in_scope=in_scope,
        out_of_scope=out_of_scope,
        references=references,
        research_pack_ref=research_pack_ref,
        work_mode=work_mode,
    )
    work_orders = build_role_work_orders(
        brief,
        active_roles,
        seats=seats,
        council_round_cap=council_round_cap,
    )
    councils = tuple(
        build_deterministic_role_council(
            work_order=order,
            brief=brief,
            round_index=1,
            consensus_status=CouncilConsensusStatus.AGREED,
        )
        for order in work_orders
    )
    substage = determine_substage(councils, council_round_cap=council_round_cap)
    provider_matrix = build_provider_seat_matrix(
        [o.role for o in work_orders],
        seats=seats,
        manifest_loader=manifest_loader,
        available_providers=available_providers,
    )
    extras_update: dict[str, Any] = {
        TASK_BRIEF_EXTRA_KEY: dict(task_brief_to_payload(brief)),
        ROLE_WORK_ORDERS_EXTRA_KEY: [
            dict(role_work_order_to_payload(o)) for o in work_orders
        ],
        ROLE_COUNCILS_EXTRA_KEY: {
            role: [dict(p) for p in payloads]
            for role, payloads in role_councils_to_extra(councils).items()
        },
        LIFECYCLE_SUBSTAGE_EXTRA_KEY: substage,
        PROVIDER_SEAT_MATRIX_EXTRA_KEY: {
            role: {seat: list(provs) for seat, provs in by_seat.items()}
            for role, by_seat in provider_matrix.items()
        },
    }
    if available_providers is not None:
        availability = build_provider_availability(
            provider_matrix, available_providers=available_providers
        )
        extras_update[PROVIDER_AVAILABILITY_EXTRA_KEY] = {
            role: dict(payload) for role, payload in availability.items()
        }
    escalation_digest = _build_escalation_digest(councils)
    if escalation_digest is not None:
        extras_update[COUNCIL_ESCALATION_EXTRA_KEY] = dict(escalation_digest)
    # C4 — multi-role aggregate (head-only digest stays as backward-
    # compat for legacy readers). Empty aggregate is skipped.
    from .council import aggregate_escalations, escalation_aggregate_to_payload  # local import — keeps top-level lean
    aggregate = aggregate_escalations(councils)
    if not aggregate.is_empty():
        extras_update[COUNCIL_ESCALATION_AGGREGATE_EXTRA_KEY] = dict(
            escalation_aggregate_to_payload(aggregate)
        )
    return CouncilBootstrapResult(
        brief=brief,
        work_orders=work_orders,
        councils=councils,
        substage=substage,
        escalated_roles=_escalated_roles(councils),
        extras_update=extras_update,
    )


# ---------------------------------------------------------------------------
# Council re-entry — advance_council_round
# ---------------------------------------------------------------------------


def _read_task_brief_from_extra(session_extra: Mapping[str, Any]) -> Optional[TaskBrief]:
    payload = session_extra.get(TASK_BRIEF_EXTRA_KEY) if isinstance(session_extra, Mapping) else None
    if not isinstance(payload, Mapping):
        return None
    try:
        return task_brief_from_payload(payload)
    except Exception:  # noqa: BLE001
        return None


def _read_work_orders_from_extra(
    session_extra: Mapping[str, Any],
) -> Tuple[RoleWorkOrder, ...]:
    payload = (
        session_extra.get(ROLE_WORK_ORDERS_EXTRA_KEY)
        if isinstance(session_extra, Mapping)
        else None
    )
    if not isinstance(payload, list):
        return ()
    out: list[RoleWorkOrder] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        try:
            out.append(role_work_order_from_payload(item))
        except Exception:  # noqa: BLE001
            continue
    return tuple(out)


def _read_role_history(
    session_extra: Mapping[str, Any],
) -> Mapping[str, Tuple[RoleCouncilResult, ...]]:
    payload = (
        session_extra.get(ROLE_COUNCILS_EXTRA_KEY)
        if isinstance(session_extra, Mapping)
        else None
    )
    if not isinstance(payload, Mapping):
        return {}
    out: dict[str, Tuple[RoleCouncilResult, ...]] = {}
    for role, entries in payload.items():
        if not isinstance(entries, list):
            continue
        rounds: list[RoleCouncilResult] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            try:
                rounds.append(role_council_result_from_payload(entry))
            except Exception:  # noqa: BLE001
                continue
        if rounds:
            out[canonical_role(str(role))] = tuple(
                sorted(rounds, key=lambda r: r.round_index)
            )
    return out


def _next_round_index(history: Sequence[RoleCouncilResult]) -> int:
    return max((r.round_index for r in history), default=0) + 1


def _resolve_round_outcome(
    *,
    role: str,
    next_round: int,
    council_round_cap: int,
    requested_status: Optional[CouncilConsensusStatus],
) -> tuple[CouncilConsensusStatus, Optional[str]]:
    """Decide the (status, disagreement_summary) for an advanced round.

    Rules:
    - If caller passes an explicit status, honour it (still goes through
      the cap-aware escalation guard below).
    - If the next_round would exceed the cap and the caller did not
      explicitly settle, force ESCALATED.
    - Otherwise default to NEEDS_ANOTHER_ROUND so the caller can call
      again to drive the round-2 path.
    """

    status = requested_status
    if status is None:
        status = CouncilConsensusStatus.NEEDS_ANOTHER_ROUND

    settled = status in (
        CouncilConsensusStatus.AGREED,
        CouncilConsensusStatus.AGREED_WITH_CONDITIONS,
    )
    if not settled and next_round >= council_round_cap:
        # Hitting the cap without a settle → ESCALATED hard rail.
        status = CouncilConsensusStatus.ESCALATED

    summary_hint: Optional[str] = None
    if status is CouncilConsensusStatus.ESCALATED:
        summary_hint = (
            f"[{short_role(role)}] round {next_round} 미합의 — "
            f"council_round_cap={council_round_cap} 도달"
        )
    return status, summary_hint


def advance_council_round(
    session_extra: Mapping[str, Any],
    *,
    role: str,
    requested_status: Optional[CouncilConsensusStatus] = None,
    requested_disagreement: Optional[str] = None,
    council_round_cap: int = DEFAULT_COUNCIL_ROUND_CAP,
) -> Optional[CouncilBootstrapResult]:
    """Recompute the next council round for one role.

    Reads the existing ``task_brief`` / ``role_work_orders`` /
    ``role_councils`` off ``session_extra`` and produces a new
    :class:`CouncilBootstrapResult` whose ``extras_update`` should be
    merged on top of ``session.extra``.

    Returns ``None`` when the prerequisites are missing — the caller can
    decide whether to bootstrap from scratch or escalate.
    """

    brief = _read_task_brief_from_extra(session_extra)
    work_orders = _read_work_orders_from_extra(session_extra)
    history_by_role = _read_role_history(session_extra)
    if brief is None or not work_orders:
        return None

    role = canonical_role(role)
    matching_order = next((o for o in work_orders if canonical_role(o.role) == role), None)
    if matching_order is None:
        return None

    history = history_by_role.get(role, ())
    next_round = _next_round_index(history)

    status, hinted_disagreement = _resolve_round_outcome(
        role=role,
        next_round=next_round,
        council_round_cap=council_round_cap,
        requested_status=requested_status,
    )
    disagreement = requested_disagreement or hinted_disagreement

    new_round = build_deterministic_role_council(
        work_order=matching_order,
        brief=brief,
        round_index=next_round,
        consensus_status=status,
        disagreement_summary=disagreement,
    )

    # Merge into existing role_councils history — preserve other roles
    # exactly as-is, append the new round for this role.
    flat_history = [
        r for entries in history_by_role.values() for r in entries
    ]
    flat_history.append(new_round)

    # Substage decision must take the *full* history into account
    # because escalated state for any role wins over a settled one
    # elsewhere.
    substage = determine_substage(
        flat_history, council_round_cap=council_round_cap
    )

    rebuilt_role_councils: dict[str, list[Mapping[str, Any]]] = {}
    for other_role, rounds in history_by_role.items():
        if other_role == role:
            rebuilt_role_councils[other_role] = [
                dict(role_council_result_to_payload(r))
                for r in (*rounds, new_round)
            ]
        else:
            rebuilt_role_councils[other_role] = [
                dict(role_council_result_to_payload(r)) for r in rounds
            ]
    rebuilt_role_councils.setdefault(role, []).extend(
        []
        if role in rebuilt_role_councils
        and any(p.get("round_index") == new_round.round_index for p in rebuilt_role_councils[role])
        else [dict(role_council_result_to_payload(new_round))]
    )

    extras_update: dict[str, Any] = {
        ROLE_COUNCILS_EXTRA_KEY: rebuilt_role_councils,
        LIFECYCLE_SUBSTAGE_EXTRA_KEY: substage,
    }
    # Escalation digest reflects the latest *all* roles state.
    digest = _build_escalation_digest(tuple(flat_history))
    if digest is not None:
        extras_update[COUNCIL_ESCALATION_EXTRA_KEY] = dict(digest)
    # C4 — refresh multi-role aggregate too so the operator surface
    # always sees the current set of escalated roles, not just the head.
    from .council import aggregate_escalations, escalation_aggregate_to_payload  # local import — keeps top-level lean
    aggregate = aggregate_escalations(tuple(flat_history))
    if not aggregate.is_empty():
        extras_update[COUNCIL_ESCALATION_AGGREGATE_EXTRA_KEY] = dict(
            escalation_aggregate_to_payload(aggregate)
        )

    return CouncilBootstrapResult(
        brief=brief,
        work_orders=work_orders,
        councils=tuple(flat_history),
        substage=substage,
        escalated_roles=_escalated_roles(tuple(flat_history)),
        extras_update=extras_update,
    )


# ---------------------------------------------------------------------------
# Session.extra round-trip — best-effort persistence
# ---------------------------------------------------------------------------


def persist_bootstrap_to_session(
    session: Any,
    bootstrap: CouncilBootstrapResult,
    *,
    persist_extra_keys: Optional[Any] = None,
) -> Any:
    """Stamp the bootstrap onto ``session.extra``.

    ``persist_extra_keys`` is injected so the router can pass its
    ``_persist_extra_keys`` (which does the SQLite round-trip). When not
    supplied we fall back to a live-mutation update of ``session.extra``
    if it's a dict — useful for unit-test fixtures.
    """

    if session is None:
        return session
    updates = dict(bootstrap.extras_update)
    if persist_extra_keys is not None:
        try:
            return persist_extra_keys(session, updates)
        except Exception:  # noqa: BLE001 — best-effort, never block intake
            return session
    live = getattr(session, "extra", None)
    if isinstance(live, dict):
        for key, value in updates.items():
            live[key] = value
    return session


def synthesis_gate(
    session_extra: Mapping[str, Any],
) -> Optional[str]:
    """Read-only synthesis gate from ``session.extra``.

    Returns ``None`` when synthesis can proceed, else a 1-line reason —
    same vocabulary as :func:`council.synthesis_block_reason`. Lets the
    router / status surface tell the operator *why* synthesis hasn't
    triggered without having to rebuild the dataclasses.
    """

    payload = session_extra.get(ROLE_COUNCILS_EXTRA_KEY) if isinstance(session_extra, Mapping) else None
    if not payload:
        return "no_role_councils_recorded"
    from .council import role_councils_from_extra  # local import — keeps top-level lean

    results = role_councils_from_extra(payload)
    return synthesis_block_reason(results)


__all__ = [
    "CouncilBootstrapResult",
    "TASK_BRIEF_EXTRA_KEY",
    "ROLE_WORK_ORDERS_EXTRA_KEY",
    "PROVIDER_SEAT_MATRIX_EXTRA_KEY",
    "PROVIDER_AVAILABILITY_EXTRA_KEY",
    "build_task_brief",
    "build_role_work_orders",
    "build_deterministic_role_council",
    "build_provider_seat_matrix",
    "build_provider_availability",
    "required_outputs_for_role",
    "determine_substage",
    "already_bootstrapped",
    "bootstrap_council",
    "advance_council_round",
    "persist_bootstrap_to_session",
    "synthesis_gate",
]
