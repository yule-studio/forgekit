"""Deterministic conversational intent classifier for the runtime loop.

Phase 2 lands a keyword/phrase-based classifier that maps every Discord
message to one of the nine runtime intents defined in
``runtime.models.KNOWN_INTENTS``. The classifier is intentionally
deterministic so:

- the Discord gateway never silently ships a new intake when the user
  is actually asking a status / continuation question,
- tests can cover real Korean phrasings without an LLM in the loop,
- the LLM-backed classifier added later can plug in via the
  :class:`IntentClassifier` protocol while keeping this module as the
  fallback.

Detection order matters — the most-specific intents are checked first
so a phrase like ``어제 작업 이어서 요약해줘`` resolves to
``summarize_previous_work`` (back-reference + summarize verb) instead
of being captured by the broader ``continue_existing_work`` rule.
"""

from __future__ import annotations

import re
from typing import Optional, Protocol, Sequence

from .models import (
    INTENT_APPEND_CONTEXT,
    INTENT_CLARIFICATION_NEEDED,
    INTENT_CONTINUE_EXISTING_WORK,
    INTENT_DIAGNOSTIC_QUESTION,
    INTENT_EXECUTE_EXISTING_STEP,
    INTENT_GENERAL_CHAT,
    INTENT_NEW_WORK_REQUEST,
    INTENT_STATUS_QUESTION,
    INTENT_SUMMARIZE_PREVIOUS_WORK,
    RuntimeInput,
    RuntimeIntent,
    RuntimeObservation,
)


class IntentClassifier(Protocol):
    """LLM-backed classifier seam.

    Returns a :class:`RuntimeIntent` to override the deterministic
    fallback, or ``None`` to defer. The runtime loop always falls
    back to :func:`classify_intent_deterministic` when the protocol
    returns ``None`` — that way an LLM outage or low-confidence
    response never silently downgrades a clear deterministic match.
    """

    def __call__(
        self,
        observation: RuntimeObservation,
        input_: RuntimeInput,
    ) -> Optional[RuntimeIntent]:  # pragma: no cover - structural typing
        ...


# ---------------------------------------------------------------------------
# Phrase banks — Korean & English
# ---------------------------------------------------------------------------


_BACKREFERENCE_PHRASES: tuple[str, ...] = (
    "어제",
    "그제",
    "이틀 전",
    "지난번",
    "지난 번",
    "지난주",
    "지난 주",
    "지난달",
    "지난 달",
    "방금",
    "아까",
    "조금 전",
    "조금전",
    "전에",
    "그 작업",
    "그작업",
    "그 세션",
    "그세션",
    "그 thread",
    "그 토의",
    "그 흐름",
    "이전 작업",
    "이전작업",
    "기존 작업",
    "기존작업",
    "기존 세션",
    "기존 토의",
    "기존 토론",
    "기존 thread",
    "yesterday",
    "earlier",
    "previous task",
    "the previous",
    "the prior",
    "last session",
)


# Distinct project / topic name references — exact tokens that, when
# combined with any verb, almost certainly point to an existing piece
# of work (e.g. "헤르메스 작업 이어서 가자"). These are kept short and
# case-insensitive; phrase tests append " 작업" / " 검토" combinations
# to detect the wrap-around forms.
_NAMED_PROJECT_CUES: tuple[str, ...] = (
    "헤르메스",
    "hermes",
    "프로젝트 a",
    "프로젝트a",
    "project a",
)


_CONTINUE_VERBS: tuple[str, ...] = (
    "이어가",
    "이어 가",
    "이어서 가",
    "이어서가",
    "이어서 진행",
    "이어 진행",
    "계속 가자",
    "계속 진행",
    "계속해",
    "이어서",
    "이어서 해",
    "이어서해",
    "그대로 진행",
    "continue from",
    "pick up where",
    "keep going",
    "resume",
)


