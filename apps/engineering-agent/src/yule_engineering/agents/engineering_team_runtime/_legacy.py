"""Team conversation runtime for engineering-agent member bots.

The original MVP walked ``role_sequence`` in a fixed chain. That path still
exists for backwards-compatible ``team-turn`` directives, but research forum
work now uses an open-call marker: the gateway posts one job brief and each
member bot decides from its own role policy whether to respond. This is closer
to an autonomous department: gateway publishes the work; members gather their
own evidence and post their own take without being handed a rigid speaking
order.

The runtime is pure-Python so it can be exercised without a Discord client:
- ``build_turn_plan`` returns the ordered turn list for a session.
- ``handle_team_turn_message`` is what each member bot calls inside its
  ``on_message`` handler — it parses the dispatch marker, decides whether
  *this* role should speak, and returns the rendered message + the next
  dispatch directive.
- ``mark_turn_played`` / ``next_pending_turn`` give the gateway and the bot
  a shared view of "who has spoken so far" via ``WorkflowSession.extra``.

The Discord-side glue (creating the thread, posting the kickoff, mutating
the session state) lives in the gateway. Member bots only need this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from yule_engineering.agents.deliberation import (
    DeliberationContext,
    RoleTake,
    TechLeadSynthesis,
    render_role_take,
    render_synthesis,
    run_role_deliberation,
    synthesize,
)
from yule_engineering.agents.research.pack import ResearchPack
from yule_engineering.agents.workflow_state import WorkflowSession, load_session


TEAM_CONVERSATION_KEY = "team_conversation"
PLAYED_ROLES_KEY = "played_roles"

DISPATCH_MARKER_RE = re.compile(
    r"\[team-turn:(?P<sid>[A-Za-z0-9_\-]+)(?:\s+(?P<role>[A-Za-z0-9_\-]+))?\]"
)

RESEARCH_DISPATCH_MARKER_RE = re.compile(
    r"\[research-turn:(?P<sid>[A-Za-z0-9_\-]+)(?:\s+(?P<role>[A-Za-z0-9_\-]+))?\]"
)
RESEARCH_OPEN_MARKER_RE = re.compile(
    r"\[research-open:(?P<sid>[A-Za-z0-9_\-]+)\]"
)


# Default ordering for the research-turn chain in the operations forum.
# tech-lead opens, then ai-engineer brings the model/memory perspective,
# then product-designer, backend-engineer, frontend-engineer, qa-engineer,
# and finally tech-lead synthesises. ``deliberation_research_role_sequence``
# normalises an arbitrary session.role_sequence against this ideal so
# operators can override per-task without losing the synthesis bookend.
DEFAULT_RESEARCH_ROLE_SEQUENCE: Tuple[str, ...] = (
    "tech-lead",
    "ai-engineer",
    "product-designer",
    "backend-engineer",
    "frontend-engineer",
    "qa-engineer",
    "devops-engineer",
)


# Sentinel that closes the research-turn chain and triggers the tech-lead
# synthesis comment in the forum thread. The synthesis uses a regular
# research-turn directive so the same handler dispatches it.
RESEARCH_SYNTHESIS_ROLE = "tech-lead-synthesis"


@dataclass(frozen=True)
class TeamTurn:
    """One role's scripted turn inside a session thread."""

    session_id: str
    role: str
    is_executor: bool
    sequence_index: int
    thread_id: int
    header: str
    body: str

    def render(self) -> str:
        return f"**[{self.role}]** {self.header}\n{self.body}"


@dataclass(frozen=True)
class TeamTurnOutcome:
    """What a member bot should post in response to a dispatch directive.

    ``message`` is what to say. ``next_directive`` is appended so the next
    role's bot picks up the chain; ``None`` means this turn closes the
    conversation and the bot should not chain further.
    """

    turn: TeamTurn
    message: str
    next_directive: Optional[str]
    is_final: bool

    def full_post(self) -> str:
        if self.next_directive is None:
            return self.message
        return f"{self.message}\n\n{self.next_directive}"


# ---------------------------------------------------------------------------
# Role-specific opening templates
# ---------------------------------------------------------------------------


_ROLE_HEADERS: Mapping[str, str] = {
    "tech-lead": "팀 합류, 작업 정리부터 갑니다.",
    "product-designer": "디자인 관점에서 짚어볼게요.",
    "frontend-engineer": "프론트 관점에서 정리해 둘게요.",
    "backend-engineer": "백엔드 관점에서 정리합니다.",
    "qa-engineer": "QA 관점에서 체크리스트 잡습니다.",
}

_ROLE_BODY_BUILDERS: Mapping[
    str, Callable[["_TurnContext"], str]
] = {}


@dataclass(frozen=True)
class _TurnContext:
    session: WorkflowSession
    role: str
    is_executor: bool

    @property
    def task_type(self) -> str:
        return self.session.task_type or "unknown"

    @property
    def executor_role(self) -> str:
        return self.session.executor_role or "tech-lead"

    @property
    def references(self) -> Tuple[str, ...]:
        merged = tuple(self.session.references_user) + tuple(
            self.session.references_suggested
        )
        return merged

    @property
    def prompt_excerpt(self) -> str:
        first_line = (self.session.prompt or "").strip().splitlines()
        if not first_line:
            return "(요청 본문 없음)"
        head = first_line[0].strip()
        if len(head) > 80:
            head = head[:77] + "..."
        return head or "(요청 본문 없음)"


def _tech_lead_body(ctx: _TurnContext) -> str:
    lines = [
        f"분류: `{ctx.task_type}` · 실행 후보: `{ctx.executor_role}`.",
        f"요청: {ctx.prompt_excerpt}",
    ]
    if ctx.session.write_requested and ctx.session.write_blocked_reason:
        lines.append(
            "쓰기 작업은 승인 대기 중입니다. 먼저 의견 정리부터 받겠습니다."
        )
    if ctx.references:
        lines.append(
            f"참고 reference {len(ctx.references)}건 공유 — 각자 본인 영역에서 어떻게 활용할지 짧게 댓글 부탁드립니다."
        )
    else:
        lines.append("자료 reference는 따로 없습니다. 각자 도메인 기준으로 시작합시다.")
    lines.append("필요한 역할이 각자 판단해서 메모를 남깁니다.")
    return "\n".join(lines)


def _product_designer_body(ctx: _TurnContext) -> str:
    refs = ", ".join(ctx.references[:3]) if ctx.references else "(reference 없음)"
    role_self = "내가 실행 후보" if ctx.is_executor else f"실행 후보({ctx.executor_role})"
    return (
        f"reference 검토: {refs}.\n"
        f"{role_self}에게 톤·레이아웃 가이드 1차 정리해서 thread에 붙이겠습니다."
    )


def _frontend_engineer_body(ctx: _TurnContext) -> str:
    role_self = "본인 영역" if ctx.is_executor else f"실행 후보({ctx.executor_role})"
    return (
        "컴포넌트/레이아웃 분해 검토 시작합니다.\n"
        f"{role_self} 합류 시 협업 포인트(상태 / 데이터 바인딩)는 thread에서 동기화하겠습니다."
    )


def _backend_engineer_body(ctx: _TurnContext) -> str:
    role_self = "내가 실행 후보" if ctx.is_executor else f"실행 후보({ctx.executor_role})"
    return (
        "도메인 / API 영향 검토 들어갑니다.\n"
        f"{role_self}와 schema·migration 충돌 여부 thread에 메모로 남기겠습니다."
    )


def _qa_engineer_body(ctx: _TurnContext) -> str:
    role_self = "내가 실행 후보" if ctx.is_executor else f"실행 후보({ctx.executor_role})"
    return (
        "테스트 시나리오 초안 잡습니다.\n"
        f"{role_self} 작업이 끝나면 회귀 영향 점검 결과를 같은 thread에 회신하겠습니다."
    )


_ROLE_BODY_BUILDERS = {
    "tech-lead": _tech_lead_body,
    "product-designer": _product_designer_body,
    "frontend-engineer": _frontend_engineer_body,
    "backend-engineer": _backend_engineer_body,
    "qa-engineer": _qa_engineer_body,
}


def _generic_body(ctx: _TurnContext) -> str:
    role_self = "내가 실행 후보" if ctx.is_executor else f"실행 후보({ctx.executor_role})"
    return f"{ctx.role} 관점에서 검토 들어가겠습니다. {role_self} 기준으로 thread 회신 이어가겠습니다."


