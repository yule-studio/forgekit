"""Meeting minutes — deterministic team-discussion summary.

After a research + deliberation round, the tech-lead needs to hand
the user a "회의록" they can read like an actual meeting note (who
showed up, what they covered, what's still open, what we decided
next). This module renders that artefact deterministically from
``session.extra`` data the existing runtime already records, so the
gateway can produce minutes even when no LLM runner is wired —
runners can splice in richer prose later by reading the same
``MeetingMinutes`` dataclass.

Input expectations (read from ``session.extra``):

  * ``active_research_roles`` (list[str]) — Phase 1 selection result
    used as the participant list. Falls back to ``played_roles`` /
    deterministic role sequence if missing.
  * ``research_pack`` (dict | ``ResearchPack``) — provides the
    ``role_summaries`` excerpt + reference count.
  * ``research_synthesis`` (dict, schema-versioned) — agreements
    (``consensus``), open research (``open_research``), and explicit
    user decisions (``user_decisions_needed``) flow into the minutes.
  * ``role_turns`` (list[dict]) — best-effort extraction of risks /
    disagreements when role bots emit them; absent entries are
    skipped, never raised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple


__all__ = (
    "MeetingMinutes",
    "build_meeting_minutes",
    "format_meeting_minutes_markdown",
)


@dataclass(frozen=True)
class MeetingMinutes:
    """Structured "회의록" for one engineering session.

    Fields follow the standard Korean operations meeting note layout
    (참가자 / 안건 / 합의 / 미합의 / 위험 / 미해결 질문 / 다음 액션)
    so downstream renderers can hit either Discord (compact) or
    Obsidian (long-form) without translating field names.
    """

    session_id: Optional[str]
    topic: str
    participants: Tuple[str, ...]
    role_summaries: Mapping[str, str] = field(default_factory=dict)
    discussed_options: Tuple[str, ...] = ()
    agreements: Tuple[str, ...] = ()
    disagreements: Tuple[str, ...] = ()
    risks: Tuple[str, ...] = ()
    open_questions: Tuple[str, ...] = ()
    next_actions: Tuple[str, ...] = ()
    selection_source: Optional[str] = None
    selection_reasons: Mapping[str, str] = field(default_factory=dict)


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


def _resolve_participants(extra: Mapping[str, Any]) -> Tuple[str, ...]:
    """Pick the participant list with a clear precedence order:
    Phase 1 active_research_roles → recorded played_roles → empty
    (caller may fall back to ``role_sequence``)."""

    raw = extra.get("active_research_roles")
    participants = _coerce_str_list(raw)
    if participants:
        return participants
    raw = extra.get("played_roles")
    return _coerce_str_list(raw)


def _resolve_role_summaries(extra: Mapping[str, Any]) -> Mapping[str, str]:
    """Pull per-role summary text from the research pack when present.

    Accepts both the dict shape persisted in ``session.extra`` and a
    ``ResearchPack`` instance — the conversation layer round-trips
    through dicts for storage but in-process callers may hand the
    dataclass directly.
    """

    pack = extra.get("research_pack")
    if pack is None:
        return {}
    summaries: dict[str, str] = {}
    raw = (
        pack.get("role_summaries") if isinstance(pack, Mapping) else getattr(pack, "role_summaries", None)
    )
    if not raw:
        return {}
    if isinstance(raw, Mapping):
        for role, text in raw.items():
            if isinstance(role, str) and text is not None:
                summaries[role] = str(text).strip()
    return {role: text for role, text in summaries.items() if text}


def _resolve_synthesis_lists(extra: Mapping[str, Any]) -> Tuple[
    Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]
]:
    """Return (agreements, open_questions, user_decisions_needed) from
    a persisted research_synthesis dict. Missing keys → empty tuples
    so the minutes never fail closed."""

    synthesis = extra.get("research_synthesis")
    if not isinstance(synthesis, Mapping):
        return ((), (), ())
    consensus_text = str(synthesis.get("consensus") or "").strip()
    agreements = (consensus_text,) if consensus_text else ()
    open_questions = _coerce_str_list(synthesis.get("open_research"))
    user_decisions = _coerce_str_list(synthesis.get("user_decisions_needed"))
    return agreements, open_questions, user_decisions


def _extract_risks_from_turns(extra: Mapping[str, Any]) -> Tuple[str, ...]:
    """Best-effort risk extraction from recorded role turns.

    The runtime stores per-role takes under various keys (``role_takes``
    / ``role_turns``) depending on the deliberation stage. We sniff
    for an explicit ``risks`` list on each entry; runners that don't
    populate it simply yield nothing.
    """

    risks: list[str] = []
    seen: set[str] = set()
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
                for risk in _coerce_str_list(entry.get(risk_key)):
                    if risk not in seen:
                        seen.add(risk)
                        risks.append(risk)
    return tuple(risks)


def build_meeting_minutes(
    *,
    session_id: Optional[str],
    topic: str,
    extra: Mapping[str, Any],
    fallback_participants: Sequence[str] = (),
) -> MeetingMinutes:
    """Render :class:`MeetingMinutes` from session.extra data.

    *topic* should be the canonical task description (e.g. session
    prompt or ``canonical_prompt_override``) — never a routing-command
    phrase. Caller is responsible for passing the right text.

    *fallback_participants* is consulted when neither
    ``active_research_roles`` nor ``played_roles`` is recorded — the
    gateway typically passes ``session.role_sequence`` here.
    """

    extra_map = dict(extra or {})
    participants = _resolve_participants(extra_map)
    if not participants and fallback_participants:
        participants = _coerce_str_list(fallback_participants)

    role_summaries = _resolve_role_summaries(extra_map)
    agreements, open_questions, user_decisions = _resolve_synthesis_lists(extra_map)
    risks = _extract_risks_from_turns(extra_map)

    selection_source = extra_map.get("role_selection_source")
    selection_reasons_raw = extra_map.get("role_selection_reasons") or {}
    selection_reasons: dict[str, str] = {}
    if isinstance(selection_reasons_raw, Mapping):
        for role, reason in selection_reasons_raw.items():
            if isinstance(role, str) and reason is not None:
                selection_reasons[role] = str(reason)

    next_actions: Tuple[str, ...] = ()
    if user_decisions:
        # User decisions get promoted to "next actions" so the meeting
        # note ends with a clear hand-off line per the team's spec.
        next_actions = user_decisions

    discussed_options = _coerce_str_list(extra_map.get("discussed_options"))

    return MeetingMinutes(
        session_id=session_id,
        topic=topic.strip() if isinstance(topic, str) else "",
        participants=participants,
        role_summaries=role_summaries,
        discussed_options=discussed_options,
        agreements=agreements,
        disagreements=_coerce_str_list(extra_map.get("disagreements")),
        risks=risks,
        open_questions=open_questions,
        next_actions=next_actions,
        selection_source=str(selection_source) if selection_source else None,
        selection_reasons=selection_reasons,
    )


def format_meeting_minutes_markdown(minutes: MeetingMinutes) -> str:
    """Render *minutes* as a Markdown body suitable for Discord
    forum posts and the Obsidian meeting-note kind.

    Sections: 안건 → 참가자 → 역할별 요약 → 합의 → 미해결 질문 →
    위험 → 다음 액션. Empty sections are dropped so the note doesn't
    stay padded with empty headings.
    """

    lines: list[str] = []
    if minutes.session_id:
        lines.append(f"**Session**: `{minutes.session_id}`")
    if minutes.topic:
        lines.append(f"**안건**: {minutes.topic}")
    if minutes.participants:
        joined = ", ".join(minutes.participants)
        lines.append(f"**참가자**: {joined}")
        if minutes.selection_source:
            lines.append(f"_(participant selection: {minutes.selection_source})_")
    if minutes.role_summaries:
        lines.append("")
        lines.append("**역할별 요약**")
        for role, text in minutes.role_summaries.items():
            lines.append(f"- `{role}` — {text}")
    if minutes.agreements:
        lines.append("")
        lines.append("**합의 / consensus**")
        for item in minutes.agreements:
            lines.append(f"- {item}")
    if minutes.disagreements:
        lines.append("")
        lines.append("**미합의**")
        for item in minutes.disagreements:
            lines.append(f"- {item}")
    if minutes.risks:
        lines.append("")
        lines.append("**위험**")
        for item in minutes.risks:
            lines.append(f"- {item}")
    if minutes.open_questions:
        lines.append("")
        lines.append("**미해결 질문 / open research**")
        for item in minutes.open_questions:
            lines.append(f"- {item}")
    if minutes.next_actions:
        lines.append("")
        lines.append("**다음 액션**")
        for item in minutes.next_actions:
            lines.append(f"- {item}")
    return "\n".join(lines).strip()