_SUMMARY_VERBS: tuple[str, ...] = (
    "요약",
    "요약해",
    "요약 해줘",
    "요약해줘",
    "정리해줘",
    "정리 해줘",
    "정리 해 줘",
    "정리 해주",
    "정리해 주",
    "정리해 줘",
    "summarize",
    "summary",
    "recap",
    "tldr",
    "tl;dr",
)


_EXECUTE_STEP_PHRASES: tuple[str, ...] = (
    "obsidian에 정리",
    "obsidian 에 정리",
    "obsidian 에 저장",
    "obsidian에 저장",
    "옵시디언에 정리",
    "옵시디언에 저장",
    "옵시디언에 export",
    "obsidian export",
    "obsidian 동기화",
    "옵시디언 동기화",
    "운영-리서치에 정리",
    "운영 리서치에 정리",
    "운영-리서치 정리",
    "운영 리서치 정리",
    "운영-리서치에 저장",
    "운영 리서치에 저장",
    "토의 기록 정리",
    "토의기록 정리",
    "토의 결과 정리",
    "토의 정리",
    "회의록 정리",
    "회의 정리",
    "회고 정리",
    "노트 정리",
    "결과 정리",
    "이 세션 기준",
    "이 세션 기준으로",
    "이 세션 정리",
    "이 세션 기록",
    "이 thread 기준",
    "이 스레드 기준",
    "approval 만들",
    "승인 문서 만들",
    "approval 문서",
    "vault 에 저장",
    "vault에 저장",
)


# Phrases that explicitly mean "stay on the existing work" — they
# come up after the gateway has already shown a clarification (or the
# user is sitting inside a work thread) and replies with a plain
# "기존 세션으로 진행" / "여기서 이어가자" rather than naming the
# session. These must NOT collide with ``_FORCE_NEW_WORK_PHRASES``
# (which contains "새 작업으로 진행"); detection order in
# :func:`classify_intent_deterministic` checks force-new first so
# "새 작업으로 진행" still wins.
_FORCE_CONTINUE_WORK_PHRASES: tuple[str, ...] = (
    "기존 세션으로 진행",
    "기존 세션으로 시작",
    "기존 세션 진행",
    "기존 세션으로 이어",
    "기존 작업으로 진행",
    "기존 작업으로 시작",
    "기존 작업 진행",
    "기존 작업으로 이어",
    "기존 작업으로 등록",
    "기존 thread로 진행",
    "기존 thread에서 진행",
    "기존 thread에서 이어",
    "기존 스레드로 진행",
    "기존 스레드에서 진행",
    "기존 스레드에서 이어",
    "이 thread로 진행",
    "이 thread에서 진행",
    "이 thread에서 이어",
    "이 thread에서 가",
    "이 스레드로 진행",
    "이 스레드에서 진행",
    "이 스레드에서 이어",
    "여기서 진행",
    "여기서 이어",
    "여기 thread에서",
    "여기 스레드에서",
    "이 세션으로 진행",
    "이 세션으로 이어",
    "continue this thread",
    "continue this session",
    "stay on this thread",
)


_APPEND_CONTEXT_PHRASES: tuple[str, ...] = (
    "기존 작업에 참고",
    "기존 작업에 자료",
    "기존 작업에 붙",
    "기존 세션에 참고",
    "기존 세션에 붙",
    "이 자료만 기존",
    "이 자료만 참고",
    "이 자료를 참고로",
    "이 자료 참고로",
    "이 링크만 참고",
    "참고로 붙여",
    "참고만 붙여",
    "맥락만 추가",
    "context만 추가",
    "append context",
    "as context only",
    "as a reference only",
)


