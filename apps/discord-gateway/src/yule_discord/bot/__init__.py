"""Discord bot — package facade (P0-Q step 4).

Historical monolith (``bot.py``, 3126 lines) is being structurally
decomposed in this PR. As an initial structural cut the file is moved
into a package (``bot/_legacy.py`` + this facade) so every external
import site (tests + module wiring + integrations) keeps working
without source changes.

Follow-up work on the same audit (``docs/p0q-discord-large-files-decomposition.md``)
splits ``_legacy.py`` further into responsibility-aligned modules
(config / scheduling / message_routing / engineering_handlers /
forum_factories / main). Scheduled as commits within this same PR.

Production entry points exposed unchanged:

- :func:`run_discord_bot` — main bot entry.
- :func:`build_engineering_gateway_bot` — engineering gateway constructor.
- :func:`run_engineering_gateway_until_shutdown` — gateway lifecycle.
"""

from __future__ import annotations

from ._legacy import *  # noqa: F401,F403 — facade re-export
from ._legacy import (  # noqa: F401 — explicit symbols for IDE / static analysis
    _ENGINEERING_LAST_PROPOSED,
    _ENGINEERING_LAST_RESEARCH_CONTEXT,
    _checkpoint_window_minutes,
    _collect_due_daily_preparation_steps,
    _daily_preparation_schedule_for,
    _default_engineering_conversation_fn,
    _extract_conversation_prompt,
    _extract_session_id_from_text,
    _filter_unsent_checkpoints,
    _find_session_with_resumed_thread,
    _format_engineering_kickoff_message,
    _format_research_forum_disabled_status,
    _format_research_hints_for_outcome,
    _persist_forum_comment_mode_to_session,
    _research_loop_report_from_publish,
    _install_engineering_role_runner_dispatch_for_gateway,
    _is_command_only_prompt,
    _mark_checkpoints_sent,
    _next_checkpoint_scan,
    _next_daily_preparation_runs,
    _next_scheduled_briefing_run,
    _record_engineering_continuation,
    _resolve_due_checkpoints,
    _resolve_messageable_channel,
    _should_handle_message,
    _startup_messages,
    build_engineering_gateway_bot,
    run_discord_bot,
    run_engineering_gateway_until_shutdown,
)
