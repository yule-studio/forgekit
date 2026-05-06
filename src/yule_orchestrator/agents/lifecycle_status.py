"""Engineering lifecycle readiness — single source of truth.

Stabilisation Phase 6 surfaced the same readiness booleans (research
pack present, source count, role coverage, synthesis present) being
re-derived in three places:

  - :mod:`agents.work_report` (``build_work_report``'s status gate)
  - :mod:`discord.engineering_channel_router._can_save_to_obsidian`
  - :mod:`discord.engineering_conversation.format_status_diagnostic_response`

When the formulas drift apart you get the live-MVP regression where
the work-report says "ready" but the Obsidian gate refuses to save.
This module consolidates the pure read-only computations onto
``session.extra`` so callers pull from one canonical implementation.

All functions are deterministic and side-effect free. They never
write to the session — that's :mod:`agents.lifecycle_persistence`'s
responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple


__all__ = (
    "LifecycleStatus",
    "RESEARCH_STATUS_MISSING",
    "RESEARCH_STATUS_INSUFFICIENT",
    "RESEARCH_STATUS_READY",
    "REPORT_STATUS_INTERIM",
    "REPORT_STATUS_INSUFFICIENT",
    "REPORT_STATUS_READY",
    "REPORT_STATUS_FINAL",
    "compute_role_coverage",
    "compute_research_source_count",
    "compute_research_status",
    "compute_report_status",
    "can_generate_final_work_report",
    "can_write_obsidian_record",
)


# Research-side state names. Phase 2 stabilisation persists
# ``session.extra['research_status']`` using these labels.
RESEARCH_STATUS_MISSING: str = "missing"
RESEARCH_STATUS_INSUFFICIENT: str = "insufficient"
RESEARCH_STATUS_READY: str = "ready"


# Work-report state names. Mirrors ``agents.work_report`` constants
# but kept here as the canonical authority — :mod:`agents.work_report`
# imports these so the two stay in lockstep.
REPORT_STATUS_INTERIM: str = "interim"
REPORT_STATUS_INSUFFICIENT: str = "insufficient"
REPORT_STATUS_READY: str = "ready"
REPORT_STATUS_FINAL: str = "final"


@dataclass(frozen=True)
class LifecycleStatus:
    """Summary of where a session sits in the engineering lifecycle.

    All fields are derived from ``session.extra`` only — no SQLite
    round-trip, no Discord API. Returned by
    :func:`compute_lifecycle_status` and consumed by callers that
    need to surface a coherent picture (Discord status, work-report
    gate, Obsidian gate).
    """

    research_status: str
    source_count: int
    has_research_pack: bool
    has_synthesis: bool
    active_roles: Tuple[str, ...]
    played_roles: Tuple[str, ...]
    missing_roles: Tuple[str, ...]
    report_status: str
    can_save_obsidian: bool
    obsidian_block_reason: Optional[str]


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def _coerce_str_list(value: Any) -> Tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    try:
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text:
                out.append(text)
        return tuple(out)
    except TypeError:
        return ()


def _safe_extra(session: Any) -> Mapping[str, Any]:
    if session is None:
        return {}
    try:
        raw = getattr(session, "extra", None) or {}
        if isinstance(raw, Mapping):
            return raw
        return dict(raw)  # best effort for non-Mapping iterables
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# Role coverage
# ---------------------------------------------------------------------------


def compute_role_coverage(
    active_roles: Sequence[str],
    played_roles: Sequence[str],
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Return ``(played_subset, missing_subset)`` against *active_roles*.

    ``played_subset`` keeps the order of *active_roles* but only the
    members that actually contributed; ``missing_subset`` is the
    complement (also in *active_roles* order). Empty *active_roles*
    yields two empty tuples — callers fall back to legacy semantics
    rather than treating "no active list" as "everything missing".
    """

    active_clean = _coerce_str_list(active_roles)
    if not active_clean:
        return ((), ())
    played_set = {role for role in _coerce_str_list(played_roles)}
    played_subset = tuple(role for role in active_clean if role in played_set)
    missing_subset = tuple(role for role in active_clean if role not in played_set)
    return played_subset, missing_subset


