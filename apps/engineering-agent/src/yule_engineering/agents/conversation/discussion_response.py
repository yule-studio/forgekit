"""4-block 토의 답변 빌더 — F6 / #93.

본 모듈은 Discord 토의 turn 의 결과를 *결론 (conclusion) / 이유
(reasoning) / 리스크 (risks) / 다음 액션 (next_actions)* 네 가지로
정형화한 :class:`DiscussionResponse` 를 만든다.

설계 원칙:

1. **순수 데이터 변환.** I/O 는 호출자 책임. retrieval / memory 입력
   은 :class:`RetrievalProvider` / :class:`MemoryProvider` Protocol
   로 주입한다. 본 PR 은 fake provider 만 land 한다 — F5 (#92) /
   F10 후속 PR 에서 실 wiring.
2. **mode_hint 전환 가시화.** 입력 mode 가 단정적이지 않으면 답변
   말미에 mode 전환 제안 (`clarification_needed` /
   `research_only` / `implementation_candidate`) 을 첨부.
3. **Hard rails.**

   * evidence ``score < EVIDENCE_SCORE_THRESHOLD`` 이면 evidence
     를 채택하지 않고 risks 에 "근거 부족" 명시.
   * 어떤 필드도 빈 문자열로 두지 않는다 — 최소한 한 문장을 작성.
   * 본 모듈은 plain 출력. PasteGuard 호출은 caller 책임.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from ..decision.context_pack import ContextPack
from ..decision.router import (
    DecisionRequest,
    MODE_CLARIFICATION_NEEDED,
    MODE_DISCUSSION,
    MODE_IMPLEMENTATION_CANDIDATE,
    MODE_RESEARCH_ONLY,
)


# ---------------------------------------------------------------------------
# Mode hint vocabulary
# ---------------------------------------------------------------------------


MODE_HINT_DISCUSSION: str = MODE_DISCUSSION
MODE_HINT_RESEARCH_ONLY: str = MODE_RESEARCH_ONLY
MODE_HINT_IMPLEMENTATION_CANDIDATE: str = MODE_IMPLEMENTATION_CANDIDATE
MODE_HINT_CLARIFICATION_NEEDED: str = MODE_CLARIFICATION_NEEDED

MODE_HINTS: Tuple[str, ...] = (
    MODE_HINT_DISCUSSION,
    MODE_HINT_RESEARCH_ONLY,
    MODE_HINT_IMPLEMENTATION_CANDIDATE,
    MODE_HINT_CLARIFICATION_NEEDED,
)


# Evidence / memory score threshold — 그 아래는 채택 안 함.
EVIDENCE_SCORE_THRESHOLD: float = 0.3


# ---------------------------------------------------------------------------
# Refs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceRef:
    """retrieval provider 가 만들어 주는 evidence 한 건.

    * ``kind`` — ``"note"`` / ``"issue"`` / ``"pr"`` / ``"commit"`` 등 자유
      문자열. 본 모듈은 검증하지 않는다.
    * ``title`` — 사람이 읽는 짧은 제목.
    * ``url_or_path`` — 클릭/접근 가능한 위치. 비어 있을 수 있다.
    * ``snippet`` — 1~2 문장 요약. None 허용.
    * ``score`` — 0.0 ~ 1.0. ``EVIDENCE_SCORE_THRESHOLD`` 미만이면
      :func:`build_discussion_response` 가 채택하지 않는다.
    """

    kind: str
    title: str
    url_or_path: str
    snippet: Optional[str] = None
    score: float = 0.0

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "url_or_path": self.url_or_path,
            "snippet": self.snippet,
            "score": self.score,
        }


@dataclass(frozen=True)
class MemoryRef:
    """memory provider 가 만들어 주는 shard 한 건.

    Claude memory / Obsidian shard 등 *recall* 류 자료. 본 모듈은
    의미 검증을 하지 않는다 — provider 가 score 까지 채워서 넘긴다.
    """

    kind: str
    title: str
    summary: str
    source: str
    score: float = 0.0

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "score": self.score,
        }


# ---------------------------------------------------------------------------
# Provider protocols (F5 / F10 후속 PR 에서 실 wiring)
# ---------------------------------------------------------------------------


class RetrievalProvider(Protocol):
    """F5 후속 PR 에서 실 ranking 결과를 채울 seam.

    * ``role`` — tech-lead / backend-engineer / ... role 키.
    * ``keywords`` — DecisionRequest 에서 뽑아 낸 키워드.
    * ``limit`` — 최대 반환 개수.
    """

    def for_request(
        self,
        *,
        role: Optional[str],
        keywords: Sequence[str],
        limit: int,
    ) -> Tuple[EvidenceRef, ...]:  # pragma: no cover - Protocol
        ...


class MemoryProvider(Protocol):
    """F10 후속 PR 에서 실 Claude memory recall 을 채울 seam."""

    def recent_shards(
        self,
        *,
        role: Optional[str],
        topic: str,
        limit: int,
    ) -> Tuple[MemoryRef, ...]:  # pragma: no cover - Protocol
        ...


class NullRetrievalProvider:
    """본 PR 의 default — 빈 evidence 반환 fake."""

    def for_request(
        self,
        *,
        role: Optional[str],
        keywords: Sequence[str],
        limit: int,
    ) -> Tuple[EvidenceRef, ...]:
        return ()


class NullMemoryProvider:
    """본 PR 의 default — 빈 memory 반환 fake."""

    def recent_shards(
        self,
        *,
        role: Optional[str],
        topic: str,
        limit: int,
    ) -> Tuple[MemoryRef, ...]:
        return ()


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscussionResponse:
    """4-block 답변 컨테이너 — 결론 / 이유 / 리스크 / 다음 액션.

    * ``conclusion`` — 단정형 1~2 줄. 빈 문자열 금지.
    * ``reasoning`` — 결론에 도달한 이유 (>=1 항).
    * ``risks`` — 위험 / 미확정 (>=1 항). 위험이 정말 없으면 ``"낮음"``.
    * ``next_actions`` — 다음 단계 1~3 항.
    * ``evidence_refs`` — 채택된 :class:`EvidenceRef` (score >= threshold).
    * ``memory_refs`` — 채택된 :class:`MemoryRef`.
    * ``mode_hint`` — 다음 turn 에 제안할 mode (None 이면 mode 전환 없음).
    """

    conclusion: str
    reasoning: Tuple[str, ...]
    risks: Tuple[str, ...]
    next_actions: Tuple[str, ...]
    evidence_refs: Tuple[EvidenceRef, ...] = ()
    memory_refs: Tuple[MemoryRef, ...] = ()
    mode_hint: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (self.conclusion or "").strip():
            raise ValueError("DiscussionResponse.conclusion must not be empty")
        if not self.reasoning:
            raise ValueError("DiscussionResponse.reasoning must have ≥1 item")
        if not self.risks:
            raise ValueError("DiscussionResponse.risks must have ≥1 item")
        if not self.next_actions:
            raise ValueError("DiscussionResponse.next_actions must have ≥1 item")
        if self.mode_hint is not None and self.mode_hint not in MODE_HINTS:
            raise ValueError(
                f"DiscussionResponse.mode_hint must be one of {MODE_HINTS}, "
                f"got {self.mode_hint!r}"
            )

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "conclusion": self.conclusion,
            "reasoning": list(self.reasoning),
            "risks": list(self.risks),
            "next_actions": list(self.next_actions),
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "memory_refs": [ref.to_dict() for ref in self.memory_refs],
            "mode_hint": self.mode_hint,
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_discussion_response(
    *,
    request: DecisionRequest,
    context_pack: ContextPack,
    retrieval: Optional[RetrievalProvider] = None,
    memory: Optional[MemoryProvider] = None,
    role_perspective: Optional[str] = None,
    mode_hint: Optional[str] = None,
    evidence_limit: int = 3,
    memory_limit: int = 3,
) -> DiscussionResponse:
    """``request`` + ``context_pack`` 으로 4-block 답변 합성.

    * ``mode_hint`` 가 명시되면 그대로 채택 — 단, 알 수 없는 값이면
      ``ValueError`` (Response __post_init__ 가드).
    * ``mode_hint`` 가 ``None`` 이면 deterministic 추정:
      pack 비어 있으면 ``clarification_needed``, research_only 키워드
      가 prompt 에 있으면 ``research_only``, 구현 키워드면
      ``implementation_candidate``, 그 외에는 mode_hint 없음.
    * evidence / memory 는 ``score >= EVIDENCE_SCORE_THRESHOLD`` 인
      항목만 채택. 채택 결과 0 건이면 ``risks`` 에 "근거 부족" 명시.
    * Reasoning 은 context pack 의 related notes / issues / PRs /
      code hints 를 사람 읽기용 한국어 한 줄씩 변환.
    """

    retrieval_provider: RetrievalProvider = retrieval or NullRetrievalProvider()
    memory_provider: MemoryProvider = memory or NullMemoryProvider()

    prompt = (request.prompt or "").strip()
    keywords = _extract_keywords(prompt)
    topic = _short_topic(prompt)

    raw_evidence = retrieval_provider.for_request(
        role=role_perspective, keywords=keywords, limit=evidence_limit
    )
    raw_memory = memory_provider.recent_shards(
        role=role_perspective, topic=topic, limit=memory_limit
    )

    evidence = tuple(
        ref for ref in raw_evidence if ref.score >= EVIDENCE_SCORE_THRESHOLD
    )
    memory_refs = tuple(
        ref for ref in raw_memory if ref.score >= EVIDENCE_SCORE_THRESHOLD
    )

    derived_mode = mode_hint if mode_hint is not None else _derive_mode_hint(
        prompt=prompt, context_pack=context_pack
    )

    conclusion = _build_conclusion(
        topic=topic,
        mode_hint=derived_mode,
        role_perspective=role_perspective,
        context_pack=context_pack,
        evidence=evidence,
    )
    reasoning = _build_reasoning(
        context_pack=context_pack,
        evidence=evidence,
        memory=memory_refs,
        role_perspective=role_perspective,
    )
    risks = _build_risks(
        context_pack=context_pack,
        evidence=evidence,
        raw_evidence=raw_evidence,
        mode_hint=derived_mode,
    )
    next_actions = _build_next_actions(
        mode_hint=derived_mode,
        context_pack=context_pack,
        role_perspective=role_perspective,
    )

    return DiscussionResponse(
        conclusion=conclusion,
        reasoning=reasoning,
        risks=risks,
        next_actions=next_actions,
        evidence_refs=evidence,
        memory_refs=memory_refs,
        mode_hint=derived_mode,
        metadata={
            "context_pack_id": context_pack.id,
            "role_perspective": role_perspective,
            "evidence_threshold": EVIDENCE_SCORE_THRESHOLD,
            "evidence_total": len(raw_evidence),
            "evidence_accepted": len(evidence),
            "memory_total": len(raw_memory),
            "memory_accepted": len(memory_refs),
        },
    )


# ---------------------------------------------------------------------------
# Builder internals
# ---------------------------------------------------------------------------


_RESEARCH_HINTS: Tuple[str, ...] = (
    "research",
    "조사",
    "리서치",
    "참고 자료",
    "자료 수집",
    "정리만",
)

_IMPLEMENTATION_HINTS: Tuple[str, ...] = (
    "구현",
    "implement",
    "수정해",
    "버그 고",
    "버그 수정",
    "open a pr",
    "draft pr",
    "pr 만들",
    "pr 올려",
)

_AMBIGUOUS_HINTS: Tuple[str, ...] = (
    "??",
    "모르겠",
    "헷갈",
    "not sure",
    "maybe",
)


def _derive_mode_hint(
    *, prompt: str, context_pack: ContextPack
) -> Optional[str]:
    if not prompt:
        return MODE_HINT_CLARIFICATION_NEEDED

    lowered = prompt.lower()

    if any(hint in lowered for hint in _AMBIGUOUS_HINTS) and context_pack.is_empty:
        return MODE_HINT_CLARIFICATION_NEEDED

    has_research = any(hint in lowered for hint in _RESEARCH_HINTS)
    has_impl = any(hint in lowered for hint in _IMPLEMENTATION_HINTS)

    if has_research and not has_impl:
        return MODE_HINT_RESEARCH_ONLY
    if has_impl and not has_research:
        return MODE_HINT_IMPLEMENTATION_CANDIDATE

    if context_pack.is_empty:
        return MODE_HINT_CLARIFICATION_NEEDED
    return None


def _short_topic(text: str, max_chars: int = 60) -> str:
    if not text:
        return "(요청 본문 없음)"
    head = text.strip().splitlines()[0].strip()
    if not head:
        return "(요청 본문 없음)"
    if len(head) <= max_chars:
        return head
    return head[: max_chars - 1].rstrip() + "…"


def _extract_keywords(text: str, *, limit: int = 6) -> Tuple[str, ...]:
    if not text:
        return ()
    cleaned: list = []
    for token in text.replace("\n", " ").split():
        stripped = token.strip(".,!?;:()[]{}\"'`").lower()
        if len(stripped) < 2:
            continue
        if stripped in cleaned:
            continue
        cleaned.append(stripped)
        if len(cleaned) >= limit:
            break
    return tuple(cleaned)


def _build_conclusion(
    *,
    topic: str,
    mode_hint: Optional[str],
    role_perspective: Optional[str],
    context_pack: ContextPack,
    evidence: Tuple[EvidenceRef, ...],
) -> str:
    role_label = (role_perspective or "tech-lead").split("/")[-1]
    if mode_hint == MODE_HINT_CLARIFICATION_NEEDED:
        return (
            f"\"{topic}\" 는 지금 정보만으로 결론을 내기엔 부족합니다 — "
            "추가 정보를 받은 뒤 합의로 이어 가는 게 안전합니다."
        )
    if mode_hint == MODE_HINT_RESEARCH_ONLY:
        return (
            f"\"{topic}\" 는 코드 변경 전에 자료부터 모으는 단계로 진행합니다."
        )
    if mode_hint == MODE_HINT_IMPLEMENTATION_CANDIDATE:
        return (
            f"\"{topic}\" 는 구현 후보로 보입니다 — 권한 제안을 먼저 만들어 검토합니다."
        )
    # discussion 또는 mode_hint 없음
    if evidence:
        return (
            f"\"{topic}\" 는 모은 근거 {len(evidence)} 건을 기준으로 "
            f"{role_label} 관점에서 토의를 이어 가겠습니다."
        )
    return (
        f"\"{topic}\" 는 {role_label} 관점에서 다음 단계를 함께 정해 보겠습니다."
    )


def _build_reasoning(
    *,
    context_pack: ContextPack,
    evidence: Tuple[EvidenceRef, ...],
    memory: Tuple[MemoryRef, ...],
    role_perspective: Optional[str],
) -> Tuple[str, ...]:
    out: list = []

    if context_pack.related_notes:
        out.append(
            f"Obsidian 관련 노트 {len(context_pack.related_notes)} 건 "
            f"(예: {context_pack.related_notes[0]})"
        )
    if context_pack.related_issues:
        issue_list = ", ".join(f"#{n}" for n in context_pack.related_issues[:3])
        out.append(f"관련 이슈 — {issue_list}")
    if context_pack.related_prs:
        pr_list = ", ".join(f"#{n}" for n in context_pack.related_prs[:3])
        out.append(f"관련 PR — {pr_list}")
    if context_pack.code_hints:
        hints = ", ".join(context_pack.code_hints[:3])
        out.append(f"코드 힌트 — {hints}")

    for ref in evidence:
        prefix = ref.kind or "evidence"
        title = ref.title or "(제목 없음)"
        out.append(f"근거({prefix}): {title}")

    for ref in memory:
        out.append(f"기억({ref.kind}): {ref.title}")

    if role_perspective:
        out.append(
            f"역할 관점 — {role_perspective.split('/')[-1]} 시선으로 1차 검토"
        )

    if not out:
        out.append(
            "현재 context pack 이 비어 있어, 추가 정보가 확보된 이후 재평가합니다."
        )
    return tuple(out)


def _build_risks(
    *,
    context_pack: ContextPack,
    evidence: Tuple[EvidenceRef, ...],
    raw_evidence: Tuple[EvidenceRef, ...],
    mode_hint: Optional[str],
) -> Tuple[str, ...]:
    risks: list = []

    if not evidence:
        if raw_evidence:
            risks.append(
                "근거 부족 — retrieval 결과가 신뢰 임계값 미달이라 채택하지 않았습니다."
            )
        else:
            risks.append(
                "근거 부족 — retrieval 가 빈 결과를 반환했습니다."
            )

    if context_pack.is_empty:
        risks.append("Context pack 이 비어 있어 잘못된 가정이 들어갈 위험.")

    if mode_hint == MODE_HINT_IMPLEMENTATION_CANDIDATE:
        risks.append(
            "구현 단계는 권한 제안 + 사용자 승인 phrase 가 선행되지 않으면 코드 변경 금지."
        )
    if mode_hint == MODE_HINT_CLARIFICATION_NEEDED:
        risks.append(
            "추가 정보를 받기 전 진행하면 잘못된 방향으로 작업이 분기할 위험."
        )

    if not risks:
        risks.append("낮음 — 결정적 차단 요인 없음. 합의 즉시 다음 단계로 진행 가능.")
    return tuple(risks)


def _build_next_actions(
    *,
    mode_hint: Optional[str],
    context_pack: ContextPack,
    role_perspective: Optional[str],
) -> Tuple[str, ...]:
    if mode_hint == MODE_HINT_CLARIFICATION_NEEDED:
        return (
            "막혀 있는 지점 / 원하는 결과를 한 줄로 알려 주세요.",
            "참고 가능한 링크 · PR · 스크린샷이 있으면 함께 주세요.",
            "정보가 정리되면 토의 / 조사 / 구현 중 어느 흐름으로 갈지 정합니다.",
        )
    if mode_hint == MODE_HINT_RESEARCH_ONLY:
        return (
            "research collector 호출로 1차 자료 수집.",
            "정리 결과를 Obsidian research note + thread 에 게시.",
            "구현 필요 여부는 별도 요청 (`수정 권한 제안`) 으로 분기.",
        )
    if mode_hint == MODE_HINT_IMPLEMENTATION_CANDIDATE:
        return (
            "tech-lead 가 추천 executor 역할을 1차 제안.",
            "권한 제안 (CodingAuthorizationProposal) 을 사용자에게 표시.",
            "사용자 승인 phrase 전까지는 어떤 파일도 수정하지 않음.",
        )
    # discussion / 기본
    actions: list = [
        "결정이 필요한 항목을 한 번에 하나씩 합의.",
    ]
    if role_perspective:
        actions.append(
            f"{role_perspective.split('/')[-1]} 관점 체크 포인트 1차 답변 받기."
        )
    else:
        actions.append("관련 역할 관점 (backend / frontend / devops / qa) 보강 필요.")
    actions.append(
        "방향이 정해지면 `수정 권한 제안` 으로 답해 권한 제안 흐름으로 이동."
    )
    return tuple(actions)


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
)
