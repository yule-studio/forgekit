"""Deliberation loop — structured per-role outputs + tech-lead synthesis.

This is the *contract layer* on top of the existing sequential runtime
(``discord/engineering_team_runtime.py``). It accepts:

- a :class:`WorkflowSession` (dispatcher decisions, write gate state),
- an optional :class:`ResearchPack` (자료 수집 결과),
- ``previous_turns`` — what other roles already said in the same thread,

and produces typed role contracts (one dataclass per role) plus a
:class:`TechLeadSynthesis` that closes the loop with 합의안 / 해야 할 일 /
더 조사할 것 / 사용자 결정 필요 / 승인 필요 여부.

Each role take carries a uniform 4-section contract:

- ``perspective`` — 관점 한 줄 (역할이 이 작업을 어떻게 보는가).
- ``evidence``   — 근거 (ResearchPack에서 본인 역할 우선 source 를 인용).
- ``risks``      — 리스크 (역할 관점에서 보이는 위험).
- ``next_actions`` — 다음 행동 (본인 또는 실행자가 즉시 해야 할 일).

Role-specific historical fields (``task_breakdown``, ``ux_direction``,
``api_impact``, …) remain for backward compatibility and concrete shape;
they are merged into the rendered output alongside the four sections.

Each role also has a **research profile**: an ordered list of
``source_type`` values it cares about most. ``filter_pack_for_role`` and
``evidence_lines_for_role`` use the profile to surface the right
artifacts for that role first (e.g. product-designer sees image
references before raw URLs; backend-engineer sees official_docs first).

LLM runner integration is optional. ``run_role_deliberation`` accepts a
``runner_fn`` injection point; when None or when the runner raises, a
**deterministic fallback** based on session/pack metadata is used so the
loop always produces a usable output. That keeps the MVP testable and
the production path resilient when a backend is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple, Union

from .research_pack import ResearchAttachment, ResearchPack, ResearchSource
from .workflow_state import WorkflowSession


# ---------------------------------------------------------------------------
# Source type catalog (matches policies/.../team-conversation.md §6)
# ---------------------------------------------------------------------------


SOURCE_TYPE_USER_MESSAGE = "user_message"
SOURCE_TYPE_URL = "url"
SOURCE_TYPE_WEB_RESULT = "web_result"
SOURCE_TYPE_IMAGE_REFERENCE = "image_reference"
SOURCE_TYPE_FILE_ATTACHMENT = "file_attachment"
SOURCE_TYPE_GITHUB_ISSUE = "github_issue"
SOURCE_TYPE_GITHUB_PR = "github_pr"
SOURCE_TYPE_CODE_CONTEXT = "code_context"
SOURCE_TYPE_OFFICIAL_DOCS = "official_docs"
SOURCE_TYPE_COMMUNITY_SIGNAL = "community_signal"
SOURCE_TYPE_DESIGN_REFERENCE = "design_reference"


KNOWN_SOURCE_TYPES: Tuple[str, ...] = (
    SOURCE_TYPE_USER_MESSAGE,
    SOURCE_TYPE_URL,
    SOURCE_TYPE_WEB_RESULT,
    SOURCE_TYPE_IMAGE_REFERENCE,
    SOURCE_TYPE_FILE_ATTACHMENT,
    SOURCE_TYPE_GITHUB_ISSUE,
    SOURCE_TYPE_GITHUB_PR,
    SOURCE_TYPE_CODE_CONTEXT,
    SOURCE_TYPE_OFFICIAL_DOCS,
    SOURCE_TYPE_COMMUNITY_SIGNAL,
    SOURCE_TYPE_DESIGN_REFERENCE,
)


# Role research profiles: each role's ordered preference of source_type.
# A source_type not in the profile still ranks (just last). This mapping is
# the single source of truth — both filter_pack_for_role and the fallback
# templates read from it so the policy doc and code can't drift.
ROLE_RESEARCH_PROFILES: Mapping[str, Tuple[str, ...]] = {
    "tech-lead": (
        SOURCE_TYPE_USER_MESSAGE,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_WEB_RESULT,
    ),
    "product-designer": (
        SOURCE_TYPE_IMAGE_REFERENCE,
        SOURCE_TYPE_DESIGN_REFERENCE,
        SOURCE_TYPE_FILE_ATTACHMENT,
        SOURCE_TYPE_USER_MESSAGE,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_WEB_RESULT,
    ),
    "backend-engineer": (
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_WEB_RESULT,
    ),
    "frontend-engineer": (
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_DESIGN_REFERENCE,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_IMAGE_REFERENCE,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_WEB_RESULT,
    ),
    "qa-engineer": (
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_COMMUNITY_SIGNAL,
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_USER_MESSAGE,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_URL,
    ),
    "ai-engineer": (
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_COMMUNITY_SIGNAL,
        SOURCE_TYPE_WEB_RESULT,
        SOURCE_TYPE_URL,
    ),
}


# ---------------------------------------------------------------------------
# Per-role contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TechLeadOpening:
    """tech-lead가 thread 시작에 정리하는 작업 분해.

    All 5 role takes share the same 4-section contract via the
    ``perspective`` / ``evidence`` / ``risks`` / ``next_actions`` fields.
    Role-specific historical fields are kept alongside for callers that
    need the structured shape.
    """

    role: str = "engineering-agent/tech-lead"
    task_breakdown: Sequence[str] = field(default_factory=tuple)
    dependencies: Sequence[str] = field(default_factory=tuple)
    decisions_needed: Sequence[str] = field(default_factory=tuple)
    notes: Optional[str] = None
    perspective: Optional[str] = None
    evidence: Sequence[str] = field(default_factory=tuple)
    risks: Sequence[str] = field(default_factory=tuple)
    next_actions: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class ProductDesignerTake:
    """product-designer 관점의 reference / UX / 시각 방향."""

    role: str = "engineering-agent/product-designer"
    reference_summary: Sequence[str] = field(default_factory=tuple)
    ux_direction: Optional[str] = None
    visual_direction: Optional[str] = None
    risks: Sequence[str] = field(default_factory=tuple)
    perspective: Optional[str] = None
    evidence: Sequence[str] = field(default_factory=tuple)
    next_actions: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class BackendEngineerTake:
    """backend-engineer 관점의 데이터 / API / 저장소 영향."""

    role: str = "engineering-agent/backend-engineer"
    data_impact: Optional[str] = None
    api_impact: Optional[str] = None
    storage_impact: Optional[str] = None
    risks: Sequence[str] = field(default_factory=tuple)
    perspective: Optional[str] = None
    evidence: Sequence[str] = field(default_factory=tuple)
    next_actions: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class FrontendEngineerTake:
    """frontend-engineer 관점의 UI / 상태 / 사용자 흐름."""

    role: str = "engineering-agent/frontend-engineer"
    ui_components: Sequence[str] = field(default_factory=tuple)
    state_strategy: Optional[str] = None
    user_flow: Optional[str] = None
    risks: Sequence[str] = field(default_factory=tuple)
    perspective: Optional[str] = None
    evidence: Sequence[str] = field(default_factory=tuple)
    next_actions: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class QaEngineerTake:
    """qa-engineer 관점의 검증 기준 / 리스크 / 회귀."""

    role: str = "engineering-agent/qa-engineer"
    acceptance_criteria: Sequence[str] = field(default_factory=tuple)
    risks: Sequence[str] = field(default_factory=tuple)
    regression_targets: Sequence[str] = field(default_factory=tuple)
    perspective: Optional[str] = None
    evidence: Sequence[str] = field(default_factory=tuple)
    next_actions: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class AiEngineerTake:
    """ai-engineer 관점의 모델/메모리/RAG/평가 영향."""

    role: str = "engineering-agent/ai-engineer"
    model_strategy: Optional[str] = None
    memory_strategy: Optional[str] = None
    retrieval_strategy: Optional[str] = None
    evaluation_strategy: Optional[str] = None
    risks: Sequence[str] = field(default_factory=tuple)
    perspective: Optional[str] = None
    evidence: Sequence[str] = field(default_factory=tuple)
    next_actions: Sequence[str] = field(default_factory=tuple)


RoleTake = Union[
    TechLeadOpening,
    ProductDesignerTake,
    BackendEngineerTake,
    FrontendEngineerTake,
    QaEngineerTake,
    AiEngineerTake,
]


@dataclass(frozen=True)
class TechLeadSynthesis:
    """thread 마지막 tech-lead 종합."""

    consensus: str
    todos: Sequence[str] = field(default_factory=tuple)
    open_research: Sequence[str] = field(default_factory=tuple)
    user_decisions_needed: Sequence[str] = field(default_factory=tuple)
    approval_required: bool = False
    approval_reason: Optional[str] = None


SYNTHESIS_PERSIST_VERSION = 1


def synthesis_to_dict(synthesis: TechLeadSynthesis) -> dict:
    """Serialize a :class:`TechLeadSynthesis` for ``session.extra``.

    The ``v`` key lets future readers branch on schema changes; current
    consumers read ``v=1``.
    """

    return {
        "v": SYNTHESIS_PERSIST_VERSION,
        "consensus": synthesis.consensus,
        "todos": list(synthesis.todos),
        "open_research": list(synthesis.open_research),
        "user_decisions_needed": list(synthesis.user_decisions_needed),
        "approval_required": bool(synthesis.approval_required),
        "approval_reason": synthesis.approval_reason,
    }


def synthesis_from_dict(data: Mapping[str, Any]) -> TechLeadSynthesis:
    """Reverse :func:`synthesis_to_dict` — best-effort reconstruction.

    Missing/malformed lists fall back to empty tuples. ``consensus`` is
    coerced to ``str`` so a malformed payload still produces a usable
    synthesis (the export will simply have an empty consensus block).
    """

    consensus = data.get("consensus")
    consensus_text = str(consensus) if consensus is not None else ""
    return TechLeadSynthesis(
        consensus=consensus_text,
        todos=tuple(_synthesis_str_list(data.get("todos"))),
        open_research=tuple(_synthesis_str_list(data.get("open_research"))),
        user_decisions_needed=tuple(
            _synthesis_str_list(data.get("user_decisions_needed"))
        ),
        approval_required=bool(data.get("approval_required")),
        approval_reason=_synthesis_optional_str(data.get("approval_reason")),
    )


def _synthesis_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(item) for item in value if item is not None]
    except TypeError:
        return []


def _synthesis_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class DeliberationContext:
    """Bundled inputs for one role's deliberation turn.

    ``memory_context`` is filled by the retrieval layer (Phase 3) when
    available. Each entry is a ``RetrievedMemory`` with title/snippet/
    source_kind/role/note_kind so a runner can splice it into a prompt
    or a deterministic fallback can quote it. Retrieval failure leaves
    this empty — deterministic takes still work end-to-end.
    """

    session: WorkflowSession
    role: str
    research_pack: Optional[ResearchPack] = None
    previous_turns: Sequence[RoleTake] = field(default_factory=tuple)
    memory_context: Sequence["RetrievedMemory"] = field(default_factory=tuple)


@dataclass(frozen=True)
class RetrievedMemory:
    """One retrieval hit, decoupled from the memory.search SQLite shape.

    Kept here so deliberation/team-runtime can depend on agents.* without
    pulling in the FTS5 layer transitively. The retrieval helper translates
    :class:`yule_orchestrator.memory.MemorySearchResult` to this shape.
    """

    title: str
    snippet: str
    source_kind: str
    role: Optional[str] = None
    note_kind: Optional[str] = None
    path: Optional[str] = None
    score: float = 0.0


# ---------------------------------------------------------------------------
# Runner injection (LLM-backed; optional)
# ---------------------------------------------------------------------------


# Returns either a RoleTake (already structured) or a string the caller will
# parse. For MVP we only support the structured return; raw strings trigger
# fallback so production starts simple and incrementally adds parsers.
RunnerFn = Callable[[DeliberationContext], Any]


# ---------------------------------------------------------------------------
# ResearchSource introspection
# ---------------------------------------------------------------------------


def source_type(source: ResearchSource) -> str:
    """Resolve a ``source_type`` string for *source*.

    Lookup order:

    1. ``source.extra["source_type"]`` if present (most explicit).
    2. Attachment-driven heuristic — kind ``image`` → image_reference,
       kind ``file`` → file_attachment, kind ``embed`` → web_result.
    3. URL host heuristic — github.com → github_issue/pr, mdn/docs.* →
       official_docs, pinterest/notefolio/behance/awwwards/dribbble →
       design_reference.
    4. ``url`` if a URL is present, else ``user_message``.
    """

    extra = source.extra or {}
    explicit = extra.get("source_type")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    if source.attachments:
        first = source.attachments[0]
        kind = (first.kind or "").lower()
        if kind == "image":
            return SOURCE_TYPE_IMAGE_REFERENCE
        if kind == "file":
            return SOURCE_TYPE_FILE_ATTACHMENT
        if kind == "embed":
            return SOURCE_TYPE_WEB_RESULT

    inferred = _infer_from_url(source.source_url)
    if inferred is not None:
        return inferred

    if source.source_url:
        return SOURCE_TYPE_URL
    return SOURCE_TYPE_USER_MESSAGE


def collected_by_role(source: ResearchSource) -> Optional[str]:
    """Best-guess of which role collected *source*.

    Prefers ``source.extra["collected_by_role"]`` when set; otherwise
    falls back to ``source.author_role`` (which is what the forum adapter
    already populates).
    """

    extra = source.extra or {}
    explicit = extra.get("collected_by_role")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if source.author_role:
        return source.author_role
    return None


def source_meta(source: ResearchSource) -> Mapping[str, Any]:
    """Return the structured metadata block for *source* with safe defaults.

    Produces a dict with the keys mandated by the team-conversation
    policy: title, url/attachment_id, source_type, collected_by_role,
    summary, why_relevant, risk_or_limit, collected_at, confidence.

    Renderers and synthesis read this so callers have a single function
    to look at instead of multiple ad-hoc lookups.
    """

    extra = source.extra or {}
    attachment_id = (
        source.attachments[0].url
        if source.attachments and not source.source_url
        else None
    )
    return {
        "title": (source.title or "").strip() or None,
        "url": source.source_url or None,
        "attachment_id": attachment_id,
        "source_type": source_type(source),
        "collected_by_role": collected_by_role(source),
        "summary": (source.summary or "").strip() or None,
        "why_relevant": _stripped_string(extra.get("why_relevant")),
        "risk_or_limit": _stripped_string(extra.get("risk_or_limit")),
        "collected_at": source.posted_at,
        "confidence": _coerce_confidence(extra.get("confidence")),
    }


def filter_pack_for_role(
    pack: Optional[ResearchPack],
    role: str,
) -> Tuple[ResearchSource, ...]:
    """Sort *pack*.sources by *role*'s research profile preference.

    Sources whose type sits earlier in the role's profile come first;
    unknown / out-of-profile types fall to the back but still appear so
    nothing is hidden. Original order is preserved within the same rank.
    """

    if pack is None or not pack.sources:
        return ()
    short = _short_role(role)
    profile = ROLE_RESEARCH_PROFILES.get(short, ())

    indexed = list(enumerate(pack.sources))

    def rank(item: Tuple[int, ResearchSource]) -> Tuple[int, int]:
        idx, source = item
        st = source_type(source)
        try:
            type_rank = profile.index(st)
        except ValueError:
            type_rank = len(profile) + 1
        return type_rank, idx

    indexed.sort(key=rank)
    return tuple(source for _, source in indexed)


# ---------------------------------------------------------------------------
# RetrievedMemory helpers (Phase 3 follow-up)
# ---------------------------------------------------------------------------


def memory_evidence_lines(
    memory_context: Sequence["RetrievedMemory"],
    *,
    limit: int = 2,
) -> Tuple[str, ...]:
    """Render up to *limit* memory hits as evidence-style one-liners.

    Format: ``[memory:<source>·<note_kind>] <title> — <path> · <snippet>``.
    FTS5 highlight markers (``«»``) are stripped so the line stays clean
    in Discord. Empty input or hits with neither title nor snippet are
    skipped — never raises.
    """

    if not memory_context:
        return ()
    out: list[str] = []
    for hit in memory_context:
        title = (getattr(hit, "title", "") or "").strip()
        snippet = _strip_fts_markers(
            (getattr(hit, "snippet", "") or "").strip()
        )
        if not title and not snippet:
            continue
        source_kind = (getattr(hit, "source_kind", "") or "memory").strip() or "memory"
        note_kind = getattr(hit, "note_kind", None)
        tag = f"{source_kind}·{note_kind}" if note_kind else source_kind
        path = (getattr(hit, "path", None) or "").strip()
        line = f"[memory:{tag}] {title or '(제목 없음)'}"
        if path:
            line += f" — {path}"
        if snippet:
            line += f" · {snippet[:160]}"
        out.append(line)
        if len(out) >= max(0, limit):
            break
    return tuple(out)


def memory_hint_for_role(
    memory_context: Sequence["RetrievedMemory"],
    *,
    kind: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 1,
) -> Optional[str]:
    """Return a short ``"기억된 …: <title>"`` style phrase for the first hit.

    Filters by ``note_kind`` and/or ``source_kind`` if provided. Returns
    ``None`` when no hit matches — callers branch on None to keep their
    deterministic output stable.
    """

    if not memory_context:
        return None
    matches = []
    for hit in memory_context:
        if kind is not None and getattr(hit, "note_kind", None) != kind:
            continue
        if source is not None and getattr(hit, "source_kind", None) != source:
            continue
        matches.append(hit)
        if len(matches) >= max(1, limit):
            break
    if not matches:
        return None
    first = matches[0]
    title = (getattr(first, "title", "") or "").strip() or "(제목 없음)"
    return title


def _strip_fts_markers(text: str) -> str:
    """Remove FTS5 highlight markers (``«»``) and trim whitespace."""

    if not text:
        return ""
    return text.replace("«", "").replace("»", "").strip()


def evidence_lines_for_role(
    pack: Optional[ResearchPack],
    role: str,
    *,
    limit: int = 3,
) -> Tuple[str, ...]:
    """Render up to *limit* evidence lines using the role's profile order.

    Each line is shaped ``[<source_type>] <title> — <url> · <why_relevant>``
    so the role's perspective is grounded in concrete artifacts instead of
    free-floating prose. The rendered string is what bot members post; the
    structured ``source_meta`` is what synthesis can introspect later.
    """

    sources = filter_pack_for_role(pack, role)
    if not sources:
        return ()
    lines: list[str] = []
    for src in sources[:limit]:
        meta = source_meta(src)
        title = meta["title"] or "(제목 없음)"
        st = meta["source_type"]
        ref = meta["url"] or meta["attachment_id"] or "(reference 미상)"
        line = f"[{st}] {title} — {ref}"
        why = meta["why_relevant"]
        if why:
            line += f" · {why}"
        lines.append(line)
    return tuple(lines)


def role_specific_attachments(
    pack: Optional[ResearchPack],
    role: str,
) -> Tuple[ResearchAttachment, ...]:
    """Attachments tied to sources prioritized for *role*.

    Used by product-designer/frontend-engineer fallbacks to mention
    image/file references in evidence even when the source itself has
    no URL.
    """

    if pack is None:
        return ()
    seen: dict[Tuple[str, str], ResearchAttachment] = {}
    for src in filter_pack_for_role(pack, role):
        for att in src.attachments:
            key = (att.kind, att.url)
            if key not in seen:
                seen[key] = att
    return tuple(seen.values())


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run_role_deliberation(
    context: DeliberationContext,
    *,
    runner_fn: Optional[RunnerFn] = None,
) -> RoleTake:
    """Produce one role's structured take.

    Tries *runner_fn* first; on None / exception / unstructured return,
    falls back to deterministic templates that read role research profile,
    research pack, previous_turns, and session metadata.
    """

    if runner_fn is not None:
        try:
            outcome = runner_fn(context)
        except Exception:  # noqa: BLE001 - fall back to deterministic template
            outcome = None
        else:
            structured = _coerce_structured_outcome(outcome, context.role)
            if structured is not None:
                return structured

    return _deterministic_role_take(context)


def synthesize(
    session: WorkflowSession,
    role_takes: Sequence[RoleTake],
    *,
    research_pack: Optional[ResearchPack] = None,
    memory_context: Sequence["RetrievedMemory"] = (),
) -> TechLeadSynthesis:
    """Produce the final tech-lead synthesis from collected role takes.

    Pure-deterministic for MVP. A future iteration may delegate to an LLM
    runner; the dataclass shape is the contract either way.

    When ``memory_context`` is provided, prior decision/policy memories
    are folded into the consensus prefix and open_research follow-ups so
    the synthesis visibly references past notes — the seam the gateway
    forum hook and synthesize_thread now feed.
    """

    todos: list[str] = []
    open_research: list[str] = []
    user_decisions: list[str] = []

    for take in role_takes:
        todos.extend(_todos_from_take(take))
        if isinstance(take, TechLeadOpening):
            user_decisions.extend(take.decisions_needed)

    # Reference gaps → open research.
    if research_pack is None or not research_pack.urls:
        open_research.append("자료 reference 보강 필요 — 현재 thread에 첨부된 링크 없음")
    if research_pack is not None and 0 < len(research_pack.urls) < 3:
        open_research.append(
            "권장 reference 3건 이상이 아직 모이지 않았습니다 — 추가 자료 수집 권장"
        )

    # Per-role research profile gaps: if a role spoke but its profile's
    # top type was missing, surface a follow-up.
    if research_pack is not None and research_pack.sources:
        for take in role_takes:
            short = _short_role(getattr(take, "role", ""))
            profile = ROLE_RESEARCH_PROFILES.get(short)
            if not profile:
                continue
            available = {source_type(s) for s in research_pack.sources}
            top_type = profile[0]
            if top_type not in available:
                open_research.append(
                    f"{short} 우선 자료 유형({top_type})이 비어 있음 — 보강 권장"
                )

    # Memory-driven follow-ups: surface relevant prior decisions/policies
    # as additional research/decision items so the operator sees that
    # synthesis read past memory before proposing the consensus.
    decision_memory_hit = memory_hint_for_role(memory_context, kind="decision")
    if decision_memory_hit:
        todos.append(f"이전 결정({decision_memory_hit}) 재확인")
    policy_memory_hit = memory_hint_for_role(memory_context, source="policy")
    if policy_memory_hit:
        open_research.append(
            f"관련 정책({policy_memory_hit}) 검토 후 합의안 확정"
        )

    approval_required = bool(session.write_requested) and not _session_approved(session)
    approval_reason = (
        session.write_blocked_reason
        if approval_required and session.write_blocked_reason
        else (
            "쓰기 작업 승인이 필요합니다."
            if approval_required
            else None
        )
    )

    consensus = _consensus_summary(session, role_takes)
    if decision_memory_hit:
        consensus = f"기억된 결정({decision_memory_hit}) 맥락에서: {consensus}"
    return TechLeadSynthesis(
        consensus=consensus,
        todos=tuple(_dedup_keep_order(todos)),
        open_research=tuple(_dedup_keep_order(open_research)),
        user_decisions_needed=tuple(_dedup_keep_order(user_decisions)),
        approval_required=approval_required,
        approval_reason=approval_reason,
    )


def render_role_take(take: RoleTake) -> str:
    """Render a role take as a Discord-friendly multi-line string.

    Always emits the 4-section contract (관점 / 근거 / 리스크 / 다음 행동)
    plus the role's specific structured fields. Empty sections render as
    "없음" so readers can tell missing vs. not-applicable apart.
    """

    if isinstance(take, TechLeadOpening):
        return _render_tech_lead_opening(take)
    if isinstance(take, ProductDesignerTake):
        return _render_product_designer(take)
    if isinstance(take, BackendEngineerTake):
        return _render_backend_engineer(take)
    if isinstance(take, FrontendEngineerTake):
        return _render_frontend_engineer(take)
    if isinstance(take, QaEngineerTake):
        return _render_qa_engineer(take)
    if isinstance(take, AiEngineerTake):
        return _render_ai_engineer(take)
    raise TypeError(f"unsupported role take type: {type(take)!r}")


def render_synthesis(synth: TechLeadSynthesis) -> str:
    lines: list[str] = ["**[tech-lead 종합]**"]
    lines.append(f"합의안: {synth.consensus}")
    lines.append(_bullet_block("해야 할 일", synth.todos))
    lines.append(_bullet_block("더 조사할 것", synth.open_research))
    lines.append(_bullet_block("사용자 결정 필요", synth.user_decisions_needed))
    if synth.approval_required:
        reason = synth.approval_reason or "쓰기 승인 필요"
        lines.append(f"승인 필요: yes — {reason}")
    else:
        lines.append("승인 필요: no")
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# Deterministic fallback templates
# ---------------------------------------------------------------------------


def _deterministic_role_take(context: DeliberationContext) -> RoleTake:
    role_short = _short_role(context.role)
    if role_short == "tech-lead":
        return _fallback_tech_lead_opening(context)
    if role_short == "product-designer":
        return _fallback_product_designer(context)
    if role_short == "backend-engineer":
        return _fallback_backend_engineer(context)
    if role_short == "frontend-engineer":
        return _fallback_frontend_engineer(context)
    if role_short == "qa-engineer":
        return _fallback_qa_engineer(context)
    if role_short == "ai-engineer":
        return _fallback_ai_engineer(context)
    # Unknown role — coerce to a generic tech-lead-shaped take so callers
    # always get something renderable.
    return TechLeadOpening(
        role=context.role,
        task_breakdown=(f"{context.role} 영역 검토",),
        notes="해당 역할의 결정 양식이 아직 정의되지 않았습니다.",
        perspective=f"{context.role} 관점에서 합류",
        evidence=evidence_lines_for_role(context.research_pack, context.role),
        next_actions=(f"{context.role} 영역 결정 양식 정리 필요",),
    )


def _fallback_tech_lead_opening(ctx: DeliberationContext) -> TechLeadOpening:
    session = ctx.session
    pack = ctx.research_pack
    breakdown = [
        f"분류 `{session.task_type}` · 실행 후보 `{session.executor_role or 'tech-lead'}`",
        f"요청 본문: {_excerpt(session.prompt, 80)}",
    ]
    if session.role_sequence:
        breakdown.append("참여 후보: " + ", ".join(session.role_sequence))

    dependencies: list[str] = []
    if session.references_user:
        dependencies.append(
            "사용자 제공 reference 우선 검토 — " + ", ".join(session.references_user[:2])
        )
    if pack is not None and pack.urls:
        dependencies.append(
            "ResearchPack 자료 " + str(len(pack.urls)) + "건 thread에 동기화"
        )
    if not dependencies:
        dependencies.append("외부 의존 없음 — 각자 도메인 기준으로 시작")

    decisions_needed: list[str] = []
    if session.write_requested and not _session_approved(session):
        decisions_needed.append("쓰기 진행 승인 (operator 확인)")
    if pack is not None and len(pack.urls) < 3:
        decisions_needed.append("reference 추가 수집 여부")

    perspective = (
        f"`{session.task_type}` 작업 — 실행 후보 `{session.executor_role or 'tech-lead'}` "
        "를 기준으로 각 멤버가 자기 정책에 맞게 검토 의견 제출."
    )
    evidence = list(evidence_lines_for_role(pack, ctx.role))
    evidence.extend(memory_evidence_lines(ctx.memory_context, limit=2))
    risks: list[str] = [
        "멤버별 의견 수렴 지연 — thread 응답이 늦어지면 실행 후보 작업이 막힘",
    ]
    policy_hit = memory_hint_for_role(ctx.memory_context, source="policy")
    if policy_hit:
        risks.append(f"기존 정책과의 충돌 점검: {policy_hit}")
    if session.write_requested and not _session_approved(session):
        risks.append("승인 전 쓰기 진행 시 정책 위반 — write 게이트 차단 유지")
    if pack is None or not pack.urls:
        risks.append("reference 부족 — 결정 근거가 약해 결과 품질 불안정")

    next_actions: list[str] = []
    next_actions.append("각 역할에게 thread에서 본인 관점 take 제출 요청")
    if pack is None or not pack.urls:
        next_actions.append("운영-리서치 forum에 reference 후속 수집 요청")
    if session.write_requested and not _session_approved(session):
        next_actions.append("operator에게 ✅ 승인 요청")

    decision_hit = memory_hint_for_role(ctx.memory_context, kind="decision")
    notes: Optional[str] = None
    if decision_hit:
        notes = f"이전 결정 참고: {decision_hit}"

    return TechLeadOpening(
        task_breakdown=tuple(breakdown),
        dependencies=tuple(dependencies),
        decisions_needed=tuple(decisions_needed),
        notes=notes,
        perspective=perspective,
        evidence=tuple(evidence),
        risks=tuple(risks),
        next_actions=tuple(next_actions),
    )


def _fallback_product_designer(ctx: DeliberationContext) -> ProductDesignerTake:
    pack = ctx.research_pack
    role = ctx.role

    refs: Tuple[str, ...] = ()
    visual_lines = evidence_lines_for_role(pack, role)
    image_attachments = role_specific_attachments(pack, role)
    if visual_lines:
        refs = visual_lines
    elif ctx.session.references_user:
        refs = tuple(f"[url] {r} — (사용자 제공)" for r in ctx.session.references_user[:3])
    elif ctx.session.references_suggested:
        refs = tuple(
            f"[design_reference] {r} — (task_type 추천)"
            for r in ctx.session.references_suggested[:3]
        )

    # Memory-driven references take priority — surfacing past visual /
    # research notes ahead of pack-derived URLs gives the designer the
    # "we already looked at this before" anchor.
    memory_lines = memory_evidence_lines(ctx.memory_context, limit=2)
    if memory_lines:
        refs = memory_lines + refs

    summary = refs or (
        "reference 미공급 — 사용자 제공 자료 또는 suggested 카테고리 우선",
    )

    risks: list[str] = [
        "기존 디자인 시스템과의 일관성 영향 — 토큰/스타일 변경 범위 한정 필요",
    ]
    if not refs:
        risks.append("reference 부재 — 단순 복제 위험 회피 위해 추가 자료 권장")
    if pack is not None and not _has_visual_signal(pack, role):
        risks.append(
            "이미지/디자인 reference 비어 있음 — 톤·시각 결정의 근거가 없음"
        )

    perspective = (
        "사용자 입장에서 시각·정보 흐름을 어떻게 받아들일지 — "
        "톤과 레이아웃, 그리고 reference에서 차용할 패턴을 결정한다."
    )

    next_actions: list[str] = []
    if image_attachments:
        next_actions.append(
            f"이미지/디자인 첨부 {len(image_attachments)}건 thread에 정리해서 공유"
        )
    next_actions.append("UX 흐름 단계별 wireframe 1차 메모 thread에 첨부")
    if not refs:
        next_actions.append("디자인 reference 1건 이상 추가 수집")

    tech_lead_breakdown = _previous_tech_lead_decisions(ctx.previous_turns)
    if tech_lead_breakdown:
        next_actions.append(
            f"tech-lead 결정 사항({tech_lead_breakdown[0]}) 반영해 시각 가이드 1차 정리"
        )

    return ProductDesignerTake(
        reference_summary=summary,
        ux_direction="현재 흐름 기준으로 step 단위 분해 후 영역별 친절도 점검",
        visual_direction="기존 톤 유지하되 reference에서 색·여백 패턴만 차용",
        risks=tuple(risks),
        perspective=perspective,
        evidence=summary,
        next_actions=tuple(next_actions),
    )


def _fallback_backend_engineer(ctx: DeliberationContext) -> BackendEngineerTake:
    pack = ctx.research_pack
    role = ctx.role
    evidence = list(evidence_lines_for_role(pack, role))
    evidence.extend(memory_evidence_lines(ctx.memory_context, limit=1))

    risks: list[str] = [
        "schema 변경 동시 작업 충돌 가능",
        "기존 cache key 포맷 영향 점검",
    ]
    if pack is not None and not _has_doc_or_code_signal(pack, role):
        risks.append("공식 문서/code_context 부족 — 가정 기반 결정 위험")

    policy_hit = memory_hint_for_role(ctx.memory_context, source="policy")
    if policy_hit:
        risks.append(f"정책 점검: {policy_hit}")

    perspective = (
        "데이터 모델, 외부 API 계약, 인증/권한, 저장소 영향을 점검해 "
        "실행 후보가 안전하게 변경을 적용할 수 있는지 판단한다."
    )

    next_actions: list[str] = [
        "관련 schema/migration 영향 thread에 정리",
        "외부 API 변경 시 backward-compat 메모 PR description에 포함",
    ]
    decision_hit = memory_hint_for_role(ctx.memory_context, kind="decision")
    if decision_hit:
        next_actions.append(
            f"이전 결정({decision_hit}) 데이터 영향 재확인"
        )
    if pack is not None:
        # If product-designer already decided UX direction, surface it as
        # a backend-side validation step.
        designer_ux = _previous_field(ctx.previous_turns, ProductDesignerTake, "ux_direction")
        if designer_ux:
            next_actions.append(
                f"디자이너 UX 방향({designer_ux}) 데이터 흐름과 충돌 여부 확인"
            )

    return BackendEngineerTake(
        data_impact=_first_line(
            ctx.session.prompt,
            "도메인 모델 영향 점검 — schema 변경 여부 확인 필요",
        ),
        api_impact="외부 계약 변경 가능성 검토 — 변경 시 backward compatibility 메모",
        storage_impact="저장소 마이그레이션 필요 시 off-peak 적용 권장",
        risks=tuple(risks),
        perspective=perspective,
        evidence=tuple(evidence),
        next_actions=tuple(next_actions),
    )


def _fallback_frontend_engineer(ctx: DeliberationContext) -> FrontendEngineerTake:
    pack = ctx.research_pack
    role = ctx.role
    evidence = list(evidence_lines_for_role(pack, role))
    evidence.extend(memory_evidence_lines(ctx.memory_context, limit=1))

    risks: list[str] = [
        "모바일 가로폭에서 CTA 절단 가능",
        "에러 메시지 i18n 누락 위험",
    ]
    if pack is not None and not _has_ui_signal(pack, role):
        risks.append("UI 구현 reference/접근성 자료 부족 — 컴포넌트 결정 근거 약함")

    perspective = (
        "디자인 결정과 백엔드 계약을 받아 어떤 컴포넌트로 구현할지, "
        "상태/접근성/반응형을 어떻게 풀지 결정한다."
    )

    next_actions: list[str] = [
        "필수 컴포넌트 분해 + 재사용 가능한 패턴 thread에 정리",
        "접근성(ARIA) 점검 항목 PR checklist에 포함",
    ]
    reference_hit = memory_hint_for_role(
        ctx.memory_context, kind="reference"
    ) or memory_hint_for_role(ctx.memory_context, kind="decision")
    if reference_hit:
        next_actions.append(
            f"이전 reference({reference_hit}) 컴포넌트 패턴 재사용 검토"
        )
    designer_visual = _previous_field(ctx.previous_turns, ProductDesignerTake, "visual_direction")
    if designer_visual:
        next_actions.append(
            f"디자이너 시각 방향({designer_visual}) 토큰/스타일 정의에 반영"
        )
    backend_api = _previous_field(ctx.previous_turns, BackendEngineerTake, "api_impact")
    if backend_api:
        next_actions.append(
            f"백엔드 API 변경({backend_api}) 클라이언트 SDK/페치 레이어에 반영"
        )

    return FrontendEngineerTake(
        ui_components=("hero / CTA", "필수 폼", "상태 indicator"),
        state_strategy="form 상태는 로컬, 검증 결과만 글로벌 — 기존 패턴 유지",
        user_flow="첫 화면 → 정보 입력 → 검증 → 결과 노출 4단계 유지",
        risks=tuple(risks),
        perspective=perspective,
        evidence=tuple(evidence),
        next_actions=tuple(next_actions),
    )


def _fallback_qa_engineer(ctx: DeliberationContext) -> QaEngineerTake:
    pack = ctx.research_pack
    role = ctx.role
    evidence = list(evidence_lines_for_role(pack, role))
    evidence.extend(memory_evidence_lines(ctx.memory_context, limit=1))

    risks: list[str] = [
        "기존 회귀 케이스 영향",
        "비동기 race condition",
    ]
    if pack is not None and not _has_qa_signal(pack, role):
        risks.append(
            "장애/회귀 사례 reference 부족 — risk-based test 우선 순위 약함"
        )
    workflow_hit = memory_hint_for_role(ctx.memory_context, source="workflow")
    if workflow_hit:
        risks.append(f"과거 세션({workflow_hit}) 회귀 위험 재점검")

    perspective = (
        "수용 기준과 회귀 영향을 정의해 실행 후보가 만든 변경이 "
        "기존 사용자/플로우를 깨뜨리지 않는지 검증한다."
    )

    next_actions: list[str] = [
        "수용 기준 thread에 commit-by-commit 매핑",
        "회귀 묶음 영향 확인 — 실행 후보 PR에 라벨 부착",
    ]
    decision_hit = memory_hint_for_role(ctx.memory_context, kind="decision")
    if decision_hit:
        next_actions.append(
            f"이전 결정({decision_hit}) 영향 회귀 시나리오 보강"
        )
    backend_data = _previous_field(ctx.previous_turns, BackendEngineerTake, "data_impact")
    if backend_data:
        next_actions.append(
            f"백엔드 데이터 영향({backend_data}) 회귀 시나리오 1건 추가"
        )
    frontend_flow = _previous_field(ctx.previous_turns, FrontendEngineerTake, "user_flow")
    if frontend_flow:
        next_actions.append(
            f"프론트 사용자 흐름({frontend_flow}) e2e 시나리오 1건 추가"
        )

    regression_targets: list[str] = [
        "회원가입 onboarding 회귀 묶음",
        "공통 layout 컴포넌트",
    ]
    if decision_hit:
        regression_targets.append(f"이전 결정 영역({decision_hit}) 회귀 추적")

    return QaEngineerTake(
        acceptance_criteria=(
            "주요 흐름 e2e 1건 추가",
            "에러/빈 상태 스냅샷 확인",
        ),
        risks=tuple(risks),
        regression_targets=tuple(regression_targets),
        perspective=perspective,
        evidence=tuple(evidence),
        next_actions=tuple(next_actions),
    )


def _fallback_ai_engineer(ctx: DeliberationContext) -> AiEngineerTake:
    """ai-engineer 관점의 모델·메모리·검색·평가 영향 정리."""

    pack = ctx.research_pack
    role = ctx.role
    pack_evidence = evidence_lines_for_role(pack, role)
    memory_lines = memory_evidence_lines(ctx.memory_context, limit=2)
    evidence = tuple(list(pack_evidence) + list(memory_lines))

    risks: list[str] = [
        "context window 초과로 응답 품질 저하 가능",
        "벡터 인덱스가 비어 있으면 first-pass quality 낮음",
    ]
    if pack is None or not pack_evidence:
        risks.append("RAG/메모리 reference 부족 — 모델 결정 근거 약함")
    decision_hit = memory_hint_for_role(ctx.memory_context, kind="decision")
    if decision_hit:
        risks.append(
            f"기존 결정({decision_hit})과 retrieval 결과 일관성 점검"
        )

    perspective = (
        "사용자 자료를 모델 컨텍스트로 어떻게 들여보내고, 메모리/RAG 구조와 "
        "평가 신호를 어떻게 분리할지 정리한다."
    )

    next_actions: list[str] = [
        "session 단위 메모리 구조와 외부 vault 동기화 경계 정의",
        "RAG retrieval/recall 평가 metric 1개 명시",
    ]
    backend_data = _previous_field(ctx.previous_turns, BackendEngineerTake, "data_impact")
    if backend_data:
        next_actions.append(
            f"백엔드 데이터 영향({backend_data})에 맞춰 embedding pipeline 동기화"
        )

    return AiEngineerTake(
        model_strategy="작은 컨텍스트 모델로 정리 → 필요 시 long-context 모델로 확장",
        memory_strategy="thread/session 메모리는 in-process로 유지, 외부 vault export는 별도 contract",
        retrieval_strategy="ResearchPack의 source_type 우선순위 그대로 사용 — 임의 scraping 금지",
        evaluation_strategy="역할별 evidence 인용률과 사용자 confirm 비율을 1차 metric으로",
        risks=tuple(risks),
        perspective=perspective,
        evidence=evidence,
        next_actions=tuple(next_actions),
    )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_tech_lead_opening(t: TechLeadOpening) -> str:
    short = _short_role(t.role)
    lines = [f"**[{short}]**"]
    if t.perspective:
        lines.append(f"관점: {t.perspective}")
    lines.append(_bullet_block("작업 분해", t.task_breakdown))
    lines.append(_bullet_block("의존성", t.dependencies))
    lines.append(_bullet_block("결정 필요 사항", t.decisions_needed))
    lines.append(_bullet_block("근거", t.evidence))
    lines.append(_bullet_block("리스크", t.risks))
    lines.append(_bullet_block("다음 행동", t.next_actions))
    if t.notes:
        lines.append(f"메모: {t.notes}")
    return "\n".join(line for line in lines if line)


def _render_product_designer(t: ProductDesignerTake) -> str:
    lines = ["**[product-designer]**"]
    if t.perspective:
        lines.append(f"관점: {t.perspective}")
    lines.append(_bullet_block("레퍼런스", t.reference_summary))
    if t.ux_direction:
        lines.append(f"UX 방향: {t.ux_direction}")
    if t.visual_direction:
        lines.append(f"시각 방향: {t.visual_direction}")
    lines.append(_bullet_block("근거", t.evidence))
    lines.append(_bullet_block("리스크", t.risks))
    lines.append(_bullet_block("다음 행동", t.next_actions))
    return "\n".join(line for line in lines if line)


def _render_backend_engineer(t: BackendEngineerTake) -> str:
    lines = ["**[backend-engineer]**"]
    if t.perspective:
        lines.append(f"관점: {t.perspective}")
    if t.data_impact:
        lines.append(f"데이터 영향: {t.data_impact}")
    if t.api_impact:
        lines.append(f"API 영향: {t.api_impact}")
    if t.storage_impact:
        lines.append(f"저장소 영향: {t.storage_impact}")
    lines.append(_bullet_block("근거", t.evidence))
    lines.append(_bullet_block("리스크", t.risks))
    lines.append(_bullet_block("다음 행동", t.next_actions))
    return "\n".join(line for line in lines if line)


def _render_frontend_engineer(t: FrontendEngineerTake) -> str:
    lines = ["**[frontend-engineer]**"]
    if t.perspective:
        lines.append(f"관점: {t.perspective}")
    lines.append(_bullet_block("UI 컴포넌트", t.ui_components))
    if t.state_strategy:
        lines.append(f"상태 전략: {t.state_strategy}")
    if t.user_flow:
        lines.append(f"사용자 흐름: {t.user_flow}")
    lines.append(_bullet_block("근거", t.evidence))
    lines.append(_bullet_block("리스크", t.risks))
    lines.append(_bullet_block("다음 행동", t.next_actions))
    return "\n".join(line for line in lines if line)


def _render_qa_engineer(t: QaEngineerTake) -> str:
    lines = ["**[qa-engineer]**"]
    if t.perspective:
        lines.append(f"관점: {t.perspective}")
    lines.append(_bullet_block("수용 기준", t.acceptance_criteria))
    lines.append(_bullet_block("근거", t.evidence))
    lines.append(_bullet_block("리스크", t.risks))
    lines.append(_bullet_block("회귀 대상", t.regression_targets))
    lines.append(_bullet_block("다음 행동", t.next_actions))
    return "\n".join(line for line in lines if line)


def _render_ai_engineer(t: AiEngineerTake) -> str:
    lines = ["**[ai-engineer]**"]
    if t.perspective:
        lines.append(f"관점: {t.perspective}")
    if t.model_strategy:
        lines.append(f"모델 전략: {t.model_strategy}")
    if t.memory_strategy:
        lines.append(f"메모리 전략: {t.memory_strategy}")
    if t.retrieval_strategy:
        lines.append(f"검색 전략: {t.retrieval_strategy}")
    if t.evaluation_strategy:
        lines.append(f"평가 전략: {t.evaluation_strategy}")
    lines.append(_bullet_block("근거", t.evidence))
    lines.append(_bullet_block("리스크", t.risks))
    lines.append(_bullet_block("다음 행동", t.next_actions))
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_role(role: str) -> str:
    if "/" in role:
        return role.split("/", 1)[1]
    return role


def _excerpt(text: Optional[str], max_len: int) -> str:
    body = (text or "").strip()
    if not body:
        return "(요청 본문 없음)"
    head = body.splitlines()[0].strip()
    if len(head) > max_len:
        head = head[: max_len - 3] + "..."
    return head or "(요청 본문 없음)"


def _first_line(text: Optional[str], default: str) -> str:
    body = (text or "").strip()
    if not body:
        return default
    head = body.splitlines()[0].strip()
    return head or default


def _bullet_block(label: str, items: Sequence[str]) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return f"{label}: 없음"
    bullets = "\n".join(f"  - {item}" for item in cleaned)
    return f"{label}:\n{bullets}"


def _coerce_structured_outcome(outcome: Any, role: str) -> Optional[RoleTake]:
    """Allow the runner to return either a typed RoleTake or shape-compatible dict."""

    if outcome is None:
        return None
    if isinstance(
        outcome,
        (
            TechLeadOpening,
            ProductDesignerTake,
            BackendEngineerTake,
            FrontendEngineerTake,
            QaEngineerTake,
        ),
    ):
        return outcome
    return None


def _todos_from_take(take: RoleTake) -> list[str]:
    short = _short_role(getattr(take, "role", "") or "")
    items: list[str] = []

    # next_actions is the new uniform source for todos.
    next_actions = getattr(take, "next_actions", None) or ()
    items.extend(f"[{short}] {a}" for a in next_actions if a)

    if items:
        return items

    # Backward-compatible fallback for runners that return a take built
    # from older field set without next_actions.
    if isinstance(take, TechLeadOpening):
        return [f"[tech-lead] {b}" for b in take.task_breakdown]
    if isinstance(take, ProductDesignerTake):
        if take.ux_direction:
            return [f"[product-designer] {take.ux_direction}"]
        return []
    if isinstance(take, BackendEngineerTake):
        items_legacy: list[str] = []
        if take.data_impact:
            items_legacy.append(f"[backend-engineer] data — {take.data_impact}")
        if take.api_impact:
            items_legacy.append(f"[backend-engineer] api — {take.api_impact}")
        return items_legacy
    if isinstance(take, FrontendEngineerTake):
        if take.user_flow:
            return [f"[frontend-engineer] flow — {take.user_flow}"]
        return []
    if isinstance(take, QaEngineerTake):
        return [f"[qa-engineer] {ac}" for ac in take.acceptance_criteria]
    return []


def _consensus_summary(session: WorkflowSession, takes: Sequence[RoleTake]) -> str:
    role_names = [_short_role(getattr(take, "role", "")) for take in takes]
    role_text = ", ".join(r for r in role_names if r) or "tech-lead"
    return (
        f"{session.task_type} 작업에 {role_text}가 참여해 검토했습니다 — "
        f"실행 후보 `{session.executor_role or 'tech-lead'}`가 결정 사항을 반영해 진행."
    )


def _session_approved(session: WorkflowSession) -> bool:
    state = getattr(session, "state", None)
    state_value = getattr(state, "value", state)
    return state_value not in (None, "intake")


def _dedup_keep_order(items: Sequence[str]) -> Tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        text = (item or "").strip()
        if text and text not in seen:
            seen[text] = None
    return tuple(seen.keys())


def _stripped_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_confidence(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result < 0.0:
        return 0.0
    if result > 1.0:
        return 1.0
    return result


_GITHUB_HOSTS = ("github.com", "www.github.com")
_OFFICIAL_DOC_HOST_HINTS = (
    "developer.mozilla.org",
    "docs.python.org",
    "react.dev",
    "vuejs.org",
    "nextjs.org",
    "kubernetes.io",
    "cloud.google.com",
    "docs.aws.amazon.com",
    "learn.microsoft.com",
    "fastapi.tiangolo.com",
    "docs.djangoproject.com",
)
_DESIGN_HOST_HINTS = (
    "pinterest.com",
    "notefolio.net",
    "behance.net",
    "awwwards.com",
    "dribbble.com",
    "canva.com",
    "wix.com",
    "mobbin.com",
    "pageflows.com",
)
_COMMUNITY_HOST_HINTS = (
    "reddit.com",
    "news.ycombinator.com",
    "stackoverflow.com",
    "twitter.com",
    "x.com",
)


def _infer_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    lowered = url.lower()
    if "/issues/" in lowered and any(host in lowered for host in _GITHUB_HOSTS):
        return SOURCE_TYPE_GITHUB_ISSUE
    if "/pull/" in lowered and any(host in lowered for host in _GITHUB_HOSTS):
        return SOURCE_TYPE_GITHUB_PR
    if any(host in lowered for host in _OFFICIAL_DOC_HOST_HINTS):
        return SOURCE_TYPE_OFFICIAL_DOCS
    if any(host in lowered for host in _DESIGN_HOST_HINTS):
        return SOURCE_TYPE_DESIGN_REFERENCE
    if any(host in lowered for host in _COMMUNITY_HOST_HINTS):
        return SOURCE_TYPE_COMMUNITY_SIGNAL
    return None


def _has_visual_signal(pack: ResearchPack, role: str) -> bool:
    for src in filter_pack_for_role(pack, role):
        st = source_type(src)
        if st in (SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_DESIGN_REFERENCE, SOURCE_TYPE_FILE_ATTACHMENT):
            return True
    return False


def _has_doc_or_code_signal(pack: ResearchPack, role: str) -> bool:
    for src in filter_pack_for_role(pack, role):
        st = source_type(src)
        if st in (SOURCE_TYPE_OFFICIAL_DOCS, SOURCE_TYPE_CODE_CONTEXT, SOURCE_TYPE_GITHUB_PR):
            return True
    return False


def _has_ui_signal(pack: ResearchPack, role: str) -> bool:
    for src in filter_pack_for_role(pack, role):
        st = source_type(src)
        if st in (
            SOURCE_TYPE_OFFICIAL_DOCS,
            SOURCE_TYPE_DESIGN_REFERENCE,
            SOURCE_TYPE_IMAGE_REFERENCE,
            SOURCE_TYPE_CODE_CONTEXT,
        ):
            return True
    return False


def _has_qa_signal(pack: ResearchPack, role: str) -> bool:
    for src in filter_pack_for_role(pack, role):
        st = source_type(src)
        if st in (
            SOURCE_TYPE_GITHUB_ISSUE,
            SOURCE_TYPE_COMMUNITY_SIGNAL,
            SOURCE_TYPE_OFFICIAL_DOCS,
        ):
            return True
    return False


def _previous_tech_lead_decisions(takes: Sequence[RoleTake]) -> Tuple[str, ...]:
    for take in takes:
        if isinstance(take, TechLeadOpening) and take.decisions_needed:
            return tuple(take.decisions_needed)
    return ()


def _previous_field(
    takes: Sequence[RoleTake],
    target_type: type,
    field_name: str,
) -> Optional[str]:
    for take in takes:
        if isinstance(take, target_type):
            value = getattr(take, field_name, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None
