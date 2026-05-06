"""Session candidate recall — match Discord messages to existing work.

Phase 3A wires the runtime Recall stage onto the workflow session
store. The classifier in Phase 2 produced an intent like
``continue_existing_work`` / ``summarize_previous_work``; Recall's job
is to translate "어제 작업", "헤르메스 작업", "그 작업" etc. into a
concrete session id (or a small candidate list for clarification).

Design choices:

- ``list_sessions_fn`` is injected so tests can supply a synthetic
  list and the production wiring can hand in
  :func:`workflow_state.list_sessions`.
- Channel / thread match is the strongest signal: a thread already
  scoped to a session id wins over any token overlap.
- Token scoring borrows the rules used by ``agents.routing._score_one``
  but we keep a separate copy here so the runtime layer doesn't depend
  on the engineering router.
- When the top two candidates are within ``AMBIGUITY_MARGIN`` of each
  other we deliberately leave ``matched_session_id`` empty so the
  Decide stage can ask for clarification.

Phase 3B will plug in memory hits and role-aware filtering on top of
this base.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional, Sequence, Tuple

from .models import (
    INTENT_APPEND_CONTEXT,
    INTENT_CONTINUE_EXISTING_WORK,
    INTENT_DIAGNOSTIC_QUESTION,
    INTENT_EXECUTE_EXISTING_STEP,
    INTENT_NEW_WORK_REQUEST,
    INTENT_STATUS_QUESTION,
    INTENT_SUMMARIZE_PREVIOUS_WORK,
    RuntimeInput,
    RuntimeIntent,
    RuntimeObservation,
    RuntimeRecallResult,
    SessionCandidate,
)


SCORE_HIGH = 0.45
SCORE_MEDIUM = 0.20
AMBIGUITY_MARGIN = 0.10
MAX_CANDIDATES_RETURNED = 5


# Intents where Recall must search for an existing session before the
# loop is allowed to proceed. Used by ``make_recall_fn`` to short-
# circuit lookups for clearly-new requests.
RECALL_SEEKING_INTENTS = frozenset(
    {
        INTENT_CONTINUE_EXISTING_WORK,
        INTENT_SUMMARIZE_PREVIOUS_WORK,
        INTENT_STATUS_QUESTION,
        INTENT_DIAGNOSTIC_QUESTION,
        INTENT_EXECUTE_EXISTING_STEP,
        INTENT_APPEND_CONTEXT,
    }
)


_RECENCY_CUES: Tuple[str, ...] = (
    "어제",
    "그제",
    "지난번",
    "지난 번",
    "방금",
    "아까",
    "조금 전",
    "조금전",
    "최근",
    "yesterday",
    "earlier",
    "just now",
)


_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "into",
        "from",
        "that",
        "this",
        "have",
        "그리고",
        "그래서",
        "근데",
        "그냥",
        "다시",
        "일단",
        "하면",
        "하자",
        "처럼",
        "관련",
        "내용",
        "부분",
        "작업",
        "이어서",
        "정리",
        "정리해줘",
        "요약",
        "요약해줘",
    }
)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


ListSessionsFn = Callable[..., Sequence[Any]]


def make_recall_fn(
    list_sessions_fn: Optional[ListSessionsFn] = None,
    *,
    limit: int = 50,
    open_state_filter: Optional[Callable[[Any], bool]] = None,
):
    """Build a ``recall_fn`` for ``run_runtime_loop``.

    *list_sessions_fn* must return a sequence of objects with at least
    ``session_id``, ``prompt``, ``task_type``, ``state``, ``updated_at``,
    ``channel_id``, ``thread_id``, ``extra``, ``summary``. The production
    wiring will pass :func:`workflow_state.list_sessions`. Tests can
    pass a list literal.

    *open_state_filter* identifies which sessions are still "open"
    (i.e. eligible for recall). The default skips terminal states
    (``COMPLETED`` / ``REJECTED``).
    """

    state_filter = open_state_filter or _default_open_filter

    def recall(
        observation: RuntimeObservation,
        intent: RuntimeIntent,
        input_: RuntimeInput,
    ) -> RuntimeRecallResult:
        if list_sessions_fn is None:
            return RuntimeRecallResult(reason="no list_sessions_fn provided")

        if intent.intent_id == INTENT_NEW_WORK_REQUEST:
            # Even for explicit new work we still surface the channel/
            # thread-bound session if any — the Decide stage may want
            # to warn the operator that another session is open in the
            # same thread. But we skip token scoring entirely.
            anchor = _find_anchor_session(
                list_sessions_fn=list_sessions_fn,
                limit=limit,
                state_filter=state_filter,
                input_=input_,
            )
            if anchor is None:
                return RuntimeRecallResult(reason="new work — no anchor session")
            return RuntimeRecallResult(
                matched_session_id=None,  # do NOT auto-attach for new work
                candidates=(_session_to_candidate(anchor, score=0.0, why="anchor (channel/thread)"),),
                confidence="low",
                reason="new work — anchor session in same channel/thread",
            )

        try:
            raw_sessions = list_sessions_fn(limit=limit)
        except TypeError:
            # Older list_sessions_fn signatures don't accept kwargs.
            raw_sessions = list_sessions_fn()

        sessions = [s for s in raw_sessions if state_filter(s)]
        if not sessions:
            return RuntimeRecallResult(reason="no open sessions")

        # 1. Channel/thread anchoring — wins outright when present.
        anchor_id = _resolve_thread_anchor(sessions, input_)
        if anchor_id is not None:
            anchored = next(s for s in sessions if s.session_id == anchor_id)
            return RuntimeRecallResult(
                matched_session_id=anchored.session_id,
                matched_thread_id=getattr(anchored, "thread_id", None),
                matched_forum_thread_id=_extract_forum_thread_id(anchored),
                candidates=tuple(
                    _session_to_candidate(
                        s,
                        score=1.0 if s.session_id == anchor_id else 0.0,
                        why="thread anchor" if s.session_id == anchor_id else "in scope",
                    )
                    for s in sessions[:MAX_CANDIDATES_RETURNED]
                ),
                confidence="high",
                reason="thread/channel anchor",
            )

        # 2. Token scoring against prompt / pack / synthesis.
        prompt_tokens = _tokenize(observation.message_text)
        scored: list[tuple[float, Any, str]] = []
        for session in sessions:
            score, why = _score_session(prompt_tokens, session)
            if score > 0:
                scored.append((score, session, why))
        scored.sort(key=lambda t: t[0], reverse=True)

        # 3. Recency fallback — when the user said "어제/방금/지난번"
        # without a distinct token match, pick the most-recently-updated
        # open session.
        if not scored and _has_recency_cue(observation):
            recent = _most_recent(sessions)
            if recent is not None:
                return RuntimeRecallResult(
                    matched_session_id=recent.session_id,
                    matched_thread_id=getattr(recent, "thread_id", None),
                    matched_forum_thread_id=_extract_forum_thread_id(recent),
                    candidates=(
                        _session_to_candidate(
                            recent,
                            score=0.5,
                            why="recency fallback",
                        ),
                    ),
                    confidence="medium",
                    reason="recency cue + latest open session",
                )

        if not scored:
            return RuntimeRecallResult(
                candidates=(),
                confidence="low",
                reason="no token match",
            )

        top_score, top_session, top_why = scored[0]
        runner_up_score = scored[1][0] if len(scored) > 1 else 0.0
        margin = top_score - runner_up_score

        candidates = tuple(
            _session_to_candidate(s, score=score, why=why)
            for score, s, why in scored[:MAX_CANDIDATES_RETURNED]
        )

        if top_score >= SCORE_HIGH and margin >= AMBIGUITY_MARGIN:
            return RuntimeRecallResult(
                matched_session_id=top_session.session_id,
                matched_thread_id=getattr(top_session, "thread_id", None),
                matched_forum_thread_id=_extract_forum_thread_id(top_session),
                candidates=candidates,
                confidence="high",
                reason=f"top score {top_score:.2f} · {top_why}",
            )
        if top_score >= SCORE_MEDIUM and margin >= AMBIGUITY_MARGIN:
            return RuntimeRecallResult(
                matched_session_id=top_session.session_id,
                matched_thread_id=getattr(top_session, "thread_id", None),
                matched_forum_thread_id=_extract_forum_thread_id(top_session),
                candidates=candidates,
                confidence="medium",
                reason=f"top score {top_score:.2f} · {top_why}",
            )
        # Ambiguous — surface candidates but do NOT match.
        return RuntimeRecallResult(
            candidates=candidates,
            confidence="low",
            reason=(
                f"ambiguous · top {top_score:.2f} vs runner-up {runner_up_score:.2f} "
                f"(margin {margin:.2f})"
            ),
        )

    return recall


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _default_open_filter(session: Any) -> bool:
    state = getattr(session, "state", None)
    state_value = getattr(state, "value", state)
    return str(state_value).lower() not in {"completed", "rejected"}


def _resolve_thread_anchor(
    sessions: Sequence[Any],
    input_: RuntimeInput,
) -> Optional[str]:
    if input_.thread_id is not None:
        for session in sessions:
            if getattr(session, "thread_id", None) == input_.thread_id:
                return session.session_id
    return None


def _find_anchor_session(
    *,
    list_sessions_fn: ListSessionsFn,
    limit: int,
    state_filter: Callable[[Any], bool],
    input_: RuntimeInput,
) -> Optional[Any]:
    if input_.thread_id is None and input_.channel_id is None:
        return None
    try:
        raw_sessions = list_sessions_fn(limit=limit)
    except TypeError:
        raw_sessions = list_sessions_fn()
    sessions = [s for s in raw_sessions if state_filter(s)]
    for session in sessions:
        if input_.thread_id is not None and getattr(session, "thread_id", None) == input_.thread_id:
            return session
    if input_.channel_id is not None:
        for session in sessions:
            if getattr(session, "channel_id", None) == input_.channel_id:
                return session
    return None


def _tokenize(text: str) -> set:
    if not text:
        return set()
    raw = _TOKEN_RE.findall(text.lower())
    return {tok for tok in raw if len(tok) >= 2 and tok not in _STOPWORDS}


def _score_session(prompt_tokens: set, session: Any) -> Tuple[float, str]:
    if not prompt_tokens:
        return 0.0, ""
    fields: list[Tuple[str, str]] = []
    fields.append(("prompt", getattr(session, "prompt", "") or ""))
    fields.append(("task_type", getattr(session, "task_type", "") or ""))
    summary = getattr(session, "summary", None)
    if summary:
        fields.append(("summary", summary))
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
    union_tokens: set = set()
    for label, text in fields:
        toks = _tokenize(text)
        if not toks:
            continue
        union_tokens.update(toks)
        overlap = len(prompt_tokens & toks)
        if overlap > best_overlap:
            best_overlap = overlap
            best_field = label

    if best_overlap == 0:
        return 0.0, ""

    score = best_overlap / max(1, len(prompt_tokens))
    if best_field == "prompt" and best_overlap >= 2:
        score = min(1.0, score + 0.1)
    why = (
        f"매칭 {best_overlap}/{len(prompt_tokens)} 토큰 (영역: {best_field or 'union'})"
    )
    return score, why


def _has_recency_cue(observation: RuntimeObservation) -> bool:
    haystack = observation.normalized_text or (observation.message_text or "").lower()
    return any(cue in haystack for cue in _RECENCY_CUES)


def _most_recent(sessions: Sequence[Any]) -> Optional[Any]:
    if not sessions:
        return None
    def _key(s: Any) -> datetime:
        value = getattr(s, "updated_at", None)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return datetime.min.replace(tzinfo=timezone.utc)

    return max(sessions, key=_key)


def _session_to_candidate(session: Any, *, score: float, why: str) -> SessionCandidate:
    extra = dict(getattr(session, "extra", None) or {})
    pack = extra.get("research_pack")
    title = ""
    if isinstance(pack, dict) and pack.get("title"):
        title = str(pack.get("title"))
    elif getattr(session, "summary", None):
        title = str(session.summary)
    elif getattr(session, "prompt", None):
        prompt = str(session.prompt)
        title = prompt[:80] + ("..." if len(prompt) > 80 else "")

    state = getattr(session, "state", None)
    state_value = getattr(state, "value", state)
    return SessionCandidate(
        session_id=session.session_id,
        title=title,
        score=score,
        why=why,
        state=str(state_value) if state_value is not None else None,
        task_type=getattr(session, "task_type", None),
        thread_id=getattr(session, "thread_id", None),
        forum_thread_id=_extract_forum_thread_id(session),
        has_research_pack=isinstance(pack, dict) and bool(pack),
        has_synthesis=isinstance(extra.get("research_synthesis"), dict)
        and bool(extra.get("research_synthesis")),
        extra={"updated_at": _isoformat_or_none(getattr(session, "updated_at", None))},
    )


def _extract_forum_thread_id(session: Any) -> Optional[int]:
    extra = dict(getattr(session, "extra", None) or {})
    raw = extra.get("research_forum_thread_id") or extra.get("forum_thread_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _isoformat_or_none(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


__all__ = (
    "RECALL_SEEKING_INTENTS",
    "SCORE_HIGH",
    "SCORE_MEDIUM",
    "AMBIGUITY_MARGIN",
    "make_recall_fn",
)
