"""Engineering-agent free-form conversation layer — package facade.

This package is the **conversational front door** for the engineering-agent
gateway in the ``#업무-접수`` channel. It receives a user's natural-language
message and returns a structured :class:`EngineeringConversationResponse`
that downstream code (bot.py, commands.py, future dispatcher) consumes to
decide whether to:

- reply only (general help / clarification questions),
- propose a task split before intake,
- or actually call ``workflow.intake`` because the user confirmed.

P0-L (#138 followup) decomposition:

The historical monolith (``engineering_conversation.py``, 3000+ lines) was
split into 6 responsibility-aligned modules. Each module owns one slice of
the conversation layer:

  * :mod:`.models`              — dataclasses + intent ID constants.
  * :mod:`.intent_detection`    — :func:`detect_engineering_intent` + matchers.
  * :mod:`.task_shaping`        — ``_suggest_task_type`` + write-intent heuristics.
  * :mod:`.status_responses`    — status / session / blocked / continue / change-direction responders.
  * :mod:`.research_bootstrap`  — ``_maybe_run_auto_collect`` + collection/intake formatting + research candidates.
  * :mod:`.response_formatters` — :func:`build_engineering_conversation_response` main entry + generic surface formatters.

This ``__init__.py`` is the **thin facade** — re-exports the public API so
``from yule_orchestrator.discord.engineering_conversation import X`` keeps
working for the 28 existing import sites without source changes.

How this differs from ``discord/conversation.py`` (planning-agent):

- planning conversation is *snapshot-bound* — it leans on
  ``DailyPlanSnapshot`` and answers deterministic queries about the day.
- engineering conversation is *task-shaping* — it interprets a free-form
  request, asks for missing context, suggests breaking down multi-prong
  asks, and only commits to a session once the user explicitly says so.
"""

from __future__ import annotations

# Canonical dataclasses + intent ID constants live in .models (P0-L step 3).
from .models import (  # noqa: F401 — facade re-export
    APPROVAL_ACTION,
    BLOCKED_REASON_QUERY,
    CHANGE_DIRECTION,
    CONFIRM_INTAKE,
    CONTINUE_EXISTING_WORK,
    EngineeringConversationResponse,
    EngineeringIntentMatch,
    GENERAL_ENGINEERING_HELP,
    NEEDS_CLARIFICATION,
    READ_ONLY_INTENTS,
    SESSION_COUNT_QUERY,
    SESSION_LIST_QUERY,
    SPLIT_TASK_PROPOSAL,
    STATUS_DIAGNOSTIC,
    TASK_INTAKE_CANDIDATE,
)
# task_type / write-intent heuristics live in .task_shaping (P0-L step 4).
from .task_shaping import (  # noqa: F401 — facade re-export
    _suggest_task_type,
)
# Status / read-only responders live in .status_responses (P0-L step 5).
from .status_responses import (  # noqa: F401 — facade re-export
    format_blocked_reason_response,
    format_change_direction_response,
    format_continue_existing_response,
    format_session_count_response,
    format_session_list_response,
    format_status_diagnostic_response,
)
# Intent classification + phrase matchers live in .intent_detection (P0-L step 6).
from .intent_detection import (  # noqa: F401 — facade re-export
    detect_engineering_intent,
    split_task_branches,
)
# Research candidate classification + collector wiring + intake body
# formatters live in .research_bootstrap (P0-L step 7).
from .research_bootstrap import (  # noqa: F401 — facade re-export
    ResearchCandidate,
    ResearchCollectionResult,
    classify_attachment,
    classify_url,
    collect_research_candidates_from_message,
    format_insufficient_research_prompt,
    suggest_role_research_assignments,
)

# Remaining content (response_formatters) is still in _legacy.py —
# re-export until the last module is extracted in step 8.
from ._legacy import *  # noqa: F401,F403 — facade re-export
from ._legacy import (  # noqa: F401 — explicit symbols for IDE/static analysis
    build_engineering_conversation_response,
)


__all__ = (
    # public dataclasses
    "EngineeringConversationResponse",
    "EngineeringIntentMatch",
    "ResearchCandidate",
    "ResearchCollectionResult",
    # intent ID constants
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
    # main entry + intent detection
    "build_engineering_conversation_response",
    "detect_engineering_intent",
    "split_task_branches",
    # status / read-only responders
    "format_blocked_reason_response",
    "format_change_direction_response",
    "format_continue_existing_response",
    "format_session_count_response",
    "format_session_list_response",
    "format_status_diagnostic_response",
    # research bootstrap surface
    "classify_attachment",
    "classify_url",
    "collect_research_candidates_from_message",
    "format_insufficient_research_prompt",
    "suggest_role_research_assignments",
)
