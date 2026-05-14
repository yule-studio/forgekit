"""Engineering channel router — package facade (P0-P decomposition).

Historical monolith (``engineering_channel_router.py``, 3316 lines)
is being split into 11 responsibility-aligned modules per the audit at
``docs/p0p-engineering-channel-router-decomposition.md``. Each module
owns one slice of the gateway orchestration:

  * :mod:`.models`              — dataclasses + type aliases.
  * :mod:`.utils`               — env coercion + message parsing + async helpers.
  * :mod:`.intent_detection`    — channel + confirmation + continuation signals.
  * :mod:`.session_persistence` — session.extra mutations + load helpers.
  * :mod:`.coding_gate`         — "수정 권한 제안" / "수정 승인" handler.
  * :mod:`.obsidian_gate`       — "저장 승인" / "이대로 저장" handler.
  * :mod:`.research_loop`       — P0-K command-only guard + research_loop hook
                                    + forum status persistence.
  * :mod:`.reporting`           — work_report preview + clarification display
                                    + outcome coercion.
  * :mod:`.runtime_preflight`   — runtime intent + recall short-circuit.
  * :mod:`..engineering.clarification` (already extracted in P0-N4) —
                                    clarification cache + TTL + selection.
  * :mod:`.main`                — `route_engineering_message` orchestration
                                    + clarification CREATE driver.

This ``__init__.py`` is the **thin facade** — re-exports the public API
so ``from yule_orchestrator.discord.engineering_channel_router import X``
keeps working for every external import site (bot.py, commands.py,
supervisor.py, all test fixtures) without source changes.
"""

from __future__ import annotations

# Until commits 3-12 fully extract each module, re-export everything
# from the legacy single-file body so existing callers (and tests) keep
# working verbatim. Subsequent commits replace these wildcard imports
# with explicit per-module imports as content moves out of ``_legacy``.
from ._legacy import *  # noqa: F401,F403 — facade re-export
from ._legacy import (  # noqa: F401 — explicit symbols for IDE/static analysis
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    EngineeringRouteContext,
    EngineeringRouteResult,
    EngineeringThreadContinuation,
    EngineeringThreadKickoff,
    _GATEWAY_CLARIFICATION_CONTEXT,
    _attach_recall_coverage,
    _can_save_to_obsidian,
    _clarification_context_key,
    _clear_clarification_context,
    _coerce_outcome,
    _emit_work_report_preview,
    _extract_session_id_from_router_text,
    _handle_clarification_selection,
    _handle_join_or_append,
    _looks_like_new_work_selection,
    _maybe_await,
    _optional_bool_env,
    _persist_lifecycle_mode,
    _persist_thread_id,
    _recall_clarification_candidates,
    _recall_clarification_canonical_prompt,
    _remember_clarification_candidates,
    _research_loop_blocked_by_command_only,
    _run_research_loop_hook,
    _run_runtime_preflight,
    _try_select_candidate,
    detect_confirmation_signal,
    extract_message_attachments,
    is_coding_approval_phrase,
    is_coding_proposal_request,
    is_engineering_channel,
    make_default_research_loop,
    persist_research_forum_status,
    route_engineering_message,
    should_continue_existing_thread,
    should_start_new_thread,
)
