"""Open work matching + routing decisions for the engineering gateway.

The gateway needs to answer one question every time a user posts in
``#업무-접수``: do we *join* an existing open work item, do we *create*
a new one, do we *ask* the user to disambiguate, or are we just
*appending context* to an existing thread? The previous heuristic
collapsed several of those signals into a single "continue existing
thread" boolean, which conflated "참고만 붙여줘" with "이어가" and
forced gateway routing to pick the latest open session blindly.

This module replaces that heuristic with a deterministic, testable
classifier. It has no LLM dependency — token-overlap scoring against
session prompts/summaries/research artifacts is plenty for the MVP.
The shape is also the natural seam for a future LLM-backed reranker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence, Tuple

from .workflow_state import WorkflowSession, WorkflowState, list_sessions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


ACTION_JOIN = "join_existing_work"
ACTION_CREATE = "create_new_work"
ACTION_ASK = "ask_for_clarification"
ACTION_APPEND_CONTEXT = "append_context_only"


@dataclass(frozen=True)
class CandidateSummary:
    """One scored open-work candidate the router considered."""

    session_id: str
    score: float
    title: str
    task_type: Optional[str]
    thread_id: Optional[int]
    forum_thread_id: Optional[int]
    why: str


@dataclass(frozen=True)
class EngineeringRoutingDecision:
    """Where the gateway should land an inbound engineering message."""

    action: str
    matched_session_id: Optional[str] = None
    matched_thread_id: Optional[int] = None
    matched_forum_thread_id: Optional[int] = None
    confidence: str = "low"  # "low" | "medium" | "high"
    reason: str = ""
    candidate_summaries: Tuple[CandidateSummary, ...] = field(default_factory=tuple)


# Similarity score thresholds. Tuned for token-overlap scoring on
# Korean/English mixed prompts; bumped only when signal is dense.
SCORE_HIGH = 0.45
SCORE_MEDIUM = 0.25
SCORE_AMBIGUOUS_MARGIN = 0.07  # gap between top two → ask for clarification


# ---------------------------------------------------------------------------
# Decision entry point
# ---------------------------------------------------------------------------


def decide_routing(
    *,
    prompt: str,
    open_sessions: Optional[Sequence[WorkflowSession]] = None,
    session_loader=None,
    list_open_fn=None,
) -> EngineeringRoutingDecision:
    """Classify how the gateway should route ``prompt``.

    *open_sessions* is the list of currently open workflow sessions. If
    not provided, it's derived via ``list_open_fn`` (defaults to a thin
    wrapper around :func:`list_sessions` that filters non-terminal
    states). *session_loader* is reserved for callers that resolve
    "기존 세션 <id>" references — we accept the seam without using it
    in the deterministic path so production can plug in a custom
    resolver later.
    """

    text = (prompt or "").strip()
    if not text:
        return EngineeringRoutingDecision(
            action=ACTION_CREATE,
            reason="empty prompt — defaulting to create_new_work",
        )

    explicit_session = _explicit_session_request(text)
    if explicit_session:
        loader = session_loader or _default_session_loader
        session = loader(explicit_session)
        if session is not None:
            return _decision_from_session(
                session,
                action=ACTION_JOIN,
                confidence="high",
                reason=f"explicit '기존 세션 {explicit_session}' override",
            )
        return EngineeringRoutingDecision(
            action=ACTION_ASK,
            reason=(
                f"사용자가 기존 세션 `{explicit_session}`를 지목했지만 해당 "
                "세션을 찾지 못했습니다. 세션 ID를 다시 확인하거나 새 작업으로 진행하세요."
            ),
        )

    if _explicit_new_work(text):
        return EngineeringRoutingDecision(
            action=ACTION_CREATE,
            reason="explicit '새 작업으로 진행' override",
            confidence="high",
        )

    if _explicit_append_context(text):
        # Best-effort: try to find the most recently updated open
        # session to attach the context to. If none exist, fall back
        # to create_new_work so context isn't dropped on the floor.
        sessions = _resolve_open_sessions(open_sessions, list_open_fn)
        if sessions:
            latest = sessions[0]
            return _decision_from_session(
                latest,
                action=ACTION_APPEND_CONTEXT,
                confidence="medium",
                reason="explicit '이 자료만 기존 작업에 참고로 붙여줘'",
            )
        return EngineeringRoutingDecision(
            action=ACTION_CREATE,
            reason=(
                "context append 요청을 받았지만 열린 작업이 없어 새 작업으로 시작합니다."
            ),
            confidence="medium",
        )

    sessions = _resolve_open_sessions(open_sessions, list_open_fn)
    if not sessions:
        return EngineeringRoutingDecision(
            action=ACTION_CREATE,
            reason="no open sessions — create_new_work",
            confidence="medium",
        )

    scored = _score_sessions(text, sessions)
    if not scored or scored[0][0] <= 0.0:
        return EngineeringRoutingDecision(
            action=ACTION_CREATE,
            reason="no similarity overlap with open sessions",
            confidence="medium",
            candidate_summaries=_top_candidates(scored, limit=3),
        )

    top_score, top_session, top_why = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    candidates = _top_candidates(scored, limit=3)

    if top_score < SCORE_MEDIUM:
        return EngineeringRoutingDecision(
            action=ACTION_CREATE,
            reason=(
                f"top similarity {top_score:.2f} below medium threshold "
                f"{SCORE_MEDIUM} — create_new_work"
            ),
            confidence="medium",
            candidate_summaries=candidates,
        )

    # Ambiguous when top two are close above the medium bar.
    if second_score >= SCORE_MEDIUM and (top_score - second_score) < SCORE_AMBIGUOUS_MARGIN:
        return EngineeringRoutingDecision(
            action=ACTION_ASK,
            confidence="medium",
            reason=(
                "유사한 후보 2건 이상이 비슷한 점수로 나와 어느 작업에 합류할지 "
                "사용자 확인이 필요합니다."
            ),
            candidate_summaries=candidates,
        )

    confidence = "high" if top_score >= SCORE_HIGH else "medium"
    return _decision_from_session(
        top_session,
        action=ACTION_JOIN,
        confidence=confidence,
        reason=top_why,
        candidate_summaries=candidates,
    )


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


_EXPLICIT_NEW_WORK_PATTERNS = (
    "새 작업으로 진행",
    "새 작업으로 등록",
    "새 작업 만들어",
    "새 세션으로",
    "create new work",
    "create a new task",
    "start a new session",
)


_EXPLICIT_APPEND_PATTERNS = (
    "이 자료만 기존 작업에 참고",
    "이 자료만 기존 작업에 붙여",
    "참고로만 붙여",
    "참고만 붙여",
    "context only",
    "append only",
)


_EXPLICIT_SESSION_RE = re.compile(
    r"기존\s*세션\s*[`'\"]?([0-9a-fA-F]{6,})[`'\"]?"
    r"|session[_-]?id[\s:=`'\"]+([0-9a-fA-F]{6,})",
    re.IGNORECASE,
)


def _explicit_new_work(text: str) -> bool:
    lowered = text.lower()
    return any(p in text or p in lowered for p in _EXPLICIT_NEW_WORK_PATTERNS)


def _explicit_append_context(text: str) -> bool:
    lowered = text.lower()
    return any(p in text or p in lowered for p in _EXPLICIT_APPEND_PATTERNS)


def _explicit_session_request(text: str) -> Optional[str]:
    match = _EXPLICIT_SESSION_RE.search(text)
    if not match:
        return None
    return next((g for g in match.groups() if g), None)


# ---------------------------------------------------------------------------
# Open session resolution + similarity scoring
# ---------------------------------------------------------------------------


def list_open_sessions(*, limit: int = 50) -> Tuple[WorkflowSession, ...]:
    """Return open (non-terminal) sessions, newest first.

    Open := state not in {COMPLETED, REJECTED}. Tap point for routing
    so callers don't have to repeat the filter inline.
    """

    open_states = {WorkflowState.COMPLETED, WorkflowState.REJECTED}
    return tuple(s for s in list_sessions(limit=limit) if s.state not in open_states)


def _resolve_open_sessions(
    open_sessions: Optional[Sequence[WorkflowSession]],
    list_open_fn,
) -> Tuple[WorkflowSession, ...]:
    if open_sessions is not None:
        return tuple(open_sessions)
    fn = list_open_fn or list_open_sessions
    try:
        return tuple(fn())
    except Exception:  # noqa: BLE001 - routing must not crash on cache problems
        return ()


def _default_session_loader(session_id: str):
    try:
        from .workflow_state import load_session
    except Exception:  # noqa: BLE001
        return None
    try:
        return load_session(session_id)
    except Exception:  # noqa: BLE001
        return None


def _score_sessions(
    prompt: str,
    sessions: Sequence[WorkflowSession],
) -> list[tuple[float, WorkflowSession, str]]:
    """Score each open session against ``prompt`` and return descending."""

    prompt_tokens = _tokenize(prompt)
    if not prompt_tokens:
        return []
    scored: list[tuple[float, WorkflowSession, str]] = []
    for session in sessions:
        score, why = _score_one(prompt_tokens, session)
        if score > 0:
            scored.append((score, session, why))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored


_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    raw = _TOKEN_RE.findall(text.lower())
    return {tok for tok in raw if len(tok) >= 2 and tok not in _STOPWORDS}


# Conservative stopword list — pure boilerplate that drops signal.
_STOPWORDS = {
    "the", "and", "for", "with", "into", "from", "that", "this", "have",
    "그리고", "그래서", "근데", "그냥", "다시", "일단",
    "하면", "하자", "처럼", "관련", "내용", "부분",
}


def _score_one(
    prompt_tokens: set[str], session: WorkflowSession
) -> tuple[float, str]:
    """Return overlap score in [0,1] + a short ``why`` string."""

    fields: list[tuple[str, str]] = []
    fields.append(("prompt", session.prompt or ""))
    fields.append(("task_type", session.task_type or ""))
    if session.summary:
        fields.append(("summary", session.summary))

    extra = dict(getattr(session, "extra", None) or {})
    pack = extra.get("research_pack")
    if isinstance(pack, dict):
        if pack.get("title"):
            fields.append(("pack.title", str(pack.get("title"))))
        if pack.get("summary"):
            fields.append(("pack.summary", str(pack.get("summary"))))
    synthesis = extra.get("research_synthesis")
    if isinstance(synthesis, dict):
        consensus = synthesis.get("consensus")
        if consensus:
            fields.append(("synthesis.consensus", str(consensus)))

    best_overlap = 0
    best_field: Optional[str] = None
    union_tokens: set[str] = set()
    for label, text in fields:
        toks = _tokenize(text)
        if not toks:
            continue
        union_tokens.update(toks)
        overlap = len(prompt_tokens & toks)
        if overlap > best_overlap:
            best_overlap = overlap
            best_field = label

    if not union_tokens:
        return 0.0, ""

    # Jaccard-ish: shared / (|prompt_tokens|). Anchors the score to the
    # incoming request rather than the (potentially huge) session text.
    score = best_overlap / max(1, len(prompt_tokens))
    # Bonus when the strongest match is the prompt itself (most direct
    # follow-up signal).
    if best_field == "prompt" and best_overlap >= 2:
        score = min(1.0, score + 0.1)
    why = (
        f"`{session.session_id}` 매칭 {best_overlap}/{len(prompt_tokens)} "
        f"토큰 (영역: {best_field or 'union'})"
    )
    return score, why


def _decision_from_session(
    session: WorkflowSession,
    *,
    action: str,
    confidence: str,
    reason: str,
    candidate_summaries: Tuple[CandidateSummary, ...] = (),
) -> EngineeringRoutingDecision:
    extra = dict(getattr(session, "extra", None) or {})
    forum_thread_id = extra.get("research_forum_thread_id") or extra.get(
        "forum_thread_id"
    )
    forum_id_int: Optional[int]
    try:
        forum_id_int = int(forum_thread_id) if forum_thread_id is not None else None
    except (TypeError, ValueError):
        forum_id_int = None
    return EngineeringRoutingDecision(
        action=action,
        matched_session_id=session.session_id,
        matched_thread_id=session.thread_id,
        matched_forum_thread_id=forum_id_int,
        confidence=confidence,
        reason=reason,
        candidate_summaries=candidate_summaries,
    )


def _top_candidates(
    scored: Iterable[tuple[float, WorkflowSession, str]],
    *,
    limit: int = 3,
) -> Tuple[CandidateSummary, ...]:
    out: list[CandidateSummary] = []
    for score, session, why in list(scored)[:limit]:
        title = session.summary or session.prompt or session.session_id
        if len(title) > 80:
            title = title[:77] + "…"
        extra = dict(getattr(session, "extra", None) or {})
        forum_id = extra.get("research_forum_thread_id") or extra.get("forum_thread_id")
        try:
            forum_id_int = int(forum_id) if forum_id is not None else None
        except (TypeError, ValueError):
            forum_id_int = None
        out.append(
            CandidateSummary(
                session_id=session.session_id,
                score=float(score),
                title=title,
                task_type=session.task_type,
                thread_id=session.thread_id,
                forum_thread_id=forum_id_int,
                why=why,
            )
        )
    return tuple(out)
