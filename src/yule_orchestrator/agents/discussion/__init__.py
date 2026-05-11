"""Discussion mode v1 — tech-lead 토의/조사/구현/질문 분류 + 응답 합성.

이 패키지는 engineering-agent gateway 가 받은 자유 발화를 단순한 intake
파이프라인이 아니라 tech-lead 가 끌고 갈 토의 흐름으로 옮길 때 쓴다.

## gateway vs tech-lead 경계 (master plan §4)

- **gateway 책임 — 외부 surface 전담**
  - Discord 채널/스레드 응답 게시
  - intake metadata 정리, status / kickoff / closure
  - 운영자 surface (`#봇-상태`, 승인 대기, blocked 알림)
  - tech-lead 가 만든 결과물을 *그대로 게시* 만 한다 — 본문은 만들지 않음.
- **tech-lead 책임 — 본 모듈**
  - 긴 문맥 들고 모드 판단 (discussion / research_only / implementation /
    clarification)
  - role 관점 종합 (backend/frontend/devops/...)
  - 구현 필요 여부 + handoff payload 생성
  - 합성 응답 본문 (response_text / header_text / role perspective) 생산
  - 절대 외부 I/O 하지 않음 — Discord/GitHub/Obsidian 기록은 호출자 책임

핵심 진입점:

- :class:`DiscussionMode` — 4가지 판단 모드.
- :func:`classify_discussion_mode` — deterministic fast-path + LLM seam.
- :class:`ContextPack` / :class:`ContextPackBuilder` — 한 요청에 들어가는
  최근 thread 요약 / session.extra / 관련 issue·PR / Obsidian note 후보 /
  코드 힌트 / role profile 묶음.
- :class:`RelevantMemorySelector` — 후보 note/issue 중 이 요청과 관련 있는
  것만 골라 ``ContextPack``에 끼워 넣는 보조 단계.
- :class:`DiscussionSynthesis` / :func:`synthesize_discussion` — 위 분류와
  pack을 받아 토의/설계 논의/조사/구현 후보 응답 텍스트 + 다음 행동을
  생산한다. ``escalation_state`` / ``primary_actor`` / ``header_text``
  3 슬롯이 gateway 가 사용자/운영자 surface 를 만들 때 그대로 쓰는 값.
- :class:`RolePerspective` — 설계 논의 출력에 들어가는 역할별 1줄 헤드
  라인 + 구체 체크 bullet 묶음. response 본문 외에도 PR body / Obsidian
  decision note 가 dict 그대로 인용할 수 있다.
- :func:`build_implementation_handoff` — synthesis가 구현 후보로 넘어갈
  때 ``CodingAuthorizationProposal``로 이어주는 얇은 어댑터. 실패하면
  :class:`HandoffBlocker` 로 reason/detail/remediation 을 정렬한다 —
  gateway 는 그 텍스트를 그대로 사용자/운영자에게 보여주면 된다.

I/O는 하지 않는다. Discord/GitHub/Obsidian에 실제로 기록하는 일은 호출자
(gateway 채널 라우터, forum hook 등)의 책임으로 둔다.
"""

from .mode import (
    DiscussionMode,
    DiscussionModeMatch,
    classify_discussion_mode,
)
from .context_pack import (
    ContextPack,
    ContextPackBuilder,
    EngineeringKnowledgeRef,
    ObsidianNoteRef,
    GithubIssueRef,
    GithubPRRef,
    CodeHint,
    ThreadMessage,
)
from .memory_selector import (
    MemoryCandidate,
    RelevantMemorySelector,
    score_memory_candidate,
)
from .synthesizer import (
    DiscussionSynthesis,
    DiscussionTemplate,
    RolePerspective,
    synthesize_discussion,
)
from .handoff import (
    DiscussionHandoff,
    HandoffBlocker,
    HANDOFF_BLOCKER_KIND_NOT_IMPL,
    HANDOFF_BLOCKER_KIND_EMPTY_REQUEST,
    HANDOFF_BLOCKER_KIND_RESEARCH_CONFLICT,
    HANDOFF_BLOCKER_KIND_INTERNAL_ERROR,
    build_implementation_handoff,
)


__all__ = (
    "DiscussionMode",
    "DiscussionModeMatch",
    "classify_discussion_mode",
    "ContextPack",
    "ContextPackBuilder",
    "EngineeringKnowledgeRef",
    "ObsidianNoteRef",
    "GithubIssueRef",
    "GithubPRRef",
    "CodeHint",
    "ThreadMessage",
    "MemoryCandidate",
    "RelevantMemorySelector",
    "score_memory_candidate",
    "DiscussionSynthesis",
    "DiscussionTemplate",
    "RolePerspective",
    "synthesize_discussion",
    "DiscussionHandoff",
    "HandoffBlocker",
    "HANDOFF_BLOCKER_KIND_NOT_IMPL",
    "HANDOFF_BLOCKER_KIND_EMPTY_REQUEST",
    "HANDOFF_BLOCKER_KIND_RESEARCH_CONFLICT",
    "HANDOFF_BLOCKER_KIND_INTERNAL_ERROR",
    "build_implementation_handoff",
)
