"""P1-Z4 A — slash intake approval card eligibility (structured signals).

배경
----
``_maybe_post_intake_approval_card`` 는 옛 wiring 에서
:func:`should_route_to_github_workos` 를 그대로 재사용했다.  그 helper
는 fresh intake routing 용이라 ``detect_coding_intent(request_text)``
까지 강제 — prompt 가 자연어 ("실제 구현 가능한 상태까지 구현" / repo
URL only / "issue 만들어서 시작") 면 ``no_coding_intent`` 로 skip 됐다.

결과: receipt 는 "승인 필요" 라고 안내했는데 ``#승인-대기`` 카드는
안 떠서 operator 가 무한 대기.  canonical session ``000f13fb121b`` 가
직접 사례.

본 모듈
========
intake 시점의 approval card posting 은 **구조 신호** 만으로 판단 —
prompt phrase 게이트 재사용 금지.  체크 신호:

  * ``write_requested == True``  — operator 가 명시 코딩 의도
  * ``write_blocked_reason`` 존재 또는 lifecycle 가 implementation
    (approval-needed 동등 신호)
  * ``extra.lifecycle_mode == "implementation"`` (research_only 아님)
  * ``extra.github_target`` 있음
  * ``extra.coding_handoff_packet`` 있음
  * obsidian intent 아님 (prompt 가 vault 저장 요청이면 카드 안 띄움)

본 helper 는 pure decision (no I/O).  caller 가 결과만 보고 카드
게시 여부 결정.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


ELIGIBILITY_ELIGIBLE: str = "eligible"
ELIGIBILITY_SKIPPED: str = "skipped"

SKIP_REASON_NOT_WRITE_REQUESTED: str = "not_write_requested"
SKIP_REASON_RESEARCH_ONLY_LIFECYCLE: str = "research_only_lifecycle"
SKIP_REASON_OBSIDIAN_INTENT: str = "obsidian_intent"
SKIP_REASON_NO_GITHUB_TARGET: str = "no_github_target"
SKIP_REASON_NO_HANDOFF_PACKET: str = "no_coding_handoff_packet"
SKIP_REASON_NO_IMPLEMENTATION_SIGNAL: str = "no_implementation_signal"
SKIP_REASON_NO_EXTRA: str = "session_extra_missing"


@dataclass(frozen=True)
class IntakeApprovalCardDecision:
    """Pure decision result.  ``eligible == True`` 이면 caller 가
    카드 게시 진행, False 면 ``skip_reason`` 만 audit.
    """

    eligible: bool
    skip_reason: Optional[str] = None
    lifecycle_mode: Optional[str] = None


def decide_intake_approval_card_eligibility(
    *,
    session: Any,
    prompt_text: str,
) -> IntakeApprovalCardDecision:
    """intake 시점에 ``#승인-대기`` 카드 게시 여부를 판단.

    P1-Z4 — should_route_to_github_workos 를 호출하지 않는다.  prompt
    phrase 게이트는 fresh intake routing 의 다른 경로 (channel router
    coding_gate) 가 담당.  본 helper 는 오직 구조 신호만 본다.
    """

    # obsidian intent 는 명확히 다른 domain — 어떤 경우든 차단.
    if _detect_obsidian_intent_safe(prompt_text):
        return IntakeApprovalCardDecision(
            eligible=False, skip_reason=SKIP_REASON_OBSIDIAN_INTENT
        )

    # write_requested 가 False 면 operator 가 코딩 의도 명시 안 함.
    if not getattr(session, "write_requested", False):
        return IntakeApprovalCardDecision(
            eligible=False, skip_reason=SKIP_REASON_NOT_WRITE_REQUESTED
        )

    extra_raw = getattr(session, "extra", None)
    if not isinstance(extra_raw, Mapping):
        return IntakeApprovalCardDecision(
            eligible=False, skip_reason=SKIP_REASON_NO_EXTRA
        )
    extra: Mapping[str, Any] = extra_raw

    lifecycle_mode = str(extra.get("lifecycle_mode") or "").strip().lower() or None
    if lifecycle_mode == "research_only":
        return IntakeApprovalCardDecision(
            eligible=False,
            skip_reason=SKIP_REASON_RESEARCH_ONLY_LIFECYCLE,
            lifecycle_mode=lifecycle_mode,
        )
    if lifecycle_mode != "implementation":
        # 명시 implementation 신호 없으면 silently skip — operator 가
        # 잘못 fresh intake 만 한 케이스 보호 (false-positive 차단).
        return IntakeApprovalCardDecision(
            eligible=False,
            skip_reason=SKIP_REASON_NO_IMPLEMENTATION_SIGNAL,
            lifecycle_mode=lifecycle_mode,
        )

    target = extra.get("github_target")
    if not isinstance(target, Mapping) or not target:
        return IntakeApprovalCardDecision(
            eligible=False,
            skip_reason=SKIP_REASON_NO_GITHUB_TARGET,
            lifecycle_mode=lifecycle_mode,
        )

    handoff = extra.get("coding_handoff_packet")
    if not isinstance(handoff, Mapping) or not handoff:
        return IntakeApprovalCardDecision(
            eligible=False,
            skip_reason=SKIP_REASON_NO_HANDOFF_PACKET,
            lifecycle_mode=lifecycle_mode,
        )

    return IntakeApprovalCardDecision(
        eligible=True, skip_reason=None, lifecycle_mode=lifecycle_mode
    )


def _detect_obsidian_intent_safe(text: str) -> bool:
    """``detect_obsidian_intent`` lazy import — circular import 방지."""

    try:
        from .github_workos_adapter import detect_obsidian_intent

        return detect_obsidian_intent(text or "")
    except Exception:  # noqa: BLE001
        return False


__all__ = (
    "ELIGIBILITY_ELIGIBLE",
    "ELIGIBILITY_SKIPPED",
    "IntakeApprovalCardDecision",
    "SKIP_REASON_NOT_WRITE_REQUESTED",
    "SKIP_REASON_NO_EXTRA",
    "SKIP_REASON_NO_GITHUB_TARGET",
    "SKIP_REASON_NO_HANDOFF_PACKET",
    "SKIP_REASON_NO_IMPLEMENTATION_SIGNAL",
    "SKIP_REASON_OBSIDIAN_INTENT",
    "SKIP_REASON_RESEARCH_ONLY_LIFECYCLE",
    "decide_intake_approval_card_eligibility",
)