def format_role_turn_text(
    session: WorkflowSession,
    role: str,
    *,
    is_executor: bool,
) -> Tuple[str, str]:
    """Return ``(header, body)`` for one role's turn message.

    Roles outside the canonical engineering set fall back to a generic
    template so a custom role sequence still produces a coherent line.
    """

    ctx = _TurnContext(session=session, role=role, is_executor=is_executor)
    header = _ROLE_HEADERS.get(role, f"{role} 관점에서 정리합니다.")
    builder = _ROLE_BODY_BUILDERS.get(role, _generic_body)
    return header, builder(ctx)


# ---------------------------------------------------------------------------
# Plan / state helpers
# ---------------------------------------------------------------------------


def build_turn_plan(session: WorkflowSession) -> Tuple[TeamTurn, ...]:
    """Build the ordered turn plan for a session.

    Requires ``session.thread_id`` and a non-empty ``role_sequence``. The
    gateway (D's territory) is responsible for setting both before calling.

    A-M7.5: the plan is **filtered by the effective active-roles set**
    so excluded roles never receive dispatch markers in the legacy
    work-thread chain. Resolution order (from
    :func:`agents.lifecycle.role_selection.get_effective_active_roles`):

      1. ``session.extra['active_research_roles']``
      2. ``session.role_sequence`` (legacy ordered hint)
      3. ``("tech-lead",)`` — minimum fallback so the chain is never empty.

    The original sequence order is preserved for any role that survives
    the filter; tech-lead is always present and is always the closer
    when it's the last role left.
    """

    if session.thread_id is None:
        raise ValueError(
            f"session {session.session_id} has no thread_id; thread must be created before team conversation"
        )
    if not session.role_sequence:
        raise ValueError(
            f"session {session.session_id} has no role_sequence; dispatcher must populate it"
        )

    # A-M7.5 — gate the plan to the effective active-roles set.
    # Without this gate, every role in role_sequence renders even when
    # the user-driven role-selection narrowed participation.
    from yule_engineering.agents.lifecycle.role_selection import get_effective_active_roles

    active = set(get_effective_active_roles(session))
    plan: list[TeamTurn] = []
    for idx, role in enumerate(session.role_sequence):
        if role not in active:
            continue
        is_executor = role == session.executor_role
        header, body = format_role_turn_text(session, role, is_executor=is_executor)
        plan.append(
            TeamTurn(
                session_id=session.session_id,
                role=role,
                is_executor=is_executor,
                sequence_index=idx,
                thread_id=int(session.thread_id),
                header=header,
                body=body,
            )
        )
    return tuple(plan)


def played_roles(session: WorkflowSession) -> Tuple[str, ...]:
    """Roles that have already taken their turn in this session."""

    block = (session.extra or {}).get(TEAM_CONVERSATION_KEY) or {}
    return tuple(str(r) for r in (block.get(PLAYED_ROLES_KEY) or ()))


def next_pending_turn(session: WorkflowSession) -> Optional[TeamTurn]:
    """First turn in the plan whose role has not posted yet."""

    plan = build_turn_plan(session)
    played = set(played_roles(session))
    for turn in plan:
        if turn.role not in played:
            return turn
    return None


def mark_turn_played(session: WorkflowSession, role: str) -> WorkflowSession:
    """Return a copy of *session* with ``role`` recorded as having spoken.

    The caller is responsible for persisting via
    ``workflow_state.update_session`` so this module stays free of side
    effects (and easy to test without a SQLite cache).
    """

    extra = dict(session.extra or {})
    block = dict(extra.get(TEAM_CONVERSATION_KEY) or {})
    played = list(block.get(PLAYED_ROLES_KEY) or ())
    if role not in played:
        played.append(role)
    block[PLAYED_ROLES_KEY] = played
    extra[TEAM_CONVERSATION_KEY] = block
    return replace(session, extra=extra)


# ---------------------------------------------------------------------------
# Dispatch protocol
# ---------------------------------------------------------------------------


def parse_dispatch_marker(text: str) -> Optional[Tuple[str, Optional[str]]]:
    """Parse ``[team-turn:<sid> <role>]`` (role optional) out of a message.

    Returns ``(session_id, role_or_None)`` or ``None`` if no marker is
    present. Used both by the gateway when emitting directives and by
    member bots when filtering inbound messages.
    """

    match = DISPATCH_MARKER_RE.search(text or "")
    if not match:
        return None
    return match.group("sid"), match.group("role")


def dispatch_directive(turn: TeamTurn) -> str:
    """Marker the *previous* speaker appends to hand off to *turn*'s role."""

    return f"[team-turn:{turn.session_id} {turn.role}]"


def kickoff_directive(session: WorkflowSession) -> str:
    """Marker the gateway posts in the thread to start the chain.

    Always targets the first role in ``role_sequence`` (typically
    ``tech-lead``). Raises ``ValueError`` if the session has no plan yet.
    """

    plan = build_turn_plan(session)
    return dispatch_directive(plan[0])


# ---------------------------------------------------------------------------
# Research-turn protocol (운영-리서치 forum)
# ---------------------------------------------------------------------------


def parse_research_dispatch_marker(
    text: str,
) -> Optional[Tuple[str, Optional[str]]]:
    """Parse ``[research-turn:<sid> <role>]`` (role optional) out of a message.

    Returns ``(session_id, role_or_None)`` or ``None`` if no marker is
    present. Mirrors :func:`parse_dispatch_marker` for the forum chain
    so the working thread (team-turn) and the operations-research forum
    (research-turn) stay independent — flipping one channel's policy
    never disturbs the other.
    """

    match = RESEARCH_DISPATCH_MARKER_RE.search(text or "")
    if not match:
        return None
    return match.group("sid"), match.group("role")


def parse_research_open_marker(text: str) -> Optional[str]:
    """Parse ``[research-open:<sid>]`` out of a forum message.

    Open-call markers are intentionally role-less. Every active member bot
    can see the same job brief and decide whether its role belongs in the
    session's participant set.
    """

    match = RESEARCH_OPEN_MARKER_RE.search(text or "")
    if not match:
        return None
    return match.group("sid")


def research_dispatch_directive(session_id: str, role: str) -> str:
    """Marker that hands the next research turn to *role* in the forum thread."""

    return f"[research-turn:{session_id} {role}]"


def research_open_call_directive(session: WorkflowSession) -> str:
    """Marker the gateway posts when member bots should self-start research."""

    return f"[research-open:{session.session_id}]"


def deliberation_research_role_sequence(
    session: WorkflowSession,
    *,
    base: Optional[Sequence[str]] = None,
) -> Tuple[str, ...]:
    """Normalise the research-turn role sequence for a session.

    Resolution order (first non-empty wins for the middle of the chain):

      1. *base* — caller-supplied override.
      2. ``session.extra['active_research_roles']`` — Phase 1 role
         selection result. **Stabilisation Phase 5**: when present this
         takes precedence over ``session.role_sequence`` so member
         bots whose role isn't active stay silent on research-open
         calls (no typing, no comment).
      3. ``session.role_sequence`` — legacy seed.
      4. :data:`DEFAULT_RESEARCH_ROLE_SEQUENCE`.

    Other rules:

    - ``tech-lead`` always opens the chain.
    - Unknown roles pass through (so a future ``security-review`` turn
      still lands in the chain even before its dataclass exists).
    - Duplicates are dropped (first-seen wins).
    - The returned tuple does **not** include the synthesis sentinel —
      callers append :data:`RESEARCH_SYNTHESIS_ROLE` themselves when
      they need it for the closing comment.
    """

    candidate: list[str] = ["tech-lead"]
    if base is not None:
        requested: Sequence[str] = list(base)
    else:
        # Phase 5 stab: prefer active_research_roles over role_sequence
        # so excluded member bots quietly ignore research-open calls.
        active: Sequence[str] = ()
        try:
            extra = dict(getattr(session, "extra", None) or {})
            raw_active = extra.get("active_research_roles")
            if isinstance(raw_active, (list, tuple)):
                active = [
                    str(role) for role in raw_active if isinstance(role, str)
                ]
        except Exception:  # noqa: BLE001 - best-effort role-list lookup
            active = ()
        if active:
            requested = list(active)
        else:
            requested = list(
                getattr(session, "role_sequence", ()) or DEFAULT_RESEARCH_ROLE_SEQUENCE
            )
    for role in requested:
        short = (role or "").split("/", 1)[-1]
        short = short.strip()
        if not short:
            continue
        if short in candidate:
            continue
        candidate.append(short)
    return tuple(candidate)


def research_kickoff_directive(session: WorkflowSession) -> str:
    """Marker the gateway posts in the forum thread to start research turns.

    Always targets the first role in :func:`deliberation_research_role_sequence`
    (``tech-lead``). The session id is required so member bots can scope
    each chain to a single workflow run.
    """

    sequence = deliberation_research_role_sequence(session)
    return research_dispatch_directive(session.session_id, sequence[0])


