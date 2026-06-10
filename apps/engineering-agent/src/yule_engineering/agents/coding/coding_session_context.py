"""Gateway integration helper — P0-H stage 2 (#140).

Single entry point that ties together the 5 stage-2 building blocks:

  1. :func:`agents.git.github_url.parse_github_targets` — URL parse.
  2. :func:`yule_vcs.repo_contract.discover_repo_contract` — RepoContract.
  3. :func:`agents.lifecycle.session_mode.ensure_session_mode` — ask-once mode.
  4. :func:`agents.coding.handoff_packet.build_coding_handoff_packet` — tech-lead envelope.

The gateway calls :func:`prepare_coding_session_context` with the
user's message text, already-extracted URLs, and the session's
current ``extra`` dict (may be empty for a new session). The helper
returns a :class:`CodingSessionContext` describing what should be
merged into ``session.extra``, whether a mode question is pending,
and the composed handoff packet.

Critically: when the session already has a mode set,
:func:`ensure_session_mode` returns ``needs_question=False`` and we
*do not* prompt again. That's the core acceptance contract.

The helper is pure — no SQLite / Discord side effects. Callers
persist the extras dict via the workflow_state layer themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple

from yule_vcs.github_url import GithubTarget, parse_github_targets
from yule_vcs.repo_contract import RepoContract, discover_repo_contract
from ..lifecycle.session_mode import (
    SessionMode,
    SessionModeDecision,
    build_mode_question_text,
    ensure_session_mode,
    parse_mode_hints,
)
from .handoff_packet import CodingHandoffPacket, build_coding_handoff_packet


@dataclass(frozen=True)
class CodingSessionContext:
    """Composed gateway context for a coding-capable request.

    Fields:

      * ``extras_update`` — mapping that callers merge into
        ``session.extra``. Already includes work_mode / topology /
        scope / github_target / repo_contract / repo_contract_summary /
        coding_handoff_packet keys when relevant.
      * ``mode_question`` — Korean prompt to surface when the user
        needs to confirm / override the inferred mode. ``None`` when
        the session already has a decided mode.
      * ``handoff_packet`` — tech-lead envelope (always built; the
        coding executor may ignore it).
      * ``github_target`` / ``repo_contract`` / ``session_mode`` —
        intermediate objects for inspection / tests.
      * ``coding_capable`` — True when at least one of (github_target
        is set / message had repo / message looks coding-like). The
        caller decides whether to even use this context.
    """

    extras_update: Mapping[str, Any]
    mode_question: Optional[str]
    handoff_packet: CodingHandoffPacket
    github_target: Optional[GithubTarget]
    repo_contract: Optional[RepoContract]
    session_mode: SessionMode
    coding_capable: bool
    mode_decision: SessionModeDecision


def prepare_coding_session_context(
    *,
    message_text: str,
    user_links: Sequence[str] = (),
    existing_extra: Optional[Mapping[str, Any]] = None,
    workspace_root: Optional[str] = None,
    gh_cli_runner=None,
    existing_session_id: Optional[str] = None,
    canonical_request: Optional[str] = None,
    discover_contract: bool = True,
) -> CodingSessionContext:
    """Compose the coding session context.

    *message_text* / *user_links* feed URL parsing. *existing_extra*
    is the session's current ``extra`` dict (or ``{}`` for a fresh
    session). *discover_contract* lets tests skip the discovery step
    when they don't care about RepoContract.
    """

    extra_in: dict = dict(existing_extra or {})

    # 1. URL parsing — primary target is the first GitHub URL we recognize.
    targets = parse_github_targets(user_links)
    primary_target: Optional[GithubTarget] = targets[0] if targets else None

    # 2. RepoContract discovery — best-effort, never raises.
    repo_contract: Optional[RepoContract] = None
    if primary_target is not None and discover_contract:
        repo_contract = discover_repo_contract(
            owner=primary_target.owner,
            repo=primary_target.repo,
            workspace_root=workspace_root,
            gh_cli_runner=gh_cli_runner,
        )

    # 3. Mode/topology/scope negotiation — ask-once.
    hints = parse_mode_hints(message_text or "")
    # Auto-bump topology hint to multi_repo when the message mentions
    # multiple distinct owner/repo pairs.
    if hints.get("topology") is None:
        distinct = {(t.owner, t.repo) for t in targets}
        if len(distinct) >= 2:
            hints["topology"] = "multi_repo"
    decision = ensure_session_mode(
        extra_in,
        user_hint_work_mode=hints.get("work_mode"),
        user_hint_topology=hints.get("topology"),
        user_hint_scope=hints.get("scope"),
    )
    mode = decision.mode

    mode_question = build_mode_question_text(decision) if decision.needs_question else None

    # 4. Coding handoff packet (always built; cheap).
    canonical = canonical_request or (message_text or "").strip()
    handoff_packet = build_coding_handoff_packet(
        canonical_request=canonical,
        github_target=primary_target,
        repo_contract=repo_contract,
        work_mode=mode.work_mode,
        topology=mode.topology,
        scope=mode.scope,
        existing_session_id=existing_session_id,
    )

    # 5. Compose extras_update — caller merges into session.extra.
    extras_update: dict = {}
    if decision.changed:
        # ensure_session_mode mutated extra_in in place; surface only
        # the work_mode/topology/scope/decided_at/decided_by keys it set.
        for key in (
            "work_mode",
            "topology",
            "scope",
            "mode_decided_at",
            "mode_decided_by",
        ):
            if key in extra_in:
                extras_update[key] = extra_in[key]
    if primary_target is not None:
        extras_update["github_target"] = dict(primary_target.to_dict())
        if primary_target.kind == "pull_request":
            extras_update["pull_request_number"] = primary_target.number
        if primary_target.branch_or_sha:
            extras_update["branch_name"] = primary_target.branch_or_sha
    if repo_contract is not None:
        extras_update["repo_contract"] = dict(repo_contract.to_dict())
        extras_update["repo_contract_summary"] = repo_contract.summary_line()
    extras_update["coding_handoff_packet"] = dict(handoff_packet.to_dict())

    coding_capable = primary_target is not None

    return CodingSessionContext(
        extras_update=extras_update,
        mode_question=mode_question,
        handoff_packet=handoff_packet,
        github_target=primary_target,
        repo_contract=repo_contract,
        session_mode=mode,
        coding_capable=coding_capable,
        mode_decision=decision,
    )


def merge_into_extra(
    existing_extra: Optional[Mapping[str, Any]],
    extras_update: Mapping[str, Any],
) -> dict:
    """Merge *extras_update* into *existing_extra*, returning a fresh dict.

    Convenience for callers that just want the new shape without
    mutating the original mapping.
    """

    merged = dict(existing_extra or {})
    for key, value in extras_update.items():
        merged[key] = value
    return merged


__all__ = (
    "CodingSessionContext",
    "merge_into_extra",
    "prepare_coding_session_context",
)
