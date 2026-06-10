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
    thread_id: Optional[int] = None,
) -> EngineeringRoutingDecision:
    """Classify how the gateway should route ``prompt``.

    *open_sessions* is the list of currently open workflow sessions. If
    not provided, it's derived via ``list_open_fn`` (defaults to a thin
    wrapper around :func:`list_sessions` that filters non-terminal
    states). *session_loader* is reserved for callers that resolve
    "기존 세션 <id>" references — we accept the seam without using it
    in the deterministic path so production can plug in a custom
    resolver later.

    *thread_id* — when set, sessions whose ``thread_id`` matches win
    outright (high confidence JOIN), unless the user explicitly typed
    a "새 작업으로 진행" override. This guarantees that a confirm
    phrase typed inside an existing work thread always lands on that
    thread's session, even when the prompt body is something the
    token scorer would otherwise pull toward an unrelated zombie
    session (the live MVP confirm-routing bug).
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

    # Thread-local priority — if the message arrived in a Discord
    # thread that already anchors an open session, that session wins
    # over any token-scored candidate. Skipped for the explicit
    # "새 작업으로 진행" override above so users can still force a
    # fresh session even from inside a work thread.
    if thread_id is not None:
        thread_sessions = _resolve_open_sessions(open_sessions, list_open_fn)
        for session in thread_sessions:
            if getattr(session, "thread_id", None) == thread_id:
                return _decision_from_session(
                    session,
                    action=ACTION_JOIN,
                    confidence="high",
                    reason=f"thread anchor — session.thread_id={thread_id}",
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


# Phrases that are pure confirmation / control commands, not task
# descriptions. Sessions whose ``prompt`` is one of these have no real
# content to score against — they sit on top of the canonical prompt
# in ``extra.canonical_prompt_override`` (modern fix) or are zombie
# rows from an earlier bug that stored the confirmation phrase as the
# session prompt. Either way, the router must NOT let an inbound
# command-only confirm phrase ("이대로 진행") match a command-only
# session prompt at score 1.0; that bug surfaces as Discord recall
# returning 3+ identical "이대로 진행" sessions as candidates.
_COMMAND_ONLY_PROMPTS: frozenset[str] = frozenset(
    {
        "새 작업으로 진행",
        "새 작업으로 시작",
        "이대로 진행",
        "이대로 등록",
        "그대로 진행",
        "그대로 등록",
        "기존 세션으로 진행",
        "기존 세션으로 시작",
        "기존 세션 진행",
        "기존 작업으로 진행",
        "기존 작업으로 시작",
        "기존 작업 진행",
        "이 thread로 진행",
        "이 thread에서 진행",
        "여기서 진행",
        "여기서 이어가",
        "확정",
        "진행",
        "진행해",
        "진행해줘",
        "진행 해줘",
        "이대로 진행해줘",
        "이대로 진행 해줘",
        "승인",
        "승인할게",
        "승인 할게",
        "승인해줘",
        "승인 해줘",
        "승인했어",
        "수정 승인",
        "오케이",
        "오케이 진행",
        "오케이 진행해줘",
        "ok",
        "okay",
        "예",
        "네",
        "계속",
        "계속 해",
        "계속해",
        "계속 진행",
        "계속 진행해",
        "이어서",
        "이어서 해",
        "이어서 진행",
        "이어서 진행해",
        # 합성 phrase — 사용자 보고된 P0-K 예시.
        "승인하고 진행해",
        "승인하고 진행",
        "작업 승인 할게 진행 해줘",
        "작업 승인 할게",
        "작업 승인할게",
        "작업 승인",
    }
)


# Approval/proceed token fragments — used by the substring sweep in
# :func:`is_command_only_prompt` to catch compound command-only
# phrases that aren't in the exact set above (P0-K). These are *tokens
# inside* command-only messages, not standalone task verbs. Removing
# them from the normalized text should leave nothing substantive.
_COMMAND_ONLY_TOKEN_FRAGMENTS: tuple[str, ...] = (
    "이대로",
    "그대로",
    "여기서",
    "기존",
    "세션",
    "세션으로",
    "thread",
    "작업",
    "작업으로",
    "진행해줘",
    "진행 해줘",
    "진행해",
    "진행할게",
    "진행",
    "승인하고",
    "승인할게",
    "승인 할게",
    "승인해줘",
    "승인 해줘",
    "승인했어",
    "승인",
    "확정해줘",
    "확정",
    "계속해",
    "계속 진행",
    "계속",
    "이어서",
    "이어가",
    "이어",
    "오케이",
    "okay",
    "ok",
    "go ahead",
    "go",
    "yes",
    "proceed",
    "approve",
    "approved",
    "continue",
    "할게",
    "해줘",
    "해라",
    "해",
)


def is_command_only_prompt(value: object) -> bool:
    """True when *value* is just a confirmation/command phrase rather
    than a real task description.

    Three matching layers (most specific → most permissive):

      1. exact normalised match against :data:`_COMMAND_ONLY_PROMPTS`.
      2. very short input (≤2 chars).
      3. **P0-K substring sweep** — strip every
         :data:`_COMMAND_ONLY_TOKEN_FRAGMENTS` and Korean particle
         from the normalized text; if ≤2 chars remain the whole
         message was approval/proceed boilerplate.

    The sweep is what catches compound forms like
    ``작업 승인 할게 진행 해줘`` that aren't in the exact set but
    decompose entirely into command-only fragments.

    Used in two places:
      • routing/recall scoring — sessions whose prompt is itself a
        command-only phrase must not match against an inbound
        command-only confirm phrase at score 1.0.
      • route guard — the gateway must refuse to route on a bare
        confirm phrase, otherwise it would silently create yet
        another command-only session or join a zombie one.
    """

    if not isinstance(value, str):
        return False
    normalised = " ".join(value.lower().split())
    if not normalised:
        return True
    if len(normalised) <= 2:
        return True
    if normalised in _COMMAND_ONLY_PROMPTS:
        return True
    # P0-K substring sweep — fragment-by-fragment stripping.
    return _strips_to_empty(normalised)


def _strips_to_empty(normalised: str) -> bool:
    """Return True iff *normalised* reduces to ≤2 chars after removing
    every command-only fragment + Korean particle + punctuation."""

    text = normalised
    # Strip exact fragments (longest first to avoid partial overlaps).
    for fragment in sorted(_COMMAND_ONLY_TOKEN_FRAGMENTS, key=len, reverse=True):
        if fragment in text:
            text = text.replace(fragment, " ")
    # Strip standalone Korean particles / punctuation that often glue
    # command-only fragments together.
    for particle in (
        " 을 ", " 를 ", " 도 ", " 는 ", " 은 ", " 이 ", " 가 ",
        " 의 ", " 에 ", " 와 ", " 과 ", " 만 ", " 좀 ",
    ):
        text = text.replace(particle, " ")
    # Drop punctuation residue.
    for char in (",", ".", "!", "?", "~", "·", "/", "\\", "'", "\""):
        text = text.replace(char, " ")
    text = " ".join(text.split())
    return len(text) <= 2


# Distinct phrases the bot itself emits in its intake / sufficiency
# templates. When the user copies one of these back into the channel
# the gateway must not treat it as a fresh research request — that's
# the bot-echo loop reported in the live MVP test (gateway prompts
# "좋습니다. 이대로 작업을 등록할게요…" → user pastes it back → gateway
# auto-collects 11 sources → user types "이대로 진행" → gateway shows
# command-only candidates → loop). Match as substrings so multi-line
# pastes still trip the guard.
_BOT_ECHO_FRAGMENTS: tuple[str, ...] = (
    "좋습니다. 이대로 작업을 등록할게요",
    "좋습니다 이대로 작업을 등록할게요",
    "intake가 만들어지면 세션 id",
    "intake가 만들어지면 세션 ID",
    "자료가 부족합니다",
    "참고할 링크나 이미지를 올려주실까요",
    "참고 링크나 이미지를 올려주세요",
    "열려 있는 thread를 찾아 이어갈게요",
    "이 작업의 코딩 권한 제안을 정리했습니다",
    "코딩 권한 승인 완료",
    "**[engineering-agent]",
    "engineer-agent intake",
    "engineer intake 실패",
    "research loop 실패",
)


def is_bot_echo_phrase(value: object) -> bool:
    """True when *value* contains a phrase the gateway itself emits.

    Catches the live MVP bug where the user copies one of the bot's
    template lines back into the channel and the gateway treats it as
    a new research request. Substring match is intentionally
    aggressive — false positives here just nudge the user to restate
    the task, while false negatives perpetuate the auto-collect loop.
    """

    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(fragment.lower() in lowered for fragment in _BOT_ECHO_FRAGMENTS)


def is_non_actionable_prompt(value: object) -> bool:
    """Convenience predicate combining ``is_command_only_prompt`` and
    ``is_bot_echo_phrase``. Use this whenever you're about to commit
    *value* as a session prompt, research query, or routing key — it
    catches both bare confirmation phrases and bot-echo paste-backs.
    """

    return is_command_only_prompt(value) or is_bot_echo_phrase(value)


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
    extra = dict(getattr(session, "extra", None) or {})

    # Prefer ``canonical_prompt_override`` (the real task description
    # captured on a continuation turn) over the raw session.prompt
    # whenever it is present. When the session.prompt is itself a
    # command-only confirm phrase ("이대로 진행" / "새 작업으로 진행")
    # and there is no override, drop the prompt field entirely so the
    # session does not win on a spurious confirm-vs-confirm overlap.
    canonical_override = extra.get("canonical_prompt_override")
    if isinstance(canonical_override, str) and canonical_override.strip():
        fields.append(("prompt", canonical_override))
    elif not is_command_only_prompt(session.prompt):
        fields.append(("prompt", session.prompt or ""))

    fields.append(("task_type", session.task_type or ""))
    if session.summary:
        fields.append(("summary", session.summary))

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
