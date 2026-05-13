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

import re
from typing import Sequence, Tuple

from .models import (
    ACTION_APPEND_CONTEXT,
    ACTION_ASK_CLARIFICATION,
    ACTION_CREATE_SESSION,
    ACTION_JOIN_SESSION,
    ACTION_REPLY,
    ACTION_REQUEST_ROLE_TURN,
    ACTION_RUN_RESEARCH,
    COVERAGE_HIGH,
    COVERAGE_LOW,
    COVERAGE_MEDIUM,
    GATEWAY_APPEND_CONTEXT,
    GATEWAY_ASK_CLARIFICATION,
    GATEWAY_FULL_RESEARCH,
    GATEWAY_HANDOFF_TECH_LEAD_NO_RESEARCH,
    GATEWAY_JOIN_EXISTING,
    GATEWAY_REPLY_ONLY,
    GATEWAY_TARGETED_RESEARCH,
    INTENT_APPEND_CONTEXT,
    INTENT_CLARIFICATION_NEEDED,
    INTENT_CONTINUE_EXISTING_WORK,
    INTENT_DIAGNOSTIC_QUESTION,
    INTENT_EXECUTE_EXISTING_STEP,
    INTENT_GENERAL_CHAT,
    INTENT_NEW_WORK_REQUEST,
    INTENT_STATUS_QUESTION,
    INTENT_SUMMARIZE_PREVIOUS_WORK,
    RecallCoverage,
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


# F16 — Gateway recall-first decision (docs/runtime-recall-first.md §2.2).
#
# These regexes detect "explicit research" and "direction-asking"
# phrases so the gateway path can short-circuit coverage scoring when
# the user clearly wants research, or hand off to tech-lead instead
# of triggering a fresh research cycle.
_EXPLICIT_RESEARCH_RE = re.compile(
    r"(조사해줘|리서치만|리서치\s*해줘|research\s*only|run\s*research)",
    re.IGNORECASE,
)
_DIRECTION_INQUIRY_RE = re.compile(
    r"(어떻게\s*생각|어떤\s*방향|어느\s*쪽|옵션은|추천\s*해|어디\s*가\s*나\s*아|"
    r"option|recommend|direction|approach)",
    re.IGNORECASE,
)

_GATEWAY_TARGETED_MAX_PROVIDER_CALLS = 2


def decide_gateway(
    observation: RuntimeObservation,
    intent: RuntimeIntent,
    recall: RuntimeRecallResult,
    plan: RuntimeResearchPlan,
    input_: RuntimeInput,
) -> RuntimeDecision:
    """Recall-first gateway decision — emits one of 7 GATEWAY_* labels.

    The function reuses the existing ``RuntimeAction.action_id``
    vocabulary so the action stage stays compatible; the gateway
    branch is carried in ``payload["gateway_action"]``.

    Branching rules match docs/runtime-recall-first.md §2.2:

      * **REPLY_ONLY** — general_chat / status / diagnostic with high
        coverage and non-stale sources.
      * **ASK_CLARIFICATION** — clarification_needed *or* the
        intent classifier produced a low-confidence intent.
      * **JOIN_EXISTING** — continue/summarize/execute with a matched
        session id.
      * **APPEND_CONTEXT** — append_context with a matched session id.
      * **HANDOFF_TECH_LEAD_NO_RESEARCH** — new_work_request +
        coverage=high + a direction-asking phrase. Tech-lead gets a
        structured handoff without running research.
      * **TARGETED_RESEARCH** — new_work_request + coverage=medium *or*
        coverage=high+stale. Caps provider calls at 2.
      * **FULL_RESEARCH** — new_work_request + coverage=low *or* the
        user explicitly asked for research ("조사해줘", "리서치만",
        "research only"). Explicit phrase bypasses coverage scoring.
    """

    intent_id = intent.intent_id
    coverage = recall.coverage or RecallCoverage(level=COVERAGE_LOW, stale=True)
    message_text = observation.message_text or ""

    explicit_research = bool(_EXPLICIT_RESEARCH_RE.search(message_text))

    # 1. Clarification — never delegate or research blindly.
    if intent_id == INTENT_CLARIFICATION_NEEDED:
        return _gateway_decision(
            intent,
            plan,
            (
                _gateway_action(
                    ACTION_ASK_CLARIFICATION,
                    GATEWAY_ASK_CLARIFICATION,
                    {"variant": "low_confidence"},
                    reason="intent=clarification_needed",
                ),
            ),
            notes="ask clarification",
            coverage=coverage,
        )

    # 2. Session-bound back-references — Join / Append before anything else.
    if intent_id in _SESSION_SEEKING_INTENTS and recall.matched_session_id:
        return _gateway_decision(
            intent,
            plan,
            (
                _gateway_action(
                    ACTION_JOIN_SESSION,
                    GATEWAY_JOIN_EXISTING,
                    {"session_id": recall.matched_session_id},
                    reason="continue/summarize/execute with matched session",
                ),
            ),
            notes="join existing session",
            coverage=coverage,
        )

    if intent_id == INTENT_APPEND_CONTEXT and recall.matched_session_id:
        return _gateway_decision(
            intent,
            plan,
            (
                _gateway_action(
                    ACTION_APPEND_CONTEXT,
                    GATEWAY_APPEND_CONTEXT,
                    {"session_id": recall.matched_session_id},
                    reason="append context to matched session",
                ),
            ),
            notes="append context",
            coverage=coverage,
        )

    # 3. status/diagnostic/general_chat — reply only when grounded.
    chat_like = intent_id in (
        INTENT_STATUS_QUESTION,
        INTENT_DIAGNOSTIC_QUESTION,
        INTENT_GENERAL_CHAT,
    )
    if chat_like and coverage.level == COVERAGE_HIGH and not coverage.stale and not explicit_research:
        return _gateway_decision(
            intent,
            plan,
            (
                _gateway_action(
                    ACTION_REPLY,
                    GATEWAY_REPLY_ONLY,
                    {"variant": "recall_grounded", "sources": list(coverage.sources)},
                    reason=f"coverage=high stale=False intent={intent_id}",
                ),
            ),
            notes="reply only — recall grounded",
            coverage=coverage,
        )

    # 4. Explicit "research only" — always full research, bypass coverage.
    if explicit_research:
        research_plan = RuntimeResearchPlan(
            run=True,
            reason="explicit research phrase",
            providers=plan.providers,
            max_provider_calls=plan.max_provider_calls,
            role_targets=plan.role_targets,
        )
        return _gateway_decision(
            intent,
            research_plan,
            (
                _gateway_action(
                    ACTION_RUN_RESEARCH,
                    GATEWAY_FULL_RESEARCH,
                    {"mode": "full", "trigger": "explicit_phrase"},
                    reason="explicit phrase",
                ),
            ),
            notes="full research — user explicit",
            coverage=coverage,
        )

    # 5. new_work_request branches by coverage.
    if intent_id == INTENT_NEW_WORK_REQUEST:
        direction_inquiry = bool(_DIRECTION_INQUIRY_RE.search(message_text))

        # 5a. High + direction-asking → handoff to tech-lead, no research.
        if coverage.level == COVERAGE_HIGH and not coverage.stale and direction_inquiry:
            return _gateway_decision(
                intent,
                plan,  # research_plan.run stays False
                (
                    _gateway_action(
                        ACTION_REQUEST_ROLE_TURN,
                        GATEWAY_HANDOFF_TECH_LEAD_NO_RESEARCH,
                        {
                            "role": "tech-lead",
                            "run_research": False,
                            "sources": list(coverage.sources),
                        },
                        reason="coverage=high + direction inquiry",
                    ),
                ),
                notes="handoff to tech-lead without research",
                coverage=coverage,
            )

        # 5b. Medium coverage OR high+stale → targeted research.
        if coverage.level == COVERAGE_MEDIUM or (
            coverage.level == COVERAGE_HIGH and coverage.stale
        ):
            research_plan = RuntimeResearchPlan(
                run=True,
                reason=f"coverage={coverage.level} stale={coverage.stale}",
                providers=plan.providers,
                max_provider_calls=max(
                    plan.max_provider_calls, _GATEWAY_TARGETED_MAX_PROVIDER_CALLS
                )
                if plan.max_provider_calls == 0
                else min(plan.max_provider_calls, _GATEWAY_TARGETED_MAX_PROVIDER_CALLS),
                role_targets=plan.role_targets,
            )
            return _gateway_decision(
                intent,
                research_plan,
                (
                    _gateway_action(
                        ACTION_RUN_RESEARCH,
                        GATEWAY_TARGETED_RESEARCH,
                        {
                            "mode": "targeted",
                            "max_provider_calls": _GATEWAY_TARGETED_MAX_PROVIDER_CALLS,
                        },
                        reason=f"coverage={coverage.level} stale={coverage.stale}",
                    ),
                ),
                notes="targeted research",
                coverage=coverage,
            )

        # 5c. Low coverage → full research.
        research_plan = RuntimeResearchPlan(
            run=True,
            reason=f"coverage={coverage.level} stale={coverage.stale}",
            providers=plan.providers,
            max_provider_calls=plan.max_provider_calls,
            role_targets=plan.role_targets,
        )
        return _gateway_decision(
            intent,
            research_plan,
            (
                _gateway_action(
                    ACTION_RUN_RESEARCH,
                    GATEWAY_FULL_RESEARCH,
                    {"mode": "full"},
                    reason=f"coverage={coverage.level}",
                ),
            ),
            notes="full research — low coverage",
            coverage=coverage,
        )

    # 6. Fallback for chat-like intents that didn't qualify for REPLY_ONLY:
    #    coverage isn't high or sources are stale → ask clarification so
    #    the gateway never silently falls back to research.
    if chat_like:
        return _gateway_decision(
            intent,
            plan,
            (
                _gateway_action(
                    ACTION_ASK_CLARIFICATION,
                    GATEWAY_ASK_CLARIFICATION,
                    {
                        "variant": "coverage_insufficient",
                        "coverage_level": coverage.level,
                        "stale": coverage.stale,
                    },
                    reason="chat intent without high+fresh coverage",
                ),
            ),
            notes="clarify — coverage too thin",
            coverage=coverage,
        )

    # 7. Anything we couldn't classify (session-seeking intents without a
    #    matched session id, etc.) — fall through to clarification.
    return _gateway_decision(
        intent,
        plan,
        (
            _gateway_action(
                ACTION_ASK_CLARIFICATION,
                GATEWAY_ASK_CLARIFICATION,
                {"variant": "unmatched_intent", "intent_id": intent_id},
                reason="no gateway branch matched",
            ),
        ),
        notes="clarify — no branch matched",
        coverage=coverage,
    )


def _gateway_action(
    action_id: str,
    gateway_action: str,
    extra_payload: dict,
    *,
    reason: str = "",
) -> RuntimeAction:
    payload = {"gateway_action": gateway_action}
    payload.update(extra_payload)
    return RuntimeAction(action_id=action_id, payload=payload, reason=reason)


def _gateway_decision(
    intent: RuntimeIntent,
    plan: RuntimeResearchPlan,
    actions: Tuple[RuntimeAction, ...],
    *,
    notes: str,
    coverage: RecallCoverage,
) -> RuntimeDecision:
    enriched_notes = (
        f"{notes} [coverage={coverage.level} stale={coverage.stale} "
        f"sources={','.join(coverage.sources) or 'none'}]"
    )
    return _decision(intent, plan, actions, notes=enriched_notes)


__all__ = ("decide_default", "decide_gateway")
