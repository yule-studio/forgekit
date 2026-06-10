"""RelevantMemorySelector — 한 요청에 관련 있는 note만 골라 반환.

마스터 플랜 §8.2를 따른다. 모든 note를 들고 들어가지 않는다.

기본 점수 규칙 (deterministic):

- 같은 topic (title/summary/tags에 query 키워드 매칭): +3
- 같은 task_type (note.tags / note.kind): +2
- 같은 부서/역할 (note.tags에 role 토큰): +2
- 최근 회고/실패 note (note.kind이 ``retrospective`` / ``decision``): +1
- 관련 PR/issue 언급 (note.summary에 ``#<n>`` 또는 ``PR <n>``): +1
- 본문이 비어 있는 note는 −2 (시그널이 약함)

점수가 0 이하인 note는 잘라낸다. 같은 점수면 ``updated_at``이 최근일수록
앞으로. ``limit``만큼 자른다.

I/O 없음. note retrieval(외부 source 호출)은 builder의 ``note_loader``가
하고, 본 모듈은 그 결과만 정렬한다.

본 모듈은 **Obsidian note** 만 다룬다. engineering_intelligence 가 vault 에
쌓아둔 ``EngineeringKnowledgeItem`` (= 역할별 학습 자료) 의 retrieval 은
:mod:`yule_engineering.agents.engineering_intelligence.retrieval` 의
``KnowledgeRetriever`` 가 책임지고, ContextPackBuilder 는 두 selector
를 별개 슬롯 (``relevant_notes`` / ``relevant_knowledge``) 에 채운다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .context_pack import ObsidianNoteRef


@dataclass(frozen=True)
class MemoryCandidate:
    """note 한 건과 함께 계산된 점수/매치 신호.

    호출자(synthesizer / 디버그 dump)가 왜 이 note가 골라졌는지 보일 때
    사용한다. ``RelevantMemorySelector``는 직접 :class:`ObsidianNoteRef`를
    돌려주지만, ``score_memory_candidate``는 :class:`MemoryCandidate`를
    개별 단위로 노출한다.
    """

    note: ObsidianNoteRef
    score: float
    signals: Sequence[str]


_TOKEN_PATTERN = re.compile(r"[\w가-힣]+", re.UNICODE)


def _tokens(text: Optional[str]) -> set[str]:
    if not text:
        return set()
    return {tok.lower() for tok in _TOKEN_PATTERN.findall(text) if len(tok) >= 2}


def _shared_tokens(left: Optional[str], right: Optional[str]) -> set[str]:
    return _tokens(left) & _tokens(right)


_RETRO_KINDS = {"retrospective", "decision", "task-log", "report"}
_ROLE_TOKENS = {
    "tech-lead",
    "product-designer",
    "backend-engineer",
    "frontend-engineer",
    "qa-engineer",
    "ai-engineer",
    "devops-engineer",
}


def score_memory_candidate(
    note: ObsidianNoteRef,
    *,
    query: Optional[str],
    task_type: Optional[str],
    role: Optional[str],
) -> MemoryCandidate:
    """단일 note의 관련도 점수.

    점수는 0~10 범위에 머무는 게 보통이지만 수학적으로 강제하지는 않는다.
    상한 cap이 필요하면 호출자가 ``min(score, cap)``로 잘라 쓰면 된다.
    """

    score = 0.0
    signals: list[str] = []

    haystack = " ".join(
        filter(
            None,
            [
                note.title or "",
                note.summary or "",
                " ".join(note.tags or ()),
            ],
        )
    )

    shared = _shared_tokens(query, haystack)
    if shared:
        score += 3.0
        signals.append(f"topic_overlap:{len(shared)}")

    if task_type:
        normalized_task = task_type.strip().lower()
        if normalized_task and (
            normalized_task in {t.lower() for t in (note.tags or ())}
            or (note.kind or "").lower() == normalized_task
        ):
            score += 2.0
            signals.append("task_type_match")

    role_normalized = (role or "").lower()
    role_short = role_normalized.split("/")[-1] if role_normalized else ""
    note_tags_lower = {str(t).lower() for t in (note.tags or ())}
    role_hits = (
        {role_short, role_normalized}
        & (note_tags_lower | {(note.kind or "").lower()})
    ) - {""}
    if role_hits:
        score += 2.0
        signals.append("role_match")
    # 일반 role 토큰도 가산
    elif note_tags_lower & _ROLE_TOKENS:
        score += 0.5
        signals.append("any_role_tag")

    if (note.kind or "").lower() in _RETRO_KINDS:
        score += 1.0
        signals.append("retrospective_or_decision")

    if note.summary and re.search(r"#\d+|pr\s*\d+|issue\s*\d+", note.summary, re.IGNORECASE):
        score += 1.0
        signals.append("references_pr_or_issue")

    if not (note.summary or note.tags):
        score -= 2.0
        signals.append("body_empty")

    return MemoryCandidate(note=note, score=score, signals=tuple(signals))


@dataclass
class RelevantMemorySelector:
    """note 후보 목록 → 추려진 ``ObsidianNoteRef`` 시퀀스.

    ``min_score`` 미만은 잘라낸다 (default 0.5 — 약한 신호 1개도 통과).
    callable로 직접 호출 가능: ``selector(candidates=..., query=..., ...)``.
    """

    min_score: float = 0.5

    def __call__(
        self,
        *,
        candidates: Iterable[ObsidianNoteRef],
        query: Optional[str] = None,
        task_type: Optional[str] = None,
        role: Optional[str] = None,
        limit: int = 5,
    ) -> Sequence[ObsidianNoteRef]:
        scored: list[tuple[float, int, ObsidianNoteRef]] = []
        for idx, note in enumerate(candidates):
            if not isinstance(note, ObsidianNoteRef):
                continue
            cand = score_memory_candidate(
                note, query=query, task_type=task_type, role=role
            )
            if cand.score < self.min_score:
                continue
            # 정렬: 점수 desc, updated_at desc, idx asc (안정).
            scored.append((cand.score, idx, note))
        scored.sort(
            key=lambda triplet: (
                -triplet[0],
                _updated_at_sort_key(triplet[2].updated_at),
                triplet[1],
            )
        )
        return tuple(note for _, _, note in scored[:limit])


def _updated_at_sort_key(value: Optional[str]) -> str:
    """ISO 시각 문자열은 그대로 비교 가능 — 없는 값은 가장 오래된 취급."""

    if not value:
        return ""
    # desc 정렬을 위해 음수 변환 대신 reverse 문자열 (z-prefix) 트릭.
    # 단순화를 위해 정상 문자열을 쓰고 호출부에서 reverse 정렬은 위에서
    # 음수 점수와 같이 묶어 처리. 여기서는 빈 문자열을 가장 오래된 것으로
    # 취급하기 위해 그대로 반환하고, 호출부 sort에서는 desc 가산을 위해
    # 0 위치 음수 점수에 맞춰 두 번째 키는 asc — 따라서 큰 ISO 시각이
    # asc 정렬에서 뒤로 가는 문제가 있다. 이를 보정하기 위해 ASCII chr
    # 보수치를 만든다.
    return "".join(chr(255 - ord(c)) if ord(c) < 255 else c for c in value)


__all__ = (
    "MemoryCandidate",
    "RelevantMemorySelector",
    "score_memory_candidate",
)
