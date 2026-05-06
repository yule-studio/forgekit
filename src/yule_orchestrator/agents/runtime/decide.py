"""Decide stage — turn (intent, recall) into ordered runtime actions.

Phase 4 keeps Decide deterministic: given an intent and a recall
result, pick the smallest set of actions a role bot should take. The
actions themselves are still abstract (``ACTION_JOIN_SESSION``,
``ACTION_ASK_CLARIFICATION``, ...); the router preflight that wires
this into Discord then turns each action into a concrete IO call.

Mapping (gateway role):

- ``status_question`` → reply (variant=status)
- ``diagnostic_question`` → reply (variant=diagnostic)
- ``continue_existing_work`` / ``summarize_previous_work`` /
  ``execute_existing_step`` →
    - matched session → join_session
    - no matched session → ask_clarification
- ``append_context`` →
    - matched session → append_context
    - no matched session → ask_clarification
- ``new_work_request`` → create_session
- ``clarification_needed`` → ask_clarification
- ``general_chat`` → reply (variant=chat)

The decision keeps the original ``RuntimeIntent`` + research plan so
downstream consumers (router preflight, future record stage) get the
full context without having to reach back into the recall result.
"""

from __future__ import annotations

from typing import Sequence, Tuple

from .models import (
    ACTION_APPEND_CONTEXT,
    ACTION_ASK_CLARIFICATION,
    ACTION_CREATE_SESSION,
    ACTION_JOIN_SESSION,
    ACTION_REPLY,
    INTENT_APPEND_CONTEXT,
    INTENT_CLARIFICATION_NEEDED,
    INTENT_CONTINUE_EXISTING_WORK,
    INTENT_DIAGNOSTIC_QUESTION,
    INTENT_EXECUTE_EXISTING_STEP,
    INTENT_GENERAL_CHAT,
    INTENT_NEW_WORK_REQUEST,
    INTENT_STATUS_QUESTION,
    INTENT_SUMMARIZE_PREVIOUS_WORK,
    RuntimeAction,
    RuntimeDecision,
    RuntimeInput,
    RuntimeIntent,
    RuntimeObservation,
    RuntimeRecallResult,
    RuntimeResearchPlan,
    SessionCandidate,
)


_SESSION_SEEKING_INTENTS = frozenset(
    {
        INTENT_CONTINUE_EXISTING_WORK,
        INTENT_SUMMARIZE_PREVIOUS_WORK,
        INTENT_EXECUTE_EXISTING_STEP,
    }
)


def decide_default(
    observation: RuntimeObservation,
    intent: RuntimeIntent,
    recall: RuntimeRecallResult,
    plan: RuntimeResearchPlan,
    input_: RuntimeInput,
) -> RuntimeDecision:
    """Build the deterministic action sequence for *intent*.

    The router preflight (Phase 4B) only branches on the first action's
    ``action_id``; the rest of the action list is preserved for future
    stages (e.g. Phase 5 record_memory) so the decision object remains
    rich without forcing every consumer to look at it.
    """

    intent_id = intent.intent_id

    if intent_id == INTENT_STATUS_QUESTION:
        return _decision(
            intent,
            plan,
            (
                RuntimeAction(
                    action_id=ACTION_REPLY,
                    payload={
                        "variant": "status",
                        "matched_session_id": recall.matched_session_id,
                    },
                    reason="status question",
                ),
            ),
            notes="status",
        )

    if intent_id == INTENT_DIAGNOSTIC_QUESTION:
        return _decision(
            intent,
            plan,
            (
                RuntimeAction(
                    action_id=ACTION_REPLY,
                    payload={
                        "variant": "diagnostic",
                        "matched_session_id": recall.matched_session_id,
                    },
                    reason="diagnostic question",
                ),
            ),
            notes="diagnostic",
        )

    if intent_id in _SESSION_SEEKING_INTENTS:
        if recall.matched_session_id is not None:
            return _decision(
                intent,
                plan,
                (
                    RuntimeAction(
                        action_id=ACTION_JOIN_SESSION,
                        payload={
                            "session_id": recall.matched_session_id,
                            "thread_id": recall.matched_thread_id,
                            "forum_thread_id": recall.matched_forum_thread_id,
                            "intent": intent_id,
                        },
                        reason=f"matched existing work · {recall.reason}",
                    ),
                ),
                notes="join existing work",
            )
        return _decision(
            intent,
            plan,
            (
                RuntimeAction(
                    action_id=ACTION_ASK_CLARIFICATION,
                    payload={
                        "intent": intent_id,
                        "reason": "no_matched_session",
                        "candidates": _pack_candidates(recall.candidates),
                    },
                    reason="no matched session for back-reference",
                ),
            ),
            notes="ask clarification — no session match",
        )

    if intent_id == INTENT_APPEND_CONTEXT:
        if recall.matched_session_id is not None:
            return _decision(
                intent,
                plan,
                (
                    RuntimeAction(
                        action_id=ACTION_APPEND_CONTEXT,
                        payload={
                            "session_id": recall.matched_session_id,
                            "thread_id": recall.matched_thread_id,
                            "forum_thread_id": recall.matched_forum_thread_id,
                        },
                        reason=f"append to {recall.matched_session_id}",
                    ),
                ),
                notes="append context",
            )
        return _decision(
            intent,
            plan,
            (
                RuntimeAction(
                    action_id=ACTION_ASK_CLARIFICATION,
                    payload={
                        "intent": intent_id,
                        "reason": "no_matched_session",
                        "candidates": _pack_candidates(recall.candidates),
                    },
                    reason="append-context but no matched session",
                ),
            ),
            notes="ask clarification — append context",
        )

    if intent_id == INTENT_NEW_WORK_REQUEST:
        return _decision(
            intent,
            plan,
            (
                RuntimeAction(
                    action_id=ACTION_CREATE_SESSION,
                    payload={"prompt": observation.message_text},
                    reason="new work request",
                ),
            ),
            notes="create new session",
        )

    if intent_id == INTENT_CLARIFICATION_NEEDED:
        return _decision(
            intent,
            plan,
            (
                RuntimeAction(
                    action_id=ACTION_ASK_CLARIFICATION,
                    payload={"reason": "vague"},
                    reason="clarification needed",
                ),
            ),
            notes="ask clarification — vague",
        )

    # general_chat (or any unknown intent) → reply.
    return _decision(
        intent,
        plan,
        (
            RuntimeAction(
                action_id=ACTION_REPLY,
                payload={"variant": "chat"},
                reason="general chat / fallback",
            ),
        ),
        notes="general chat",
    )


def _decision(
    intent: RuntimeIntent,
    plan: RuntimeResearchPlan,
    actions: Tuple[RuntimeAction, ...],
    *,
    notes: str = "",
) -> RuntimeDecision:
    return RuntimeDecision(
        intent=intent,
        research_plan=plan,
        actions=actions,
        notes=notes,
    )


def _pack_candidates(candidates: Sequence[SessionCandidate]):
    """Render candidates as JSON-friendly dicts for the action payload.

    Action payloads round-trip through Discord/CLI so we keep them as
    plain dicts; the candidate dataclass itself isn't pickled.
    """

    out = []
    for cand in candidates[:5]:
        out.append(
            {
                "session_id": cand.session_id,
                "title": cand.title,
                "score": cand.score,
                "why": cand.why,
                "state": cand.state,
                "task_type": cand.task_type,
                "thread_id": cand.thread_id,
                "forum_thread_id": cand.forum_thread_id,
                "has_research_pack": cand.has_research_pack,
                "has_synthesis": cand.has_synthesis,
            }
        )
    return out


__all__ = ("decide_default",)
