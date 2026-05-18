"""Session work mode / topology / scope ask-once negotiation — P0-H (#140).

Implements the policy land in stage 1 ``docs/autonomy-policy.md`` §0:
gateway asks the user *once* at session start which mode / topology /
scope governs the work, persists the decision in ``session.extra``,
and never re-asks within the same session.

Three mutually-exclusive enums:

  * ``work_mode``  — ``autonomous_merge`` | ``approval_required``
  * ``topology``   — ``single_repo`` | ``multi_repo``
  * ``scope``      — ``single_scope`` | ``full_stack_single_repo`` |
                     ``layer_scoped`` | ``cross_repo_program``

The helper is pure / network-free. It reads / writes ``session.extra``
through caller-provided functions (no direct SQLite touch) so tests
inject simple dicts.

Defaults (per stage-1 §0.4):

  * ``work_mode``  — ``approval_required``
  * ``topology``   — ``single_repo``
  * ``scope``      — ``single_scope``

The caller is responsible for asking the user when ``decision.needs_question``
is True. Once ``ensure_session_mode`` returns ``decision.persisted=True``,
the keys are in ``session.extra`` and *never* re-prompted in the same session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


WORK_MODE_AUTONOMOUS = "autonomous_merge"
WORK_MODE_APPROVAL = "approval_required"
WORK_MODE_VALUES = (WORK_MODE_AUTONOMOUS, WORK_MODE_APPROVAL)
WORK_MODE_DEFAULT = WORK_MODE_APPROVAL

TOPOLOGY_SINGLE = "single_repo"
TOPOLOGY_MULTI = "multi_repo"
TOPOLOGY_VALUES = (TOPOLOGY_SINGLE, TOPOLOGY_MULTI)
TOPOLOGY_DEFAULT = TOPOLOGY_SINGLE

SCOPE_SINGLE = "single_scope"
SCOPE_FULL_STACK = "full_stack_single_repo"
SCOPE_LAYER = "layer_scoped"
SCOPE_CROSS_REPO = "cross_repo_program"
SCOPE_VALUES = (SCOPE_SINGLE, SCOPE_FULL_STACK, SCOPE_LAYER, SCOPE_CROSS_REPO)
SCOPE_DEFAULT = SCOPE_SINGLE

DECIDED_BY_USER = "user_explicit"
DECIDED_BY_INFERRED = "gateway_inferred"

# session.extra keys (matching stage-1 autonomy-policy §0.4).
EXTRA_WORK_MODE = "work_mode"
EXTRA_TOPOLOGY = "topology"
EXTRA_SCOPE = "scope"
EXTRA_DECIDED_AT = "mode_decided_at"
EXTRA_DECIDED_BY = "mode_decided_by"

# P1-R — intake governance contract 확장.  사용자 명시 5 새 키.  운영자가
# 한눈에 "이 세션은 어떤 거버넌스 모드인지" 보고 라우터 / executor /
# merge continuation 도 분기 시 동일 contract 를 본다.
BRANCH_STRATEGY_GIT_FLOW = "git_flow"
BRANCH_STRATEGY_VALUES = (BRANCH_STRATEGY_GIT_FLOW,)
BRANCH_STRATEGY_DEFAULT = BRANCH_STRATEGY_GIT_FLOW

RELEASE_STRATEGY_TAGGED = "tagged_release"
RELEASE_STRATEGY_VALUES = (RELEASE_STRATEGY_TAGGED,)
RELEASE_STRATEGY_DEFAULT = RELEASE_STRATEGY_TAGGED

ISSUE_POLICY_REQUIRED = "issue_required"
ISSUE_POLICY_VALUES = (ISSUE_POLICY_REQUIRED,)
ISSUE_POLICY_DEFAULT = ISSUE_POLICY_REQUIRED

EXTRA_BRANCH_STRATEGY = "branch_strategy"
EXTRA_RELEASE_STRATEGY = "release_strategy"
EXTRA_ISSUE_POLICY = "issue_policy"


@dataclass(frozen=True)
class SessionMode:
    """Snapshot of session work_mode / topology / scope."""

    work_mode: str
    topology: str
    scope: str
    decided_at: Optional[str] = None  # iso8601 UTC
    decided_by: Optional[str] = None  # user_explicit | gateway_inferred


@dataclass(frozen=True)
class SessionModeDecision:
    """What :func:`ensure_session_mode` decided.

    ``mode`` is the resolved current mode (post-decision).
    ``needs_question`` is True when the caller should ask the user
    before proceeding (mode was unset *and* the caller didn't pre-decide).
    ``persisted`` is True when the resolved mode is now in extra
    (either was already there, or we just wrote defaults).
    ``changed`` is True when this call mutated the extra dict.
    """

    mode: SessionMode
    needs_question: bool
    persisted: bool
    changed: bool


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_session_mode(extra: Mapping[str, Any]) -> Optional[SessionMode]:
    """Return the SessionMode from *extra* if all three keys are present + valid.

    Returns ``None`` when any of work_mode / topology / scope is missing
    or invalid — caller must negotiate / write defaults.
    """

    if not isinstance(extra, Mapping):
        return None
    work_mode = extra.get(EXTRA_WORK_MODE)
    topology = extra.get(EXTRA_TOPOLOGY)
    scope = extra.get(EXTRA_SCOPE)
    if work_mode not in WORK_MODE_VALUES:
        return None
    if topology not in TOPOLOGY_VALUES:
        return None
    if scope not in SCOPE_VALUES:
        return None
    return SessionMode(
        work_mode=work_mode,
        topology=topology,
        scope=scope,
        decided_at=_str_or_none(extra.get(EXTRA_DECIDED_AT)),
        decided_by=_str_or_none(extra.get(EXTRA_DECIDED_BY)),
    )


def ensure_session_mode(
    extra: dict,
    *,
    user_hint_work_mode: Optional[str] = None,
    user_hint_topology: Optional[str] = None,
    user_hint_scope: Optional[str] = None,
    apply_defaults: bool = True,
    now: Optional[datetime] = None,
) -> SessionModeDecision:
    """Resolve session mode + persist to *extra* dict in place.

    Algorithm:

    1. If extra already has a valid SessionMode → ``persisted=True``,
       ``needs_question=False``, ``changed=False``. **Never re-prompt.**
    2. Else if any user hint is given (user explicitly stated mode in
       the message), merge those + fill rest with defaults, mark as
       ``decided_by=user_explicit`` if all three came from hints, else
       ``gateway_inferred``.
    3. Else if *apply_defaults* is True, write defaults +
       ``decided_by=gateway_inferred``, return ``needs_question=True``
       so caller can confirm with user.
    4. Else (apply_defaults=False, no hints) → ``persisted=False``,
       ``needs_question=True``.

    *extra* is mutated in place when persistence happens.

    *now* is injectable for tests; defaults to current UTC.
    """

    existing = read_session_mode(extra)
    if existing is not None:
        return SessionModeDecision(
            mode=existing,
            needs_question=False,
            persisted=True,
            changed=False,
        )

    # Validate hints — invalid hint values are silently ignored.
    work_hint = user_hint_work_mode if user_hint_work_mode in WORK_MODE_VALUES else None
    topology_hint = user_hint_topology if user_hint_topology in TOPOLOGY_VALUES else None
    scope_hint = user_hint_scope if user_hint_scope in SCOPE_VALUES else None
    any_hint = any(h is not None for h in (work_hint, topology_hint, scope_hint))
    all_hints = all(h is not None for h in (work_hint, topology_hint, scope_hint))

    if any_hint:
        work_mode = work_hint or WORK_MODE_DEFAULT
        topology = topology_hint or TOPOLOGY_DEFAULT
        scope = scope_hint or SCOPE_DEFAULT
        decided_by = DECIDED_BY_USER if all_hints else DECIDED_BY_INFERRED
        _persist(
            extra=extra,
            work_mode=work_mode,
            topology=topology,
            scope=scope,
            decided_by=decided_by,
            now=now,
        )
        return SessionModeDecision(
            mode=SessionMode(
                work_mode=work_mode,
                topology=topology,
                scope=scope,
                decided_at=extra.get(EXTRA_DECIDED_AT),
                decided_by=decided_by,
            ),
            needs_question=not all_hints,
            persisted=True,
            changed=True,
        )

    if not apply_defaults:
        # Caller wants to prompt the user without writing defaults yet.
        return SessionModeDecision(
            mode=SessionMode(
                work_mode=WORK_MODE_DEFAULT,
                topology=TOPOLOGY_DEFAULT,
                scope=SCOPE_DEFAULT,
            ),
            needs_question=True,
            persisted=False,
            changed=False,
        )

    _persist(
        extra=extra,
        work_mode=WORK_MODE_DEFAULT,
        topology=TOPOLOGY_DEFAULT,
        scope=SCOPE_DEFAULT,
        decided_by=DECIDED_BY_INFERRED,
        now=now,
    )
    return SessionModeDecision(
        mode=SessionMode(
            work_mode=WORK_MODE_DEFAULT,
            topology=TOPOLOGY_DEFAULT,
            scope=SCOPE_DEFAULT,
            decided_at=extra.get(EXTRA_DECIDED_AT),
            decided_by=DECIDED_BY_INFERRED,
        ),
        needs_question=True,
        persisted=True,
        changed=True,
    )


def explicit_mode_change(
    extra: dict,
    *,
    work_mode: Optional[str] = None,
    topology: Optional[str] = None,
    scope: Optional[str] = None,
    now: Optional[datetime] = None,
) -> SessionModeDecision:
    """Apply an explicit user-driven mode change.

    Only the fields the caller passes are updated; missing fields
    retain their existing value. ``decided_by`` becomes
    ``user_explicit`` and ``decided_at`` is bumped.
    """

    current = read_session_mode(extra) or SessionMode(
        work_mode=WORK_MODE_DEFAULT,
        topology=TOPOLOGY_DEFAULT,
        scope=SCOPE_DEFAULT,
    )
    new_work_mode = work_mode if work_mode in WORK_MODE_VALUES else current.work_mode
    new_topology = topology if topology in TOPOLOGY_VALUES else current.topology
    new_scope = scope if scope in SCOPE_VALUES else current.scope
    changed = (
        new_work_mode != current.work_mode
        or new_topology != current.topology
        or new_scope != current.scope
    )
    _persist(
        extra=extra,
        work_mode=new_work_mode,
        topology=new_topology,
        scope=new_scope,
        decided_by=DECIDED_BY_USER,
        now=now,
    )
    return SessionModeDecision(
        mode=SessionMode(
            work_mode=new_work_mode,
            topology=new_topology,
            scope=new_scope,
            decided_at=extra.get(EXTRA_DECIDED_AT),
            decided_by=DECIDED_BY_USER,
        ),
        needs_question=False,
        persisted=True,
        changed=changed,
    )


def build_mode_question_text(decision: SessionModeDecision) -> str:
    """Render the ask-once question shown when ``needs_question=True``.

    Korean, concise. Tells the user what defaults were applied so they
    can confirm or override. The caller decides whether to send.
    """

    mode = decision.mode
    return (
        "🛠️ 새 세션이라 작업 모드를 한 번만 확인해 둘게요 (다음부터는 다시 묻지 않습니다).\n\n"
        f"- 머지 모드: `{mode.work_mode}`\n"
        f"- 작업 범위 topology: `{mode.topology}`\n"
        f"- 작업 범위 scope: `{mode.scope}`\n\n"
        "이 기본값이면 그대로 진행할게요. 바꾸려면\n"
        "`모드: autonomous_merge`, `topology: multi_repo`, `scope: cross_repo_program` 처럼 적어 주세요."
    )


# ---------------------------------------------------------------------------
# Hint parsing — turn user text into mode/topology/scope hints
# ---------------------------------------------------------------------------


def parse_mode_hints(text: str) -> Mapping[str, Optional[str]]:
    """Extract `mode / topology / scope` hints from a user message.

    Recognized phrases (case-insensitive):

    work_mode:
      * "autonomous_merge" / "autonomous merge" / "자율 머지" / "자율머지" → ``autonomous_merge``
      * "approval_required" / "승인 필요" / "approval required" → ``approval_required``

    topology:
      * "single_repo" / "single repo" / "한 repo" / "단일 repo" → ``single_repo``
      * "multi_repo" / "multi repo" / "여러 repo" → ``multi_repo``

    scope:
      * "single_scope" / "single scope" → ``single_scope``
      * "full_stack_single_repo" / "full stack" → ``full_stack_single_repo``
      * "layer_scoped" / "layer scoped" / "layer-scoped" → ``layer_scoped``
      * "cross_repo_program" / "cross repo program" → ``cross_repo_program``

    Returns dict with ``work_mode`` / ``topology`` / ``scope`` keys,
    value ``None`` when not detected. Never raises.
    """

    out: dict = {
        "work_mode": None,
        "topology": None,
        "scope": None,
        # P1-R — intake governance contract 확장.  prompt 토큰으로 명시
        # 가능; 명시 없으면 default (git_flow / tagged_release / issue_required).
        "branch_strategy": None,
        "release_strategy": None,
        "issue_policy": None,
    }
    if not text:
        return out
    lowered = text.lower()

    # branch_strategy / release_strategy / issue_policy
    if any(
        token in lowered for token in ("git_flow", "git-flow", "git flow")
    ):
        out["branch_strategy"] = BRANCH_STRATEGY_GIT_FLOW
    if any(
        token in lowered
        for token in ("tagged_release", "tagged release", "tag release")
    ):
        out["release_strategy"] = RELEASE_STRATEGY_TAGGED
    if any(
        token in lowered for token in ("issue_required", "issue required")
    ):
        out["issue_policy"] = ISSUE_POLICY_REQUIRED

    # work_mode
    if any(
        token in lowered
        for token in ("autonomous_merge", "autonomous merge", "자율 머지", "자율머지")
    ):
        out["work_mode"] = WORK_MODE_AUTONOMOUS
    elif any(
        token in lowered
        for token in ("approval_required", "approval required", "승인 필요", "승인필요")
    ):
        out["work_mode"] = WORK_MODE_APPROVAL

    # topology
    if any(
        token in lowered
        for token in ("multi_repo", "multi repo", "여러 repo", "여러repo")
    ):
        out["topology"] = TOPOLOGY_MULTI
    elif any(
        token in lowered
        for token in ("single_repo", "single repo", "한 repo", "단일 repo")
    ):
        out["topology"] = TOPOLOGY_SINGLE

    # scope — more specific first
    if any(
        token in lowered
        for token in ("cross_repo_program", "cross repo program", "cross-repo")
    ):
        out["scope"] = SCOPE_CROSS_REPO
    elif any(
        token in lowered
        for token in ("full_stack_single_repo", "full stack", "full-stack")
    ):
        out["scope"] = SCOPE_FULL_STACK
    elif any(
        token in lowered for token in ("layer_scoped", "layer scoped", "layer-scoped")
    ):
        out["scope"] = SCOPE_LAYER
    elif "single_scope" in lowered or "single scope" in lowered:
        out["scope"] = SCOPE_SINGLE

    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _persist(
    *,
    extra: dict,
    work_mode: str,
    topology: str,
    scope: str,
    decided_by: str,
    now: Optional[datetime],
) -> None:
    extra[EXTRA_WORK_MODE] = work_mode
    extra[EXTRA_TOPOLOGY] = topology
    extra[EXTRA_SCOPE] = scope
    extra[EXTRA_DECIDED_BY] = decided_by
    extra[EXTRA_DECIDED_AT] = _now_iso(now)
    # P1-R — intake governance contract 확장.  branch_strategy /
    # release_strategy / issue_policy 도 같이 영속 — 모두 default 값.
    # 명시적 override 는 ensure_session_mode_governance 헬퍼 또는 직접
    # caller 가 prompt 파싱 후 setdefault 로 가능.
    extra.setdefault(EXTRA_BRANCH_STRATEGY, BRANCH_STRATEGY_DEFAULT)
    extra.setdefault(EXTRA_RELEASE_STRATEGY, RELEASE_STRATEGY_DEFAULT)
    extra.setdefault(EXTRA_ISSUE_POLICY, ISSUE_POLICY_DEFAULT)


def apply_governance_hints(
    extra: dict,
    *,
    branch_strategy: Optional[str] = None,
    release_strategy: Optional[str] = None,
    issue_policy: Optional[str] = None,
) -> None:
    """P1-R — prompt 파싱 결과 (또는 explicit override) 를 extra 에 머지.

    값이 None 또는 알 수 없는 값이면 default 보존.  명시 hint 만 영속.
    """

    if branch_strategy and branch_strategy in BRANCH_STRATEGY_VALUES:
        extra[EXTRA_BRANCH_STRATEGY] = branch_strategy
    elif EXTRA_BRANCH_STRATEGY not in extra:
        extra[EXTRA_BRANCH_STRATEGY] = BRANCH_STRATEGY_DEFAULT
    if release_strategy and release_strategy in RELEASE_STRATEGY_VALUES:
        extra[EXTRA_RELEASE_STRATEGY] = release_strategy
    elif EXTRA_RELEASE_STRATEGY not in extra:
        extra[EXTRA_RELEASE_STRATEGY] = RELEASE_STRATEGY_DEFAULT
    if issue_policy and issue_policy in ISSUE_POLICY_VALUES:
        extra[EXTRA_ISSUE_POLICY] = issue_policy
    elif EXTRA_ISSUE_POLICY not in extra:
        extra[EXTRA_ISSUE_POLICY] = ISSUE_POLICY_DEFAULT


def _now_iso(now: Optional[datetime]) -> str:
    moment = now or datetime.now(tz=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = (
    "BRANCH_STRATEGY_DEFAULT",
    "BRANCH_STRATEGY_GIT_FLOW",
    "BRANCH_STRATEGY_VALUES",
    "DECIDED_BY_INFERRED",
    "DECIDED_BY_USER",
    "EXTRA_BRANCH_STRATEGY",
    "EXTRA_DECIDED_AT",
    "EXTRA_DECIDED_BY",
    "EXTRA_ISSUE_POLICY",
    "EXTRA_RELEASE_STRATEGY",
    "ISSUE_POLICY_DEFAULT",
    "ISSUE_POLICY_REQUIRED",
    "ISSUE_POLICY_VALUES",
    "RELEASE_STRATEGY_DEFAULT",
    "RELEASE_STRATEGY_TAGGED",
    "RELEASE_STRATEGY_VALUES",
    "apply_governance_hints",
    "EXTRA_SCOPE",
    "EXTRA_TOPOLOGY",
    "EXTRA_WORK_MODE",
    "SCOPE_CROSS_REPO",
    "SCOPE_DEFAULT",
    "SCOPE_FULL_STACK",
    "SCOPE_LAYER",
    "SCOPE_SINGLE",
    "SCOPE_VALUES",
    "SessionMode",
    "SessionModeDecision",
    "TOPOLOGY_DEFAULT",
    "TOPOLOGY_MULTI",
    "TOPOLOGY_SINGLE",
    "TOPOLOGY_VALUES",
    "WORK_MODE_APPROVAL",
    "WORK_MODE_AUTONOMOUS",
    "WORK_MODE_DEFAULT",
    "WORK_MODE_VALUES",
    "build_mode_question_text",
    "ensure_session_mode",
    "explicit_mode_change",
    "parse_mode_hints",
    "read_session_mode",
)
