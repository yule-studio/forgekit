"""Council-specific status signals for :mod:`session_status`.

Split from ``session_status.py`` (854 lines) so that file stays
orchestration-only. This module reads ``session.extra`` (which the C2
bootstrap and C3 advance_council_round stamp) and emits a tuple of
:class:`session_status.SessionStatusSignal` records the caller appends
to the report.

Surfaced situations (C3 scope):

- ``council_bootstrap_error`` — bootstrap silently swallowed an
  exception. The operator sees the 1-line reason instead of an empty
  council.
- ``council_state_missing`` — intake completed but ``role_councils`` is
  empty. Differs from bootstrap error: no exception, just no council.
- ``council_round_2_pending`` — at least one role council finished round
  1 unsettled and round 2 has not been triggered yet. Operator-visible
  prompt to call ``advance_council_for_role``.
- ``council_escalated`` — ``must_escalate_to_tech_lead`` is True for at
  least one role. tech-lead needs to intervene.
- ``council_ready_for_synthesis`` (info severity) — every role settled;
  synthesis can proceed.

The module **does not** modify ``session.extra`` or trigger any council
action. It is a pure read.
"""

from __future__ import annotations

from typing import Any, Mapping, Tuple

from ..council import (
    CouncilConsensusStatus,
    DEFAULT_COUNCIL_ROUND_CAP,
    must_escalate_to_tech_lead,
    ready_for_synthesis,
    role_councils_from_extra,
)
from .council_substage import (
    COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY,
    COUNCIL_ESCALATION_AGGREGATE_EXTRA_KEY,
    COUNCIL_ESCALATION_EXTRA_KEY,
    GATEWAY_SURFACE_PAYLOAD_EXTRA_KEY,
    ROLE_COUNCILS_EXTRA_KEY,
    SUBSTAGE_APPROVAL_PACKET_DRAFTED,
    SUBSTAGE_APPROVAL_SURFACE_POSTED,
    SUBSTAGE_COUNCIL_ESCALATED,
    SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS,
    SUBSTAGE_TECH_LEAD_SYNTHESIS,
    TECH_LEAD_SIGNOFF_EXTRA_KEY,
    LIFECYCLE_SUBSTAGE_EXTRA_KEY,
)
from .session_status import SessionStatusSignal


COUNCIL_BOOTSTRAP_ERROR = "council_bootstrap_error"
COUNCIL_STATE_MISSING = "council_state_missing"
COUNCIL_ROUND_2_PENDING = "council_round_2_pending"
COUNCIL_ESCALATED_CODE = "council_escalated"
COUNCIL_READY_FOR_SYNTHESIS_CODE = "council_ready_for_synthesis"
# C4 — approval-flow signals
APPROVAL_PACKET_DRAFTED = "approval_packet_drafted"
APPROVAL_SURFACE_POSTED = "approval_surface_posted"
TECH_LEAD_SIGNOFF_BLOCKED = "tech_lead_signoff_blocked"


def _short(role: str) -> str:
    return role.rsplit("/", 1)[-1] if "/" in role else role


_COUNCIL_EXPECTED_KEYS: Tuple[str, ...] = (
    "task_brief",
    "role_work_orders",
    ROLE_COUNCILS_EXTRA_KEY,
    COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY,
    COUNCIL_ESCALATION_EXTRA_KEY,
    COUNCIL_ESCALATION_AGGREGATE_EXTRA_KEY,
    TECH_LEAD_SIGNOFF_EXTRA_KEY,
    GATEWAY_SURFACE_PAYLOAD_EXTRA_KEY,
    "approval_packet",
    LIFECYCLE_SUBSTAGE_EXTRA_KEY,
    "active_research_roles",
)


def _council_is_expected(extra: Mapping[str, Any]) -> bool:
    """True when the session has *any* council-relevant key.

    Legacy sessions (pre-C2) carry none of these. For those we keep
    silent — council signals are not meaningful, and emitting
    ``council_state_missing`` would falsely promote pure-info status
    reports into "actionable" surfaces.
    """

    for key in _COUNCIL_EXPECTED_KEYS:
        value = extra.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, Mapping)) and len(value) == 0:
            continue
        return True
    return False


