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
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from ..agents.deliberation import (
    DeliberationContext,
    RoleTake,
    TechLeadSynthesis,
    render_role_take,
    render_synthesis,
    run_role_deliberation,
    synthesize,
)
from ..agents.research_pack import ResearchPack
from ..agents.workflow_state import WorkflowSession, load_session


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
    """

    if session.thread_id is None:
        raise ValueError(
            f"session {session.session_id} has no thread_id; thread must be created before team conversation"
        )
    if not session.role_sequence:
        raise ValueError(
            f"session {session.session_id} has no role_sequence; dispatcher must populate it"
        )

    plan: list[TeamTurn] = []
    for idx, role in enumerate(session.role_sequence):
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

    Rules:
    - ``tech-lead`` always opens the chain.
    - The middle is taken from ``session.role_sequence`` when provided
      (short role names — ``ai-engineer``/``product-designer``/...);
      otherwise from :data:`DEFAULT_RESEARCH_ROLE_SEQUENCE`.
    - Unknown roles pass through (so a future ``security-review`` turn
      still lands in the chain even before its dataclass exists).
    - Duplicates are dropped (first-seen wins).
    - The returned tuple does **not** include the synthesis sentinel —
      callers append :data:`RESEARCH_SYNTHESIS_ROLE` themselves when
      they need it for the closing comment.
    """

    candidate: list[str] = ["tech-lead"]
    requested = (
        list(base)
        if base is not None
        else list(getattr(session, "role_sequence", ()) or DEFAULT_RESEARCH_ROLE_SEQUENCE)
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
        return _handle_research_open_call(
            role=role,
            session_id=open_session_id,
            session_loader=session_loader,
            pack_loader=pack_loader,
        )

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

    sequence = deliberation_research_role_sequence(session)
    if effective_role == RESEARCH_SYNTHESIS_ROLE:
        # tech-lead synthesis comment closes the chain. Re-use the
        # existing ``synthesize_thread`` so the forum and working thread
        # converge on the same wording.
        research_pack = _maybe_load_pack(pack_loader, session)
        accumulated = _replay_role_takes(session, sequence, research_pack)
        _, synthesis_text = synthesize_thread(
            session, accumulated, research_pack=research_pack
        )
        return ResearchTurnOutcome(
            role=role,
            session_id=session_id,
            message=synthesis_text,
            next_directive=None,
            is_synthesis=True,
        )

    if effective_role not in sequence:
        return None

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
    next_directive: Optional[str]
    if next_role is None:
        next_directive = research_dispatch_directive(
            session_id, RESEARCH_SYNTHESIS_ROLE
        )
    else:
        next_directive = research_dispatch_directive(session_id, next_role)

    message = rendered
    if next_directive:
        message = f"{rendered}\n\n{next_directive}"
    return ResearchTurnOutcome(
        role=role,
        session_id=session_id,
        message=message,
        next_directive=next_directive,
        is_synthesis=False,
    )


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

    research_pack = _collect_role_research_pack(session=session, role=role)
    if research_pack is None:
        research_pack = _maybe_load_pack(pack_loader, session)

    take, rendered = deliberation_role_turn(
        session,
        _role_address(role),
        research_pack=research_pack,
        previous_turns=(),
    )
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


def _collect_role_research_pack(*, session: WorkflowSession, role: str) -> Any:
    """Best-effort per-role collection for open-call research.

    The gateway persists its first pack, but autonomous member bots should not
    be limited to that shared seed. Each bot gets one cheap collection pass
    with its own role profile; if the collector is disabled or empty, we fall
    back to the shared pack.
    """

    try:
        from ..agents.research_collector import auto_collect_or_request_more_input
    except Exception:  # noqa: BLE001
        return None
    try:
        outcome = auto_collect_or_request_more_input(
            role=_role_address(role),
            prompt=getattr(session, "prompt", "") or "",
            task_type=getattr(session, "task_type", None),
            user_links=tuple(getattr(session, "references_user", ()) or ()),
            session_id=getattr(session, "session_id", None),
        )
    except Exception:  # noqa: BLE001
        return None
    return getattr(outcome, "pack", None)


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
        from ..agents.research_pack import pack_from_dict
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
        from ..memory.retrieval import fetch_role_context
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

    Tech-lead retrieval also runs here so the synthesis can reference
    prior decisions/policies via the deterministic fallback. Retrieval
    is opportunistic; failure is silent.
    """

    # Retrieval is currently advisory — synthesize() does not consume it
    # in the deterministic path. Calling it keeps the seam warm for an
    # LLM-backed tech-lead and gives observability hooks one place to
    # see what would have been pulled in.
    _retrieve_memory_for_role(
        role="tech-lead",
        session=session,
        research_pack=research_pack,
    )
    synth = synthesize(session, role_takes, research_pack=research_pack)
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
