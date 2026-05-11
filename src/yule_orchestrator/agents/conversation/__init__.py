"""Conversation surface — F6 4-block 토의 답변 빌더 (#93).

Discord 토의가 단순 답변 봇이 아니라 *결론 / 이유 / 리스크 / 다음 액션*
4-block 으로 정형화된 답변으로 끝나도록 묶어 주는 surface 모듈.

본 패키지의 범위는 데이터 변환만이다 — 외부 I/O (LLM 호출 / Discord
post / GitHub 등) 는 **호출자 책임**. F5 (live retrieval provider) /
F10 (Claude memory provider) 는 후속 PR 에서 실 wiring 되며, 본 PR
은 :class:`RetrievalProvider` / :class:`MemoryProvider` Protocol seam
+ deterministic fake 만 land 한다.

엔트리 포인트:

* :class:`DiscussionResponse` — frozen dataclass, 4 필드 + evidence /
  memory ref + ``mode_hint``.
* :func:`build_discussion_response` — :class:`ContextPack` +
  :class:`DecisionRequest` 받아 deterministic 합성.
* :func:`render_user_surface` / :func:`render_operator_surface` —
  사용자 surface vs operator surface 분리. operator trace 가
  사용자 surface 로 누출되지 않도록 가드.

PasteGuard 호출은 본 모듈 책임이 아니다. caller (gateway / status
poster) 가 outbound 전에 :func:`guard_outbound` 으로 한 번 더 감싼다.
"""

from .discussion_response import (
    DiscussionResponse,
    EvidenceRef,
    MemoryProvider,
    MemoryRef,
    MODE_HINT_CLARIFICATION_NEEDED,
    MODE_HINT_DISCUSSION,
    MODE_HINT_IMPLEMENTATION_CANDIDATE,
    MODE_HINT_RESEARCH_ONLY,
    MODE_HINTS,
    RetrievalProvider,
    EVIDENCE_SCORE_THRESHOLD,
    NullMemoryProvider,
    NullRetrievalProvider,
    build_discussion_response,
)
from .response_format import (
    render_operator_surface,
    render_user_surface,
)

__all__ = (
    "DiscussionResponse",
    "EvidenceRef",
    "MemoryProvider",
    "MemoryRef",
    "MODE_HINT_CLARIFICATION_NEEDED",
    "MODE_HINT_DISCUSSION",
    "MODE_HINT_IMPLEMENTATION_CANDIDATE",
    "MODE_HINT_RESEARCH_ONLY",
    "MODE_HINTS",
    "RetrievalProvider",
    "EVIDENCE_SCORE_THRESHOLD",
    "NullMemoryProvider",
    "NullRetrievalProvider",
    "build_discussion_response",
    "render_operator_surface",
    "render_user_surface",
)