def _resolve_played_roles(extra: Mapping[str, Any]) -> Tuple[str, ...]:
    """Pull played role list from session.extra. Mirrors the lookup in
    work_report.build_work_report but available to non-work-report
    callers too."""

    played: Tuple[str, ...] = _coerce_str_list(extra.get("played_roles"))
    if not played:
        played = _coerce_str_list(extra.get("team_played_roles"))
    team = extra.get("team_conversation")
    if isinstance(team, Mapping):
        played = played + _coerce_str_list(team.get("played_roles"))
    # dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for role in played:
        if role not in seen:
            seen.add(role)
            out.append(role)
    return tuple(out)


# ---------------------------------------------------------------------------
# Research state
# ---------------------------------------------------------------------------


def compute_research_source_count(session: Any) -> int:
    """Best-effort source count.

    Phase 2 stamped ``research_source_count`` directly. Older rows
    from before that fix didn't, so we fall back to inspecting the
    persisted ``research_pack['sources']`` list.
    """

    extra = _safe_extra(session)
    raw = extra.get("research_source_count")
    if isinstance(raw, (int, float)):
        return int(raw)
    pack = extra.get("research_pack")
    if isinstance(pack, Mapping):
        sources = pack.get("sources")
        try:
            return int(len(sources)) if sources is not None else 0
        except TypeError:
            return 0
    return 0


def compute_research_status(session: Any) -> Tuple[str, int, bool]:
    """Return ``(research_status, source_count, has_pack)``.

    ``research_status`` mirrors the persisted Phase 2 stamp when
    present; otherwise it's derived from pack + source count so older
    sessions still classify cleanly.
    """

    extra = _safe_extra(session)
    pack = extra.get("research_pack")
    has_pack = isinstance(pack, Mapping) and bool(pack)
    source_count = compute_research_source_count(session)
    persisted = str(extra.get("research_status") or "").strip().lower()

    if persisted in (RESEARCH_STATUS_INSUFFICIENT, RESEARCH_STATUS_READY):
        return persisted, source_count, has_pack

    if has_pack and source_count > 0:
        return RESEARCH_STATUS_READY, source_count, has_pack
    if has_pack:
        return RESEARCH_STATUS_INSUFFICIENT, source_count, has_pack
    return RESEARCH_STATUS_MISSING, source_count, False


# ---------------------------------------------------------------------------
# Synthesis presence
# ---------------------------------------------------------------------------


def has_synthesis(session: Any) -> bool:
    extra = _safe_extra(session)
    synthesis = extra.get("research_synthesis")
    if not isinstance(synthesis, Mapping):
        return False
    consensus = synthesis.get("consensus")
    return isinstance(consensus, str) and bool(consensus.strip())


# ---------------------------------------------------------------------------
# Report status
# ---------------------------------------------------------------------------


def compute_report_status(session: Any) -> Tuple[str, Tuple[str, ...]]:
    """Return ``(report_status, missing_roles)`` for *session*.

    Same gate as :func:`agents.work_report.build_work_report`. Pulled
    here so :mod:`agents.work_report` can import the canonical
    implementation and the Discord / Obsidian layers don't have to
    re-derive it.
    """

    extra = _safe_extra(session)
    research_status, source_count, has_pack = compute_research_status(session)
    explicit_research = str(extra.get("research_status") or "").strip().lower()

    if (
        not has_pack
        or source_count <= 0
        or explicit_research == RESEARCH_STATUS_INSUFFICIENT
    ):
        return REPORT_STATUS_INSUFFICIENT, _coerce_str_list(
            extra.get("research_missing_roles")
        )

    active = _coerce_str_list(extra.get("active_research_roles"))
    played = _resolve_played_roles(extra)
    _, missing = compute_role_coverage(active, played)

    if missing or not has_synthesis(session):
        return REPORT_STATUS_INTERIM, missing
    return REPORT_STATUS_READY, ()