_STATUS_DIAGNOSTIC_PHRASES: tuple[str, ...] = (
    "운영 리서치는 안 열",
    "운영-리서치는 안 열",
    "운영 리서치 안 열",
    "운영-리서치 안 열",
    "운영 리서치 왜 안 열",
    "운영-리서치 왜 안 열",
    "운영 리서치 왜",
    "운영-리서치 왜",
    "리서치 왜 실패",
    "리서치 왜 안",
    "리서치는 왜 안",
    "리서치 어떻게 됐",
    "왜 안 됐",
    "왜 안됐",
    "왜 멈췄",
    "왜 멈춰",
    "왜 막혔",
    "왜 막혀",
    "왜 안 열",
    "왜 안열",
    "왜 안 열렸",
    "왜 안열렸",
    "왜 못",
    "왜 실패",
    "지금 뭐 하",
    "지금 뭐하",
    "지금 무엇",
    "지금 어떤 상태",
    "지금 어디까지",
    "현재 상태",
    "현재 진행",
    "상태 알려",
    "상태 좀 알려",
    "상태 확인",
    "상태 체크",
    "다시 확인해",
    "다시 한번 확인",
    "다시 한 번 확인",
    "진행 상황",
    "진행상황",
    "진행 어디",
    "진행 어떻게",
    "어디까지 됐",
    "어디까지 갔",
    "어디까지 진행",
    "어떻게 됐",
    "어떻게 되어",
    "어떻게 되고",
    "어떻게 진행",
    "obsidian 왜 안",
    "obsidian 왜 못",
    "obsidian 안 들어갔",
    "obsidian 안들어갔",
    "옵시디언 왜 안",
    "옵시디언 안 들어갔",
    "forum 왜 안",
    "forum 안 열",
    "포럼 왜 안",
    "포럼 안 열",
    "what's the status",
    "what is the status",
    "what happened",
    "where are we",
    "why did it fail",
    "why didn't it",
    "status check",
    "status update",
    "progress check",
)


# Phrases that explicitly mean "this should be a brand-new session" —
# they override every back-reference rule above.
_FORCE_NEW_WORK_PHRASES: tuple[str, ...] = (
    "새 작업으로 진행",
    "새 작업으로 시작",
    "새로 등록",
    "새 스레드로",
    "새 thread로",
    "새 세션으로",
    "new thread",
    "new session",
    "start a new task",
)


# Single-line confirmation tokens — when the message is *only* one of
# these, classification depends on whether the gateway has a pending
# proposal (``last_proposed_prompt``).
_CONFIRMATION_STANDALONE: frozenset[str] = frozenset(
    {
        "ok",
        "okay",
        "오케이",
        "오케",
        "오키",
        "yes",
        "yep",
        "go",
        "고",
        "ㄱㄱ",
        "확정",
        "진행",
        "등록",
    }
)


_CONFIRMATION_PHRASES: tuple[str, ...] = (
    "이대로 진행",
    "이대로 등록",
    "이걸로 등록",
    "이걸로 진행",
    "그럼 이걸로",
    "그럼 등록",
    "그럼 진행",
    "그렇게 진행",
    "그렇게 등록",
    "진행해줘",
    "진행해 주세요",
    "등록해줘",
    "등록해 주세요",
    "yes 진행",
    "yes 등록",
)


_VAGUE_TOKENS: tuple[str, ...] = (
    "도와줘",
    "도와 줘",
    "할 일 있어",
    "할일 있어",
    "작업 있어",
    "뭐 해야",
    "뭐해야",
    "할 거",
    "할거",
)


_QUESTION_TAILS: tuple[str, ...] = ("?", "?", "까", "어", "야")