@dataclass(frozen=True)
class ResearchTurnOutcome:
    """What the bot for one role should post into the operations forum.

    ``message`` contains the rendered role take. Legacy ``research-turn``
    chains may still use ``next_directive``; open-call research leaves it
    empty so each member bot speaks independently.
    """

    role: str
    session_id: str
    message: str
    next_directive: Optional[str]
    is_synthesis: bool = False


def handle_research_turn_message(
    *,
    role: str,
    text: str,
    session_loader: Optional[Callable[[str], Optional[WorkflowSession]]] = None,
    pack_loader: Optional[Callable[[WorkflowSession], Any]] = None,
) -> Optional[ResearchTurnOutcome]:
    """Decide whether the bot for *role* should post in the research forum.

    Parses ``[research-turn:<sid> <role>]`` out of *text*. If the marker
    targets this role, loads the session, optionally restores the
    :class:`ResearchPack` via *pack_loader*, runs the role's deliberation
    take, renders it, and appends the next directive (or the tech-lead
    synthesis marker when the role is last).

    Returns ``None`` when the marker is missing, targets a different
    role, the session can't be loaded, or any transient failure makes
    the take unsafe to post — keeping the forum clean of half-baked
    comments.
    """

    open_session_id = parse_research_open_marker(text)
    if open_session_id is not None:
        if _was_recently_handled(role=role, session_id=open_session_id, kind="open"):
            return None
        outcome = _handle_research_open_call(
            role=role,
            session_id=open_session_id,
            session_loader=session_loader,
            pack_loader=pack_loader,
        )
        if outcome is not None:
            _mark_recently_handled(
                role=role, session_id=open_session_id, kind="open"
            )
        return outcome

    parsed = parse_research_dispatch_marker(text)
    if parsed is None:
        return None
    session_id, target_role = parsed
    if target_role is None:
        # Unscoped marker — the gateway always emits a role-scoped one,
        # but we tolerate missing role for ops "ping all" recovery.
        target_role = role

    effective_role = target_role
    if target_role == RESEARCH_SYNTHESIS_ROLE and role == "tech-lead":
        effective_role = RESEARCH_SYNTHESIS_ROLE
    elif target_role != role:
        return None

    loader = session_loader or load_session
    session = loader(session_id)
    if session is None:
        return None

    if _was_recently_handled(
        role=role, session_id=session_id, kind=str(effective_role)
    ):
        return None

    sequence = deliberation_research_role_sequence(session)
    if effective_role == RESEARCH_SYNTHESIS_ROLE:
        # tech-lead synthesis comment closes the chain. We route the
        # actual render through the M4 queue so the supervisor sees
        # the work + dedup catches duplicates; the queue body still
        # falls back to a persisted synthesis text when present so
        # rebuilds stay consistent across the team.
        #
        # A-M7-final: delegate to the standalone helper so the
        # legacy in-process gateway path picks up the same
        # degrade / fallback automation as ``yule run-service
        # eng-role-tech-lead``. The helper handles the
        # role-take-result scan, the deterministic-fallback synth,
        # and the audit persistence; we just hand it the captured
        # session + pack_loader.
        def _synthesis_runner() -> Optional[ResearchTurnOutcome]:
            from yule_engineering.agents.job_queue.standalone_runners import (
                _default_build_synthesis_outcome,
            )

            return _default_build_synthesis_outcome(
                role=role,
                session_id=session_id,
                session=session,
                pack_loader=pack_loader,
            )

        outcome = _run_role_take_via_queue(
            session_id=session_id,
            role=role,
            kind=ROLE_TURN_KIND_SYNTHESIS,
            runner_body=_synthesis_runner,
            payload={"effective_role": str(effective_role)},
        )
        if outcome is not None:
            _mark_recently_handled(
                role=role, session_id=session_id, kind=str(effective_role)
            )
        return outcome

    if effective_role not in sequence:
        return None

    def _turn_runner() -> Optional[ResearchTurnOutcome]:
        # M4 NOTE: full dependency edge between research_collect and
        # role_take is deferred to M5/M6 (the gateway needs to surface
        # the in-flight research_collect job_id to member bots first).
        # ``research_pack=None`` is therefore tolerated here — the
        # legacy deliberation_role_turn happily renders deterministic
        # takes even without a pack, so silencing turns purely on
        # pack absence would regress sessions that pre-date M3.
        research_pack = _maybe_load_pack(pack_loader, session)
        take, rendered = deliberation_role_turn(
            session,
            _role_address(effective_role),
            research_pack=research_pack,
            previous_turns=_replay_role_takes_until(
                session, sequence, effective_role, research_pack
            ),
        )
        next_role = _next_research_role(sequence, effective_role)
        if next_role is None:
            next_directive = research_dispatch_directive(
                session_id, RESEARCH_SYNTHESIS_ROLE
            )
        else:
            next_directive = research_dispatch_directive(session_id, next_role)
        message = rendered
        if next_directive:
            message = f"{rendered}\n\n{next_directive}"
        outcome = ResearchTurnOutcome(
            role=role,
            session_id=session_id,
            message=message,
            next_directive=next_directive,
            is_synthesis=False,
        )
        return _apply_role_runner_for_open_or_turn(
            outcome=outcome,
            role=role,
            session_id=session_id,
            session=session,
            pack_loader=pack_loader,
            kind=ROLE_TURN_KIND_TURN,
            payload={"effective_role": str(effective_role)},
        )

    outcome = _run_role_take_via_queue(
        session_id=session_id,
        role=role,
        kind=ROLE_TURN_KIND_TURN,
        runner_body=_turn_runner,
        payload={"effective_role": str(effective_role)},
    )
    if outcome is not None:
        _mark_recently_handled(
            role=role, session_id=session_id, kind=str(effective_role)
        )
    return outcome


def _handle_research_open_call(
    *,
    role: str,
    session_id: str,
    session_loader: Optional[Callable[[str], Optional[WorkflowSession]]],
    pack_loader: Optional[Callable[[WorkflowSession], Any]],
) -> Optional[ResearchTurnOutcome]:
    if role == RESEARCH_SYNTHESIS_ROLE:
        return None

    loader = session_loader or load_session
    session = loader(session_id)
    if session is None:
        return None

    sequence = deliberation_research_role_sequence(session)
    if role not in sequence:
        return None

    # M4: route the actual render through the role_take queue so the
    # supervisor sees the work + (session, role, kind="open") dedup
    # blocks duplicate kicks. Per-role collection (which produces the
    # per-role research_pack) stays inside the runner because the
    # open-call path is precisely the one that *creates* a fresh
    # pack — gating on its prior existence would deadlock the kickoff.
    def _open_runner() -> Optional[ResearchTurnOutcome]:
        outcome = _build_open_call_outcome(
            role=role,
            session_id=session_id,
            session=session,
            pack_loader=pack_loader,
        )
        return _apply_role_runner_for_open_or_turn(
            outcome=outcome,
            role=role,
            session_id=session_id,
            session=session,
            pack_loader=pack_loader,
            kind=ROLE_TURN_KIND_OPEN,
            payload={"trigger": "research_open"},
        )

    return _run_role_take_via_queue(
        session_id=session_id,
        role=role,
        kind=ROLE_TURN_KIND_OPEN,
        runner_body=_open_runner,
        payload={"trigger": "research_open"},
    )


def _build_open_call_outcome(
    *,
    role: str,
    session_id: str,
    session: WorkflowSession,
    pack_loader: Optional[Callable[[WorkflowSession], Any]],
) -> Optional[ResearchTurnOutcome]:
    """Pre-M4 body of :func:`_handle_research_open_call` extracted as
    a runner so :class:`RoleTakeWorker` can drive it under queue
    state-machine framing without changing the outcome shape.

    Kept as its own function (rather than an inline closure inside
    ``_handle_research_open_call``) so tests that want to exercise
    only the render — without involving the queue — have a clean seam
    to import.
    """

    research_pack, role_research_record = _collect_role_research_pack(
        session=session, role=role
    )
    if research_pack is None:
        research_pack = _maybe_load_pack(pack_loader, session)

    take, rendered = deliberation_role_turn(
        session,
        _role_address(role),
        research_pack=research_pack,
        previous_turns=(),
    )

    # Phase B role-runtime MVP: prepend a runtime-shaped preface so the
    # output meets the 5-section contract (이해한 작업 / 역할 관점의 판단
    # / 참고한 자료 / 리스크 / 다음 행동). The deterministic deliberation
    # render already covers 관점 / 근거 / 리스크 / 다음 행동, so the
    # preface only adds the missing 'understood task' line plus the role
    # policy short_name + memory filter so an operator can see which
    # policy drove the take. Failures fall back to the deterministic
    # render with the legacy memo footer — deterministic templates stay
    # the safety net.
    preface = _render_role_runtime_preface(
        session=session,
        role=role,
        research_pack=research_pack,
        role_research_record=role_research_record,
    )
    if preface:
        message = (
            f"{preface}\n\n{rendered}\n\n"
            "자율 조사 메모: 이 댓글은 gateway가 정한 순번이 아니라, "
            "해당 역할 봇이 공개 research 요청을 보고 독립적으로 제출한 take입니다."
        )
    else:
        message = (
            f"{rendered}\n\n"
            "자율 조사 메모: 이 댓글은 gateway가 정한 순번이 아니라, "
            "해당 역할 봇이 공개 research 요청을 보고 독립적으로 제출한 take입니다."
        )
    return ResearchTurnOutcome(
        role=role,
        session_id=session_id,
        message=message,
        next_directive=None,
        is_synthesis=False,
    )


