"""DiscussionMode — 4-state classifier with deterministic fast-path + LLM seam.

분류 모드는 마스터 플랜 §7.1을 그대로 따른다:

- ``discussion`` — 자유 토의 / 설계 토의 / 의견 묻기
- ``research_only`` — 조사/리서치만 진행
- ``implementation_candidate`` — 구현 후보 (코드 변경 의도 명확)
- ``clarification_needed`` — 추가 질문 없이는 위 셋 중 어느 것도 확정 못 함

원칙:

1. 명확한 신호는 ``classify_discussion_mode``가 deterministic 단계에서
   바로 결정한다 (토큰 매칭 + 휴리스틱).
2. deterministic이 결정 못 하면 ``llm_classifier`` 콜러블이 있으면 호출,
   결과가 4 모드 중 하나가 아니면 fallback ``DiscussionMode.DISCUSSION``.
3. 콜러블이 없으면 곧바로 fallback. 토의 모드를 default로 두는 이유는
   "분류 못 함 → 일단 새 작업 등록"보다 "분류 못 함 → 일단 토의로 받음"이
   덜 위험하고, 사용자가 토의 안에서 다음 단계를 안내받을 수 있기 때문.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Sequence


class DiscussionMode(str, Enum):
    """tech-lead가 한 요청에 대해 가질 수 있는 4가지 판단 모드."""

    DISCUSSION = "discussion"
    RESEARCH_ONLY = "research_only"
    IMPLEMENTATION_CANDIDATE = "implementation_candidate"
    CLARIFICATION_NEEDED = "clarification_needed"


@dataclass(frozen=True)
class DiscussionModeMatch:
    """분류 결과.

    ``mode``는 결정된 모드이고, ``rationale``는 사람이 읽기 위한 한 줄
    이유, ``signals``는 어떤 휴리스틱/키워드가 매치됐는지를 디버깅과
    상태 진단용으로 보존한다. ``source``는 ``deterministic`` /
    ``llm`` / ``fallback`` 중 하나.
    """

    mode: DiscussionMode
    rationale: str
    signals: Sequence[str] = field(default_factory=tuple)
    source: str = "deterministic"
    confidence: str = "medium"


# ---------------------------------------------------------------------------
# 신호 사전 — 의도적으로 짧게. 새 신호 추가 시 본 모듈 docstring + tests
# 양쪽을 같이 갱신해야 한다.
# ---------------------------------------------------------------------------


_RESEARCH_ONLY_PHRASES: tuple[str, ...] = (
    "조사만",
    "리서치만",
    "정리까지만",
    "정리 까지만",
    "코드 수정 없이",
    "코드 수정없이",
    "코드 수정 안 하고",
    "코드 수정안 하고",
    "수정하지 말고 조사",
    "수정 하지 말고 조사",
    "수정하지 말고 리서치",
    "자료 수집만",
    "자료수집만",
    "자료 모으기만",
    "research only",
    "research-only",
    "no code change",
    "리서치해줘",
    "리서치 해줘",
    "조사해줘",
    "조사 해줘",
    "일단 조사",
    "일단 리서치",
    "조사부터",
    "리서치부터",
)


_IMPLEMENTATION_PHRASES: tuple[str, ...] = (
    "구현해",
    "구현 해",
    "구현 시작",
    "구현 진행",
    "구현 부탁",
    "고쳐줘",
    "고쳐 줘",
    "고치자",
    "수정해줘",
    "수정 해줘",
    "수정해주세요",
    "패치해줘",
    "패치 해줘",
    "리팩",
    "refactor",
    "implement",
    "build it",
    "fix this",
    "fix the",
    "patch this",
    "패치 작성",
    "코드 작성",
    "코드 짜",
    "pr 올려",
    "pr을 올려",
    "pr 만들",
    "pr을 만들",
    "pull request",
    "draft pr",
    "이슈로 만들",
    "이슈 만들어",
    "기능 구현",
    "기능을 구현",
)

# 검토/분석 신호 — 있으면 implementation으로 가지 않게 막는다.
_REVIEW_BLOCKERS: tuple[str, ...] = (
    "어떻게 생각",
    "이 구조 맞아",
    "이 구조가 맞",
    "이 설계 맞",
    "이 접근 맞",
    "리뷰 부탁",
    "리뷰해줘",
    "리뷰 해줘",
    "검토 부탁",
    "검토해줘",
    "검토 해줘",
    "분석 부탁",
    "분석해줘",
    "review this",
    "review please",
    "what do you think",
)


_DISCUSSION_PHRASES: tuple[str, ...] = (
    "어떻게 생각",
    "어떻게 푸",
    "어떻게 풀",
    "이 구조 맞",
    "이 구조가 맞",
    "이 설계 맞",
    "이 접근이 맞",
    "이 접근 맞",
    "관점에서",
    "관점에 대해",
    "관점에서 보면",
    "리스크부터",
    "리스크 먼저",
    "리스크 정리",
    "tradeoff",
    "트레이드오프",
    "어디서부터 봐",
    "어디부터 봐",
    "토의",
    "디스커션",
    "discussion",
    "의견 좀",
    "의견 부탁",
    "어떻게 봐",
    "어떻게 보",
    "이게 맞아",
    "이게 맞을까",
    "이게 맞나",
    "이게 옳",
    "어떤 방식이 좋",
    "어떤 게 좋",
    "어느 쪽이 좋",
    "방향이 맞",
    "전략 좀",
    "design discussion",
    "architecture discussion",
)


_CLARIFICATION_TOKENS: tuple[str, ...] = (
    "도와줘",
    "도와 줘",
    "할 일",
    "할일",
    "작업 있",
    "뭐 해야",
    "뭐해야",
)


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _matches_any(haystack: str, phrases: Sequence[str]) -> tuple[str, ...]:
    return tuple(p for p in phrases if p and p in haystack)


def _looks_too_vague(normalized: str) -> bool:
    """clarification_needed로 떨어뜨릴 만큼 모호한지.

    매우 짧거나 (3자 이하), 한 단어이거나, 모호 토큰만 들어 있는 경우.
    """

    if not normalized:
        return True
    if len(normalized) <= 3:
        return True
    word_count = len(normalized.split())
    if word_count == 1:
        return True
    if word_count <= 3 and any(token in normalized for token in _CLARIFICATION_TOKENS):
        return True
    return False


# ---------------------------------------------------------------------------
# 메인 분류기
# ---------------------------------------------------------------------------


def classify_discussion_mode(
    message_text: str,
    *,
    context_pack: Optional[Mapping[str, Any]] = None,
    llm_classifier: Optional[Callable[..., Any]] = None,
) -> DiscussionModeMatch:
    """deterministic 단계 → 필요 시 LLM seam → fallback 순으로 모드 결정.

    *context_pack*은 :class:`ContextPack`를 ``as_dict``로 직렬화한 것을
    그대로 넘기면 된다 (또는 :class:`ContextPack` 자체). LLM seam에 같이
    전달돼서 더 넓은 문맥으로 분류할 수 있게 한다.

    *llm_classifier*는 ``(message_text, normalized, context_pack) -> Any``
    형태의 콜러블. 반환값이 ``DiscussionMode``이거나 그 value 문자열이면
    채택, 아니면 fallback. 예외도 fallback으로 흡수한다 — 토의 흐름은
    LLM 장애로 멈추면 안 된다.
    """

    normalized = _normalize(message_text)

    # 1. clarification — 가장 먼저. 빈/매우 짧은/단어 1개는 다른 신호가
    #    있어도 기본적으로 추가 질문이 필요하다.
    if _looks_too_vague(normalized):
        return DiscussionModeMatch(
            mode=DiscussionMode.CLARIFICATION_NEEDED,
            rationale="요청이 비어 있거나 너무 짧아 의도를 확정할 수 없다",
            signals=("too_vague",),
            source="deterministic",
            confidence="high",
        )

    # 2. research_only — 사용자가 명시적으로 "조사/리서치만"이라고 했을 때.
    research_signals = _matches_any(normalized, _RESEARCH_ONLY_PHRASES)
    if research_signals:
        return DiscussionModeMatch(
            mode=DiscussionMode.RESEARCH_ONLY,
            rationale=(
                "사용자가 명시적으로 조사/리서치만 진행해 달라고 요청했다"
            ),
            signals=research_signals,
            source="deterministic",
            confidence="high",
        )

    # 3. discussion — 검토/분석 신호 또는 design/structure 토의 신호가 있다.
    review_signals = _matches_any(normalized, _REVIEW_BLOCKERS)
    discussion_signals = _matches_any(normalized, _DISCUSSION_PHRASES)
    union_signals = tuple(sorted(set(review_signals + discussion_signals)))
    if union_signals:
        return DiscussionModeMatch(
            mode=DiscussionMode.DISCUSSION,
            rationale=(
                "설계/접근 검토를 요청하는 표현이 포함되어 있다 — "
                "구현보다 토의로 받는 것이 안전하다"
            ),
            signals=union_signals,
            source="deterministic",
            confidence="high",
        )

    # 4. implementation_candidate — 검토 신호 없이 구현 동사가 있다.
    impl_signals = _matches_any(normalized, _IMPLEMENTATION_PHRASES)
    if impl_signals and not review_signals:
        return DiscussionModeMatch(
            mode=DiscussionMode.IMPLEMENTATION_CANDIDATE,
            rationale=(
                "구현/수정/PR 동사가 직접 등장 — 코드 변경 의도가 강하게 보인다"
            ),
            signals=impl_signals,
            source="deterministic",
            confidence="high",
        )

    # 5. ambiguous — LLM seam으로 보낸다.
    if llm_classifier is not None:
        try:
            verdict = llm_classifier(
                message_text=message_text,
                normalized=normalized,
                context_pack=context_pack,
            )
        except Exception:  # noqa: BLE001 - never let LLM crash the gateway
            verdict = None
        mode = _coerce_mode(verdict)
        if mode is not None:
            rationale = "LLM classifier 결정"
            signals: tuple[str, ...] = ("llm_classified",)
            if isinstance(verdict, Mapping):
                rationale = str(verdict.get("rationale") or rationale)
                raw_signals = verdict.get("signals") or ()
                if isinstance(raw_signals, (list, tuple)):
                    signals = tuple(str(s) for s in raw_signals if s)
            return DiscussionModeMatch(
                mode=mode,
                rationale=rationale,
                signals=signals,
                source="llm",
                confidence="medium",
            )

    # 6. 최종 fallback — discussion으로 받는다. 새 작업 등록보다 안전하다.
    return DiscussionModeMatch(
        mode=DiscussionMode.DISCUSSION,
        rationale=(
            "deterministic 신호 부족이고 LLM 결정도 없음 — "
            "토의로 받아 다음 단계를 함께 정한다"
        ),
        signals=("fallback",),
        source="fallback",
        confidence="low",
    )


def _coerce_mode(value: Any) -> Optional[DiscussionMode]:
    """LLM seam이 돌려주는 다양한 모양을 :class:`DiscussionMode`로 정규화."""

    if value is None:
        return None
    if isinstance(value, DiscussionMode):
        return value
    if isinstance(value, Mapping):
        return _coerce_mode(value.get("mode"))
    if isinstance(value, str):
        try:
            return DiscussionMode(value.strip().lower())
        except ValueError:
            return None
    return None


__all__ = (
    "DiscussionMode",
    "DiscussionModeMatch",
    "classify_discussion_mode",
)