_NORMALIZE_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def classify_intent_deterministic(
    observation: RuntimeObservation,
    input_: RuntimeInput,
) -> RuntimeIntent:
    """Return the deterministic intent for *observation*.

    Detection ordering (top wins):

    1. Empty / very short → ``clarification_needed``.
    2. Explicit "force new work" override → ``new_work_request``.
    3. Status / diagnostic phrases → ``status_question`` /
       ``diagnostic_question`` (diagnostic when the message names a
       failure verb like 실패/안 열렸/막혔).
    4. Append-context phrases → ``append_context``.
    5. Back-reference + summary verb → ``summarize_previous_work``.
    6. Back-reference / named project + continue verb →
       ``continue_existing_work``.
    7. Execute-existing-step phrases → ``execute_existing_step``.
    8. Standalone confirmation token → ``new_work_request`` if a
       pending proposal exists, else ``clarification_needed``.
    9. Confirmation phrase → ``new_work_request``.
    10. Otherwise → ``new_work_request`` for substantive content,
        ``general_chat`` for very short pleasantries.
    """

    text = observation.normalized_text or _normalize(observation.message_text)
    if not text:
        return RuntimeIntent(
            intent_id=INTENT_CLARIFICATION_NEEDED,
            confidence="high",
            reason="empty message",
        )

    # 2. Explicit override — always wins.
    if _matches_any(text, _FORCE_NEW_WORK_PHRASES):
        return RuntimeIntent(
            intent_id=INTENT_NEW_WORK_REQUEST,
            confidence="high",
            reason="explicit new-work phrase",
        )

    # 2b. Explicit "stay on existing work" override. Detected before
    # status/diagnostic so a phrase like "기존 세션으로 진행" doesn't
    # accidentally match a status pattern (e.g. "진행 상황" inside the
    # diagnostic bank). Order matters: force-new wins above so
    # "새 작업으로 진행" still creates a fresh session.
    if _matches_any(text, _FORCE_CONTINUE_WORK_PHRASES):
        return RuntimeIntent(
            intent_id=INTENT_CONTINUE_EXISTING_WORK,
            confidence="high",
            reason="explicit continuation phrase",
        )

    # 3. Status / diagnostic.
    if _matches_any(text, _STATUS_DIAGNOSTIC_PHRASES):
        if any(token in text for token in ("실패", "안 열", "안열", "막혔", "막혀", "왜")):
            return RuntimeIntent(
                intent_id=INTENT_DIAGNOSTIC_QUESTION,
                confidence="high",
                reason="diagnostic phrase",
            )
        return RuntimeIntent(
            intent_id=INTENT_STATUS_QUESTION,
            confidence="high",
            reason="status phrase",
        )

    # 4. Append context.
    if _matches_any(text, _APPEND_CONTEXT_PHRASES):
        return RuntimeIntent(
            intent_id=INTENT_APPEND_CONTEXT,
            confidence="high",
            reason="append-context phrase",
        )

    has_backref = _matches_any(text, _BACKREFERENCE_PHRASES) or _matches_any(
        text, _NAMED_PROJECT_CUES
    )

    # 5. Summarize previous work.
    if has_backref and _matches_any(text, _SUMMARY_VERBS):
        return RuntimeIntent(
            intent_id=INTENT_SUMMARIZE_PREVIOUS_WORK,
            confidence="high",
            reason="back-reference + summarize verb",
        )

    # 6. Continue existing work.
    if has_backref and _matches_any(text, _CONTINUE_VERBS):
        return RuntimeIntent(
            intent_id=INTENT_CONTINUE_EXISTING_WORK,
            confidence="high",
            reason="back-reference + continue verb",
        )

    # 6b. Even without a continue verb, a clear back-reference + work
    # noun ("헤르메스 작업 정리") that isn't a summary/status falls
    # through to continue_existing_work since recall must look it up.
    if _matches_any(text, _NAMED_PROJECT_CUES) and _has_work_noun(text):
        return RuntimeIntent(
            intent_id=INTENT_CONTINUE_EXISTING_WORK,
            confidence="medium",
            reason="named project + work noun",
        )

    # 7. Execute existing step.
    if _matches_any(text, _EXECUTE_STEP_PHRASES):
        return RuntimeIntent(
            intent_id=INTENT_EXECUTE_EXISTING_STEP,
            confidence="high",
            reason="execute-step phrase",
        )

    # 8. Standalone confirmation token.
    if text in _CONFIRMATION_STANDALONE:
        if input_.last_proposed_prompt and input_.last_proposed_prompt.strip():
            return RuntimeIntent(
                intent_id=INTENT_NEW_WORK_REQUEST,
                confidence="high",
                reason="confirm pending proposal",
            )
        return RuntimeIntent(
            intent_id=INTENT_CLARIFICATION_NEEDED,
            confidence="medium",
            reason="bare confirmation without pending proposal",
        )

    # 9. Multi-token confirmation phrase.
    if _matches_any(text, _CONFIRMATION_PHRASES):
        if input_.last_proposed_prompt and input_.last_proposed_prompt.strip():
            return RuntimeIntent(
                intent_id=INTENT_NEW_WORK_REQUEST,
                confidence="high",
                reason="confirmation phrase + pending proposal",
            )
        # Without a prior proposal we still treat ``이대로 진행`` as a
        # commit-to-this gesture but mark medium confidence so Decide
        # can ask for clarification if Recall finds nothing.
        return RuntimeIntent(
            intent_id=INTENT_NEW_WORK_REQUEST,
            confidence="medium",
            reason="confirmation phrase without pending proposal",
        )

    # 10. Default.
    # Pleasantry runs before the vague-short check because "안녕하세요"
    # is a single word but it's clearly social, not "give me work".
    if _looks_like_pleasantry(text):
        return RuntimeIntent(
            intent_id=INTENT_GENERAL_CHAT,
            confidence="medium",
            reason="short pleasantry",
        )

    if _looks_too_vague(text):
        return RuntimeIntent(
            intent_id=INTENT_CLARIFICATION_NEEDED,
            confidence="medium",
            reason="vague short message",
        )

    return RuntimeIntent(
        intent_id=INTENT_NEW_WORK_REQUEST,
        confidence="medium",
        reason="default — substantive new task content",
    )


