"""Dataclasses + intent/action vocabularies for the runtime loop.

Phase 1 keeps everything frozen, immutable, and free of Discord/IO
imports so unit tests can build inputs without touching the
orchestrator's heavier modules. ``role_id`` is a free-form string
(e.g. ``"engineering-agent/tech-lead"`` or just ``"gateway"``); the
loop does not validate it so policy modules added later can map it to
a richer policy object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Intent + action vocabularies
# ---------------------------------------------------------------------------


INTENT_NEW_WORK_REQUEST = "new_work_request"
INTENT_CONTINUE_EXISTING_WORK = "continue_existing_work"
INTENT_SUMMARIZE_PREVIOUS_WORK = "summarize_previous_work"
INTENT_STATUS_QUESTION = "status_question"
INTENT_DIAGNOSTIC_QUESTION = "diagnostic_question"
INTENT_EXECUTE_EXISTING_STEP = "execute_existing_step"
INTENT_GENERAL_CHAT = "general_chat"
INTENT_CLARIFICATION_NEEDED = "clarification_needed"
INTENT_APPEND_CONTEXT = "append_context"

KNOWN_INTENTS = (
    INTENT_NEW_WORK_REQUEST,
    INTENT_CONTINUE_EXISTING_WORK,
    INTENT_SUMMARIZE_PREVIOUS_WORK,
    INTENT_STATUS_QUESTION,
    INTENT_DIAGNOSTIC_QUESTION,
    INTENT_EXECUTE_EXISTING_STEP,
    INTENT_GENERAL_CHAT,
    INTENT_CLARIFICATION_NEEDED,
    INTENT_APPEND_CONTEXT,
)


ACTION_REPLY = "reply"
ACTION_ASK_CLARIFICATION = "ask_clarification"
ACTION_CREATE_SESSION = "create_session"
ACTION_JOIN_SESSION = "join_session"
ACTION_APPEND_CONTEXT = "append_context"
ACTION_RUN_RESEARCH = "run_research"
ACTION_PUBLISH_FORUM = "publish_forum"
ACTION_REQUEST_ROLE_TURN = "request_role_turn"
ACTION_RECORD_MEMORY = "record_memory"
ACTION_PROPOSE_APPROVAL = "propose_approval"
ACTION_NOOP = "noop"

KNOWN_ACTIONS = (
    ACTION_REPLY,
    ACTION_ASK_CLARIFICATION,
    ACTION_CREATE_SESSION,
    ACTION_JOIN_SESSION,
    ACTION_APPEND_CONTEXT,
    ACTION_RUN_RESEARCH,
    ACTION_PUBLISH_FORUM,
    ACTION_REQUEST_ROLE_TURN,
    ACTION_RECORD_MEMORY,
    ACTION_PROPOSE_APPROVAL,
    ACTION_NOOP,
)


# ---------------------------------------------------------------------------
# F16 — Gateway decision actions (issue #128, docs/runtime-recall-first.md)
#
# These 7 string ids identify the recall-first gateway decision branches.
# They are **labels** that ride along on the existing KNOWN_ACTIONS via
# ``RuntimeAction.payload["gateway_action"]``; this keeps the existing
# action_id enum stable while letting the channel router dispatch on the
# gateway-specific shape.
# ---------------------------------------------------------------------------
GATEWAY_REPLY_ONLY = "gateway:reply_only"
GATEWAY_ASK_CLARIFICATION = "gateway:ask_clarification"
GATEWAY_JOIN_EXISTING = "gateway:join_existing"
GATEWAY_APPEND_CONTEXT = "gateway:append_context"
GATEWAY_HANDOFF_TECH_LEAD_NO_RESEARCH = "gateway:handoff_tech_lead_no_research"
GATEWAY_TARGETED_RESEARCH = "gateway:targeted_research"
GATEWAY_FULL_RESEARCH = "gateway:full_research"

GATEWAY_DECISION_ACTIONS = (
    GATEWAY_REPLY_ONLY,
    GATEWAY_ASK_CLARIFICATION,
    GATEWAY_JOIN_EXISTING,
    GATEWAY_APPEND_CONTEXT,
    GATEWAY_HANDOFF_TECH_LEAD_NO_RESEARCH,
    GATEWAY_TARGETED_RESEARCH,
    GATEWAY_FULL_RESEARCH,
)


# Recall coverage levels — used by ``RecallCoverage`` below.
COVERAGE_HIGH = "high"
COVERAGE_MEDIUM = "medium"
COVERAGE_LOW = "low"

KNOWN_COVERAGE_LEVELS = (COVERAGE_HIGH, COVERAGE_MEDIUM, COVERAGE_LOW)


# ---------------------------------------------------------------------------
# Runtime stage I/O
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeInput:
    """The raw envelope a role bot sees when a Discord message arrives.

    ``role_id`` identifies which role's policy the loop should apply
    (``gateway``, ``engineering-agent/tech-lead``, ...). ``message_text``
    is the user-facing prompt; ``attachments``, ``user_links``, and
    ``mentions`` carry side-channel context. ``channel_id`` /
    ``thread_id`` / ``author_id`` / ``message_id`` are the Discord
    identifiers used by Recall to scope session lookups.

    ``policy`` is a free-form mapping the runtime caller can use to
    feed role-specific knobs (memory namespace, intent overrides, ...)
    into Understand / Recall / Decide.
    """

    role_id: str
    message_text: str
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    author_id: Optional[int] = None
    message_id: Optional[int] = None
    attachments: Sequence[Any] = field(default_factory=tuple)
    user_links: Sequence[str] = field(default_factory=tuple)
    mentions: Sequence[int] = field(default_factory=tuple)
    received_at: Optional[datetime] = None
    last_proposed_prompt: Optional[str] = None
    policy: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeObservation:
    """Normalised view of the input — what the loop saw and prepared.

    Observe is allowed to enrich (e.g. extract URLs from message text,
    normalise role_id, compute message digest for dedupe) but must not
    perform IO. Tests can inject a custom observe_fn that returns a
    pre-built RuntimeObservation.
    """

    role_id: str
    message_text: str
    normalized_text: str = ""
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    author_id: Optional[int] = None
    message_id: Optional[int] = None
    extracted_urls: Sequence[str] = field(default_factory=tuple)
    has_attachments: bool = False
    received_at: Optional[datetime] = None
    last_proposed_prompt: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeIntent:
    """What the user seems to want.

    ``intent_id`` must be one of :data:`KNOWN_INTENTS`. ``confidence``
    is "low" / "medium" / "high" (free-form string for now to match the
    existing engineering_conversation API). ``alt_intents`` carries the
    runner-up intents so Decide can fall back to clarification when the
    margin is thin.
    """

    intent_id: str
    confidence: str = "medium"
    reason: str = ""
    alt_intents: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionCandidate:
    """One candidate from session/memory recall, with a score."""

    session_id: str
    title: str = ""
    score: float = 0.0
    why: str = ""
    state: Optional[str] = None
    task_type: Optional[str] = None
    thread_id: Optional[int] = None
    forum_thread_id: Optional[int] = None
    has_research_pack: bool = False
    has_synthesis: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecallCoverage:
    """F16 — How much grounded context Recall actually surfaced.

    ``level`` is one of :data:`KNOWN_COVERAGE_LEVELS` and answers
    "does the gateway already know enough to skip research?".
    ``stale`` flags that even a "high" coverage may be older than the
    freshness threshold (default 7 days) — Decide treats stale-high
    the same as medium so we don't reuse rotten context silently.
    ``sources`` lists short labels (``"session"``, ``"memory:obsidian"``,
    ``"memory:rag"``) so audit log entries can show *why* a branch
    decided to skip / take research.
    """

    level: str = COVERAGE_LOW
    stale: bool = True
    sources: Tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""


@dataclass(frozen=True)
class RuntimeRecallResult:
    """Output of Recall: which sessions / memories matched.

    ``matched_session_id`` is set only when the loop is confident
    enough to attach. When several candidates score similarly the loop
    leaves it None and the Decide stage emits ``ask_clarification``.
    ``memory_hits`` mirrors the memory adapter's free-form mapping per
    hit so role bots can show citations. ``coverage`` (F16) is a
    derived score the gateway path uses to decide whether to skip
    research; it stays ``None`` for legacy non-gateway callers that
    don't need it.
    """

    matched_session_id: Optional[str] = None
    matched_thread_id: Optional[int] = None
    matched_forum_thread_id: Optional[int] = None
    candidates: Tuple[SessionCandidate, ...] = field(default_factory=tuple)
    memory_hits: Tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    confidence: str = "low"
    reason: str = ""
    coverage: Optional[RecallCoverage] = None


@dataclass(frozen=True)
class RuntimeResearchPlan:
    """Runtime decides whether to spend a research budget right now.

    ``run`` is True only after intent is ``new_work_request`` and Recall
    did not surface a usable existing pack. ``providers`` /
    ``max_provider_calls`` mirror the research_collector knobs but
    stay optional so the deterministic skeleton can leave them empty.
    """

    run: bool = False
    reason: str = ""
    providers: Sequence[str] = field(default_factory=tuple)
    max_provider_calls: int = 0
    role_targets: Sequence[Tuple[str, int]] = field(default_factory=tuple)


@dataclass(frozen=True)
class RuntimeAction:
    """One concrete action the loop wants to take.

    ``action_id`` must be one of :data:`KNOWN_ACTIONS`. ``payload`` is
    the action-specific blob (reply text, session id to join, role for
    the role-turn request, ...). The loop may emit several actions per
    decision; Act will dispatch them in order.
    """

    action_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class RuntimeDecision:
    """Bundle of intent + research plan + ordered actions."""

    intent: RuntimeIntent
    research_plan: RuntimeResearchPlan = field(default_factory=lambda: RuntimeResearchPlan())
    actions: Tuple[RuntimeAction, ...] = field(default_factory=tuple)
    notes: str = ""


@dataclass(frozen=True)
class RuntimeRecord:
    """Append-only event the runtime emits for session.extra / memory.

    ``kind`` is a short identifier (``intent_detected``,
    ``recall_completed``, ``decision_made``, ``action_taken``,
    ``role_turn_recorded``). ``data`` is JSON-friendly so it round-
    trips through ``save_json_cache`` without surprises.
    """

    kind: str
    data: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: Optional[datetime] = None


@dataclass(frozen=True)
class RuntimeResult:
    """Composite outcome of one ``run_runtime_loop`` call."""

    role_id: str
    observation: RuntimeObservation
    intent: RuntimeIntent
    recall: RuntimeRecallResult
    research_plan: RuntimeResearchPlan
    decision: RuntimeDecision
    actions_taken: Tuple[RuntimeAction, ...] = field(default_factory=tuple)
    records: Tuple[RuntimeRecord, ...] = field(default_factory=tuple)
    error: Optional[str] = None

    @property
    def primary_action_id(self) -> Optional[str]:
        if self.actions_taken:
            return self.actions_taken[0].action_id
        if self.decision.actions:
            return self.decision.actions[0].action_id
        return None