def _render_role_runtime_preface(
    *,
    session: WorkflowSession,
    role: str,
    research_pack: Any,
    role_research_record: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Build the runtime-shaped preface for the open-call comment.

    Drives :func:`run_runtime_loop` with a :class:`RuntimeInput` carrying
    the role policy so the policy is *visibly* part of the runtime
    contract (tests can assert role policy presence in the input even
    when the deterministic fallback fires). The returned text frames the
    "이해한 작업" line plus a short policy stamp; the deterministic role
    take rendered by :func:`deliberation_role_turn` follows immediately
    afterwards and contributes 관점/근거/리스크/다음 행동.

    Returns ``None`` when the runtime layer can't be imported or the
    session has no usable prompt — in that case the caller falls back to
    the legacy memo footer alone so the post still goes through.
    """

    try:
        from yule_agent_runtime import (
            RuntimeInput,
            role_policy_for,
            run_runtime_loop,
        )
    except Exception:  # noqa: BLE001 - partial install fallback
        return None

    prompt = (getattr(session, "prompt", "") or "").strip()
    if not prompt:
        return None

    role_id = _role_address(role)
    policy = role_policy_for(role_id)
    pack_title = ""
    pack_summary = ""
    if research_pack is not None:
        pack_title = (getattr(research_pack, "title", "") or "").strip()
        pack_summary = (getattr(research_pack, "summary", "") or "").strip()

    runtime_input = RuntimeInput(
        role_id=role_id,
        message_text=prompt,
        last_proposed_prompt=getattr(session, "prompt", None),
        policy={
            "role_policy": {
                "short_name": policy.short_name,
                "memory_role_filter": policy.memory_role_filter,
                "preferred_source_kinds": list(policy.preferred_source_kinds),
                "preferred_note_kinds": list(policy.preferred_note_kinds),
                "description": policy.description,
            },
            "research_pack_title": pack_title or None,
            "task_type": getattr(session, "task_type", None),
        },
    )
    # Run the loop to confirm the runtime can produce a result; we only
    # use the input/policy in the rendered preface for now, but the call
    # exercises the contract end-to-end so future phases can swap the
    # deterministic decide with an LLM-backed one without changing the
    # member bot wiring.
    try:
        run_runtime_loop(runtime_input)
    except Exception:  # noqa: BLE001 - runtime failure must not block the post
        pass

    understood = _summarize_open_call_prompt(prompt)
    role_short = policy.short_name or role
    lines = [
        f"**[{role_short}] 역할 runtime 결과**",
        f"- 이해한 작업: {understood}",
    ]
    perspective_bits: list[str] = []
    if policy.description:
        perspective_bits.append(policy.description)
    if policy.memory_role_filter and policy.memory_role_filter != role_short:
        perspective_bits.append(f"memory filter `{policy.memory_role_filter}`")
    if perspective_bits:
        lines.append(
            "- 내 역할 관점의 판단 근거: " + " · ".join(perspective_bits)
        )
    if pack_title:
        lines.append(f"- 참고한 research_pack: {pack_title}")
    elif pack_summary:
        # Title missing but summary present — still cite it so the
        # operator can tell the bot did read the pack.
        lines.append(
            "- 참고한 research_pack: " + _truncate_one_line(pack_summary, 80)
        )

    # Phase 5 — fold RoleProfile.output_sections into the preface so the
    # role bot's deterministic / LLM take both have the canonical
    # template in front of them. Truncated to 6 sections to keep the
    # preface compact when a role lists more.
    try:
        from yule_engineering.agents.role_profiles import output_template_for_role
    except Exception:  # noqa: BLE001 - registry optional in partial installs
        output_sections: tuple = ()
    else:
        output_sections = output_template_for_role(role)
    if output_sections:
        section_summary = " · ".join(output_sections[:6])
        lines.append(f"- 출력 섹션 템플릿: {section_summary}")

    # Phase 4 — surface the role-scoped collection outcome captured by
    # ``_collect_role_research_pack`` so the user sees concrete evidence
    # ("조사 결과: N건") instead of just the role take. Prefer the live
    # record passed in from the open-call handler (frozen-dataclass
    # session.extra would be stale right after persistence) and fall
    # back to whatever a previous turn left under session.extra.
    findings_block = _render_role_research_findings_block(
        session=session,
        role=role,
        live_record=role_research_record,
    )
    if findings_block:
        lines.append(findings_block)

    return "\n".join(lines)


def _render_role_research_findings_block(
    *,
    session: WorkflowSession,
    role: str,
    live_record: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Render the "조사 결과" section from a role research record.

    Prefers *live_record* (in-memory copy from the just-completed
    collection). Falls back to ``session.extra['role_research_results']``
    when the caller didn't provide one, so a re-render after restart
    still surfaces what was saved.

    Returns ``None`` when nothing is available (legacy session,
    collection skipped, or persistence failed). The record shape
    matches :func:`record_role_research_result`.
    """

    record: Optional[Mapping[str, Any]] = live_record
    if record is None:
        extra = getattr(session, "extra", None)
        if isinstance(extra, Mapping):
            bucket = extra.get("role_research_results")
            if isinstance(bucket, Mapping):
                short = role.split("/", 1)[-1].strip() or role
                candidate = bucket.get(short) or bucket.get(role)
                if isinstance(candidate, Mapping):
                    record = candidate
    if record is None:
        return None

    status = str(record.get("status") or "").strip()
    source_count = record.get("source_count") or 0
    try:
        source_count = int(source_count)
    except (TypeError, ValueError):
        source_count = 0
    provider = record.get("provider")
    findings = record.get("top_findings") or []

    if status == ROLE_RESEARCH_STATUS_FAILED:
        error = record.get("error") or ""
        suffix = f" — {error}" if error else ""
        return f"- 조사 결과: 실패 — fallback to shared pack{suffix}"
    if source_count <= 0:
        return "- 조사 결과: 새 출처 없음 — shared pack 으로 진행"

    header = f"- 조사 결과: {source_count}건"
    if provider:
        header += f" (provider: {provider})"
    parts = [header]
    if isinstance(findings, (list, tuple)):
        for item in list(findings)[:3]:
            text = str(item).strip()
            if text:
                parts.append(f"  · {_truncate_one_line(text, 120)}")
    return "\n".join(parts)


def _summarize_open_call_prompt(prompt: str, *, limit: int = 120) -> str:
    """Return a one-line summary of the user prompt for the runtime preface."""

    cleaned = " ".join((prompt or "").split())
    return _truncate_one_line(cleaned, limit)


def _truncate_one_line(text: str, limit: int) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(1, limit - 1)].rstrip() + "…"


