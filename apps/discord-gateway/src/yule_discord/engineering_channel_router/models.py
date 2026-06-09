"""engineering_channel_router — dataclasses + type aliases (leaf module).

Six frozen dataclasses + seven Callable aliases form the contract
between the router and every adapter that drives it (bot.py / commands /
test fixtures). No in-package dependencies — every other router module
imports symbols from here.

- :class:`EngineeringRouteContext` — intake channel identity + opt-in
  recall-first flag.
- :class:`EngineeringConversationOutcome` — what the conversation
  layer returns to the router.
- :class:`EngineeringThreadKickoff` — thread creation result.
- :class:`EngineeringThreadContinuation` — thread resumption result.
- :class:`EngineeringResearchLoopReport` — research loop hook result
  (forum publish status + member-bots open-call signals).
- :class:`EngineeringRouteResult` — final router output handed back to
  the bot event loop.

Type aliases name the long ``Callable[..., ...]`` types every gateway
injection seam uses (intake_fn, conversation_fn, thread_kickoff_fn,
thread_continuation_fn, research_loop_fn, send_chunks, extract_prompt).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Union

from yule_orchestrator.agents.routing import EngineeringRoutingDecision


@dataclass(frozen=True)
class EngineeringRouteContext:
    """Where the engineering intake channel lives.

    Both ``intake_channel_id`` and ``intake_channel_name`` are optional
    individually — if either one matches the message channel (or its
    parent, for a thread), the message is treated as engineering.

    F16 (issue #128) added ``prefer_recall_first_gateway`` — an opt-in
    flag that, when set, lets the router call ``decide_gateway`` (the
    new 7-action recall-first decision) for **any** intent. While off
    (default), the router keeps the legacy preflight short-circuit
    behaviour so all pre-F16 contracts hold. The coverage scorer is
    attached to recall results unconditionally — it is **derived**
    metadata and changes no existing behaviour, but lets observability
    and future routing tap into it without re-running recall.
    """

    intake_channel_id: Optional[int] = None
    intake_channel_name: Optional[str] = None
    prefer_recall_first_gateway: bool = False

    @property
    def configured(self) -> bool:
        # Lazy import: ``utils`` is a sibling leaf module; importing at
        # method call time keeps ``models`` itself fully leaf so other
        # router modules can import it without dragging utils.
        from .utils import _normalize_channel_name

        return self.intake_channel_id is not None or bool(
            _normalize_channel_name(self.intake_channel_name)
        )

    @classmethod
    def from_env(cls) -> "EngineeringRouteContext":
        from .utils import (
            _optional_bool_env,
            _optional_int_env,
            _optional_string_env,
        )

        return cls(
            intake_channel_id=_optional_int_env("DISCORD_ENGINEERING_INTAKE_CHANNEL_ID"),
            intake_channel_name=_optional_string_env(
                "DISCORD_ENGINEERING_INTAKE_CHANNEL_NAME"
            ),
            prefer_recall_first_gateway=_optional_bool_env(
                "YULE_GATEWAY_RECALL_FIRST_ENABLED", default=False
            ),
        )


@dataclass(frozen=True)
class EngineeringConversationOutcome:
    """The shape returned by the engineering free-conversation layer.

    ``confirmed=True`` means the user just expressed intent to start
    a real intake; ``intake_prompt`` is the canonicalised request for
    the workflow.  The conversation layer is free to omit those fields
    — the router falls back to a keyword-based confirmation check on
    the original user text.

    ``research_pack`` and ``collection_outcome`` carry the autonomous
    research collector's result through to the research-loop hook
    (forum publisher / deliberation kickoff). ``role_for_research``
    lets the conversation layer signal which role profile drove the
    collection so downstream code can render labels accordingly.
    """

    content: str
    confirmed: bool = False
    intake_prompt: Optional[str] = None
    write_requested: bool = False
    thread_topic: Optional[str] = None
    research_pack: Any = None
    collection_outcome: Any = None
    role_for_research: Optional[str] = None
    # When True the conversation already answered a status/diagnostic
    # question. The router must NOT route to intake/decide/auto_collect
    # — the user wasn't filing new work, they were asking what's going
    # on with existing work.
    is_status_query: bool = False


@dataclass(frozen=True)
class EngineeringThreadKickoff:
    """Result of creating a working thread and posting kickoff."""

    thread_id: Optional[int] = None
    message: Optional[str] = None


@dataclass(frozen=True)
class EngineeringThreadContinuation:
    """Result of continuing an already-open workflow thread."""

    session: Any
    thread_id: Optional[int] = None
    message: Optional[str] = None


@dataclass(frozen=True)
class EngineeringResearchLoopReport:
    """What the research loop hook reported back to the router.

    ``follow_up_message`` is sent to the user when the loop decided the
    research pack is too thin (e.g. no URL, no attachment for a
    landing-page task). ``forum_status_message`` is the operator-facing
    summary line ("운영-리서치 forum thread 게시: …") posted after a
    successful publish. ``error`` is filled when the hook itself raised;
    callers display it as a `⚠️` line and continue.
    """

    follow_up_message: Optional[str] = None
    forum_status_message: Optional[str] = None
    forum_thread_id: Optional[int] = None
    forum_thread_url: Optional[str] = None
    insufficient: bool = False
    error: Optional[str] = None
    # member-bots vs gateway publication mode signal — populated by the
    # research-loop hook from the publication outcome so status /
    # diagnostic responses can describe the live setup correctly.
    forum_comment_mode: Optional[str] = None
    # member-bots mode only: did the gateway successfully post the
    # ``[research-open:<session_id>]`` open-call directive that each
    # member bot is supposed to react to? ``None`` in gateway mode.
    kickoff_posted: Optional[bool] = None
    # member-bots mode only: stringified error from the open-call
    # directive post when ``kickoff_posted`` is False; otherwise None.
    kickoff_error: Optional[str] = None


@dataclass(frozen=True)
class EngineeringRouteResult:
    """What the router did with one Discord message.

    ``handled=False`` means this message is *not* an engineering channel
    message; the bot should fall through to its planning conversation
    path.  ``handled=True`` means the router has already replied (and
    optionally created an intake/thread), so the bot must not double-reply.
    """

    handled: bool
    conversation_message: Optional[str] = None
    intake_message: Optional[str] = None
    kickoff_message: Optional[str] = None
    session_id: Optional[str] = None
    thread_id: Optional[int] = None
    research_loop_report: Optional[EngineeringResearchLoopReport] = None
    error: Optional[str] = None
    routing_decision: Optional[EngineeringRoutingDecision] = None


SendChunksFn = Callable[[Any, str], Awaitable[None]]
ExtractPromptFn = Callable[..., str]
ConversationFn = Callable[..., Union[
    EngineeringConversationOutcome,
    Awaitable[EngineeringConversationOutcome],
    str,
    Awaitable[str],
]]
IntakeFn = Callable[..., Any]
ThreadKickoffFn = Callable[..., Awaitable[EngineeringThreadKickoff]]
ThreadContinuationFn = Callable[..., Union[
    Optional[EngineeringThreadContinuation],
    Awaitable[Optional[EngineeringThreadContinuation]],
]]
ResearchLoopFn = Callable[..., Union[
    EngineeringResearchLoopReport,
    Awaitable[EngineeringResearchLoopReport],
]]


__all__ = (
    "EngineeringConversationOutcome",
    "EngineeringResearchLoopReport",
    "EngineeringRouteContext",
    "EngineeringRouteResult",
    "EngineeringThreadContinuation",
    "EngineeringThreadKickoff",
    "ConversationFn",
    "ExtractPromptFn",
    "IntakeFn",
    "ResearchLoopFn",
    "SendChunksFn",
    "ThreadContinuationFn",
    "ThreadKickoffFn",
)
