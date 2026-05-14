"""Engineering team runtime — package facade (P0-Q decomposition).

Historical monolith (``engineering_team_runtime.py``, 2153 lines) is
being decomposed in this PR. As an initial structural cut the file is
moved into a package (``engineering_team_runtime/_legacy.py`` + this
facade) so all 50+ external import sites (bot.py, member_bot.py, tests)
keep working without source changes.

Subsequent commits in this PR further split ``_legacy.py`` into
responsibility-aligned modules per
``docs/p0q-discord-large-files-decomposition.md``:

  * team_turn — TeamTurn / TeamTurnOutcome + turn sequencing
  * dispatch — dispatch directive parsing / formatting
  * research_turn — research-specific handler
  * role_execution — role runner dispatch / queue exec
  * recording — observability + session.extra recording
  * deliberation — deliberation loop + synthesis
"""

from __future__ import annotations

from ._legacy import *  # noqa: F401,F403 — facade re-export
from ._legacy import (  # noqa: F401 — explicit symbols for IDE / static analysis
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
