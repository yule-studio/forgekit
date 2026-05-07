"""Work report — the gateway's "업무 보고서".

After research + deliberation closes, the user wants a single
artefact that says *what the team studied, what they recommend, what
risks remain, and whether code changes are needed*. This module
renders that report deterministically from data the runtime already
records on ``session.extra`` so the gateway can show a preview even
without an LLM runner — runners can splice in richer prose later by
reading the same ``WorkReport`` dataclass.

Inputs (read from ``session.extra``):

  * ``active_research_roles`` — Phase 1 selection (participants).
  * ``research_pack`` — gives ``executive_summary``-style topic line
    and ``reference_count``.
  * ``research_synthesis`` — ``consensus`` becomes the
    recommendation; ``open_research`` becomes risks.
  * ``role_selection_reasons`` — per-role "why participated" line.
  * ``coding_proposal`` / ``coding_job`` — when present, the report
    flags ``requires_code_change=True`` and surfaces the recommended
    executor.
  * ``collection_outcome.stop_reason`` (when caller passes the
    outcome) — tells the user whether research closed sufficiently
    or hit a budget / progress wall.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple


__all__ = (
    "WorkReport",
    "WORK_REPORT_STATUS_INTERIM",
    "WORK_REPORT_STATUS_INSUFFICIENT",
    "WORK_REPORT_STATUS_READY",
    "WORK_REPORT_STATUS_FINAL",
    "build_work_report",
    "format_work_report_markdown",
)


# Lifecycle status — Phase 3 stabilisation. The gateway used to emit
# a single "final" report regardless of whether research_pack /
# synthesis / role coverage actually closed. The status field lets
# the Discord preview + Obsidian gate distinguish:
#
# - ``interim`` — research is still in progress (pack but missing
#   coverage / synthesis); preview only, do not save to Obsidian.
# - ``insufficient`` — research_pack absent or 0 sources;
#   "자료 부족" path — never present as final, never invite
#   coding authorization.
# - ``ready`` — research_pack + active role coverage + synthesis are
#   all present; the report is ready for the user but still gates
#   on user approval before Obsidian write.
# - ``final`` — caller (gateway) explicitly promoted the ``ready``
#   report after Obsidian save / coding handoff. Set by callers, not
#   by ``build_work_report`` itself.
WORK_REPORT_STATUS_INTERIM: str = "interim"
WORK_REPORT_STATUS_INSUFFICIENT: str = "insufficient"
WORK_REPORT_STATUS_READY: str = "ready"
WORK_REPORT_STATUS_FINAL: str = "final"


@dataclass(frozen=True)
class WorkReport:
    """Structured "업무 보고서" the gateway emits at lifecycle close.

    Designed to be both Discord-renderable (via
    :func:`format_work_report_markdown`) and Obsidian-persistable
    (Phase 5 wires the work-report kind so this dataclass round-
    trips into the vault).
    """

    session_id: Optional[str]
    title: str
    canonical_prompt: str
    executive_summary: str
    research_summary: str
    tech_lead_recommendation: str
    role_decisions: Mapping[str, str] = field(default_factory=dict)
    risks: Tuple[str, ...] = ()
    proposed_next_steps: Tuple[str, ...] = ()
    requires_code_change: bool = False
    recommended_executor_role: Optional[str] = None
    approval_request: Optional[str] = None
    participants: Tuple[str, ...] = ()
    reference_count: int = 0
    research_stop_reason: Optional[str] = None
    under_covered_roles: Tuple[str, ...] = ()
    # Phase 3 stabilisation — explicit lifecycle status. See module
    # docstring for the four allowed values. Defaults to interim
    # because that's the safest reading: until the caller proves
    # research_pack + coverage + synthesis are all in, treat the
    # report as a snapshot, not a deliverable.
    status: str = WORK_REPORT_STATUS_INTERIM
    # Roles the tech-lead picked but who didn't speak this lifecycle
    # (active set minus played_roles set). Surfaced so the Discord
    # preview + Obsidian note can show "토의 미완료 역할: …" instead
    # of pretending coverage is fine.
    missing_roles: Tuple[str, ...] = ()
    has_research_pack: bool = False
    has_synthesis: bool = False


def _coerce_str_list(value: Any) -> Tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    try:
        out = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return tuple(out)
    except TypeError:
        return ()


def _slugify_title(text: str, *, max_chars: int = 60) -> str:
    """Trim *text* to ``max_chars`` for use as a report title.

    Keeps Korean / English mixed phrasing readable; strips routing-
    command prefixes the live MVP loop occasionally leaves behind
    (``[Research]``, leading ``- ``…) so the rendered title is the
    actual task, not the marker.
    """

    if not text:
        return "untitled work report"
    cleaned = text.strip()
    # Strip a leading [Research] / [research] tag so the title focuses
    # on the topic; the canonical_prompt field still carries the full
    # original text for readers that want it.
    for prefix in ("[Research]", "[research]", "[RESEARCH]"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip(" :—-")
            break
    cleaned = cleaned.strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1].rstrip() + "…"
    return cleaned or "untitled work report"


def _resolve_participants(extra: Mapping[str, Any]) -> Tuple[str, ...]:
    raw = extra.get("active_research_roles")
    if raw:
        return _coerce_str_list(raw)
    raw = extra.get("played_roles")
    return _coerce_str_list(raw)


def _resolve_reference_count(extra: Mapping[str, Any]) -> int:
    pack = extra.get("research_pack")
    if not pack:
        return 0
    raw = (
        pack.get("sources") if isinstance(pack, Mapping) else getattr(pack, "sources", None)
    )
    if raw is None:
        return 0
    try:
        return len(raw)
    except TypeError:
        return 0


def _resolve_recommendation(extra: Mapping[str, Any]) -> str:
    synthesis = extra.get("research_synthesis")
    if isinstance(synthesis, Mapping):
        consensus = synthesis.get("consensus")
        if consensus:
            return str(consensus).strip()
    pack = extra.get("research_pack")
    if isinstance(pack, Mapping):
        consensus = pack.get("consensus")
        if consensus:
            return str(consensus).strip()
    return ""


def _resolve_risks(extra: Mapping[str, Any]) -> Tuple[str, ...]:
    """Combine open_research from synthesis + per-turn risks."""

    risks: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        for item in _coerce_str_list(value):
            if item not in seen:
                seen.add(item)
                risks.append(item)

    synthesis = extra.get("research_synthesis")
    if isinstance(synthesis, Mapping):
        _add(synthesis.get("open_research"))

    for key in ("role_takes", "role_turns", "deliberation_turns"):
        entries = extra.get(key)
        if not entries:
            continue
        if isinstance(entries, Mapping):
            iterable: Sequence[Any] = list(entries.values())
        else:
            try:
                iterable = list(entries)
            except TypeError:
                iterable = ()
        for entry in iterable:
            if not isinstance(entry, Mapping):
                continue
            for risk_key in ("risks", "risk", "위험"):
                _add(entry.get(risk_key))

    return tuple(risks)


def _resolve_next_steps(extra: Mapping[str, Any]) -> Tuple[str, ...]:
    """Derive the "next action" line from synthesis todos /
    user_decisions_needed. Keeps the user_decisions first because
    they're what actually unblocks the team."""

    synthesis = extra.get("research_synthesis")
    if not isinstance(synthesis, Mapping):
        return ()
    user_decisions = _coerce_str_list(synthesis.get("user_decisions_needed"))
    todos = _coerce_str_list(synthesis.get("todos"))
    combined: list[str] = []
    seen: set[str] = set()
    for item in (*user_decisions, *todos):
        if item not in seen:
            seen.add(item)
            combined.append(item)
    return tuple(combined)