# ---------------------------------------------------------------------------
# Public lifecycle gates
# ---------------------------------------------------------------------------


def can_generate_final_work_report(session: Any) -> Tuple[bool, Optional[str]]:
    """Whether a *final* (not interim/insufficient) work report can be
    emitted for *session*. Returns ``(True, None)`` or
    ``(False, "<korean reason>")``.
    """

    status, missing = compute_report_status(session)
    if status == REPORT_STATUS_READY:
        return True, None
    if status == REPORT_STATUS_INSUFFICIENT:
        return False, "research_pack 미수집 / 자료 0건"
    if missing:
        return (
            False,
            "역할 토의 미완료 ("
            + ", ".join(missing)
            + ")",
        )
    if not has_synthesis(session):
        return False, "tech-lead synthesis 미작성"
    return False, "lifecycle 미완료"


def can_write_obsidian_record(session: Any) -> Tuple[bool, Optional[str]]:
    """Whether the gateway can run Obsidian write for *session*.

    Stricter than :func:`can_generate_final_work_report` only when the
    work_report dict is already persisted with an explicit status —
    otherwise we accept any session that has at least one source so
    legacy approval flows (without the Phase 3 status field) keep
    working. Returns ``(True, None)`` / ``(False, "<korean reason>")``.
    """

    if session is None:
        return False, "세션 객체를 찾지 못했어요"

    research_status, source_count, _has_pack = compute_research_status(session)
    # Two accept paths (mirrors the pre-refactor router gate):
    #   1. Phase 2 stamped research_status="ready" (legacy rows that
    #      kept the stamp but not the pack dict still get saved).
    #   2. source_count > 0 — the pack dict has at least one source.
    # ``has_pack=True`` with sources=0 stays blocked because that's
    # exactly the live-MVP "자료 부족" state we want to refuse.
    research_ok = (
        research_status == RESEARCH_STATUS_READY or source_count > 0
    )
    if not research_ok:
        return False, "research_pack 미수집 (자료 0건) 상태라 저장할 수 없어요"

    extra = _safe_extra(session)
    work_report = extra.get("work_report")
    if isinstance(work_report, Mapping):
        wr_status = str(work_report.get("status") or "").strip().lower()
        if wr_status in (REPORT_STATUS_INSUFFICIENT, REPORT_STATUS_INTERIM):
            missing = work_report.get("missing_roles") or []
            if isinstance(missing, list) and missing:
                return (
                    False,
                    "역할 토의 미완료 ("
                    + ", ".join(str(r) for r in missing)
                    + ") 라 저장할 수 없어요",
                )
            return (
                False,
                f"work_report status={wr_status} 라 final 저장 단계가 아니에요",
            )
    return True, None


def compute_lifecycle_status(session: Any) -> LifecycleStatus:
    """Bundle every lifecycle readiness datum into a single struct."""

    extra = _safe_extra(session)
    research_status, source_count, has_pack = compute_research_status(session)
    active = _coerce_str_list(extra.get("active_research_roles"))
    played = _resolve_played_roles(extra)
    _, missing = compute_role_coverage(active, played)
    report_status, missing_from_report = compute_report_status(session)
    final_missing = missing or missing_from_report
    can_save, save_reason = can_write_obsidian_record(session)
    return LifecycleStatus(
        research_status=research_status,
        source_count=source_count,
        has_research_pack=has_pack and source_count > 0,
        has_synthesis=has_synthesis(session),
        active_roles=active,
        played_roles=played,
        missing_roles=tuple(final_missing),
        report_status=report_status,
        can_save_obsidian=can_save,
        obsidian_block_reason=save_reason,
    )
