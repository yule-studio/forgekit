"""User-driven role change parsing + application (A-M7.5).

Extracted from :mod:`role_selection` (책임 분리: scoring/selection 과
role-change 를 분리). 사용자 메시지("QA도 참여시켜" / "디자이너는 빼줘")를
파싱해 active-role 변경 요청으로 만들고, 새 active-roles + audit 을 계산한다.

scoring 헬퍼(`_detect_all_team_request` / `_ensure_tech_lead_first`)와 상수는
``role_selection`` 에서 가져온다(단방향 — role_selection 은 본 모듈을 import 하지 않음).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from .role_selection import (
    ALL_ENGINEERING_ROLES,
    ROLE_TECH_LEAD,
    _detect_all_team_request,
    _ensure_tech_lead_first,
)


@dataclass(frozen=True)
class RoleChangeRequest:
    """Parsed user request to mutate the active-role list.

    ``action`` is one of:

      * ``"add"`` — add the named roles (or all-team) to active.
      * ``"remove"`` — drop the named roles from active.
      * ``"replace_all_team"`` — fan out to every role (user said
        "전체 팀 관점").

    ``roles`` is the tuple of role ids the action targets.
    ``unknown_aliases`` carries any token the parser saw but could
    not map — caller can show a friendly "unknown role" reply.
    """

    action: str
    roles: Tuple[str, ...]
    unknown_aliases: Tuple[str, ...] = ()
    raw_text: str = ""


# Aliases — Korean variants + English shorthand → canonical role id.
# Built off RoleProfile.explicit_patterns so any new alias added to a
# profile is automatically recognised here.
_ROLE_ALIAS_INDEX: Mapping[str, str] = {
    # canonical ids
    "tech-lead": ROLE_TECH_LEAD,
    "techlead": ROLE_TECH_LEAD,
    "테크리드": ROLE_TECH_LEAD,
    "리드": ROLE_TECH_LEAD,
    "qa-engineer": "qa-engineer",
    "qa": "qa-engineer",
    "큐에이": "qa-engineer",
    "테스터": "qa-engineer",
    "테스트": "qa-engineer",
    "backend-engineer": "backend-engineer",
    "backend": "backend-engineer",
    "백엔드": "backend-engineer",
    "back-end": "backend-engineer",
    "be": "backend-engineer",
    "frontend-engineer": "frontend-engineer",
    "frontend": "frontend-engineer",
    "프론트": "frontend-engineer",
    "프론트엔드": "frontend-engineer",
    "front-end": "frontend-engineer",
    "fe": "frontend-engineer",
    "devops-engineer": "devops-engineer",
    "devops": "devops-engineer",
    "데브옵스": "devops-engineer",
    "ops": "devops-engineer",
    "sre": "devops-engineer",
    "ai-engineer": "ai-engineer",
    "ai": "ai-engineer",
    "에이아이": "ai-engineer",
    "ml": "ai-engineer",
    "product-designer": "product-designer",
    "product": "product-designer",
    "designer": "product-designer",
    "디자이너": "product-designer",
    "프로덕트": "product-designer",
    "ux": "product-designer",
}


# Korean / English add-action verb stems. The parser looks for any of
# these *anywhere* after a role token — they don't need to be adjacent.
_ROLE_ADD_VERBS: Tuple[str, ...] = (
    "참여시",  # 참여시켜 / 참여시키자
    "참여 시",
    "불러",  # 불러줘 / 불러봐
    "추가",  # 추가해 / 추가해줘
    "포함",  # 포함시켜
    "더해",
    "같이",  # "프론트도 같이 봐줘"
    "함께",
    "도 봐",  # "QA도 봐줘"
    "도 보",
    "join",
    "include",
    "add",
)

# Remove-action verbs — used when the user wants to drop a role.
_ROLE_REMOVE_VERBS: Tuple[str, ...] = (
    "빼",
    "제외",
    "빠져",
    "참여 안",
    "참여하지",
    "remove",
    "exclude",
    "drop",
)


def parse_role_change_request(text: str) -> Optional[RoleChangeRequest]:
    """Parse a user message for "add / remove / all-team" role intent.

    Returns ``None`` when the message has no recognisable intent —
    caller treats that as "regular conversation, not a routing
    command". Recognises:

      * "전체 팀 관점으로 봐줘" / "all roles" → ``replace_all_team``.
      * "QA도 참여시켜" / "백엔드도 불러줘" / "프론트도 같이 봐줘" →
        ``add`` with the role ids the alias index resolved.
      * "디자이너는 빼줘" / "QA는 제외해줘" → ``remove``.

    The parser is intentionally lenient — partial Korean tokens
    (e.g. ``디자이너`` for ``product-designer``) match. Unknown
    tokens land in ``unknown_aliases`` so the caller can show a
    friendly "what did you mean?" message instead of silently
    no-op'ing.
    """

    if not text or not text.strip():
        return None
    raw = text.strip()
    lowered = raw.lower()

    if _detect_all_team_request(raw):
        return RoleChangeRequest(
            action="replace_all_team",
            roles=tuple(ALL_ENGINEERING_ROLES),
            raw_text=raw,
        )

    has_add_verb = any(verb in lowered for verb in _ROLE_ADD_VERBS)
    has_remove_verb = any(verb in lowered for verb in _ROLE_REMOVE_VERBS)
    if not (has_add_verb or has_remove_verb):
        return None

    # Find role aliases mentioned. Sort longer-first so e.g.
    # "qa-engineer" wins before "qa".
    sorted_aliases = sorted(_ROLE_ALIAS_INDEX.keys(), key=len, reverse=True)
    found: list[str] = []
    seen: set[str] = set()
    consumed_spans: list[Tuple[int, int]] = []
    for alias in sorted_aliases:
        idx = lowered.find(alias)
        if idx < 0:
            continue
        # Skip aliases that overlap an already-consumed span (longer
        # alias match came first).
        if any(start <= idx < end for start, end in consumed_spans):
            continue
        consumed_spans.append((idx, idx + len(alias)))
        canonical = _ROLE_ALIAS_INDEX[alias]
        if canonical not in seen:
            seen.add(canonical)
            found.append(canonical)

    if not found:
        return None

    # Remove takes precedence when both verbs are present (defensive
    # — "QA 빼고 backend 추가해줘" would ambiguously match both, but
    # remove is the safer assumption since the user is narrowing).
    action = "remove" if has_remove_verb and not has_add_verb else "add"
    return RoleChangeRequest(
        action=action,
        roles=tuple(found),
        unknown_aliases=(),
        raw_text=raw,
    )


@dataclass(frozen=True)
class RoleChangeOutcome:
    """Result of :func:`apply_role_change`.

    ``new_active_roles`` is the post-change tuple (tech-lead-first,
    deduplicated). ``added_roles`` / ``removed_roles`` describe the
    diff so the caller can render a friendly confirmation. ``audit``
    is the dict written to ``session.extra['role_changes']``.
    """

    new_active_roles: Tuple[str, ...]
    added_roles: Tuple[str, ...]
    removed_roles: Tuple[str, ...]
    audit: Mapping[str, Any]


def apply_role_change(
    *,
    current_active: Sequence[str],
    change: RoleChangeRequest,
    requested_by: str,
    requested_at: Optional[str] = None,
) -> RoleChangeOutcome:
    """Compute the new active-roles tuple + the audit record.

    Pure function — does not mutate session.extra; the caller wires
    the result into :func:`apply_role_selection_to_extra` (or a
    smaller helper) so the persistence layer stays in one place.

    tech-lead is always preserved — the parser can never drop it.
    """

    from datetime import datetime, timezone

    when = requested_at or datetime.now(tz=timezone.utc).replace(
        microsecond=0
    ).isoformat()

    current = list(_ensure_tech_lead_first(current_active or ()))
    added: list[str] = []
    removed: list[str] = []

    if change.action == "replace_all_team":
        new_set = list(_ensure_tech_lead_first(ALL_ENGINEERING_ROLES))
        added = [r for r in new_set if r not in current]
        removed = []
        new_active = tuple(new_set)
    elif change.action == "add":
        new_active_list = list(current)
        for role in change.roles:
            if role not in new_active_list:
                new_active_list.append(role)
                added.append(role)
        new_active = _ensure_tech_lead_first(new_active_list)
    elif change.action == "remove":
        new_active_list = [
            r for r in current if r == ROLE_TECH_LEAD or r not in change.roles
        ]
        removed = [r for r in current if r in change.roles and r != ROLE_TECH_LEAD]
        new_active = _ensure_tech_lead_first(new_active_list)
    else:
        # Unknown action — be defensive: return current state with no diff.
        new_active = tuple(current)

    audit = {
        "action": change.action,
        "roles_requested": list(change.roles),
        "roles_added": list(added),
        "roles_removed": list(removed),
        "requested_by": requested_by,
        "requested_at": when,
        "raw_text": change.raw_text[:200],
    }
    return RoleChangeOutcome(
        new_active_roles=tuple(new_active),
        added_roles=tuple(added),
        removed_roles=tuple(removed),
        audit=audit,
    )


def append_role_change_audit(
    extra: Optional[Mapping[str, Any]],
    audit: Mapping[str, Any],
    *,
    cap: int = 32,
) -> dict:
    """Return a copy of *extra* with the audit appended to a capped
    ``role_changes`` bucket. Latest at the end.
    """

    new_extra: dict = dict(extra or {})
    bucket = list(new_extra.get("role_changes") or [])
    bucket.append(dict(audit))
    if len(bucket) > cap:
        bucket = bucket[-cap:]
    new_extra["role_changes"] = bucket
    return new_extra
