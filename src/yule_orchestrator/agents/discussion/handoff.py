"""discussion → coding authorization handoff.

토의가 ``IMPLEMENTATION_CANDIDATE``로 결론났을 때 그 자리에서 곧바로
:func:`recommend_authorization`을 부르고, 실제 ``CodingJob`` build는
사용자 승인 phrase 도착 후로 미루기 위한 얇은 어댑터.

본 모듈은 다음을 보장한다:

1. ``DiscussionSynthesis``의 ``implementation_ready=False``이거나 모드가
   ``IMPLEMENTATION_CANDIDATE``가 아니면 :class:`HandoffBlocker`를 돌려
   주고 절대 proposal을 만들지 않는다.
2. ``user_request``는 pack의 ``current_message``를 사용하되, 비어 있으면
   thread summary로 fallback. 둘 다 비면 blocker.
3. ``recommend_authorization``이 research-only로 떨어뜨리면 그 결과를
   그대로 사용하지 않고 blocker로 보고한다 — 토의 단계에서 이미 구현
   후보로 분류했는데 권한 추천이 research-only면 신호가 충돌하므로
   사용자에게 다시 물어야 한다.

destructive 동작은 절대 없다. proposal은 단순 dataclass이고, 사용자
승인 phrase + 별도 ``build_coding_job_from_proposal`` 호출이 있어야만
실제 ``CodingJob``이 만들어진다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from ..coding.authorization import (
    CodingAuthorizationProposal,
    LIFECYCLE_MODE_RESEARCH_ONLY,
    recommend_authorization,
)
from .context_pack import ContextPack
from .mode import DiscussionMode
from .synthesizer import DiscussionSynthesis


@dataclass(frozen=True)
class HandoffBlocker:
    """handoff를 만들지 못한 사유.

    blocker는 사용자에게 그대로 노출 가능 — gateway가 "권한 제안을 만들
    수 없습니다: <reason>"이라고 답한다.
    """

    reason: str
    detail: Optional[str] = None


@dataclass(frozen=True)
class DiscussionHandoff:
    """discussion → coding authorization handoff payload.

    proposal이 채워져 있으면 gateway가 그대로 ``format_authorization_message``
    로 렌더해 사용자에게 보여주고 승인 phrase를 기다린다. ``follow_up_text``
    는 토의 본문 뒤에 한 줄 덧붙이기 좋은 안내다.
    """

    proposal: Optional[CodingAuthorizationProposal]
    follow_up_text: str
    blocker: Optional[HandoffBlocker] = None
    metadata: Mapping[str, object] = field(default_factory=dict)


def build_implementation_handoff(
    *,
    synthesis: DiscussionSynthesis,
    pack: ContextPack,
    department_dir: Optional[Path] = None,
    role_profile_loader: Optional[Mapping[str, Mapping[str, object]]] = None,
) -> DiscussionHandoff:
    """``synthesis``가 구현 후보일 때 ``CodingAuthorizationProposal``을 만든다.

    실패 시에도 예외를 던지지 않고 :class:`HandoffBlocker`로 감싼 결과를
    돌려준다 — 토의 흐름은 handoff 실패로 멈추면 안 된다.
    """

    if synthesis.mode != DiscussionMode.IMPLEMENTATION_CANDIDATE or not synthesis.implementation_ready:
        return DiscussionHandoff(
            proposal=None,
            follow_up_text=(
                "_지금 turn은 구현 후보가 아니라 토의/조사 단계라서 권한 제안은 만들지 않습니다._"
            ),
            blocker=HandoffBlocker(
                reason="discussion mode가 구현 후보가 아님",
                detail=f"현재 모드: {synthesis.mode.value}",
            ),
        )

    user_request = (pack.current_message or "").strip()
    if not user_request and pack.thread_summary:
        user_request = pack.thread_summary.strip()
    if not user_request:
        return DiscussionHandoff(
            proposal=None,
            follow_up_text=(
                "_요청 본문이 비어 있어 권한 제안을 만들 수 없습니다. "
                "어떤 변경을 원하시는지 한두 문장으로 알려 주세요._"
            ),
            blocker=HandoffBlocker(
                reason="user_request가 비어 있음",
                detail="ContextPack.current_message와 thread_summary 모두 빈 값",
            ),
        )

    try:
        proposal = recommend_authorization(
            user_request=user_request,
            session_id=pack.session_id,
            department_dir=department_dir,
            role_profile_loader=role_profile_loader,
        )
    except Exception as exc:  # noqa: BLE001
        return DiscussionHandoff(
            proposal=None,
            follow_up_text=(
                "_권한 제안 생성 중 내부 오류가 발생했습니다. "
                "tech-lead에게 다시 요청해 주세요._"
            ),
            blocker=HandoffBlocker(
                reason="recommend_authorization 호출 실패",
                detail=str(exc),
            ),
        )

    if proposal.lifecycle_mode == LIFECYCLE_MODE_RESEARCH_ONLY:
        return DiscussionHandoff(
            proposal=None,
            follow_up_text=(
                "_권한 추천이 research-only로 떨어졌습니다 — 토의에서는 구현 후보로 보였지만 "
                "본문 신호가 약합니다. `수정 권한 제안`이라고 다시 답해 주시거나, "
                "조사 단계로 받아들이실지 알려 주세요._"
            ),
            blocker=HandoffBlocker(
                reason="권한 추천이 research-only",
                detail=(
                    "분류기는 implementation_candidate로 봤지만 "
                    "recommend_authorization는 코드 변경 신호를 약하다고 판단함"
                ),
            ),
        )

    follow_up_text = (
        "_권한 제안을 만들었습니다. 이대로 진행하려면 "
        "`수정 승인` / `이대로 구현 진행` / `구현 시작` 중 하나로 답해 주세요._"
    )
    return DiscussionHandoff(
        proposal=proposal,
        follow_up_text=follow_up_text,
        metadata={
            "mode": synthesis.mode.value,
            "rationale": synthesis.rationale,
            "suggested_handoff_role": synthesis.suggested_handoff_role,
        },
    )


__all__ = (
    "DiscussionHandoff",
    "HandoffBlocker",
    "build_implementation_handoff",
)
