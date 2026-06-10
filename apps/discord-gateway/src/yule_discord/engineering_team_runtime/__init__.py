"""Compat shim — ``engineering_team_runtime`` relocated to ``agents/``.

The engineering-agent team-runtime orchestration logic moved from
``discord/engineering_team_runtime`` to
``yule_orchestrator.agents.engineering_team_runtime`` to break an
artificial ``agents ↔ discord`` import cycle (the module contained zero
discord transport — only agents deliberation/research orchestration).

This shim re-exports the relocated public API so the remaining non-agents
import sites (discord's own files, cli, tests) keep working unchanged.
``discord → agents`` is the forward/legal direction; the cycle is broken
because no ``agents/`` file imports this discord path anymore.
"""

from __future__ import annotations

from yule_orchestrator.agents.engineering_team_runtime import *  # noqa: F401,F403
from yule_orchestrator.agents.engineering_team_runtime import (  # noqa: F401
    _HANDLED_TURNS,
    _HANDLED_TURNS_SET,
    _retrieve_memory_for_role,
    DEFAULT_RESEARCH_ROLE_SEQUENCE,
    DeliberationLoopResult,
    DeliberationTurnRecord,
    PLAYED_ROLES_KEY,
    RESEARCH_SYNTHESIS_ROLE,
    ROLE_ACTIVITY_RESEARCH_COMPLETED,
    ROLE_RESEARCH_STATUS_FAILED,
    ROLE_RESEARCH_STATUS_OK,
    ROLE_TURN_KIND_OPEN,
    ROLE_TURN_KIND_SYNTHESIS,
    ROLE_TURN_KIND_TURN,
    ROLE_TURN_STATUS_ERROR,
    ROLE_TURN_STATUS_POSTED,
    ResearchTurnOutcome,
    TEAM_CONVERSATION_KEY,
    TeamTurn,
    TeamTurnOutcome,
    _build_open_call_outcome,
    _collect_role_research_pack,
    _load_synthesis_text_from_session_extra,
    _maybe_load_pack,
    _next_research_role,
    _render_role_research_findings_block,
    _replay_role_takes,
    _replay_role_takes_until,
    _role_address,
    append_role_activity_event,
    build_turn_plan,
    closing_message,
    deliberation_research_role_sequence,
    deliberation_role_sequence,
    deliberation_role_turn,
    dispatch_directive,
    format_role_turn_text,
    get_role_runner_dispatch,
    handle_research_turn_message,
    handle_team_turn_message,
    kickoff_directive,
    mark_turn_played,
    next_pending_turn,
    parse_dispatch_marker,
    parse_research_dispatch_marker,
    parse_research_open_marker,
    played_roles,
    record_role_research_result,
    record_role_turn_event,
    research_dispatch_directive,
    research_open_call_directive,
    reset_handled_turns_for_tests,
    run_deliberation_loop,
    set_role_runner_dispatch,
    synthesize_thread,
)
