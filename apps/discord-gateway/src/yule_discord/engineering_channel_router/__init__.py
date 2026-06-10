"""Engineering channel router — package facade (P0-P decomposition complete).

Historical monolith (``engineering_channel_router.py``, 3316 lines)
split into 11 responsibility-aligned modules per the audit at
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
  * :mod:`.main`                — ``route_engineering_message`` orchestration
                                    + clarification CREATE/JOIN drivers.

This ``__init__.py`` is the **thin facade** — re-exports the public API
so ``from yule_orchestrator.discord.engineering_channel_router import X``
keeps working for every external import site (bot.py, commands.py,
supervisor.py, all test fixtures) without source changes.
"""

from __future__ import annotations

# Per-module explicit re-exports. The main module owns
# ``route_engineering_message`` plus the two clarification drivers; all
# other facade symbols pull directly from their responsibility-aligned
# siblings so import-time graph stays clean (no wildcard imports).
# Canonical dataclasses + type aliases live in .models (P0-P step 3).
from .models import (  # noqa: F401 — facade re-export
    ConversationFn,
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    EngineeringRouteContext,
    EngineeringRouteResult,
    EngineeringThreadContinuation,
    EngineeringThreadKickoff,
    ExtractPromptFn,
    IntakeFn,
    ResearchLoopFn,
    SendChunksFn,
    ThreadContinuationFn,
    ThreadKickoffFn,
)
# Env + coercion + message parsing + recall coverage live in .utils (P0-P step 4).
from .utils import (  # noqa: F401 — facade re-export
    _attach_recall_coverage,
    _maybe_await,
    _optional_bool_env,
    extract_message_attachments,
    extract_user_links_from_message,
)
# Channel + confirmation + continuation predicates live in .intent_detection
# (P0-P step 5).
from .intent_detection import (  # noqa: F401 — facade re-export
    detect_confirmation_signal,
    is_engineering_channel,
    should_continue_existing_thread,
    should_start_new_thread,
)
# session.extra mutations + load helpers live in .session_persistence (P0-P step 6).
from .session_persistence import (  # noqa: F401 — facade re-export
    _load_session_by_id,
    _most_recent_session,
    _persist_lifecycle_mode,
    _persist_thread_id,
)
# Backward-compat alias: tests + bot.py imported the legacy alias that
# pointed at lifecycle.resolver.extract_explicit_session_id.
from yule_orchestrator.agents.lifecycle.resolver import (  # noqa: F401 — facade re-export
    extract_explicit_session_id as _extract_session_id_from_router_text,
)
# Coding 권한 / 승인 gate (P0-P step 7).
from .coding_gate import (  # noqa: F401 — facade re-export
    _run_coding_authorization_gate,
)
# Obsidian 저장 gate (P0-P step 8).
from .obsidian_gate import (  # noqa: F401 — facade re-export
    _can_save_to_obsidian,
    _run_obsidian_approval_gate,
)
# Research loop hook + P0-K guard + forum status persistence (P0-P step 9).
from .research_loop import (  # noqa: F401 — facade re-export
    _research_loop_blocked_by_command_only,
    _run_research_loop_hook,
    make_default_research_loop,
    persist_research_forum_status,
)

# Work report + clarification display + outcome coercion (P0-P step 10).
from .reporting import (  # noqa: F401 — facade re-export
    _coerce_outcome,
    _emit_work_report_preview,
)

# Runtime preflight + join/append (P0-P step 11).
from .runtime_preflight import (  # noqa: F401 — facade re-export
    _handle_join_or_append,
    _run_runtime_preflight,
)

# Main orchestration entry + clarification drivers (P0-P step 12).
from .main import (  # noqa: F401 — facade re-export
    _drive_clarification_create_new_work,
    _handle_clarification_selection,
    is_coding_approval_phrase,
    is_coding_proposal_request,
    route_engineering_message,
)
# Clarification cache symbols — already in engineering.clarification.
from ..engineering.clarification import (  # noqa: F401 — facade re-export
    GATEWAY_CLARIFICATION_CONTEXT as _GATEWAY_CLARIFICATION_CONTEXT,
    clarification_context_key as _clarification_context_key,
    clear_clarification_context as _clear_clarification_context,
    looks_like_new_work_selection as _looks_like_new_work_selection,
    recall_clarification_candidates as _recall_clarification_candidates,
    recall_clarification_canonical_prompt as _recall_clarification_canonical_prompt,
    remember_clarification_candidates as _remember_clarification_candidates,
    try_select_candidate as _try_select_candidate,
)
