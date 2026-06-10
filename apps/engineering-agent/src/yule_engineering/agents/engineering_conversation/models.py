"""engineering_conversation — dataclasses + intent ID constants.

Leaf module: no in-package dependencies. Every other module in the
package (intent_detection, task_shaping, status_responses,
research_bootstrap, response_formatters) imports symbols from here.

Public dataclasses:

- :class:`EngineeringIntentMatch` — output of intent detection.
- :class:`EngineeringConversationResponse` — envelope produced by
  :func:`build_engineering_conversation_response`.

Intent ID constants are the small string registry the dispatch logic
keys on. ``READ_ONLY_INTENTS`` is the hard-blocklist consulted by the
auto_collect path (P0-J #146): any response carrying one of these
intent IDs must never trigger autonomous research collection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence


# ---------------------------------------------------------------------------
# Intent ID constants
# ---------------------------------------------------------------------------


GENERAL_ENGINEERING_HELP = "general_engineering_help"
TASK_INTAKE_CANDIDATE = "task_intake_candidate"
NEEDS_CLARIFICATION = "needs_clarification"
CONFIRM_INTAKE = "confirm_intake"
SPLIT_TASK_PROPOSAL = "split_task_proposal"
STATUS_DIAGNOSTIC = "status_diagnostic"
# P0-J (#146) — read-only intents. STATUS/SESSION_*/BLOCKED_REASON/
# CONTINUE/CHANGE_DIRECTION 류는 절대 _maybe_run_auto_collect 호출 금지
# (hard rule). commit 7 의 build_engineering_conversation_response
# 라우팅 분기가 이 상수들을 hard-blocklist 로 사용.
SESSION_COUNT_QUERY = "session_count_query"
SESSION_LIST_QUERY = "session_list_query"
BLOCKED_REASON_QUERY = "blocked_reason_query"
CONTINUE_EXISTING_WORK = "continue_existing_work"
CHANGE_DIRECTION = "change_direction"
# P0-K (#148) — approval/proceed-only operator phrase. Acks the
# existing session forward; never creates a new intake / forum
# thread / research loop. Distinct from CONFIRM_INTAKE which
# *promotes* a previously-proposed task into intake.
APPROVAL_ACTION = "approval_action"

# Hard-blocklist for the auto_collect path (commit 7 enforcement).
READ_ONLY_INTENTS: tuple = (
    STATUS_DIAGNOSTIC,
    SESSION_COUNT_QUERY,
    SESSION_LIST_QUERY,
    BLOCKED_REASON_QUERY,
    CONTINUE_EXISTING_WORK,
    CHANGE_DIRECTION,
    APPROVAL_ACTION,
)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineeringIntentMatch:
    """What the user seems to want from engineering-agent right now."""

    intent_id: str
    label: str
    confidence: str = "medium"  # "high" / "medium" / "low"


@dataclass(frozen=True)
class EngineeringConversationResponse:
    """Envelope returned by :func:`build_engineering_conversation_response`.

    Downstream Discord layer reads:

    - ``ready_to_intake=True`` → call ``workflow.intake`` with the
      preserved ``intake_prompt``.
    - ``needs_clarification=True`` → reply with ``content`` and wait for
      another user turn.
    - ``proposed_splits`` non-empty → reply with split proposal; user picks
      one or types a confirmation phrase to proceed with the original ask.
    - ``research_pack`` set → autonomous collector returned ≥1 result.
      Forum publisher / deliberation should consume this pack instead of
      asking the user for more material.
    - ``collection_outcome`` carries the raw ``CollectionOutcome`` (mode,
      collector_name, query, count) so the Discord wiring can post the
      ``format_collection_summary`` block to the research forum.
    """

    content: str
    intent_id: str
    ready_to_intake: bool = False
    needs_clarification: bool = False
    proposed_splits: Sequence[str] = field(default_factory=tuple)
    suggested_task_type: Optional[str] = None
    write_likely: bool = False
    intake_prompt: Optional[str] = None
    mention_user_id: Optional[int] = None
    research_pack: Optional[Any] = None
    collection_outcome: Optional[Any] = None
    # When True the gateway must NOT auto-collect, NOT create a new
    # session, NOT ask for confirmation. The user is asking what's
    # currently happening, not requesting new work — the response
    # already describes the existing state.
    is_status_query: bool = False


__all__ = (
    # intent IDs
    "APPROVAL_ACTION",
    "BLOCKED_REASON_QUERY",
    "CHANGE_DIRECTION",
    "CONFIRM_INTAKE",
    "CONTINUE_EXISTING_WORK",
    "GENERAL_ENGINEERING_HELP",
    "NEEDS_CLARIFICATION",
    "READ_ONLY_INTENTS",
    "SESSION_COUNT_QUERY",
    "SESSION_LIST_QUERY",
    "SPLIT_TASK_PROPOSAL",
    "STATUS_DIAGNOSTIC",
    "TASK_INTAKE_CANDIDATE",
    # dataclasses
    "EngineeringConversationResponse",
    "EngineeringIntentMatch",
)
