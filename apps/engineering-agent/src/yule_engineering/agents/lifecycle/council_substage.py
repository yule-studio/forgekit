"""Council substage vocabulary — read-only projection for session_status.

본 모듈은 ``agents.council`` 의 substage 상수와 valid set 을 lifecycle 측
모듈이 1 줄로 import 할 수 있게 모은 얕은 re-export 다. 실제 SSoT 는
[``agents/council.py``](../council.py) 이며, 본 모듈은 그 type / value 만
다시 surface 한다.

이 분리의 목적은 ``session_status.py`` (현재 854 줄) 가 새 vocabulary 를
하나 더 import 할 때 도메인 모듈 (council) 을 직접 끌어오지 않게 하는
것이다 — top-level 분리 신호 (`/CLAUDE.md` 책임 분리 신호) 를 미리 잡는다.

본 모듈은 helper 도 추가하지 않는다 — substage validation 같은 로직은
``agents.council.is_valid_substage`` 가 SSoT.
"""

from __future__ import annotations

from ..council import (  # noqa: F401 — re-export
    ALL_SUBSTAGES,
    DELIBERATION_SUBSTAGES,
    SYNTHESIS_SUBSTAGES,
    EXECUTION_REVIEW_SUBSTAGES,
    SUBSTAGE_ROLE_BRIEF_DISTRIBUTED,
    SUBSTAGE_ROLE_DRAFTS_IN_PROGRESS,
    SUBSTAGE_PEER_REVIEW_PENDING,
    SUBSTAGE_COUNCIL_ROUND_COMPLETE,
    SUBSTAGE_COUNCIL_ESCALATED,
    SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS,
    SUBSTAGE_TECH_LEAD_SYNTHESIS,
    SUBSTAGE_APPROVAL_PACKET_DRAFTED,
    SUBSTAGE_APPROVAL_SURFACE_POSTED,
    SUBSTAGE_CI_SIGNAL_RECEIVED,
    SUBSTAGE_ROLE_COUNCIL_RECONVENED,
    SUBSTAGE_REVIEW_FEEDBACK_ROUTED,
    SUBSTAGE_RETROSPECTIVE_CANDIDATE,
    is_valid_substage,
)

LIFECYCLE_SUBSTAGE_EXTRA_KEY = "lifecycle_substage"
"""``session.extra`` key holding the current council substage id."""

ROLE_COUNCILS_EXTRA_KEY = "role_councils"
"""``session.extra`` key holding ``role → list[RoleCouncilResult.to_payload]``."""

APPROVAL_PACKET_EXTRA_KEY = "approval_packet"
"""``session.extra`` key holding the serialized ``ApprovalPacket``."""

EXECUTION_REVIEWS_EXTRA_KEY = "execution_reviews"
"""``session.extra`` key holding ``list[ExecutionReview.to_payload]``."""

RETROSPECTIVE_CANDIDATES_EXTRA_KEY = "retrospective_candidates"
"""``session.extra`` key holding ``list[RetrospectiveCandidate.to_payload]``."""

COUNCIL_ESCALATION_EXTRA_KEY = "council_escalation"
"""``session.extra`` key holding the latest escalation digest.

Shape::

    {
        "role": "engineering-agent/backend-engineer",
        "round_index": 2,
        "reason": "...",
        "disagreement_summary": "...",
        "public_summary": "...",
        "at": iso8601,
    }

Populated by :func:`agents.council_bootstrap.advance_council_round` when
:func:`agents.council.must_escalate_to_tech_lead` is True for any role
council. Read by ``council_status_signals.collect_council_signals``.
"""

COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY = "council_bootstrap_error"
"""``session.extra`` key holding a 1-line reason when council bootstrap
failed silently. Status diagnostic surfaces this so the operator can see
*why* the council never started, even though intake / kickoff completed."""

COUNCIL_ESCALATION_AGGREGATE_EXTRA_KEY = "council_escalation_aggregate"
"""``session.extra`` key holding the multi-role escalation aggregate from
:func:`agents.council.aggregate_escalations`. The head-only digest at
:data:`COUNCIL_ESCALATION_EXTRA_KEY` stays as backward-compat, the
aggregate is the SSoT for tech-lead intervention surface."""

TECH_LEAD_SIGNOFF_EXTRA_KEY = "tech_lead_signoff"
"""``session.extra`` key holding the serialized :class:`agents.council.
TechLeadSignoff`. tech-lead's technical decision — distinct from the
gateway-mediated operator approval card."""

GATEWAY_SURFACE_PAYLOAD_EXTRA_KEY = "gateway_surface_payload"
"""``session.extra`` key holding the technical-signoff vs operator-
approval-request split surface payload. Used by the gateway to render
``#승인-대기`` cards with ``[기술]`` / ``[운영]`` prefixes."""

__all__ = [
    "ALL_SUBSTAGES",
    "DELIBERATION_SUBSTAGES",
    "SYNTHESIS_SUBSTAGES",
    "EXECUTION_REVIEW_SUBSTAGES",
    "SUBSTAGE_ROLE_BRIEF_DISTRIBUTED",
    "SUBSTAGE_ROLE_DRAFTS_IN_PROGRESS",
    "SUBSTAGE_PEER_REVIEW_PENDING",
    "SUBSTAGE_COUNCIL_ROUND_COMPLETE",
    "SUBSTAGE_COUNCIL_ESCALATED",
    "SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS",
    "SUBSTAGE_TECH_LEAD_SYNTHESIS",
    "SUBSTAGE_APPROVAL_PACKET_DRAFTED",
    "SUBSTAGE_APPROVAL_SURFACE_POSTED",
    "SUBSTAGE_CI_SIGNAL_RECEIVED",
    "SUBSTAGE_ROLE_COUNCIL_RECONVENED",
    "SUBSTAGE_REVIEW_FEEDBACK_ROUTED",
    "SUBSTAGE_RETROSPECTIVE_CANDIDATE",
    "is_valid_substage",
    "LIFECYCLE_SUBSTAGE_EXTRA_KEY",
    "ROLE_COUNCILS_EXTRA_KEY",
    "APPROVAL_PACKET_EXTRA_KEY",
    "EXECUTION_REVIEWS_EXTRA_KEY",
    "RETROSPECTIVE_CANDIDATES_EXTRA_KEY",
    "COUNCIL_ESCALATION_EXTRA_KEY",
    "COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY",
    "COUNCIL_ESCALATION_AGGREGATE_EXTRA_KEY",
    "TECH_LEAD_SIGNOFF_EXTRA_KEY",
    "GATEWAY_SURFACE_PAYLOAD_EXTRA_KEY",
]
