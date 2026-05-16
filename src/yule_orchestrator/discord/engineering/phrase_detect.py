"""Engineering router — pure phrase-detection predicates.

Extracts the substring-match style helpers (coding proposal /
approval / explicit "no code change" / continuation research keyword)
out of the giant ``engineering_channel_router`` module so the
orchestrator file stays focused on flow.

All helpers here are pure and side-effect free. They never touch a
session, never await anything. Re-exported from the router under the
historical underscore-prefixed names so existing callers / tests keep
working.
"""

from __future__ import annotations

__all__ = (
    "NO_CODING_INTENT_PHRASES",
    "RESEARCH_ONLY_PHRASES",
    "CONTINUATION_RESEARCH_KEYWORDS",
    "CODING_PROPOSAL_REQUEST_PHRASES",
    "CODING_APPROVAL_PHRASES",
    "user_explicitly_blocked_coding",
    "is_research_only_prompt",
    "continuation_requests_research",
    "is_coding_proposal_request",
    "is_coding_approval_phrase",
)


# Phrases that explicitly say "no code changes — research only". When
# any of these is in the user's message we treat it as a hard "do not
# trigger coding authorization" signal even if a proposal phrase
# appears in the same sentence (e.g. "코드 수정하지 말고 리서치만 정리해줘").
NO_CODING_INTENT_PHRASES: tuple[str, ...] = (
    "코드 수정하지 말",
    "코드 수정 하지 말",
    "코드 수정 금지",
    "수정하지 말고 리서치",
    "수정 하지 말고 리서치",
    "수정하지 말고 조사",
    "리서치만 해",
    "리서치만 정리",
    "조사만 해",
    "조사만 정리",
    "코드 변경 하지 말",
    "코드 변경하지 말",
    "코딩 하지 말고",
    "코딩하지 말고",
    "no code change",
    "research only",
)


# Softer "research only" signals — broader than NO_CODING_INTENT_PHRASES.
# These phrases mean the user wants information gathered, not code
# written. Hits here flip the authorization proposal into research-only
# mode (no executor display, lifecycle_mode=research_only) but do *not*
# hard-block the coding gate the way NO_CODING_INTENT_PHRASES does — so
# the user can still escalate to implementation later by saying "수정
# 권한 제안" / "구현 진행" without having to rephrase their original ask.
RESEARCH_ONLY_PHRASES: tuple[str, ...] = NO_CODING_INTENT_PHRASES + (
    "코드 수정 없이",
    "코드 수정없이",
    "코드 수정은 없",
    "수정 없이 자료",
    "자료 수집이 목표",
    "자료수집이 목표",
    "자료 수집만",
    "자료수집만",
    "조사해줘",
    "조사 해줘",
    "리서치해줘",
    "리서치 해줘",
    "정리까지만",
    "정리 까지만",
    "research-only",
)


# Phrases that signal the continuation prompt is asking for fresh
# research (forum collection / pack rebuild) rather than just resuming
# an idle thread. The runtime preflight passes ``research_loop_fn``
# through to ``_handle_join_or_append`` only when one of these matches
# *and* the session has no research_pack yet.
CONTINUATION_RESEARCH_KEYWORDS: tuple[str, ...] = (
    "[research]",
    "[리서치]",
    "운영-리서치",
    "운영 리서치",
    "리서치",
    "조사",
    "자료 모아",
    "자료 모집",
    "자료 정리",
    "자료 좀",
    "research",
)


CODING_PROPOSAL_REQUEST_PHRASES: tuple[str, ...] = (
    "코딩 권한 제안",
    "수정 권한 제안",
    "구현 권한 제안",
    "코딩 권한 정리",
    "수정 권한 정리",
    "코딩 권한 받자",
    "코딩 권한 잡자",
    "이 작업 코딩 권한",
    "이 작업 수정 권한",
    "이 작업 구현 권한",
    "코딩 권한 만들",
    # P0-T smoke fix (session c5278a9043f2 repro):
    # operator 가 mode 토큰을 명시한 자연어로 들어오는 경우. 이 토큰들이
    # 보이면 coding authorization gate 가 자동 발동 — 매칭 안 돼 approval
    # card / coding_proposal 둘 다 누락되는 회귀를 막는다.
    "approval_required",
    "full_stack_single_repo",
    "full-stack-single-repo",
    "single_repo",
    "single-repo",
    "single_scope",
    "single-scope",
    "코딩으로 진행",
    "구현으로 진행",
    "풀스택으로 진행",
)


CODING_APPROVAL_PHRASES: tuple[str, ...] = (
    "수정 승인",
    "코딩 진행 승인",
    "코딩 승인",
    "구현 진행 승인",
    "구현 승인",
    "이대로 구현 진행",
    "이대로 코딩 진행",
    "구현 시작",
    "코딩 시작",
    "권한 승인",
)


def _normalise(text: str) -> str:
    return " ".join((text or "").lower().split())


def user_explicitly_blocked_coding(text: str) -> bool:
    if not text:
        return False
    normalised = _normalise(text)
    return any(phrase in normalised for phrase in NO_CODING_INTENT_PHRASES)


def is_research_only_prompt(text: str) -> bool:
    """True when *text* signals research-only intent.

    Broader than :func:`user_explicitly_blocked_coding`: any message
    saying "자료 수집이 목표" / "조사해줘" / "정리까지만" should hide the
    coding executor pick from the user even though it doesn't strictly
    forbid coding. The user still has to opt in to implementation
    explicitly via "수정 권한 제안" / "구현 진행".
    """

    if not text:
        return False
    normalised = _normalise(text)
    return any(phrase in normalised for phrase in RESEARCH_ONLY_PHRASES)


def continuation_requests_research(text: str) -> bool:
    if not text:
        return False
    normalised = _normalise(text)
    return any(phrase in normalised for phrase in CONTINUATION_RESEARCH_KEYWORDS)


def is_coding_proposal_request(text: str) -> bool:
    """True when *text* asks Tech Lead to draft a coding authorization."""

    if not text:
        return False
    normalised = _normalise(text)
    return any(phrase in normalised for phrase in CODING_PROPOSAL_REQUEST_PHRASES)


def is_coding_approval_phrase(text: str) -> bool:
    """True when *text* approves a previously-shown coding proposal."""

    if not text:
        return False
    normalised = _normalise(text)
    return any(phrase in normalised for phrase in CODING_APPROVAL_PHRASES)
