"""Engineering Agent Runtime — shared conversational loop.

Every engineering role bot (gateway, tech-lead, ai/backend/frontend/
product/qa/devops) flows through the same seven-stage loop:

    Observe → Understand → Recall → Research → Decide → Act → Record

Each stage is a pluggable function so the same skeleton can host both
the deterministic fallback runtime (used by tests and offline
operation) and an LLM-backed runtime (added in later phases).

Phase 1 lands the dataclasses + skeleton loop only. The Discord
gateway and member bots keep using their existing routing modules; the
runtime is wired in incrementally in later phases.
"""

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
    KNOWN_INTENTS,
    ACTION_APPEND_CONTEXT,
    ACTION_ASK_CLARIFICATION,
    ACTION_CREATE_SESSION,
    ACTION_JOIN_SESSION,
    ACTION_NOOP,
    ACTION_PROPOSE_APPROVAL,
    ACTION_PUBLISH_FORUM,
    ACTION_RECORD_MEMORY,
    ACTION_REPLY,
    ACTION_REQUEST_ROLE_TURN,
    ACTION_RUN_RESEARCH,
    KNOWN_ACTIONS,
    RuntimeAction,
    RuntimeDecision,
    RuntimeInput,
    RuntimeIntent,
    RuntimeObservation,
    RuntimeRecallResult,
    RuntimeRecord,
    RuntimeResearchPlan,
    RuntimeResult,
    SessionCandidate,
)
from .loop import run_runtime_loop

__all__ = (
    "ACTION_APPEND_CONTEXT",
    "ACTION_ASK_CLARIFICATION",
    "ACTION_CREATE_SESSION",
    "ACTION_JOIN_SESSION",
    "ACTION_NOOP",
    "ACTION_PROPOSE_APPROVAL",
    "ACTION_PUBLISH_FORUM",
    "ACTION_RECORD_MEMORY",
    "ACTION_REPLY",
    "ACTION_REQUEST_ROLE_TURN",
    "ACTION_RUN_RESEARCH",
    "INTENT_APPEND_CONTEXT",
    "INTENT_CLARIFICATION_NEEDED",
    "INTENT_CONTINUE_EXISTING_WORK",
    "INTENT_DIAGNOSTIC_QUESTION",
    "INTENT_EXECUTE_EXISTING_STEP",
    "INTENT_GENERAL_CHAT",
    "INTENT_NEW_WORK_REQUEST",
    "INTENT_STATUS_QUESTION",
    "INTENT_SUMMARIZE_PREVIOUS_WORK",
    "KNOWN_ACTIONS",
    "KNOWN_INTENTS",
    "RuntimeAction",
    "RuntimeDecision",
    "RuntimeInput",
    "RuntimeIntent",
    "RuntimeObservation",
    "RuntimeRecallResult",
    "RuntimeRecord",
    "RuntimeResearchPlan",
    "RuntimeResult",
    "SessionCandidate",
    "run_runtime_loop",
)
