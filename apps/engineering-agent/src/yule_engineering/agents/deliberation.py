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

from .research.pack import ResearchAttachment, ResearchPack, ResearchSource
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
    "devops-engineer": (
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_WEB_RESULT,
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


@dataclass(frozen=True)
class DevOpsEngineerTake:
    """devops-engineer 관점의 CI/CD/배포/관측/롤백 영향."""

    role: str = "engineering-agent/devops-engineer"
    cicd_strategy: Optional[str] = None
    deployment_plan: Optional[str] = None
    rollback_plan: Optional[str] = None
    observability: Optional[str] = None
    secrets_and_permissions: Optional[str] = None
    release_checklist: Sequence[str] = field(default_factory=tuple)
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
    DevOpsEngineerTake,
]


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
    :class:`yule_memory.MemorySearchResult` to this shape.

    ``citation_id`` is a short, stable label like ``m1`` that the
    deliberation layer assigns so both deterministic fallbacks and a
    future LLM runner can reference the hit from text without losing the
    full path/score context.
    """

    title: str
    snippet: str
    source_kind: str
    role: Optional[str] = None
    note_kind: Optional[str] = None
    path: Optional[str] = None
    score: float = 0.0
    citation_id: str = ""


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
# RetrievedMemory helpers (moved to deliberation_memory)
# ---------------------------------------------------------------------------
#
# The citation / rendering helpers operate purely on the duck-typed
# RetrievedMemory shape (getattr + dataclasses.replace), so they live in
# the dependency-free :mod:`deliberation_memory` and are re-exported here
# for the existing ``from .deliberation import assign_citation_ids`` style
# importers and the fallback/synthesis modules.
from .deliberation_memory import (  # noqa: E402,F401
    assign_citation_ids,
    format_memory_block,
    memory_evidence_lines,
    memory_hint_for_role,
    memory_hits_by,
    _strip_fts_markers,
)


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

    # ``_deterministic_role_take`` is re-exported at module scope from
    # :mod:`deliberation_fallbacks` (bottom of this file), keeping the
    # orchestrator → extracted edge one-way without an import cycle.
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


def render_role_take(take: RoleTake) -> str:
    """Render a role take as a Discord-friendly multi-line string.

    Always emits the 4-section contract (관점 / 근거 / 리스크 / 다음 행동)
    plus the role's specific structured fields. Empty sections render as
    "없음" so readers can tell missing vs. not-applicable apart.
    """

    # Lazy import — deliberation_render imports the role-take dataclasses
    # and shared helpers from this module; wiring the dispatch here keeps
    # the edge one-way without an import-time cycle.
    from .deliberation_render import (
        _render_ai_engineer,
        _render_backend_engineer,
        _render_devops_engineer,
        _render_frontend_engineer,
        _render_product_designer,
        _render_qa_engineer,
        _render_tech_lead_opening,
    )

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
    if isinstance(take, DevOpsEngineerTake):
        return _render_devops_engineer(take)
    raise TypeError(f"unsupported role take type: {type(take)!r}")


# ---------------------------------------------------------------------------
# Deterministic fallback templates (moved to deliberation_fallbacks)
# ---------------------------------------------------------------------------


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


def _session_approved(session: WorkflowSession) -> bool:
    state = getattr(session, "state", None)
    state_value = getattr(state, "value", state)
    return state_value not in (None, "intake")


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


# ---------------------------------------------------------------------------
# RoleTake -> RoleDraft adapter (C2 wiring)
# ---------------------------------------------------------------------------
#
# 새 council vocabulary (`agents.council.RoleDraft`) 는 owner / challenger /
# reviewer seat 의 1차 draft 를 받는다. 기존 `RoleTake` 들은 owner seat 의
# 산출물로 그대로 재사용된다. 본 adapter 는 *추가 helper* — 기존 호출자는
# 그대로 둔다.
#
# 큰 리팩터링은 C3-C5 의 별 PR 에서. 본 adapter 는 기존 deliberation 자산을
# council 초안 stage 에 끼워 넣는 1줄 진입점만 제공한다.


_ROLE_STRUCTURED_FIELDS: Mapping[type, Tuple[str, ...]] = {
    TechLeadOpening: ("task_breakdown", "dependencies", "decisions_needed", "notes"),
    ProductDesignerTake: ("reference_summary", "ux_direction", "visual_direction"),
    BackendEngineerTake: ("data_impact", "api_impact", "storage_impact"),
    FrontendEngineerTake: ("ui_components", "state_strategy", "user_flow"),
    QaEngineerTake: ("acceptance_criteria", "regression_targets"),
    AiEngineerTake: (
        "model_strategy",
        "memory_strategy",
        "retrieval_strategy",
        "evaluation_strategy",
    ),
    DevOpsEngineerTake: (
        "cicd_strategy",
        "deployment_plan",
        "rollback_plan",
        "observability",
        "secrets_and_permissions",
        "release_checklist",
    ),
}


def _structured_fields_from_take(take: RoleTake) -> dict:
    """Pull role-specific fields off a take into a JSON-friendly dict.

    Generic types fall back to an empty dict so unknown future role
    dataclasses still round-trip cleanly.
    """

    keys = _ROLE_STRUCTURED_FIELDS.get(type(take), ())
    out: dict = {}
    for key in keys:
        value = getattr(take, key, None)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = list(value)
        out[key] = value
    return out


def role_take_to_role_draft(
    take: RoleTake,
    *,
    seat: Any = None,  # SeatRole — typed lazily to avoid cyclic import
    round_index: int = 1,
    provider: Optional[str] = None,
) -> Any:  # RoleDraft — typed lazily for same reason
    """Adapter — convert an existing :class:`RoleTake` into a council
    :class:`RoleDraft`.

    The four shared sections (``perspective`` / ``evidence`` / ``risks``
    / ``next_actions``) map 1:1. Role-specific fields land on
    ``structured_fields`` so reviewers and the synthesis stage can still
    read them by key.

    The adapter is intentionally lightweight — it does **not** mutate the
    take or change deliberation semantics. New runtime code can opt into
    council by calling this helper; legacy callers that just want a
    take.render() keep working unchanged.
    """

    # Lazy import — council depends on lifecycle.council_substage, which
    # this module does not. Keeping the import inside the function lets
    # ``deliberation`` itself stay free of the council edge.
    from .council import RoleDraft, SeatRole

    if seat is None:
        seat = SeatRole.OWNER
    if not isinstance(seat, SeatRole):
        seat = SeatRole(str(seat))

    role = getattr(take, "role", "") or ""

    def _seq(value: Any) -> Tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,) if value.strip() else ()
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value if str(item).strip())
        return ()

    perspective_value = getattr(take, "perspective", None)
    perspective = (
        perspective_value.strip()
        if isinstance(perspective_value, str) and perspective_value.strip()
        else None
    )

    return RoleDraft(
        role=role,
        seat=seat,
        round_index=int(round_index),
        provider=provider,
        perspective=perspective,
        evidence=_seq(getattr(take, "evidence", ())),
        risks=_seq(getattr(take, "risks", ())),
        next_actions=_seq(getattr(take, "next_actions", ())),
        structured_fields=_structured_fields_from_take(take),
    )


def role_takes_to_role_drafts(
    takes: Sequence[RoleTake],
    *,
    seat: Any = None,
    round_index: int = 1,
    provider_for_role: Optional[Mapping[str, str]] = None,
) -> Tuple[Any, ...]:
    """Batch helper — convert a list of takes into the matching drafts.

    ``provider_for_role`` (optional) lets the caller stamp provenance
    when a known provider produced the take. Keys are role addresses
    (``engineering-agent/backend-engineer``).
    """

    provider_for_role = dict(provider_for_role or {})
    out = []
    for take in takes:
        role = getattr(take, "role", "")
        out.append(
            role_take_to_role_draft(
                take,
                seat=seat,
                round_index=round_index,
                provider=provider_for_role.get(role),
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Synthesis re-exports
# ---------------------------------------------------------------------------
#
# The synthesis axis (``TechLeadSynthesis`` + serde + ``synthesize`` +
# ``render_synthesis``) lives in :mod:`deliberation_synthesis`. That module
# imports the shared dataclasses / helpers defined *above* in this file,
# so importing it here — at the bottom, after every definition it needs —
# keeps the edge one-way and free of an import-time cycle. The names are
# re-exported so existing ``from .deliberation import synthesize`` style
# importers keep resolving unchanged.
from .deliberation_synthesis import (  # noqa: E402
    SYNTHESIS_PERSIST_VERSION,
    TechLeadSynthesis,
    render_synthesis,
    synthesis_from_dict,
    synthesis_to_dict,
    synthesize,
)

# The deterministic fallback templates live in :mod:`deliberation_fallbacks`
# (imported lazily by ``run_role_deliberation``). ``_deterministic_role_take``
# is re-exported here — bottom of file, after every helper/dataclass it
# imports back — so existing direct importers keep resolving without a
# top-level import cycle.
from .deliberation_fallbacks import _deterministic_role_take  # noqa: E402,F401

__all__ = [
    "SYNTHESIS_PERSIST_VERSION",
    "TechLeadSynthesis",
    "render_synthesis",
    "synthesis_from_dict",
    "synthesis_to_dict",
    "synthesize",
]
