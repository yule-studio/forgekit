"""tech-lead discussion turn — gateway 가 호출하는 단일 진입점.

본 모듈은 **gateway / tech-lead 경계** 를 코드 측에서 강제하는 얇은
어댑터다. 마스터 플랜 §4 / §7 을 그대로 따른다.

## gateway 책임 (이 모듈의 호출자, 보통 channel router)

- Discord intake 채널 메시지 수신
- 토의로 받을지 / status diagnostic / confirm 등 fast-path 여부 판단
- ``ContextPackBuilder`` 의 외부 seam (thread / issue / PR / note /
  code / knowledge) 콜러블 주입
- 본 함수 결과의 ``rendered_text`` 게시 + ``handoff.proposal`` 카드 게시
- ``operator_status`` 를 ``#봇-상태`` / status diagnostic 으로 surface

## tech-lead 책임 (본 모듈)

- pack 합성 (``ContextPackBuilder.build``)
- 모드 분류 (``classify_discussion_mode``)
- 합성 응답 생산 (``synthesize_discussion`` — header / role perspectives /
  evidence block / next actions)
- 구현 후보면 권한 제안 handoff 생성 (``build_implementation_handoff``)
- gateway 가 그대로 surface 할 수 있는 ``operator_status`` 정렬

본 모듈은 외부 I/O 를 하지 않는다. ContextPackBuilder 의 seam 콜러블은
모두 caller 가 주입한다 — 본 모듈은 builder 를 얇게 wrapping 해서 동일
입력에서 같은 출력을 내도록 보장한다.

## operator_status 의 의미

``operator_status`` 는 운영자 surface 가 "지금 이 turn 은 누가 다음에
무엇을 해야 하는가" 를 한 키로 보고 라우팅할 수 있게 정렬한 dict 다.
필드:

- ``state`` — ``"clarification_needed"`` / ``"discussion_open"`` /
  ``"research_pending"`` / ``"needs_user_approval"`` / ``"blocked"`` /
  ``"retry_ready"`` 6 상태 중 하나.
- ``primary_actor`` — ``"user"`` / ``"tech-lead"`` / ``"operator"``.
- ``headline`` — 운영자가 한 줄로 읽을 한국어 요약.
- ``blockers`` — 표면 블록 사유들 (pack + synthesis + handoff).
- ``handoff_blocker_kind`` — handoff 가 막힌 경우 ``HandoffBlocker.kind``
  (없으면 None) — operator dashboard 가 라우팅 키로 사용.
- ``remediation`` — 사용자/운영자가 다음에 하면 되는 행동 한 줄.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ...agents.coding.authorization import (
    CodingAuthorizationProposal,
    format_authorization_message,
)
from ...agents.discussion import (
    ContextPack,
    ContextPackBuilder,
    DiscussionHandoff,
    DiscussionMode,
    DiscussionModeMatch,
    DiscussionSynthesis,
    build_implementation_handoff,
    classify_discussion_mode,
    synthesize_discussion,
)


# ---------------------------------------------------------------------------
# Operator-facing escalation states — gateway surface 가 그대로 라우팅 키
# 로 쓴다. 안정 식별자.
# ---------------------------------------------------------------------------

OPERATOR_STATE_CLARIFICATION: str = "clarification_needed"
OPERATOR_STATE_DISCUSSION: str = "discussion_open"
OPERATOR_STATE_RESEARCH_PENDING: str = "research_pending"
OPERATOR_STATE_NEEDS_APPROVAL: str = "needs_user_approval"
OPERATOR_STATE_BLOCKED: str = "blocked"
OPERATOR_STATE_RETRY_READY: str = "retry_ready"

_PRIMARY_ACTOR_USER: str = "user"
_PRIMARY_ACTOR_TECH_LEAD: str = "tech-lead"
_PRIMARY_ACTOR_OPERATOR: str = "operator"


@dataclass(frozen=True)
class DiscussionTurnResponse:
    """tech-lead가 한 turn에 만들어 내는 모든 산출물.

    Discord 게이트웨이는 ``rendered_text``를 그대로 채널에 게시하면 되고,
    ``handoff.proposal``이 채워져 있으면 그 다음 메시지로 권한 제안 카드
    를 함께 게시한다. ``synthesis``와 ``classification``,
    ``context_pack``은 status diagnostic / 디버그 / Obsidian handoff에서
    그대로 재사용 가능하도록 노출한다.

    ``operator_status``는 운영자 surface가 사용자/운영자/tech-lead 중
    누가 다음 행동을 해야 하는지 한 dict 로 라우팅할 수 있도록 정렬된
    상태 묶음이다. 필드 의미는 모듈 docstring 참고.
    """

    rendered_text: str
    classification: DiscussionModeMatch
    synthesis: DiscussionSynthesis
    context_pack: ContextPack
    handoff: Optional[DiscussionHandoff] = None
    blockers: Sequence[str] = field(default_factory=tuple)
    operator_status: Mapping[str, Any] = field(default_factory=dict)


def build_discussion_turn_response(
    *,
    message_text: str,
    session: Optional[Any] = None,
    suggested_task_type: Optional[str] = None,
    role_for_research: str = "engineering-agent/tech-lead",
    retrieval_query: Optional[str] = None,
    builder: Optional[ContextPackBuilder] = None,
    llm_classifier: Optional[Any] = None,
    llm_synthesizer: Optional[Any] = None,
    department_dir: Optional[Path] = None,
    role_profile_loader: Optional[Mapping[str, Mapping[str, object]]] = None,
) -> DiscussionTurnResponse:
    """One-shot tech-lead discussion turn.

    *builder*가 None이면 빈 :class:`ContextPackBuilder`를 사용한다 — 그
    경우 pack은 message + session에서 끌어낸 정보만 담고, issue/PR/note
    seam은 비어 있는 상태로 전달된다. caller가 풍부한 pack을 원하면
    seam이 채워진 builder를 주입한다.
    """

    if builder is None:
        builder = ContextPackBuilder()
    pack = builder.build(
        message_text=message_text,
        session=session,
        suggested_task_type=suggested_task_type,
        role_for_research=role_for_research,
        retrieval_query=retrieval_query,
    )

    classification = classify_discussion_mode(
        message_text,
        context_pack=pack.as_dict(),
        llm_classifier=llm_classifier,
    )

    synthesis = synthesize_discussion(
        pack=pack,
        classification=classification,
        llm_synthesizer=llm_synthesizer,
    )

    handoff: Optional[DiscussionHandoff] = None
    rendered_parts: list[str] = [synthesis.response_text]
    if synthesis.mode == DiscussionMode.IMPLEMENTATION_CANDIDATE and synthesis.implementation_ready:
        handoff = build_implementation_handoff(
            synthesis=synthesis,
            pack=pack,
            department_dir=department_dir,
            role_profile_loader=role_profile_loader,
        )
        rendered_parts.append(handoff.follow_up_text)
        if handoff.proposal is not None:
            rendered_parts.append("")
            rendered_parts.append(format_authorization_message(handoff.proposal))

    blockers = list(pack.blockers) + list(synthesis.blockers)
    if handoff is not None and handoff.blocker is not None:
        blocker_text = handoff.blocker.reason
        if handoff.blocker.detail:
            blocker_text += f" ({handoff.blocker.detail})"
        blockers.append(blocker_text)
    blockers_dedup = tuple(dict.fromkeys(blockers))  # dedup, stable order

    operator_status = _build_operator_status(
        classification=classification,
        synthesis=synthesis,
        handoff=handoff,
        blockers=blockers_dedup,
    )

    return DiscussionTurnResponse(
        rendered_text="\n\n".join(part for part in rendered_parts if part),
        classification=classification,
        synthesis=synthesis,
        context_pack=pack,
        handoff=handoff,
        blockers=blockers_dedup,
        operator_status=operator_status,
    )


def _build_operator_status(
    *,
    classification: DiscussionModeMatch,
    synthesis: DiscussionSynthesis,
    handoff: Optional[DiscussionHandoff],
    blockers: Sequence[str],
) -> Mapping[str, Any]:
    """gateway 상태판이 그대로 라우팅할 수 있는 정렬된 상태 dict.

    규칙은 모듈 docstring 의 6 state 정의를 그대로 따른다. handoff
    가 막혀 있으면 (``research_only_conflict``) ``retry_ready`` 로,
    proposal 이 만들어졌으면 ``needs_user_approval`` 로, 그 외에는
    synthesis 의 ``escalation_state`` 를 그대로 사용한다.
    """

    handoff_blocker_kind: Optional[str] = None
    handoff_blocker_remediation: Optional[str] = None
    if handoff is not None and handoff.blocker is not None:
        handoff_blocker_kind = handoff.blocker.kind
        handoff_blocker_remediation = handoff.blocker.remediation

    # State precedence: handoff outcome > synthesis state.
    if handoff is not None and handoff.proposal is not None:
        state = OPERATOR_STATE_NEEDS_APPROVAL
        primary_actor = _PRIMARY_ACTOR_USER
        headline = (
            "구현 권한 제안이 만들어졌습니다. 사용자 승인 phrase 를 기다리는 중."
        )
        remediation = (
            "사용자가 `수정 승인` / `이대로 구현 진행` / `구현 시작` 으로 답하면 "
            "코딩 작업으로 넘어갑니다."
        )
    elif handoff is not None and handoff.blocker is not None:
        # Conflict / empty / internal failure — gateway 가 "재시도 가능"
        # 인지 "운영자 점검 필요" 인지로 분기.
        if handoff_blocker_kind == "research_only_conflict":
            state = OPERATOR_STATE_RETRY_READY
            primary_actor = _PRIMARY_ACTOR_USER
            headline = (
                "분류기는 구현 후보로 봤지만 권한 레이어가 research-only 로 떨어뜨렸습니다."
            )
        elif handoff_blocker_kind == "user_request_empty":
            state = OPERATOR_STATE_CLARIFICATION
            primary_actor = _PRIMARY_ACTOR_USER
            headline = "요청 본문이 비어 있어 권한 제안을 만들 수 없습니다."
        else:
            state = OPERATOR_STATE_BLOCKED
            primary_actor = _PRIMARY_ACTOR_OPERATOR
            headline = (
                "권한 제안 단계에서 차단됨 — 운영자가 로그/사유를 확인해야 합니다."
            )
        remediation = handoff_blocker_remediation or "사용자/운영자 추가 액션 필요."
    else:
        state = synthesis.escalation_state
        primary_actor = synthesis.primary_actor
        headline = _state_headline(state, synthesis)
        remediation = _state_remediation(state, synthesis)

    return {
        "state": state,
        "primary_actor": primary_actor,
        "headline": headline,
        "remediation": remediation,
        "blockers": list(blockers),
        "handoff_blocker_kind": handoff_blocker_kind,
        "rationale": classification.rationale,
        "confidence": classification.confidence,
        "source": classification.source,
    }


def _state_headline(state: str, synthesis: DiscussionSynthesis) -> str:
    if state == OPERATOR_STATE_CLARIFICATION:
        return "tech-lead 가 추가 정보를 기다리는 중 (clarification_needed)."
    if state == OPERATOR_STATE_RESEARCH_PENDING:
        return "조사 단계 — research collector 호출 후 결과 정리 대기."
    if state == "implementation_ready":
        return "구현 후보로 보임 — 권한 제안을 만들어 사용자 승인 phrase 대기."
    if state == OPERATOR_STATE_BLOCKED:
        return "blocker 가 surface 되었습니다 — 운영자가 사유를 확인해야 합니다."
    return "토의 흐름 진행 중 — 사용자 추가 의견 대기."


def _state_remediation(state: str, synthesis: DiscussionSynthesis) -> str:
    if state == OPERATOR_STATE_CLARIFICATION:
        return "사용자에게 한두 문장으로 의도/대상/지점 정보를 요청."
    if state == OPERATOR_STATE_RESEARCH_PENDING:
        return (
            "조사 종료 후 사용자에게 검토 요청. 구현이 필요해지면 "
            "`수정 권한 제안` 으로 다시 받으세요."
        )
    if state == "implementation_ready":
        return (
            "사용자가 `수정 승인` / `이대로 구현 진행` 등으로 답할 때까지 대기."
        )
    if state == OPERATOR_STATE_BLOCKED:
        return "blocker 사유를 점검 후 재시도하거나 사용자에게 다시 묻기."
    # discussion_open
    return "사용자 답을 받아 한 항목씩 합의 → 방향 정해지면 권한 제안으로."


__all__ = (
    "DiscussionTurnResponse",
    "build_discussion_turn_response",
    "OPERATOR_STATE_CLARIFICATION",
    "OPERATOR_STATE_DISCUSSION",
    "OPERATOR_STATE_RESEARCH_PENDING",
    "OPERATOR_STATE_NEEDS_APPROVAL",
    "OPERATOR_STATE_BLOCKED",
    "OPERATOR_STATE_RETRY_READY",
)