def _collect_role_research_pack(
    *, session: WorkflowSession, role: str
) -> Tuple[Any, Optional[Dict[str, Any]]]:
    """Best-effort per-role collection for open-call research.

    The gateway persists its first pack, but autonomous member bots should not
    be limited to that shared seed. Each bot gets one cheap collection pass
    with its own role profile; if the collector is disabled or empty, we fall
    back to the shared pack.

    Returns a ``(pack, role_research_record)`` tuple. The record mirrors
    what :func:`record_role_research_result` writes onto
    ``session.extra['role_research_results'][<role>]`` so the renderer
    can surface "조사 결과: N건" without re-loading the session from
    the workflow store. Either side may be ``None`` — pack falls back
    to the shared one when collection skipped, and the record is None
    when the helper couldn't produce any outcome.

    Phase 4 — every collection pass also writes the same role-scoped
    record to ``session.extra['role_research_results'][<role>]`` plus
    ``research_started`` / ``research_completed`` / ``research_failed``
    events onto ``session.extra['role_activity_log']`` so the gateway
    diagnostic can describe what each role actually did without
    re-running the collection.
    """

    session_id = getattr(session, "session_id", None) or ""
    role_address = _role_address(role)
    role_query = (getattr(session, "prompt", "") or "").strip()

    append_role_activity_event(
        session_id=session_id,
        role=role,
        event_type=ROLE_ACTIVITY_RESEARCH_STARTED,
        fields={"query": role_query, "role_address": role_address},
    )

    try:
        from yule_engineering.agents.research.collector import auto_collect_or_request_more_input
    except Exception as exc:  # noqa: BLE001
        record = _build_role_research_record(
            query=role_query,
            provider=None,
            source_count=0,
            status=ROLE_RESEARCH_STATUS_FAILED,
            error=f"collector import failed: {exc}",
        )
        record_role_research_result(
            session_id=session_id,
            role=role,
            query=role_query,
            provider=None,
            source_count=0,
            status=ROLE_RESEARCH_STATUS_FAILED,
            error=f"collector import failed: {exc}",
        )
        append_role_activity_event(
            session_id=session_id,
            role=role,
            event_type=ROLE_ACTIVITY_RESEARCH_FAILED,
            status=ROLE_RESEARCH_STATUS_FAILED,
            fields={"error": str(exc)},
        )
        return None, record
    try:
        outcome = auto_collect_or_request_more_input(
            role=role_address,
            prompt=role_query,
            task_type=getattr(session, "task_type", None),
            user_links=tuple(getattr(session, "references_user", ()) or ()),
            session_id=session_id or None,
        )
    except Exception as exc:  # noqa: BLE001
        record = _build_role_research_record(
            query=role_query,
            provider=None,
            source_count=0,
            status=ROLE_RESEARCH_STATUS_FAILED,
            error=str(exc),
        )
        record_role_research_result(
            session_id=session_id,
            role=role,
            query=role_query,
            provider=None,
            source_count=0,
            status=ROLE_RESEARCH_STATUS_FAILED,
            error=str(exc),
        )
        append_role_activity_event(
            session_id=session_id,
            role=role,
            event_type=ROLE_ACTIVITY_RESEARCH_FAILED,
            status=ROLE_RESEARCH_STATUS_FAILED,
            fields={"error": str(exc)},
        )
        return None, record

    pack = getattr(outcome, "pack", None)
    sources = list(getattr(pack, "sources", ()) or []) if pack is not None else []
    source_count = len(sources)
    provider = _outcome_provider_name(outcome)
    top_findings = _summarize_top_findings_from_outcome(outcome)
    status = ROLE_RESEARCH_STATUS_OK if source_count > 0 else ROLE_RESEARCH_STATUS_EMPTY

    record = _build_role_research_record(
        query=role_query,
        provider=provider,
        source_count=source_count,
        status=status,
        top_findings=top_findings,
    )
    record_role_research_result(
        session_id=session_id,
        role=role,
        query=role_query,
        provider=provider,
        source_count=source_count,
        status=status,
        top_findings=top_findings,
    )
    append_role_activity_event(
        session_id=session_id,
        role=role,
        event_type=ROLE_ACTIVITY_RESEARCH_COMPLETED,
        status=status,
        fields={
            "provider": provider,
            "source_count": source_count,
            "collector_name": getattr(outcome, "collector_name", None),
        },
    )

    return pack, record