def _resolve_code_change(extra: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
    """Inspect coding_job / coding_proposal to decide whether code
    change is needed and which executor was recommended."""

    job = extra.get("coding_job")
    if isinstance(job, Mapping):
        executor = job.get("executor_role")
        return True, str(executor) if executor else None
    proposal = extra.get("coding_proposal")
    if isinstance(proposal, Mapping):
        executor = proposal.get("executor_role")
        return True, str(executor) if executor else None
    return False, None


def build_work_report(
    *,
    session_id: Optional[str],
    canonical_prompt: str,
    extra: Mapping[str, Any],
    research_stop_reason: Optional[str] = None,
    under_covered_roles: Sequence[str] = (),
    fallback_participants: Sequence[str] = (),
) -> WorkReport:
    """Render :class:`WorkReport` from session.extra data.

    *canonical_prompt* must be the actual task body (session prompt
    or canonical_prompt_override), never a routing-command phrase.
    *research_stop_reason* / *under_covered_roles* come from the
    final ``CollectionOutcome`` so the report can explain *why*
    research closed.

    Phase 3: the resulting report carries an explicit ``status``
    derived from research_pack presence + role coverage + synthesis.
    The Discord preview and Obsidian gate read this field instead of
    re-deriving the same booleans.
    """

    extra_map = dict(extra or {})
    canonical = (canonical_prompt or "").strip()

    participants = _resolve_participants(extra_map)
    if not participants and fallback_participants:
        participants = _coerce_str_list(fallback_participants)

    requires_change, executor = _resolve_code_change(extra_map)
    recommendation = _resolve_recommendation(extra_map)
    risks = _resolve_risks(extra_map)
    next_steps = _resolve_next_steps(extra_map)

    role_decisions: dict[str, str] = {}
    reasons = extra_map.get("role_selection_reasons")
    if isinstance(reasons, Mapping):
        for role, reason in reasons.items():
            if isinstance(role, str) and reason is not None:
                role_decisions[role] = str(reason)

    pack = extra_map.get("research_pack")
    research_summary = ""
    if isinstance(pack, Mapping):
        for key in ("summary", "executive_summary", "topic"):
            value = pack.get(key)
            if value:
                research_summary = str(value).strip()
                if research_summary:
                    break

    executive_summary = recommendation or research_summary or canonical
    if len(executive_summary) > 280:
        executive_summary = executive_summary[:277].rstrip() + "…"

    approval_request: Optional[str] = None

    # Phase 3 — derive lifecycle status. A report can only graduate to
    # ``ready`` when (a) research_pack exists with ≥1 source, (b)
    # active_research_roles set ⊆ played_roles set (every role the
    # tech-lead picked actually contributed), and (c) a synthesis is
    # recorded. Otherwise we surface ``insufficient`` or ``interim``
    # so the caller doesn't ship a hollow deliverable.
    has_pack, source_count = _resolve_pack_state(extra_map)
    has_synthesis = _resolve_has_synthesis(extra_map)
    missing_roles = _resolve_missing_roles(extra_map)
    explicit_research_status = str(extra_map.get("research_status") or "").strip().lower()

    if not has_pack or source_count <= 0 or explicit_research_status == "insufficient":
        status = WORK_REPORT_STATUS_INSUFFICIENT
    elif missing_roles or not has_synthesis:
        status = WORK_REPORT_STATUS_INTERIM
    else:
        status = WORK_REPORT_STATUS_READY

    # Coding approval CTA only fires when the lifecycle is at least
    # READY. Earlier statuses ship a "조사 진행 중" banner instead so
    # the user doesn't get nudged to approve coding on an empty
    # research_pack.
    if requires_change and status == WORK_REPORT_STATUS_READY:
        approval_request = (
            "코드 수정 작업이 포함되어 있습니다. 진행하려면 "
            "`수정 승인` 또는 `구현 시작`이라고 답해 주세요."
        )
    elif status == WORK_REPORT_STATUS_INSUFFICIENT:
        approval_request = (
            "자료 부족 상태라 final 보고서로 제출할 수 없습니다. "
            "자료를 더 모으거나 `리서치만` 같은 후속 지시를 주세요."
        )
    elif status == WORK_REPORT_STATUS_INTERIM:
        if missing_roles:
            approval_request = (
                "역할 토의 미완료 — "
                + ", ".join(missing_roles)
                + " 의견이 빠져 있어 final 보고서로 제출할 수 없습니다."
            )
        else:
            approval_request = (
                "tech-lead synthesis 미작성 상태라 final 보고서로 제출할 수 없습니다."
            )

    return WorkReport(
        session_id=session_id,
        title=_slugify_title(canonical),
        canonical_prompt=canonical,
        executive_summary=executive_summary,
        research_summary=research_summary,
        tech_lead_recommendation=recommendation,
        role_decisions=role_decisions,
        risks=risks,
        proposed_next_steps=next_steps,
        requires_code_change=requires_change,
        recommended_executor_role=executor,
        approval_request=approval_request,
        participants=participants,
        reference_count=_resolve_reference_count(extra_map),
        research_stop_reason=str(research_stop_reason) if research_stop_reason else None,
        under_covered_roles=_coerce_str_list(under_covered_roles),
        status=status,
        missing_roles=missing_roles,
        has_research_pack=has_pack and source_count > 0,
        has_synthesis=has_synthesis,
    )


def _resolve_pack_state(extra: Mapping[str, Any]) -> tuple[bool, int]:
    """Return (has_research_pack, source_count) — thin shim over
    :mod:`agents.lifecycle.status` so :mod:`agents.reports.work_report` and
    the Discord / Obsidian readers share one canonical computation.
    Kept as a private wrapper because the older signature is part of
    this module's intra-call contract."""

    from ..lifecycle.status import compute_research_source_count

    count = compute_research_source_count(_StubExtra(extra))
    return (count > 0, count)


class _StubExtra:
    """Adapter so :mod:`agents.lifecycle.status` (which expects a
    session-like object with ``.extra``) can read a bare extras dict
    without callers having to fabricate sessions."""

    __slots__ = ("extra",)

    def __init__(self, extra: Mapping[str, Any]) -> None:
        self.extra = dict(extra or {})


def _resolve_has_synthesis(extra: Mapping[str, Any]) -> bool:
    from ..lifecycle.status import has_synthesis as _has_synthesis

    return _has_synthesis(_StubExtra(extra))


def _resolve_missing_roles(extra: Mapping[str, Any]) -> Tuple[str, ...]:
    """Active_roles − played_roles, computed via the canonical
    :mod:`agents.lifecycle.status` helper.
    """

    from ..lifecycle.status import (
        compute_role_coverage,
        _resolve_played_roles,
    )

    active = _coerce_str_list(extra.get("active_research_roles"))
    if not active:
        return ()
    played = _resolve_played_roles(extra)
    _, missing = compute_role_coverage(active, played)
    return missing


_STOP_REASON_LABELS: Mapping[str, str] = {
    "sufficient": "자료 충분 (sufficient)",
    "budget_exhausted": "budget 소진 (budget_exhausted)",
    "no_progress": "신규 자료 없음 (no_progress)",
    "role_rotation_exhausted": "역할 큐 소진 (role_rotation_exhausted)",
    "no_initial_provider_hit": "초기 검색 결과 없음 (no_initial_provider_hit)",
    "missing_required_source_type": "필수 source 누락 (missing_required_source_type)",
    "user_input_needed": "사용자 입력 필요 (user_input_needed)",
}


_STATUS_LABELS: Mapping[str, str] = {
    WORK_REPORT_STATUS_INTERIM: "interim — 역할 토의 진행 중",
    WORK_REPORT_STATUS_INSUFFICIENT: "insufficient — 자료 부족",
    WORK_REPORT_STATUS_READY: "ready — 사용자 검토 대기",
    WORK_REPORT_STATUS_FINAL: "final — 보고 완료",
}


def format_work_report_markdown(report: WorkReport) -> str:
    """Render *report* as a Discord-friendly Markdown body.

    Section order:
      1. Title + session id + status
      2. 원문 (canonical_prompt)
      3. 요약 / executive_summary
      4. 참가자 + 자료 수
      5. tech-lead recommendation
      6. role decisions (why each role participated)
      7. 위험 / risks
      8. 다음 액션
      9. (optional) coding approval CTA / 미완료 사유
    """

    lines: list[str] = []
    status_label = _STATUS_LABELS.get(report.status, report.status)
    lines.append(
        f"**[engineering-agent] 업무 보고서 — {report.title}** · _{status_label}_"
    )
    if report.session_id:
        lines.append(f"`session {report.session_id}`")
    lines.append("")
    if report.canonical_prompt:
        lines.append(f"**원문**\n> {report.canonical_prompt}")
        lines.append("")
    if report.executive_summary:
        lines.append(f"**요약**\n{report.executive_summary}")
        lines.append("")
    if report.participants:
        joined = ", ".join(report.participants)
        meta = [f"자료 {report.reference_count}건"]
        if report.research_stop_reason:
            label = _STOP_REASON_LABELS.get(
                report.research_stop_reason, report.research_stop_reason
            )
            meta.append(f"stop: {label}")
        if report.under_covered_roles:
            meta.append(
                "부족 role: " + ", ".join(report.under_covered_roles)
            )
        lines.append(f"**참가자**: {joined} · " + " · ".join(meta))
        lines.append("")
    if report.tech_lead_recommendation:
        lines.append("**Tech-lead 권고**")
        lines.append(report.tech_lead_recommendation)
        lines.append("")
    if report.role_decisions:
        lines.append("**역할별 참여 사유**")
        for role, reason in report.role_decisions.items():
            lines.append(f"- `{role}` — {reason}")
        lines.append("")
    if report.risks:
        lines.append("**위험 / open research**")
        for risk in report.risks:
            lines.append(f"- {risk}")
        lines.append("")
    if report.proposed_next_steps:
        lines.append("**다음 액션**")
        for action in report.proposed_next_steps:
            lines.append(f"- {action}")
        lines.append("")
    if report.missing_roles:
        lines.append(
            "**미완료 역할 토의**: "
            + ", ".join(f"`{role}`" for role in report.missing_roles)
        )
        lines.append("")
    if report.status == WORK_REPORT_STATUS_INSUFFICIENT and report.approval_request:
        # Insufficient path: surface the "자료 부족" CTA up top so the
        # user sees the blocker before any other content.
        lines.append("**상태 안내**")
        lines.append(report.approval_request)
        lines.append("")
    elif report.status == WORK_REPORT_STATUS_INTERIM and report.approval_request:
        lines.append("**상태 안내**")
        lines.append(report.approval_request)
        lines.append("")
    if report.requires_code_change and report.status == WORK_REPORT_STATUS_READY:
        lines.append(
            "**코드 수정 필요**: "
            + (
                f"executor 후보 `{report.recommended_executor_role}`"
                if report.recommended_executor_role
                else "executor는 권한 제안 단계에서 결정"
            )
        )
        if report.approval_request:
            lines.append(report.approval_request)
        lines.append("")
    elif report.requires_code_change and report.status != WORK_REPORT_STATUS_READY:
        # Coding flag set but lifecycle isn't ready — show the
        # placeholder without the approval CTA so the user doesn't
        # green-light a half-baked implementation.
        lines.append(
            "**코드 수정 후보**: "
            + (
                f"executor 후보 `{report.recommended_executor_role}` "
                "(lifecycle 완료 후 권한 제안 가능)"
                if report.recommended_executor_role
                else "lifecycle 완료 후 권한 제안 가능"
            )
        )
        lines.append("")
    return "\n".join(line for line in lines).strip()
