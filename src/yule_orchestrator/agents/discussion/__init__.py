"""Discussion mode v1 — tech-lead 토의/조사/구현/질문 분류 + 응답 합성.

이 패키지는 engineering-agent gateway가 받은 자유 발화를 단순한 intake
파이프라인이 아니라 tech-lead가 끌고 갈 토의 흐름으로 옮길 때 쓴다.
gateway는 외부 surface(채널 응답·status·closure)만 책임지고, 본 모듈은
긴 문맥을 들고 다음 행동을 판단하는 두뇌 역할이다.

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
  생산한다.
- :func:`build_implementation_handoff` — synthesis가 구현 후보로 넘어갈
  때 ``CodingAuthorizationProposal``로 이어주는 얇은 어댑터.

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
    synthesize_discussion,
)
from .handoff import (
    DiscussionHandoff,
    HandoffBlocker,
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
    "synthesize_discussion",
    "DiscussionHandoff",
    "HandoffBlocker",
    "build_implementation_handoff",
)