def _build_role_research_record(
    *,
    query: str,
    provider: Optional[str],
    source_count: int,
    status: str,
    top_findings: Optional[Sequence[str]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """In-memory mirror of what :func:`record_role_research_result` persists.

    Lets the open-call handler hand the freshly-collected record to the
    renderer without re-loading the (stale, frozen) session via the
    workflow store.
    """

    payload: Dict[str, Any] = {
        "query": (query or "").strip(),
        "provider": str(provider).strip() if provider else None,
        "source_count": int(source_count or 0),
        "status": status or ROLE_RESEARCH_STATUS_OK,
    }
    if top_findings:
        payload["top_findings"] = [
            _truncate_one_line(str(item), 160)
            for item in top_findings
            if item is not None
        ][:5]
    if error:
        payload["error"] = str(error)
    return payload


def _next_research_role(sequence: Sequence[str], current: str) -> Optional[str]:
    found = False
    for role in sequence:
        if found:
            return role
        if role == current:
            found = True
    return None


def _maybe_load_pack(
    pack_loader: Optional[Callable[[WorkflowSession], Any]],
    session: WorkflowSession,
) -> Any:
    if pack_loader is None:
        return _load_pack_from_session_extra(session)
    try:
        return pack_loader(session)
    except Exception:  # noqa: BLE001 - never crash the chain
        return _load_pack_from_session_extra(session)


# ---------------------------------------------------------------------------
# A-M11 — runner-injection hook for the open / turn runner bodies.
# ---------------------------------------------------------------------------
#
# When a configured role-runner dispatcher is registered here, the open
# and turn runner-body closures pass their deterministic outcome through
# :func:`agents.job_queue.standalone_runners.apply_role_runner_to_outcome`
# so the message can come from a real LLM backend (Claude / Codex /
# Ollama). Failure / inactive role / unconfigured runner all keep the
# deterministic outcome intact.

_ROLE_RUNNER_DISPATCH: Optional[Callable[[Any, Any], Any]] = None


def set_role_runner_dispatch(
    dispatch: Optional[Callable[[Any, Any], Any]],
) -> None:
    """Register the gateway-level role-runner dispatcher.

    Called once by the engineering runtime bootstrap (or a test
    fixture) so the open / turn runner bodies can invoke a configured
    LLM backend on top of their deterministic outcome. Pass ``None`` to
    clear (used between tests).
    """

    global _ROLE_RUNNER_DISPATCH
    _ROLE_RUNNER_DISPATCH = dispatch


def get_role_runner_dispatch() -> Optional[Callable[[Any, Any], Any]]:
    return _ROLE_RUNNER_DISPATCH


def _apply_role_runner_for_open_or_turn(
    *,
    outcome: Optional["ResearchTurnOutcome"],
    role: str,
    session_id: str,
    session: Any,
    pack_loader: Optional[Callable[[Any], Any]],
    kind: str,
    payload: Optional[Mapping[str, Any]] = None,
) -> Optional["ResearchTurnOutcome"]:
    """Thin wrapper so the open / turn closures stay readable.

    Returns *outcome* unchanged when no dispatcher is registered or
    the dispatcher's gate excludes the role / runner errors. The
    helper imports the standalone-runner integration lazily to keep
    this module decoupled from the job_queue subpackage at import
    time.
    """

    dispatch = _ROLE_RUNNER_DISPATCH
    if dispatch is None or outcome is None:
        return outcome
    try:
        from yule_engineering.agents.job_queue.standalone_runners import (
            apply_role_runner_to_outcome,
        )
    except Exception:  # noqa: BLE001 - partial install fallback
        return outcome
    return apply_role_runner_to_outcome(
        outcome=outcome,
        dispatch=dispatch,
        role=role,
        session_id=session_id,
        session=session,
        pack_loader=pack_loader or (lambda _s: None),
        kind=kind,
        payload=dict(payload or {}),
    )


# ---------------------------------------------------------------------------
# A-M4 — route role takes through the SQLite job_queue.
# ---------------------------------------------------------------------------


def _run_role_take_via_queue(
    *,
    session_id: str,
    role: str,
    kind: str,
    runner_body: Callable[[], Optional["ResearchTurnOutcome"]],
    payload: Optional[Mapping[str, Any]] = None,
) -> Optional["ResearchTurnOutcome"]:
    """Wrap *runner_body* in a ``role_take`` job_queue lifecycle.

    Drives the row through ``queued → assigned → in_progress → saved``
    on success, or ``failed_retryable`` when *runner_body* raises.
    Returns the runner's :class:`ResearchTurnOutcome` (so callers
    keep their existing return shape) or ``None`` when the worker
    declined to start (duplicate in flight / claimed by another
    worker / runner exception).

    The worker instance is created per call so the gateway / member
    bot can stay stateless toward queue handles. Every call creates
    its own short-lived JobQueue + HeartbeatStore connection — the
    SQLite layer pools / serialises file access via WAL + RLock,
    same as the rest of the orchestrator's storage.
    """

    if not session_id or not role or not kind:
        return None

    try:
        from yule_engineering.agents.job_queue import (
            HeartbeatStore,
            JobQueue,
            RoleTakeWorker,
        )
    except Exception:  # noqa: BLE001 - partial install fallback
        # Queue infra missing (test stub / minimal install) — fall
        # back to running the body directly so legacy callers still
        # produce an outcome. The supervisor sees nothing, but the
        # forum keeps getting comments.
        return runner_body()

    queue = JobQueue()
    worker = RoleTakeWorker(queue=queue, heartbeats=HeartbeatStore())

    def _runner(_job: Any) -> Any:
        return runner_body()

    try:
        outcome = worker.run_one(
            session_id=session_id,
            role=role,
            kind=kind,
            runner=_runner,
            payload=dict(payload or {}),
        )
    except Exception:
        # The worker already moved the row to ``failed_retryable``
        # before re-raising. Member bot stays silent so the forum
        # doesn't post a half-baked comment; the supervisor / status
        # diagnostic surfaces the failure via the queue row.
        return None

    if outcome.skipped_reason is not None:
        return None
    return outcome.runner_result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Duplicate-turn suppression (process-local)
# ---------------------------------------------------------------------------
# Discord may redeliver a marker (e.g. when a member bot's reaction lands
# late). Without a guard the bot would post the same role take twice in a
# row. We keep a small in-memory set keyed by (role, session_id, kind)
# bounded to ``MAX_HANDLED_KEYS`` to avoid unbounded growth.

MAX_HANDLED_KEYS = 256
_HANDLED_TURNS: list[tuple[str, str, str]] = []
_HANDLED_TURNS_SET: set[tuple[str, str, str]] = set()


def _was_recently_handled(*, role: str, session_id: str, kind: str) -> bool:
    return (role, session_id, kind) in _HANDLED_TURNS_SET


def _mark_recently_handled(*, role: str, session_id: str, kind: str) -> None:
    key = (role, session_id, kind)
    if key in _HANDLED_TURNS_SET:
        return
    _HANDLED_TURNS_SET.add(key)
    _HANDLED_TURNS.append(key)
    while len(_HANDLED_TURNS) > MAX_HANDLED_KEYS:
        evicted = _HANDLED_TURNS.pop(0)
        _HANDLED_TURNS_SET.discard(evicted)


def reset_handled_turns_for_tests() -> None:
    """Test hook to clear duplicate-suppression state between cases."""

    _HANDLED_TURNS.clear()
    _HANDLED_TURNS_SET.clear()


# ---------------------------------------------------------------------------
# Role-turn activity events (Phase B observability)
# ---------------------------------------------------------------------------
#
# When a member bot reacts to a research-open / research-turn marker we
# record a lightweight event under ``session.extra["role_turns"][<role>]``
# so the gateway's diagnostic responder can describe which roles actually
# spoke. The event shape is intentionally JSON-friendly so it survives
# the cache round-trip without custom encoders.

ROLE_TURN_STATUS_POSTED = "posted"
ROLE_TURN_STATUS_ERROR = "error"
ROLE_TURN_STATUS_SKIPPED = "skipped"

ROLE_TURN_KIND_OPEN = "open"
ROLE_TURN_KIND_TURN = "turn"
ROLE_TURN_KIND_SYNTHESIS = "synthesis"


def record_role_turn_event(
    *,
    session_id: str,
    role: str,
    kind: str,
    status: str,
    error: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    """Append a role-turn event onto ``session.extra["role_turns"][role]``.

    Best-effort: any exception (cache miss, write failure, malformed
    session) is swallowed so a logging failure never blocks the Discord
    post. The event carries:

    - ``status`` — one of :data:`ROLE_TURN_STATUS_POSTED` /
      :data:`ROLE_TURN_STATUS_ERROR` / :data:`ROLE_TURN_STATUS_SKIPPED`.
    - ``kind`` — :data:`ROLE_TURN_KIND_OPEN` for open-call replies,
      :data:`ROLE_TURN_KIND_TURN` for legacy chained turns,
      :data:`ROLE_TURN_KIND_SYNTHESIS` for the tech-lead closing comment.
    - ``posted_at`` — ISO-8601 timestamp the event was recorded at.
    - ``error`` — stringified failure reason when ``status="error"``.
    """

    if not session_id or not role:
        return
    try:
        from dataclasses import replace as _replace
        from datetime import datetime as _dt
        from yule_engineering.agents.workflow_state import (
            load_session as _load,
            update_session as _update,
        )
    except Exception:  # noqa: BLE001 - partial install fallback
        return
    try:
        session = _load(session_id)
    except Exception:  # noqa: BLE001
        return
    if session is None:
        return

    occurred = (now or _dt.now().astimezone()).replace(microsecond=0)
    event: Dict[str, Any] = {
        "status": status,
        "kind": kind,
        "posted_at": occurred.isoformat(),
    }
    if error:
        event["error"] = str(error)

    extra = dict(getattr(session, "extra", None) or {})
    role_turns = dict(extra.get("role_turns") or {})
    short = role.split("/", 1)[-1].strip() or role
    # Keep history-light: store only the latest event per role so the
    # diagnostic surface stays compact. If callers ever need the full
    # history they can read it from per-event records elsewhere.
    role_turns[short] = event
    extra["role_turns"] = role_turns

    try:
        updated = _replace(session, extra=extra)
    except TypeError:
        # Plain object stub — best-effort mutation.
        try:
            live = getattr(session, "extra", None)
            if isinstance(live, dict):
                live["role_turns"] = role_turns
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        _update(updated, now=occurred)
    except Exception:  # noqa: BLE001 - never crash the bot from a record
        pass


# ---------------------------------------------------------------------------
# Phase 4 — role-scoped research observability
# ---------------------------------------------------------------------------
#
# Live regression: each member bot used to post "이어가겠다" without
# any evidence of role-scoped work. Phase 4 makes the per-role
# collection step that already runs inside ``_collect_role_research_pack``
# observable: every collection writes its outcome under
# ``session.extra['role_research_results'][<role>]`` and appends a
# structured event onto ``session.extra['role_activity_log']`` so the
# status diagnostic / supervisor / live regression test can describe
# *what each role actually did*. The forum comment renderer then pulls
# the saved outcome to surface "조사 결과: N건 (provider: …)" as a
# concrete signal next to the role take.

ROLE_RESEARCH_STATUS_OK = "ok"
ROLE_RESEARCH_STATUS_EMPTY = "empty"
ROLE_RESEARCH_STATUS_FAILED = "failed"

ROLE_ACTIVITY_RESEARCH_STARTED = "research_started"
ROLE_ACTIVITY_RESEARCH_COMPLETED = "research_completed"
ROLE_ACTIVITY_RESEARCH_FAILED = "research_failed"
ROLE_ACTIVITY_ROLE_TAKE_POSTED = "role_take_posted"
ROLE_ACTIVITY_ROLE_TAKE_SKIPPED = "role_take_skipped"


# Hard cap so a long-lived session doesn't grow the activity log
# unbounded — the diagnostic surface only ever needs the recent slice
# and the workflow store round-trips through JSON.
_ROLE_ACTIVITY_LOG_LIMIT = 200


def _load_session_for_record(session_id: str) -> Optional[WorkflowSession]:
    """Best-effort session load shared by the Phase 4 record helpers.

    Returns ``None`` when the workflow store can't be imported (partial
    install fallback) or the lookup raises — both are non-fatal: a
    missing record never blocks the bot from posting.
    """

    if not session_id:
        return None
    try:
        from yule_engineering.agents.workflow_state import load_session as _load
    except Exception:  # noqa: BLE001 - partial install fallback
        return None
    try:
        return _load(session_id)
    except Exception:  # noqa: BLE001
        return None


def _persist_session_extra_update(
    session: WorkflowSession,
    *,
    extra: Dict[str, Any],
    now: datetime,
) -> None:
    """Replace *session*.extra with *extra* and write back to the store.

    Any failure (frozen dataclass refusal, store error) is swallowed —
    Phase 4 records are observability, never authoritative state.
    """

    try:
        from dataclasses import replace as _replace
        from yule_engineering.agents.workflow_state import update_session as _update
    except Exception:  # noqa: BLE001
        return
    try:
        updated = _replace(session, extra=extra)
    except TypeError:
        # Plain object stub — best-effort mutation.
        try:
            live = getattr(session, "extra", None)
            if isinstance(live, dict):
                live.clear()
                live.update(extra)
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        _update(updated, now=now)
    except Exception:  # noqa: BLE001
        pass


def record_role_research_result(
    *,
    session_id: str,
    role: str,
    query: str,
    provider: Optional[str],
    source_count: int,
    status: str = ROLE_RESEARCH_STATUS_OK,
    top_findings: Optional[Sequence[str]] = None,
    error: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    """Stash a role-scoped research outcome under ``session.extra``.

    Writes ``session.extra['role_research_results'][<role-short>]`` to a
    JSON-friendly mapping: ``{query, provider, source_count, status,
    top_findings, recorded_at, error?}``. The renderer in
    :func:`_handle_research_open_call` reads this back to surface "조사
    결과" inside the forum comment so the user sees concrete evidence of
    each role's pass — not just "이어가겠다".

    Best-effort. Any failure (cache miss, load error, frozen dataclass
    refusal) is swallowed — observability must never block the post.
    """

    if not session_id or not role:
        return
    session = _load_session_for_record(session_id)
    if session is None:
        return

    occurred = (now or datetime.now().astimezone()).replace(microsecond=0)
    short = role.split("/", 1)[-1].strip() or role
    payload: Dict[str, Any] = {
        "query": (query or "").strip(),
        "provider": str(provider).strip() if provider else None,
        "source_count": int(source_count or 0),
        "status": status or ROLE_RESEARCH_STATUS_OK,
        "recorded_at": occurred.isoformat(),
    }
    if top_findings:
        # Keep findings short — they're a preview, not a replacement for
        # the pack itself. Cap each line length so the JSON record stays
        # diagnostic-friendly.
        payload["top_findings"] = [
            _truncate_one_line(str(item), 160)
            for item in top_findings
            if item is not None
        ][:5]
    if error:
        payload["error"] = str(error)

    extra = dict(getattr(session, "extra", None) or {})
    bucket = dict(extra.get("role_research_results") or {})
    bucket[short] = payload
    extra["role_research_results"] = bucket
    _persist_session_extra_update(session, extra=extra, now=occurred)


def append_role_activity_event(
    *,
    session_id: str,
    role: str,
    event_type: str,
    status: str = ROLE_RESEARCH_STATUS_OK,
    fields: Optional[Mapping[str, Any]] = None,
    now: Optional[datetime] = None,
) -> None:
    """Append a structured event onto ``session.extra['role_activity_log']``.

    The activity log is the audit trail Phase 5's status diagnostic
    consumes — it answers "누가 어디까지 했어?" without re-running any
    research. Each event is JSON-friendly:

        {timestamp, role, event_type, status, **fields}

    *fields* lets callers attach event-specific context (provider
    name, source count, error string, query). Older events get
    trimmed past :data:`_ROLE_ACTIVITY_LOG_LIMIT` so the log stays
    diagnostic-sized.

    Best-effort: never raises.
    """

    if not session_id or not role or not event_type:
        return
    session = _load_session_for_record(session_id)
    if session is None:
        return

    occurred = (now or datetime.now().astimezone()).replace(microsecond=0)
    short = role.split("/", 1)[-1].strip() or role
    event: Dict[str, Any] = {
        "timestamp": occurred.isoformat(),
        "role": short,
        "event_type": str(event_type),
        "status": status or ROLE_RESEARCH_STATUS_OK,
    }
    if fields:
        for key, value in fields.items():
            if key in event:
                continue
            # Best-effort JSON cleanliness: strings/numbers/None pass
            # through, sequences become lists, anything else is stringified.
            if value is None or isinstance(value, (str, int, float, bool)):
                event[str(key)] = value
            elif isinstance(value, Mapping):
                event[str(key)] = {str(k): v for k, v in value.items()}
            elif isinstance(value, (list, tuple)):
                event[str(key)] = list(value)
            else:
                event[str(key)] = str(value)

    extra = dict(getattr(session, "extra", None) or {})
    log = list(extra.get("role_activity_log") or [])
    log.append(event)
    if len(log) > _ROLE_ACTIVITY_LOG_LIMIT:
        log = log[-_ROLE_ACTIVITY_LOG_LIMIT:]
    extra["role_activity_log"] = log
    _persist_session_extra_update(session, extra=extra, now=occurred)


def _summarize_top_findings_from_outcome(outcome: Any) -> Tuple[str, ...]:
    """Pull a 1–5 line preview of role-relevant findings from a
    :class:`CollectionOutcome` for the activity log + comment renderer.

    The collector's ``pack.findings`` is the canonical store; we use
    ``pack.summary`` as a fallback when no structured findings landed
    (mock collector, sparse providers).
    """

    pack = getattr(outcome, "pack", None) if outcome is not None else None
    if pack is None:
        return ()
    findings = list(getattr(pack, "findings", ()) or ())
    if findings:
        items: list[str] = []
        for finding in findings[:5]:
            text = (
                getattr(finding, "summary", None)
                or getattr(finding, "headline", None)
                or getattr(finding, "title", None)
                or ""
            )
            text = (text or "").strip()
            if text:
                items.append(text)
        if items:
            return tuple(items)
    summary = (getattr(pack, "summary", "") or "").strip()
    if summary:
        return (summary,)
    return ()


def _outcome_provider_name(outcome: Any) -> Optional[str]:
    """Best-effort provider name for *outcome*.

    Falls back to the collector_name when no per-source provider
    metadata is present. Returns ``None`` on a fully empty outcome.
    """

    if outcome is None:
        return None
    pack = getattr(outcome, "pack", None)
    if pack is not None:
        # Source-level provider stamps win when present (Phase 2
        # collector tags each hit with its provider).
        for source in getattr(pack, "sources", ()) or ():
            extra = getattr(source, "extra", None) or {}
            provider = extra.get("provider") if isinstance(extra, Mapping) else None
            if provider:
                return str(provider)
    name = getattr(outcome, "collector_name", None)
    if name:
        return str(name)
    return None


def _load_synthesis_text_from_session_extra(session: WorkflowSession) -> Optional[str]:
    """Return the persisted synthesis text from session.extra if any.

    Falls back to rendering ``research_synthesis`` (structured) when only
    the dict form is present. Empty/missing returns None so callers know
    to recompute.
    """

    extra = getattr(session, "extra", None) or {}
    raw_text = extra.get("research_synthesis_text") if isinstance(extra, dict) else None
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text
    raw = extra.get("research_synthesis") if isinstance(extra, dict) else None
    if not isinstance(raw, dict):
        return None
    try:
        from yule_engineering.agents.deliberation import render_synthesis, synthesis_from_dict
    except Exception:  # noqa: BLE001 - best-effort restore
        return None
    try:
        synthesis = synthesis_from_dict(raw)
    except Exception:  # noqa: BLE001
        return None
    return render_synthesis(synthesis)


def _load_pack_from_session_extra(session: WorkflowSession) -> Any:
    """Best-effort restore of a ResearchPack stored under session.extra.

    The gateway persists the pack at collection time via
    ``pack_to_dict`` under ``session.extra["research_pack"]``. We restore
    it lazily here so the deliberation runs even when the original
    in-memory pack went away (process restart, multi-bot shard, ...).
    Falls back to ``None`` so deliberation runs deterministic templates.
    """

    extra = getattr(session, "extra", None) or {}
    raw = extra.get("research_pack") if isinstance(extra, dict) else None
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        from yule_engineering.agents.research.pack_render import pack_from_dict
    except Exception:  # noqa: BLE001
        return None
    try:
        return pack_from_dict(raw)
    except Exception:  # noqa: BLE001
        return None


def _replay_role_takes_until(
    session: WorkflowSession,
    sequence: Sequence[str],
    target_role: str,
    research_pack: Any,
) -> Tuple[Any, ...]:
    """Recreate prior turns deterministically so each role's take inherits
    the same ``previous_turns`` context regardless of which bot is running."""

    accumulated: list[Any] = []
    for role in sequence:
        if role == target_role:
            break
        take, _ = deliberation_role_turn(
            session,
            _role_address(role),
            research_pack=research_pack,
            previous_turns=tuple(accumulated),
        )
        accumulated.append(take)
    return tuple(accumulated)


def _replay_role_takes(
    session: WorkflowSession,
    sequence: Sequence[str],
    research_pack: Any,
) -> Tuple[Any, ...]:
    accumulated: list[Any] = []
    for role in sequence:
        take, _ = deliberation_role_turn(
            session,
            _role_address(role),
            research_pack=research_pack,
            previous_turns=tuple(accumulated),
        )
        accumulated.append(take)
    return tuple(accumulated)


def _role_address(role: str) -> str:
    cleaned = str(role or "").strip()
    if "/" in cleaned:
        return cleaned
    return f"engineering-agent/{cleaned}"


# ---------------------------------------------------------------------------
# Member-bot entry point
# ---------------------------------------------------------------------------


SessionLoader = Callable[[str], Optional[WorkflowSession]]


def handle_team_turn_message(
    *,
    role: str,
    text: str,
    session_loader: Optional[SessionLoader] = None,
) -> Optional[TeamTurnOutcome]:
    """Decide what (if anything) the bot for *role* should post.

    Pure-Python; the Discord layer is responsible for taking the returned
    ``TeamTurnOutcome.full_post()`` and sending it. Returns ``None`` when:

    - the message has no dispatch marker, or
    - the marker targets a different role, or
    - the session is unknown, or
    - the session does not include this role in its plan, or
    - the role has already posted.
    """

    parsed = parse_dispatch_marker(text)
    if parsed is None:
        return None
    session_id, target_role = parsed
    if target_role is not None and target_role != role:
        return None

    loader = session_loader or load_session
    session = loader(session_id)
    if session is None:
        return None

    try:
        plan = build_turn_plan(session)
    except ValueError:
        return None

    my_turn = next((t for t in plan if t.role == role), None)
    if my_turn is None:
        return None

    if role in played_roles(session):
        return None

    next_turn = _next_unplayed_after(plan, role, session)
    next_directive = dispatch_directive(next_turn) if next_turn else None
    is_final = next_turn is None
    message = my_turn.render()

    research_pack = _load_pack_from_session_extra(session)
    if research_pack is not None:
        sequence = tuple(turn.role for turn in plan)
        _, message = deliberation_role_turn(
            session,
            _role_address(role),
            research_pack=research_pack,
            previous_turns=_replay_role_takes_until(
                session, sequence, role, research_pack
            ),
        )
        if is_final:
            accumulated = _replay_role_takes(session, sequence, research_pack)
            _, synthesis_text = synthesize_thread(
                session, accumulated, research_pack=research_pack
            )
            message = f"{message}\n\n{synthesis_text}"

    return TeamTurnOutcome(
        turn=my_turn,
        message=message,
        next_directive=next_directive,
        is_final=is_final,
    )


def _next_unplayed_after(
    plan: Sequence[TeamTurn],
    role: str,
    session: WorkflowSession,
) -> Optional[TeamTurn]:
    played = set(played_roles(session)) | {role}
    saw_self = False
    for turn in plan:
        if turn.role == role:
            saw_self = True
            continue
        if not saw_self:
            continue
        if turn.role not in played:
            return turn
    return None


# ---------------------------------------------------------------------------
# Deliberation-aware extension (pack-driven structured turns)
# ---------------------------------------------------------------------------


def deliberation_role_turn(
    session: WorkflowSession,
    role: str,
    *,
    research_pack: Optional[ResearchPack] = None,
    previous_turns: Sequence[RoleTake] = (),
    runner_fn=None,
) -> Tuple[RoleTake, str]:
    """Produce a structured role take + rendered Discord text.

    Sits next to ``format_role_turn_text`` for callers that have a
    ``ResearchPack`` (e.g. forum-driven sessions) and want the richer
    contract instead of the bare templated line. ``runner_fn`` is the
    optional LLM hook; when None or when it raises, the deterministic
    fallback inside ``run_role_deliberation`` handles the response.

    Right before the run, we ask the local memory layer for relevant
    past notes/policies/workflow artifacts and stash them on the
    ``DeliberationContext``. Retrieval failure is logged and ignored —
    deterministic fallback runs unchanged.
    """

    memory_context = _retrieve_memory_for_role(
        role=role,
        session=session,
        research_pack=research_pack,
    )
    context = DeliberationContext(
        session=session,
        role=role,
        research_pack=research_pack,
        previous_turns=tuple(previous_turns),
        memory_context=memory_context,
    )
    take = run_role_deliberation(context, runner_fn=runner_fn)
    return take, render_role_take(take)


def _retrieve_memory_for_role(
    *,
    role: str,
    session: WorkflowSession,
    research_pack: Optional[ResearchPack],
):
    """Best-effort memory retrieval — never raises into the caller."""

    try:
        from yule_engineering.memory.retrieval import fetch_role_context
    except Exception:  # noqa: BLE001 - memory layer optional
        return ()
    query_parts: list[str] = []
    if research_pack is not None and getattr(research_pack, "title", None):
        query_parts.append(str(research_pack.title))
    if getattr(session, "prompt", None):
        query_parts.append(str(session.prompt))
    query = " ".join(query_parts).strip()
    if not query:
        return ()
    try:
        hits = fetch_role_context(
            role=role,
            query=query,
            task_type=getattr(session, "task_type", None),
            limit=3,
        )
    except Exception:  # noqa: BLE001 - retrieval is opportunistic
        return ()
    return tuple(hits)


def synthesize_thread(
    session: WorkflowSession,
    role_takes: Sequence[RoleTake],
    *,
    research_pack: Optional[ResearchPack] = None,
) -> Tuple[TechLeadSynthesis, str]:
    """Run tech-lead synthesis and return both the dataclass and rendered text.

    Tech-lead memory is fetched right before synthesis and piped into
    ``synthesize`` so prior decisions / policies show up in the consensus
    prefix and open_research entries. Retrieval is opportunistic —
    failure returns an empty tuple and synthesis runs unchanged.
    """

    raw_memory = _retrieve_memory_for_role(
        role="tech-lead",
        session=session,
        research_pack=research_pack,
    )
    # Stamp citation IDs once so the synthesis text and any later
    # observability hook (e.g. format_memory_block) refer to the same
    # labels.
    from yule_engineering.agents.deliberation import assign_citation_ids

    memory_context = assign_citation_ids(raw_memory) if raw_memory else ()
    synth = synthesize(
        session,
        role_takes,
        research_pack=research_pack,
        memory_context=memory_context,
    )
    return synth, render_synthesis(synth)


def closing_message(session: WorkflowSession) -> str:
    """Final wrap-up the last role appends after speaking.

    Kept as a separate helper so the gateway / closing role can reuse it
    without rebuilding the plan.
    """

    return (
        "팀 합류 1차 의견 정리 완료. "
        f"세션 `{session.session_id}` thread에서 이어서 진행합니다."
    )


# ---------------------------------------------------------------------------
# Deliberation loop (tech-lead → roles → tech-lead synthesis)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeliberationTurnRecord:
    """한 역할의 deliberation 결과를 thread-renderable 형태로 묶어 둔다."""

    role: str
    take: RoleTake
    rendered: str


@dataclass(frozen=True)
class DeliberationLoopResult:
    """tech-lead → 역할별 → tech-lead 종합 round-trip 결과.

    runtime의 단일 진실 소스. 실제 Discord chain은 dispatch marker로 끊어 흘러도,
    같은 입력을 한 곳에서 비결정적 부작용 없이 재현하는 entry point가 필요해
    이 helper를 둔다 — 테스트 / 비-Discord 시뮬레이션 / replay 디버깅 용.
    """

    turns: Tuple[DeliberationTurnRecord, ...]
    synthesis: TechLeadSynthesis
    synthesis_text: str


def deliberation_role_sequence(session: WorkflowSession) -> Tuple[str, ...]:
    """``WorkflowSession.role_sequence`` 를 deliberation 진입용으로 정규화한다.

    role_sequence가 비어 있으면 표준 순서(tech-lead → product-designer →
    backend-engineer → frontend-engineer → qa-engineer)를 default로 사용한다.
    이미 ``engineering-agent/<short>`` 형태로 prefix가 붙어 있으면 그대로 둔다.
    """

    raw_sequence = tuple(session.role_sequence or ())
    if not raw_sequence:
        raw_sequence = (
            "tech-lead",
            "product-designer",
            "backend-engineer",
            "frontend-engineer",
            "qa-engineer",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_sequence:
        role = str(raw).strip()
        if not role:
            continue
        if "/" not in role:
            role = f"engineering-agent/{role}"
        if role in seen:
            continue
        seen.add(role)
        normalized.append(role)
    if "engineering-agent/tech-lead" not in normalized:
        normalized.insert(0, "engineering-agent/tech-lead")
    return tuple(normalized)


def run_deliberation_loop(
    session: WorkflowSession,
    *,
    research_pack: Optional[ResearchPack] = None,
    runner_fn: Optional[Callable[[DeliberationContext], Any]] = None,
    role_sequence: Optional[Sequence[str]] = None,
) -> DeliberationLoopResult:
    """역할 순서대로 deliberation 을 흘려 보낸 뒤 tech-lead 종합까지 만든다.

    각 turn은 직전 turn까지의 ``previous_turns`` 를 컨텍스트로 받아 자기
    역할 관점으로 이어 발화한다. ``runner_fn`` 이 있으면 LLM 응답을 사용하고,
    없거나 실패하면 deterministic fallback 으로 대체된다 — 외부 네트워크 없이
    테스트가 항상 통과하도록 보장.
    """

    sequence = tuple(role_sequence) if role_sequence else deliberation_role_sequence(session)
    accumulated: list[RoleTake] = []
    records: list[DeliberationTurnRecord] = []

    for role in sequence:
        take, rendered = deliberation_role_turn(
            session,
            role,
            research_pack=research_pack,
            previous_turns=tuple(accumulated),
            runner_fn=runner_fn,
        )
        accumulated.append(take)
        records.append(
            DeliberationTurnRecord(role=role, take=take, rendered=rendered)
        )

    synthesis, synthesis_text = synthesize_thread(
        session,
        tuple(accumulated),
        research_pack=research_pack,
    )
    return DeliberationLoopResult(
        turns=tuple(records),
        synthesis=synthesis,
        synthesis_text=synthesis_text,
    )