def collect_council_signals(
    extra: Mapping[str, Any],
    *,
    intake_completed: bool = True,
) -> Tuple[SessionStatusSignal, ...]:
    """Read council state off ``session.extra`` and emit status signals.

    ``intake_completed`` short-circuits this to an empty tuple — there is
    no point flagging "council missing" for a session that never made it
    past intake. Likewise, sessions with no council-relevant extras are
    treated as "not on the council path yet" and produce no signals;
    this keeps legacy / closed / pure-info sessions clean.
    """

    if not intake_completed or not isinstance(extra, Mapping):
        return ()
    if not _council_is_expected(extra):
        return ()

    signals: list[SessionStatusSignal] = []

    bootstrap_error = extra.get(COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY)
    if isinstance(bootstrap_error, str) and bootstrap_error.strip():
        signals.append(
            SessionStatusSignal(
                code=COUNCIL_BOOTSTRAP_ERROR,
                severity="failed",
                title="role council bootstrap 실패",
                detail=bootstrap_error.strip(),
                propose=(
                    "active_research_roles / session_id / 모듈 import 상태를 확인하세요. "
                    "intake/kickoff 흐름은 영향 없음."
                ),
            )
        )

    role_councils_raw = extra.get(ROLE_COUNCILS_EXTRA_KEY)
    has_councils = isinstance(role_councils_raw, Mapping) and bool(role_councils_raw)
    if not has_councils:
        # Only flag "state missing" when there's no bootstrap error
        # already explaining the absence — avoid double-warning.
        if not isinstance(bootstrap_error, str) or not bootstrap_error.strip():
            signals.append(
                SessionStatusSignal(
                    code=COUNCIL_STATE_MISSING,
                    severity="blocked",
                    title="role council 미생성",
                    detail=(
                        "intake 는 완료됐지만 task_brief / role_councils 가 아직 없음"
                    ),
                    propose=(
                        "council_flow.maybe_bootstrap_council 가 호출됐는지 확인."
                    ),
                )
            )
        return tuple(signals)

    results = role_councils_from_extra(role_councils_raw)
    if not results:
        signals.append(
            SessionStatusSignal(
                code=COUNCIL_STATE_MISSING,
                severity="blocked",
                title="role council payload 비어 있음",
                detail="role_councils 키는 있지만 deserialise 결과가 없음",
                propose="payload 형식과 council module import 상태를 점검하세요.",
            )
        )
        return tuple(signals)

    # Build per-role latest-round view to drive the round-cap signals.
    latest_by_role: dict[str, Any] = {}
    history_by_role: dict[str, list] = {}
    for r in results:
        history_by_role.setdefault(r.role, []).append(r)
        cur = latest_by_role.get(r.role)
        if cur is None or r.round_index >= cur.round_index:
            latest_by_role[r.role] = r

    # C4 — tech-lead signoff BLOCKED surfaces as a failed signal so the
    # operator sees the explicit reason without paging the packet.
    signoff_payload = extra.get(TECH_LEAD_SIGNOFF_EXTRA_KEY)
    if isinstance(signoff_payload, Mapping):
        status = str(signoff_payload.get("status") or "").strip()
        if status == "blocked":
            rationale = str(signoff_payload.get("rationale") or "").strip() or "(사유 미기재)"
            signals.append(
                SessionStatusSignal(
                    code=TECH_LEAD_SIGNOFF_BLOCKED,
                    severity="blocked",
                    title="tech-lead signoff 보류",
                    detail=rationale,
                    propose=(
                        "tech-lead 가 합의 보강 후 signoff status 를 재설정하세요."
                    ),
                )
            )

    # 1) escalation — surface as `blocked` severity so it pops over info.
    escalated_roles = sorted(
        role for role, latest in latest_by_role.items() if latest.is_escalated
    )
    if not escalated_roles:
        # Even without `is_escalated`, must_escalate_to_tech_lead is the
        # SSoT — it covers the cap-reached-but-still-needs-another-round
        # edge case.
        for role, history in history_by_role.items():
            if must_escalate_to_tech_lead(
                history, council_round_cap=DEFAULT_COUNCIL_ROUND_CAP
            ):
                escalated_roles.append(role)
        escalated_roles = sorted(set(escalated_roles))

    if escalated_roles:
        digest = extra.get(COUNCIL_ESCALATION_EXTRA_KEY)
        detail = ", ".join(_short(r) for r in escalated_roles)
        propose = (
            "tech-lead 가 disagreement_summary 를 검토해 결정해야 함."
        )
        if isinstance(digest, Mapping):
            disagreement = digest.get("disagreement_summary")
            if isinstance(disagreement, str) and disagreement.strip():
                detail = f"{detail} — {disagreement.strip()}"
        signals.append(
            SessionStatusSignal(
                code=COUNCIL_ESCALATED_CODE,
                severity="blocked",
                title="council escalated to tech-lead",
                detail=detail,
                propose=propose,
            )
        )

    # 2) round-2 pending — at least one role finished round 1 (or earlier)
    # in NEEDS_ANOTHER_ROUND and has not yet been re-run.
    pending_roles = sorted(
        role
        for role, latest in latest_by_role.items()
        if latest.consensus_status is CouncilConsensusStatus.NEEDS_ANOTHER_ROUND
        and role not in escalated_roles
    )
    if pending_roles:
        signals.append(
            SessionStatusSignal(
                code=COUNCIL_ROUND_2_PENDING,
                severity="stale",
                title="council round 2 pending",
                detail=", ".join(_short(r) for r in pending_roles),
                propose=(
                    "council_flow.advance_council_for_role(role=...) 를 호출해 다음 라운드를 진행하세요."
                ),
            )
        )

    # 3) info — synthesis 진입 준비. Only when no escalation and no
    # pending round is hanging.
    substage = extra.get(LIFECYCLE_SUBSTAGE_EXTRA_KEY)
    if (
        substage == SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS
        or ready_for_synthesis(results)
    ) and not escalated_roles and not pending_roles:
        signals.append(
            SessionStatusSignal(
                code=COUNCIL_READY_FOR_SYNTHESIS_CODE,
                severity="info",
                title="council ready for synthesis",
                detail=(
                    f"{len(latest_by_role)} role 의 round 1 모두 settled — "
                    "tech-lead synthesis 진입 가능"
                ),
                propose=None,
            )
        )
    # 4) substage echo — when the substage is escalated but we did not
    # surface it above for some reason, do not double-emit. Only echo
    # when substage and our derived state disagree.
    elif substage == SUBSTAGE_COUNCIL_ESCALATED and not escalated_roles:
        signals.append(
            SessionStatusSignal(
                code=COUNCIL_ESCALATED_CODE,
                severity="blocked",
                title="council substage = escalated",
                detail="role_councils 에는 escalated 결과가 없지만 substage 가 escalated",
                propose="round 데이터 동기화 누락 가능성을 점검하세요.",
            )
        )

    # 5) approval flow substage echoes (C4) — info signals so the
    # operator can see how far the packet pipeline has advanced. Only
    # one of these fires at a time (substage is single-valued).
    if substage == SUBSTAGE_APPROVAL_PACKET_DRAFTED:
        signals.append(
            SessionStatusSignal(
                code=APPROVAL_PACKET_DRAFTED,
                severity="info",
                title="approval packet 작성됨",
                detail="tech-lead signoff 반영 + packet 초안 stamp",
                propose="gateway operator surface 게시를 진행하세요.",
            )
        )
    elif substage == SUBSTAGE_APPROVAL_SURFACE_POSTED:
        signals.append(
            SessionStatusSignal(
                code=APPROVAL_SURFACE_POSTED,
                severity="info",
                title="approval surface 게시 준비",
                detail="기술/운영 분리 payload 가 session.extra 에 stamp 됨",
                propose=None,
            )
        )

    return tuple(signals)


__all__ = [
    "COUNCIL_BOOTSTRAP_ERROR",
    "COUNCIL_STATE_MISSING",
    "COUNCIL_ROUND_2_PENDING",
    "COUNCIL_ESCALATED_CODE",
    "COUNCIL_READY_FOR_SYNTHESIS_CODE",
    "APPROVAL_PACKET_DRAFTED",
    "APPROVAL_SURFACE_POSTED",
    "TECH_LEAD_SIGNOFF_BLOCKED",
    "collect_council_signals",
]
