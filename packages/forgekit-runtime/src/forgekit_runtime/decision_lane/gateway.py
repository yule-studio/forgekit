"""Gateway intake packet — approve / reject / request-more-info (the handoff verdict).

The gateway is the org's intake desk: it takes the PM brief + meeting and decides whether
to **forward to tech-lead** (approve), **bounce it back for more info** (request-more-info),
or **refuse it** (reject) — and it does so as an explicit *packet*, not a bare boolean. It
makes NO technical decision (that's tech-lead); it only judges intake readiness + policy.

``route_to_tech_lead`` (in :mod:`.lane`) stays as the thin forward/block boolean; this is
the richer verdict surface the operator and the decision log read. A packet is honest by
construction (:func:`validate_gateway_packet`): an *approve* may not carry an info request
or a reject reason, a *request-more-info* MUST list what's missing, and a *reject* MUST
give a reason — so a fake "approved with nothing decided" packet cannot exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .schemas import MeetingRecord, PMBrief
from .validators import validate_meeting, validate_pm_brief

# gateway verdicts
GATEWAY_APPROVE = "approve"                 # forward to tech-lead
GATEWAY_REJECT = "reject"                   # refuse (out of scope / policy)
GATEWAY_REQUEST_INFO = "request_more_info"  # bounce back — fixable gaps
GATEWAY_VERDICTS: Tuple[str, ...] = (GATEWAY_APPROVE, GATEWAY_REJECT, GATEWAY_REQUEST_INFO)


@dataclass(frozen=True)
class GatewayPacket:
    """The gateway's intake verdict on a PM brief + meeting, as a handoff packet."""

    topic: str
    verdict: str                              # GATEWAY_*
    to_role: str = "tech-lead"                # forward target (approve only)
    info_requested: Tuple[str, ...] = ()      # request_more_info: what's missing
    reject_reason: str = ""                   # reject: why
    rationale: str = ""

    @property
    def forwarded(self) -> bool:
        """True only on approve — the single signal downstream stages key on."""
        return self.verdict == GATEWAY_APPROVE

    def to_dict(self) -> dict:
        return {"topic": self.topic, "verdict": self.verdict, "to_role": self.to_role,
                "info_requested": list(self.info_requested), "reject_reason": self.reject_reason,
                "rationale": self.rationale, "forwarded": self.forwarded}

    def lines(self) -> Tuple[str, ...]:
        head = {
            GATEWAY_APPROVE: f"gateway: 승인 → {self.to_role} 로 전달",
            GATEWAY_REJECT: "gateway: 반려",
            GATEWAY_REQUEST_INFO: "gateway: 추가정보 요청",
        }.get(self.verdict, f"gateway: {self.verdict}")
        out = [f"{head} — {self.topic}"]
        for i in self.info_requested:
            out.append(f"  ☐ 필요: {i}")
        if self.reject_reason:
            out.append(f"  ✗ 사유: {self.reject_reason}")
        if self.rationale:
            out.append(f"  · {self.rationale}")
        return tuple(out)


def gateway_review(
    brief: Optional[PMBrief],
    meeting: Optional[MeetingRecord],
    *,
    policy_block: str = "",
) -> GatewayPacket:
    """Produce the gateway's verdict packet. Order: policy reject > fixable info > approve.

    * ``policy_block`` set → **reject** (out of scope / forbidden — not the gateway's to fix);
    * missing/invalid PM brief or meeting → **request_more_info** with the concrete gaps;
    * a real brief + real meeting → **approve**, forward to tech-lead.
    """

    topic = getattr(brief, "topic", "") or "(no brief)"

    if policy_block:
        return GatewayPacket(topic=topic, verdict=GATEWAY_REJECT, reject_reason=policy_block,
                             rationale="정책/범위 사유로 intake 반려 — tech-lead 전달 안 함")

    info = []
    if brief is None:
        info.append("PM brief — 문제/사용자가치/acceptance/성공지표")
    else:
        info.extend(validate_pm_brief(brief))
    if meeting is None:
        info.append("design meeting — ≥2 역할 · 반대/우려")
    else:
        info.extend(validate_meeting(meeting))
    if info:
        return GatewayPacket(topic=topic, verdict=GATEWAY_REQUEST_INFO,
                             info_requested=tuple(info),
                             rationale="intake 불완전 — 보완 후 재제출 필요 (반려 아님)")

    return GatewayPacket(topic=topic, verdict=GATEWAY_APPROVE,
                         rationale="brief+meeting 실재 확인 — tech-lead 기술 승인으로 전달")


def validate_gateway_packet(packet: GatewayPacket) -> Tuple[str, ...]:
    """Anti-fake: the verdict must match its payload (no empty approve, no silent reject)."""

    v = []
    if packet.verdict not in GATEWAY_VERDICTS:
        v.append(f"gateway: verdict '{packet.verdict}' 알 수 없음")
        return tuple(v)
    if not (packet.topic or "").strip():
        v.append("gateway: topic 비어 있음")
    if packet.verdict == GATEWAY_APPROVE:
        if packet.info_requested or packet.reject_reason:
            v.append("gateway: approve 인데 info_requested/reject_reason 존재 — 모순")
    elif packet.verdict == GATEWAY_REQUEST_INFO:
        if not packet.info_requested:
            v.append("gateway: request_more_info 인데 요청 항목 없음 — fake")
    elif packet.verdict == GATEWAY_REJECT:
        if not (packet.reject_reason or "").strip():
            v.append("gateway: reject 인데 사유 없음 — 침묵 반려")
    return tuple(v)


__all__ = (
    "GATEWAY_APPROVE", "GATEWAY_REJECT", "GATEWAY_REQUEST_INFO", "GATEWAY_VERDICTS",
    "GatewayPacket", "gateway_review", "validate_gateway_packet",
)
