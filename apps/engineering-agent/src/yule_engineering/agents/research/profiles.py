"""Per-role research profiles for engineering-agent.

각 역할(tech-lead, product-designer, backend-engineer, frontend-engineer,
qa-engineer)이 자료를 수집할 때 어떤 source type을 우선시하는지, 어떤
검색 쿼리 템플릿을 쓸지, 어떤 reference 카테고리를 참고할지를 정의한다.

이 모듈은 :mod:`yule_engineering.agents.research.pack`(자료 그릇)과 별개로
"누가 무엇을 더 비중 있게 모으는가"의 **정책 표**를 책임진다. 실제 자료
수집/검색은 다른 레이어(workflow / runners / discord 입력)가 한다.

설계 원칙:
- I/O 없음. 순수 dataclass + 룩업 + 작은 텍스트 헬퍼.
- 역할별 기본 가중치는 상수로 박혀 있고, ``build_role_query_hints``가
  task_type 신호에 따라 비중을 살짝 조정한다.
- 가중치는 0~10 정수 척도이며 0은 "특별히 우선하지 않음"을 의미한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Source type 상수 (자료 유형)
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
SOURCE_TYPE_RESEARCH_PAPER = "research_paper"
SOURCE_TYPE_MODEL_DOCS = "model_docs"
SOURCE_TYPE_AI_FRAMEWORK_DOCS = "ai_framework_docs"

ALL_SOURCE_TYPES: Tuple[str, ...] = (
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
    SOURCE_TYPE_RESEARCH_PAPER,
    SOURCE_TYPE_MODEL_DOCS,
    SOURCE_TYPE_AI_FRAMEWORK_DOCS,
)


# ---------------------------------------------------------------------------
# Role 상수
# ---------------------------------------------------------------------------

ROLE_TECH_LEAD = "tech-lead"
ROLE_AI_ENGINEER = "ai-engineer"
ROLE_PRODUCT_DESIGNER = "product-designer"
ROLE_BACKEND_ENGINEER = "backend-engineer"
ROLE_FRONTEND_ENGINEER = "frontend-engineer"
ROLE_QA_ENGINEER = "qa-engineer"


ALL_ROLES: Tuple[str, ...] = (
    ROLE_TECH_LEAD,
    ROLE_AI_ENGINEER,
    ROLE_PRODUCT_DESIGNER,
    ROLE_BACKEND_ENGINEER,
    ROLE_FRONTEND_ENGINEER,
    ROLE_QA_ENGINEER,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleResearchProfile:
    """역할별 자료 수집 프로필.

    ``preferred_source_types``는 기본 우선순위(앞쪽이 더 우선).
    ``weight_hints``는 source_type → 0~10 가중치 (없으면 0으로 간주).
    ``suggested_queries``는 검색 쿼리 템플릿(``{topic}``을 치환 변수로).
    ``reference_categories``는 reference-pack.md/dispatcher.py 등에서 쓰이는
    참고 사이트/카테고리 이름들.
    """

    role: str
    preferred_source_types: Tuple[str, ...]
    suggested_queries: Tuple[str, ...]
    reference_categories: Tuple[str, ...]
    weight_hints: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RoleQueryHints:
    """``build_role_query_hints`` 출력 — task_type을 반영한 보정된 가이드.

    ``weighted_source_types``는 (source_type, 가중치) 튜플을 가중치 내림차순
    으로 정렬한 것이다. 호출자가 그대로 자료 수집 우선순위로 쓸 수 있다.
    ``notes``는 어떤 task 신호 때문에 어떤 보정이 적용됐는지 사람이 읽을
    설명 줄들이다 (디버깅/문서화/Discord 메시지에 그대로 노출 가능).
    """

    role: str
    task_type: str
    weighted_source_types: Tuple[Tuple[str, int], ...]
    suggested_queries: Tuple[str, ...]
    reference_categories: Tuple[str, ...]
    notes: Tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Task type → 신호 분류
# ---------------------------------------------------------------------------

# dispatcher.TaskType 값과 동일한 문자열 키. 새 task_type이 들어오면 여기
# 추가하면 된다. 매칭이 없으면 기본 프로필이 그대로 쓰인다.
_DESIGN_HEAVY_TASKS = frozenset(
    {
        "landing-page",
        "visual-polish",
        "onboarding-flow",
        "email-campaign",
    }
)
_BACKEND_HEAVY_TASKS = frozenset(
    {
        "backend-feature",
        "platform-infra",
    }
)
_FRONTEND_HEAVY_TASKS = frozenset(
    {
        "frontend-feature",
        "landing-page",
        "onboarding-flow",
        "visual-polish",
        "email-campaign",
    }
)
_QA_HEAVY_TASKS = frozenset(
    {
        "qa-test",
    }
)


# ---------------------------------------------------------------------------
# Default profiles
# ---------------------------------------------------------------------------


_DEFAULT_PROFILES: Mapping[str, RoleResearchProfile] = {
    ROLE_TECH_LEAD: RoleResearchProfile(
        role=ROLE_TECH_LEAD,
        preferred_source_types=(
            SOURCE_TYPE_USER_MESSAGE,
            SOURCE_TYPE_GITHUB_ISSUE,
            SOURCE_TYPE_GITHUB_PR,
            SOURCE_TYPE_OFFICIAL_DOCS,
            SOURCE_TYPE_CODE_CONTEXT,
            SOURCE_TYPE_URL,
        ),
        suggested_queries=(
            "{topic} architecture overview",
            "{topic} dependency map",
            "{topic} risk and tradeoffs",
            "{topic} rollout plan",
        ),
        reference_categories=(
            "internal docs",
            "ADR/RFC",
            "GitHub history",
        ),
        weight_hints={
            SOURCE_TYPE_USER_MESSAGE: 9,
            SOURCE_TYPE_GITHUB_ISSUE: 8,
            SOURCE_TYPE_GITHUB_PR: 7,
            SOURCE_TYPE_OFFICIAL_DOCS: 6,
            SOURCE_TYPE_CODE_CONTEXT: 6,
            SOURCE_TYPE_URL: 5,
            SOURCE_TYPE_COMMUNITY_SIGNAL: 3,
        },
    ),
    ROLE_AI_ENGINEER: RoleResearchProfile(
        role=ROLE_AI_ENGINEER,
        preferred_source_types=(
            SOURCE_TYPE_OFFICIAL_DOCS,
            SOURCE_TYPE_RESEARCH_PAPER,
            SOURCE_TYPE_MODEL_DOCS,
            SOURCE_TYPE_AI_FRAMEWORK_DOCS,
            SOURCE_TYPE_CODE_CONTEXT,
            SOURCE_TYPE_COMMUNITY_SIGNAL,
        ),
        suggested_queries=(
            "{topic} prompt engineering best practice",
            "{topic} RAG retrieval evaluation",
            "{topic} embedding / vector store options",
            "{topic} hallucination grounding strategy",
            "{topic} agent evaluation metric",
            "{topic} model routing latency cost",
        ),
        reference_categories=(
            "official model docs (Anthropic / OpenAI / Google)",
            "Hugging Face model cards",
            "arXiv / research papers",
            "RAG framework docs (LangChain, LlamaIndex)",
            "vector DB docs (pgvector, Qdrant, Chroma, Weaviate)",
            "agent eval docs (Ragas, TruLens)",
        ),
        weight_hints={
            SOURCE_TYPE_OFFICIAL_DOCS: 10,
            SOURCE_TYPE_RESEARCH_PAPER: 9,
            SOURCE_TYPE_MODEL_DOCS: 9,
            SOURCE_TYPE_AI_FRAMEWORK_DOCS: 8,
            SOURCE_TYPE_CODE_CONTEXT: 6,
            SOURCE_TYPE_COMMUNITY_SIGNAL: 5,
            SOURCE_TYPE_GITHUB_ISSUE: 4,
            SOURCE_TYPE_URL: 3,
            SOURCE_TYPE_USER_MESSAGE: 5,
        },
    ),
    ROLE_PRODUCT_DESIGNER: RoleResearchProfile(
        role=ROLE_PRODUCT_DESIGNER,
        preferred_source_types=(
            SOURCE_TYPE_IMAGE_REFERENCE,
            SOURCE_TYPE_DESIGN_REFERENCE,
            SOURCE_TYPE_FILE_ATTACHMENT,
            SOURCE_TYPE_URL,
            SOURCE_TYPE_USER_MESSAGE,
            SOURCE_TYPE_WEB_RESULT,
        ),
        suggested_queries=(
            "{topic} UI examples",
            "{topic} moodboard",
            "{topic} accessibility checklist",
            "{topic} onboarding flow patterns",
        ),
        reference_categories=(
            "Pinterest Trends",
            "Notefolio",
            "Behance",
            "Awwwards",
            "Canva Design School",
            "Wix Templates",
            "Mobbin",
            "Page Flows",
        ),
        weight_hints={
            SOURCE_TYPE_IMAGE_REFERENCE: 10,
            SOURCE_TYPE_DESIGN_REFERENCE: 9,
            SOURCE_TYPE_FILE_ATTACHMENT: 7,
            SOURCE_TYPE_URL: 5,
            SOURCE_TYPE_USER_MESSAGE: 5,
            SOURCE_TYPE_WEB_RESULT: 4,
            SOURCE_TYPE_COMMUNITY_SIGNAL: 3,
        },
    ),
    ROLE_BACKEND_ENGINEER: RoleResearchProfile(
        role=ROLE_BACKEND_ENGINEER,
        preferred_source_types=(
            SOURCE_TYPE_OFFICIAL_DOCS,
            SOURCE_TYPE_CODE_CONTEXT,
            SOURCE_TYPE_GITHUB_ISSUE,
            SOURCE_TYPE_GITHUB_PR,
            SOURCE_TYPE_URL,
            SOURCE_TYPE_USER_MESSAGE,
        ),
        suggested_queries=(
            "{topic} API reference",
            "{topic} data model",
            "{topic} authentication flow",
            "{topic} migration plan",
            "{topic} infra/deployment notes",
        ),
        reference_categories=(
            "official API docs",
            "DB engine docs",
            "auth provider docs",
            "internal repo code",
        ),
        weight_hints={
            SOURCE_TYPE_OFFICIAL_DOCS: 10,
            SOURCE_TYPE_CODE_CONTEXT: 9,
            SOURCE_TYPE_GITHUB_ISSUE: 6,
            SOURCE_TYPE_GITHUB_PR: 6,
            SOURCE_TYPE_URL: 4,
            SOURCE_TYPE_USER_MESSAGE: 5,
            SOURCE_TYPE_COMMUNITY_SIGNAL: 2,
        },
    ),
    ROLE_FRONTEND_ENGINEER: RoleResearchProfile(
        role=ROLE_FRONTEND_ENGINEER,
        preferred_source_types=(
            SOURCE_TYPE_OFFICIAL_DOCS,
            SOURCE_TYPE_CODE_CONTEXT,
            SOURCE_TYPE_DESIGN_REFERENCE,
            SOURCE_TYPE_URL,
            SOURCE_TYPE_USER_MESSAGE,
            SOURCE_TYPE_WEB_RESULT,
        ),
        suggested_queries=(
            "{topic} component example",
            "{topic} accessibility WCAG",
            "{topic} browser compatibility",
            "{topic} framework guide",
            "{topic} design system mapping",
        ),
        reference_categories=(
            "MDN",
            "framework official docs",
            "design system docs",
            "Awwwards",
            "Mobbin",
            "Page Flows",
        ),
        weight_hints={
            SOURCE_TYPE_OFFICIAL_DOCS: 9,
            SOURCE_TYPE_CODE_CONTEXT: 9,
            SOURCE_TYPE_DESIGN_REFERENCE: 6,
            SOURCE_TYPE_URL: 5,
            SOURCE_TYPE_USER_MESSAGE: 5,
            SOURCE_TYPE_WEB_RESULT: 4,
            SOURCE_TYPE_IMAGE_REFERENCE: 4,
            SOURCE_TYPE_COMMUNITY_SIGNAL: 3,
        },
    ),
    ROLE_QA_ENGINEER: RoleResearchProfile(
        role=ROLE_QA_ENGINEER,
        preferred_source_types=(
            SOURCE_TYPE_USER_MESSAGE,
            SOURCE_TYPE_GITHUB_ISSUE,
            SOURCE_TYPE_CODE_CONTEXT,
            SOURCE_TYPE_OFFICIAL_DOCS,
            SOURCE_TYPE_GITHUB_PR,
            SOURCE_TYPE_COMMUNITY_SIGNAL,
        ),
        suggested_queries=(
            "{topic} acceptance criteria",
            "{topic} regression scenarios",
            "{topic} edge cases",
            "{topic} bug reports",
            "{topic} test strategy",
        ),
        reference_categories=(
            "internal test plan",
            "GitHub issues with bug label",
            "incident postmortems",
            "regression suites",
        ),
        weight_hints={
            SOURCE_TYPE_USER_MESSAGE: 8,
            SOURCE_TYPE_GITHUB_ISSUE: 9,
            SOURCE_TYPE_CODE_CONTEXT: 7,
            SOURCE_TYPE_OFFICIAL_DOCS: 5,
            SOURCE_TYPE_GITHUB_PR: 6,
            SOURCE_TYPE_COMMUNITY_SIGNAL: 4,
            SOURCE_TYPE_URL: 4,
        },
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_role_profile(role: str) -> RoleResearchProfile:
    """Return the default profile for ``role``. Unknown role → ValueError."""

    profile = _DEFAULT_PROFILES.get(role)
    if profile is None:
        available = ", ".join(ALL_ROLES)
        raise ValueError(f"unknown role '{role}'. Available: {available}")
    return profile


def list_role_profiles() -> Tuple[RoleResearchProfile, ...]:
    """All default profiles, in canonical order."""

    return tuple(_DEFAULT_PROFILES[role] for role in ALL_ROLES)


def build_role_query_hints(
    role: str,
    task_type: Optional[str] = None,
    *,
    topic: Optional[str] = None,
) -> RoleQueryHints:
    """Combine the role's default profile with task_type-driven boosts.

    ``task_type``은 ``dispatcher.TaskType``의 ``.value`` 문자열을 그대로 받는
    것을 가정한다 (예: ``"backend-feature"``). 매칭되지 않으면 보정 없이
    기본 프로필이 그대로 반환된다.

    ``topic``이 주어지면 ``suggested_queries``의 ``{topic}`` 자리에 치환된다.
    그렇지 않으면 템플릿 그대로 노출된다.
    """

    profile = get_role_profile(role)
    weights = dict(profile.weight_hints)
    notes: list[str] = []

    normalized_task = (task_type or "").strip().lower() or "unknown"

    if role == ROLE_PRODUCT_DESIGNER and normalized_task in _DESIGN_HEAVY_TASKS:
        weights[SOURCE_TYPE_IMAGE_REFERENCE] = max(weights.get(SOURCE_TYPE_IMAGE_REFERENCE, 0), 10) + 1
        weights[SOURCE_TYPE_DESIGN_REFERENCE] = max(weights.get(SOURCE_TYPE_DESIGN_REFERENCE, 0), 9) + 1
        notes.append(
            f"design-heavy task ({normalized_task}) → image_reference / design_reference 가중치 상향"
        )

    if role == ROLE_BACKEND_ENGINEER and normalized_task in _BACKEND_HEAVY_TASKS:
        weights[SOURCE_TYPE_OFFICIAL_DOCS] = max(weights.get(SOURCE_TYPE_OFFICIAL_DOCS, 0), 10) + 1
        weights[SOURCE_TYPE_CODE_CONTEXT] = max(weights.get(SOURCE_TYPE_CODE_CONTEXT, 0), 9) + 1
        notes.append(
            f"backend-heavy task ({normalized_task}) → official_docs / code_context 가중치 상향"
        )

    if role == ROLE_FRONTEND_ENGINEER and normalized_task in _FRONTEND_HEAVY_TASKS:
        weights[SOURCE_TYPE_CODE_CONTEXT] = max(weights.get(SOURCE_TYPE_CODE_CONTEXT, 0), 9) + 1
        weights[SOURCE_TYPE_OFFICIAL_DOCS] = max(weights.get(SOURCE_TYPE_OFFICIAL_DOCS, 0), 9) + 1
        # framework docs로 분류되는 official_docs와 component 예시(code_context)를 둘 다 끌어올린다.
        # accessibility/browser 관련은 그대로 두되 design_reference도 살짝 올려 디자인-구현 다리 역할을 한다.
        weights[SOURCE_TYPE_DESIGN_REFERENCE] = weights.get(SOURCE_TYPE_DESIGN_REFERENCE, 0) + 1
        notes.append(
            f"frontend-heavy task ({normalized_task}) → component (code_context) / "
            "framework·accessibility (official_docs) 가중치 상향"
        )

    if role == ROLE_QA_ENGINEER and normalized_task in _QA_HEAVY_TASKS:
        weights[SOURCE_TYPE_GITHUB_ISSUE] = max(weights.get(SOURCE_TYPE_GITHUB_ISSUE, 0), 9) + 1
        weights[SOURCE_TYPE_CODE_CONTEXT] = max(weights.get(SOURCE_TYPE_CODE_CONTEXT, 0), 7) + 1
        notes.append(
            f"qa-heavy task ({normalized_task}) → github_issue / code_context 가중치 상향"
        )

    weighted_pairs = tuple(
        sorted(
            ((source_type, int(weight)) for source_type, weight in weights.items() if weight > 0),
            key=lambda pair: (-pair[1], _stable_index(pair[0])),
        )
    )

    suggested = tuple(
        query.replace("{topic}", topic) if topic else query
        for query in profile.suggested_queries
    )

    return RoleQueryHints(
        role=role,
        task_type=normalized_task,
        weighted_source_types=weighted_pairs,
        suggested_queries=suggested,
        reference_categories=profile.reference_categories,
        notes=tuple(notes),
    )


def format_research_hints_block(
    role_sequence: Sequence[str],
    task_type: Optional[str] = None,
    *,
    topic: Optional[str] = None,
    max_queries_per_role: int = 2,
    max_references_per_role: int = 3,
) -> str:
    """Render a Discord-friendly per-role research hints block.

    설계 의도: 사용자가 #업무-접수에서 메시지를 던지면, intake → kickoff →
    forum 게시까지 도는 흐름의 마지막에 "역할별로 어떤 자료를 보면 좋을지"를
    한 묶음으로 보여주려는 것. 각 역할의 ``RoleResearchProfile`` +
    ``build_role_query_hints``가 반환하는 task_type 보정 가중치를 사람이
    읽을 수 있는 형태로 압축한다.

    알 수 없는 role은 조용히 건너뛴다 — supporting role이 늘어나거나 줄어들
    때마다 본 함수를 깨뜨리지 않는다. role_sequence가 비어 있으면 빈 문자열을
    반환해 호출자가 출력 자체를 생략할 수 있다.
    """

    if not role_sequence:
        return ""

    blocks: list[str] = ["**역할별 자료 가이드**"]
    for role in role_sequence:
        try:
            hints = build_role_query_hints(role, task_type, topic=topic)
        except ValueError:
            continue
        top_sources = [src for src, _w in hints.weighted_source_types[:3]]
        queries = list(hints.suggested_queries[:max_queries_per_role])
        references = list(hints.reference_categories[:max_references_per_role])

        line = f"- `{role}`"
        if top_sources:
            line += f" · 우선 자료: {', '.join(top_sources)}"
        blocks.append(line)

        if queries:
            blocks.append(f"  - 추천 쿼리: {' / '.join(queries)}")
        if references:
            blocks.append(f"  - 참고 소스: {', '.join(references)}")

    if len(blocks) == 1:
        # 모든 role이 unknown이라 실제 hint가 없는 경우.
        return ""
    return "\n".join(blocks)


def replace_role_profile_for_tests(
    role: str,
    *,
    preferred_source_types: Optional[Sequence[str]] = None,
    weight_hints: Optional[Mapping[str, int]] = None,
) -> RoleResearchProfile:
    """Return a *new* profile with the given fields swapped (no mutation).

    Mostly useful for tests that want to simulate operator overrides without
    monkeypatching the module-level ``_DEFAULT_PROFILES`` map.
    """

    base = get_role_profile(role)
    return replace(
        base,
        preferred_source_types=tuple(preferred_source_types) if preferred_source_types is not None else base.preferred_source_types,
        weight_hints=dict(weight_hints) if weight_hints is not None else dict(base.weight_hints),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _stable_index(source_type: str) -> int:
    try:
        return ALL_SOURCE_TYPES.index(source_type)
    except ValueError:
        return len(ALL_SOURCE_TYPES)
