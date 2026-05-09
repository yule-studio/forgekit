"""tech-lead discussion turn — gateway가 호출하는 단일 진입점.

``engineering_conversation.py``는 fast-path / split / intake 분류를 담당
하는 외부 surface 모듈이다. 본 모듈은 그 위에 ``discussion_mode``를
얹어, **gateway가 토의로 받기로 결정한 메시지**를 처리한다.

흐름:

1. caller(보통 channel router)가 메시지가 토의로 받아져야 한다고 판단.
   (예: status diagnostic / confirm 등 fast-path가 아니고, 자유 발화일 때.)
2. 본 모듈의 :func:`build_discussion_turn_response`를 호출.
3. 함수는 ``ContextPackBuilder``로 pack을 만들고, ``classify_discussion_mode``
   로 모드를 결정하고, ``synthesize_discussion``으로 응답을 합성한다.
4. 결정된 mode가 ``IMPLEMENTATION_CANDIDATE``면 ``build_implementation_handoff``
   까지 한 번에 부르고, ``CodingAuthorizationProposal``을 follow-up text와
   함께 응답에 끼워 넣는다.

본 모듈은 외부 IO를 하지 않는다. ContextPackBuilder의 seam(thread / issue
/ PR / note / code) 콜러블은 모두 caller가 주입한다 — 본 모듈은 builder를
얇게 wrapping해서 동일 입력에서 같은 출력을 내도록 보장한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ..agents.coding.authorization import (
    CodingAuthorizationProposal,
    format_authorization_message,
)
from ..agents.discussion import (
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


@dataclass(frozen=True)
class DiscussionTurnResponse:
    """tech-lead가 한 turn에 만들어 내는 모든 산출물.

    Discord 게이트웨이는 ``rendered_text``를 그대로 채널에 게시하면
    되고, ``handoff.proposal``이 채워져 있으면 그 다음 메시지로
    권한 제안 카드를 함께 게시한다. ``synthesis``와 ``classification``,
    ``context_pack``은 status diagnostic / 디버그 / Obsidian
    handoff에서 그대로 재사용 가능하도록 노출한다.
    """

    rendered_text: str
    classification: DiscussionModeMatch
    synthesis: DiscussionSynthesis
    context_pack: ContextPack
    handoff: Optional[DiscussionHandoff] = None
    blockers: Sequence[str] = field(default_factory=tuple)


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

    return DiscussionTurnResponse(
        rendered_text="\n\n".join(part for part in rendered_parts if part),
        classification=classification,
        synthesis=synthesis,
        context_pack=pack,
        handoff=handoff,
        blockers=tuple(dict.fromkeys(blockers)),  # dedup, stable order
    )


__all__ = (
    "DiscussionTurnResponse",
    "build_discussion_turn_response",
)