def make_understand_fn(classifier: Optional[IntentClassifier] = None):
    """Build an ``understand_fn`` for ``run_runtime_loop``.

    When *classifier* is provided, its result wins as long as it
    returns a non-None :class:`RuntimeIntent`; otherwise (or on
    classifier exception) we fall back to the deterministic rule set.
    The deterministic path always works without network / LLM, so the
    runtime stays usable in every environment.
    """

    def understand(observation: RuntimeObservation, input_: RuntimeInput) -> RuntimeIntent:
        if classifier is not None:
            try:
                override = classifier(observation, input_)
            except Exception as exc:  # noqa: BLE001 - never crash the bot
                deterministic = classify_intent_deterministic(observation, input_)
                return _annotate(
                    deterministic,
                    f"classifier exception → deterministic fallback: {exc}",
                )
            if override is not None:
                return override
        return classify_intent_deterministic(observation, input_)

    return understand


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub(" ", (text or "").lower()).strip()


def _matches_any(haystack: str, needles: Sequence[str]) -> bool:
    return any(needle in haystack for needle in needles)


_WORK_NOUN_TOKENS: tuple[str, ...] = (
    "작업",
    "task",
    "프로젝트",
    "project",
    "세션",
    "토의",
    "thread",
    "검토",
    "이슈",
)


def _has_work_noun(text: str) -> bool:
    return _matches_any(text, _WORK_NOUN_TOKENS)


def _looks_too_vague(text: str) -> bool:
    if len(text) <= 3:
        return True
    word_count = len(text.split())
    if word_count == 1:
        return True
    if word_count <= 3 and _matches_any(text, _VAGUE_TOKENS):
        return True
    return False


_PLEASANTRY_TOKENS: tuple[str, ...] = (
    "안녕",
    "ㅎㅇ",
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank you",
    "고마",
    "감사",
    "수고",
    "잘 자",
    "good night",
    "good morning",
)


def _looks_like_pleasantry(text: str) -> bool:
    return len(text.split()) <= 4 and _matches_any(text, _PLEASANTRY_TOKENS)


def _annotate(intent: RuntimeIntent, suffix: str) -> RuntimeIntent:
    new_reason = (intent.reason + " · " + suffix).strip(" ·") if intent.reason else suffix
    return RuntimeIntent(
        intent_id=intent.intent_id,
        confidence=intent.confidence,
        reason=new_reason,
        alt_intents=intent.alt_intents,
        metadata=intent.metadata,
    )


__all__ = (
    "IntentClassifier",
    "classify_intent_deterministic",
    "make_understand_fn",
)
