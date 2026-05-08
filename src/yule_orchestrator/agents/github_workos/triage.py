"""Senior-engineer triage — :func:`senior_triage`.

Takes a :class:`.issue_context.WorkRequest` (built from a GitHub issue
or a Discord intake) and produces a :class:`.models.TriagePlan` shaped
like a real senior engineer's triage note: primary / support /
excluded roles with explicit per-role rationale, scope vs. non-scope,
hidden risks, an implementation step list, a test plan, an approval
gate when code change is implied, and a per-role :class:`RoleWorkOrder`
so each active engineer can act without re-reading the original
ticket.

Reuse contract:

  * Role activation / participation comes from the live
    :func:`agents.lifecycle.role_selection.recommend_active_roles`
    selector — we do **not** re-implement keyword scoring here. That
    way a domain keyword added to a :class:`RoleProfile` stays
    authoritative for both the live runtime and this triage.
  * Identities / coding surfaces come from
    :mod:`.identity` so the work order's ``files_or_domains_to_inspect``
    list matches what the role's executor (G3) will eventually be
    constrained to.
  * The autonomy/permission surface comes from :mod:`.policy` — this
    module emits ``approval_required_actions`` as canonical
    ``ACTION_*`` ids, never freeform strings.

This module is strictly offline. No GitHub / Discord / env / secret
access happens here.
"""

from __future__ import annotations

import re
from typing import List, Mapping, Optional, Tuple

from ..lifecycle.role_selection import (
    ROLE_TECH_LEAD,
    SOURCE_USER_ALL_TEAM,
    SOURCE_USER_EXPLICIT,
    SOURCE_TECH_LEAD_RULE,
    SOURCE_FALLBACK,
    RoleSelection,
    recommend_active_roles,
)
from .identity import agent_identity, all_agent_identities
from .issue_context import SourceKind, WorkRequest
from .models import (
    PermissionLevel,
    RiskLevel,
    RoleWorkOrder,
    TriagePlan,
)
from .policy import (
    ACTION_BRANCH_PLAN,
    ACTION_CODE_DRAFT_PLAN,
    ACTION_DRAFT_PR_PLAN,
    ACTION_PUSH_COMMIT,
    ACTION_READY_PR,
    ACTION_REAL_CODE_WRITE_REQUEST,
    ACTION_TEST_PLAN,
)


# ---------------------------------------------------------------------------
# Coding-needed detection
# ---------------------------------------------------------------------------
#
# We err on the side of "if any of these phrases appears, treat the
# request as code-touching" — it's cheap to add an approval gate when
# none was needed; it's expensive to skip an approval gate when one
# was needed.

_CODING_PHRASES_KOR: Tuple[str, ...] = (
    "코드",
    "코딩",
    "구현",
    "수정해서",
    "수정해줘",
    "수정해 줘",
    "고쳐",
    "고쳐줘",
    "버그 픽스",
    "버그 수정",
    "패치",
    "리팩",
    "리팩터",
    "PR 올려",
    "PR 올려줘",
    "PR 올려 줘",
    "pr 만들어",
    "pr 만들어줘",
    "ready pr",
    "draft pr",
    "draft 만들어",
    "브랜치",
    "병합",
    "merge 해",
    "merge해",
    "머지해",
)

_CODING_PHRASES_ENG: Tuple[str, ...] = (
    "implement",
    "fix the bug",
    "fix bug",
    "patch",
    "refactor",
    "open a pr",
    "open pr",
    "send a pr",
    "raise pr",
    "ship a pr",
    "ship pr",
    "push commit",
    "merge it",
    "rewrite",
    "code change",
)


# Phrases that explicitly *deny* a code change. When any of these appear
# the user has stated "research only / no code touch" and the triage
# layer must respect that even if a coding phrase happens to coexist
# (e.g. "코드는 안 만져도 돼" literally contains the substring 코드).
_NO_CODE_PHRASES: Tuple[str, ...] = (
    "코드 없이",
    "코드는 안 만져",
    "코드는 안 건드",
    "코드 안 만져",
    "코드 안 건드",
    "수정 없이",
    "수정 안 해도",
    "수정 안 하고",
    "자료 조사만",
    "자료만 수집",
    "조사만",
    "research only",
    "no code change",
    "do not modify code",
    "don't modify code",
    "without touching code",
    "without code change",
)


def _is_coding_required(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    for anti in _NO_CODE_PHRASES:
        if anti.lower() in lowered:
            return False
    for phrase in _CODING_PHRASES_KOR:
        if phrase.lower() in lowered:
            return True
    for phrase in _CODING_PHRASES_ENG:
        if phrase in lowered:
            return True
    return False


# ---------------------------------------------------------------------------
# Request-type classification
# ---------------------------------------------------------------------------


_REQUEST_TYPE_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bug|버그|결함|장애|에러|실패)\b"), "bug_fix"),
    (re.compile(r"(?i)\b(테스트|test|회귀|regression|qa)\b"), "test_or_qa"),
    (re.compile(r"(?i)\b(deploy|배포|release|릴리스|cd|pipeline)\b"), "deploy_or_ops"),
    (re.compile(r"(?i)\b(설계|design|architecture|아키텍처|시스템)\b"), "design_review"),
    (re.compile(r"(?i)(전체 ?팀|전 ?직군|모든 관점|all ?roles|all ?team)"), "team_review"),
    (re.compile(r"(?i)\b(ai|rag|cag|llm|agent|에이전트)\b"), "ai_research"),
    (re.compile(r"(?i)\b(ui|ux|디자인|랜딩|페이지|design)\b"), "ui_or_design"),
    (re.compile(r"(?i)\b(문서|docs|readme|가이드)\b"), "docs"),
    (re.compile(r"(?i)\b(조사|research|학습|공부|investigate)\b"), "research"),
)


def _classify_request_type(text: str, *, coding_required: bool) -> str:
    if not text:
        return "unknown"
    for pattern, label in _REQUEST_TYPE_PATTERNS:
        if pattern.search(text):
            return label
    if coding_required:
        return "code_change"
    return "general"


# ---------------------------------------------------------------------------
# Risk + autonomy level mapping
# ---------------------------------------------------------------------------


def _risk_and_autonomy(
    *,
    coding_required: bool,
    request_type: str,
    selection: RoleSelection,
) -> Tuple[RiskLevel, PermissionLevel]:
    # Deploy / release class → high risk, L4 territory.
    if request_type in ("deploy_or_ops",) and coding_required:
        return RiskLevel.HIGH, PermissionLevel.L4_DESTRUCTIVE
    if coding_required:
        # Code change implies an L3 real-write gate; risk depends on
        # whether deploy / infra surfaces are involved.
        if "devops-engineer" in selection.selected_roles:
            return RiskLevel.HIGH, PermissionLevel.L3_REAL_WRITE
        return RiskLevel.MEDIUM, PermissionLevel.L3_REAL_WRITE
    # No code change — at most L2 plan emission.
    if request_type in ("design_review", "team_review", "ai_research"):
        return RiskLevel.MEDIUM, PermissionLevel.L2_PLAN
    return RiskLevel.LOW, PermissionLevel.L1_LIGHT_WRITE


# ---------------------------------------------------------------------------
# Branch suggestion
# ---------------------------------------------------------------------------


_BRANCH_PREFIX_BY_REQUEST_TYPE: Mapping[str, str] = {
    "bug_fix": "fix",
    "test_or_qa": "test",
    "deploy_or_ops": "ops",
    "design_review": "design",
    "team_review": "review",
    "ai_research": "ai",
    "ui_or_design": "design",
    "docs": "docs",
    "research": "research",
    "code_change": "feat",
    "general": "feat",
    "unknown": "feat",
}


def _suggest_branch(request_type: str, title: str) -> str:
    prefix = _BRANCH_PREFIX_BY_REQUEST_TYPE.get(request_type, "feat")
    slug_source = (title or "").lower().strip()
    if not slug_source:
        return f"{prefix}/triage-todo"
    # Replace whitespace + non-allowed chars with "-", trim to 40
    cleaned = re.sub(r"[^a-z0-9]+", "-", slug_source).strip("-")
    cleaned = cleaned[:40].rstrip("-")
    if not cleaned:
        return f"{prefix}/triage-todo"
    return f"{prefix}/{cleaned}"


# ---------------------------------------------------------------------------
# Per-role work-order builders
# ---------------------------------------------------------------------------


def _mission_for_role(role: str, request_type: str) -> str:
    base = {
        "tech-lead": "전체 흐름과 우선순위를 잡고, 다른 역할이 빠뜨린 위험을 짚는다",
        "backend-engineer": "서버 사이드 로직 / API / 데이터 모델의 실현 가능성과 영향 범위를 본다",
        "frontend-engineer": "사용자가 직접 보는 화면 / 흐름 / 상태 관리가 깨지지 않는지 본다",
        "devops-engineer": "CI / 배포 / 런타임 환경 / 시크릿 표면을 본다",
        "qa-engineer": "어떤 회귀가 있을 수 있는지, 어떤 테스트가 새로 필요한지 정한다",
        "ai-engineer": "LLM / agent / RAG 동작과 안전 가드를 본다",
        "product-designer": "사용자 경험 흐름과 인터랙션 / 접근성 결정을 본다",
    }
    return base.get(role, f"{role} 관점에서 작업의 영향과 위험을 본다")


def _expected_output_for_role(role: str) -> str:
    return (
        f"역할: {role} — 위험 / 가정 / 권장 조치 1~3개 + "
        "필요 시 별도 fix 제안. 코드 직접 변경 금지(L3 이상은 승인 게이트)."
    )


def _build_work_order(
    role: str, request_type: str, next_role: Optional[str]
) -> RoleWorkOrder:
    identity = agent_identity(role)
    return RoleWorkOrder(
        role=role,
        mission=_mission_for_role(role, request_type),
        expected_output=_expected_output_for_role(role),
        files_or_domains_to_inspect=identity.coding_surface
        + identity.review_surface[:3],
        done_criteria=(
            "위 mission 항목에 대한 결론이 본문에 있다",
            "권장 조치마다 영향 범위 / 가정 / 다음 액션이 분리돼 있다",
            "코드 직접 변경 시 L3 승인 게이트가 표시돼 있다",
        ),
        handoff_to_next_role=next_role,
    )


def _excluded_rationale(
    role: str, selection: RoleSelection
) -> str:
    """Return a one-line rationale explaining why *role* is excluded.

    Reuses the selector's reason if it has one (Phase 3 surfaces a
    "rule bank score 0 · …" string for excluded roles via
    ``reason_by_role`` for supported sources). Otherwise falls back
    to a domain-shaped sentence so an operator reading the audit can
    answer "왜 이 역할이 안 들어갔어?" without rerunning the selector.
    """

    reason = selection.reason_by_role.get(role)
    if reason:
        return reason
    fallbacks = {
        "tech-lead": "(unexpected) tech-lead 는 항상 포함이어야 하지만 selector 가 제외했다",
        "backend-engineer": "서버 / API / 데이터 모델 키워드가 본문에 없다",
        "frontend-engineer": "UI / 사용자 흐름 / Next.js 키워드가 본문에 없다",
        "devops-engineer": "CI / 배포 / 인프라 키워드가 본문에 없다",
        "qa-engineer": "테스트 / 회귀 / qa 키워드가 본문에 없다",
        "ai-engineer": "AI / RAG / agent 키워드가 본문에 없다",
        "product-designer": "디자인 / UX / 인터랙션 키워드가 본문에 없다",
    }
    return fallbacks.get(role, "본문에서 이 역할 관련 키워드가 잡히지 않았다")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def senior_triage(request: WorkRequest) -> TriagePlan:
    """Triage *request* into a :class:`TriagePlan`.

    Behaviour highlights:

    * Reuses :func:`recommend_active_roles` so the role selection
      stays consistent with the runtime.
    * **Never fans out unknown to all roles.** When the selector
      returns the legacy fallback (``selection_source == fallback``
      with no explicit/all-team signal), the plan keeps tech-lead as
      primary and demotes the fallback "quartet" to support so the
      excluded list is honest.
    * **All-roles fan-out only on explicit user opt-in** —
      ``selection_source == user_all_team``.
    * For every excluded role, ``rationale_by_role`` carries a
      one-line reason. Excluded roles do **not** get a work order.
    * If the body implies a code change (Korean / English coding
      phrases), ``coding_required=True`` and
      ``approval_required_before_write=True``; the plan adds an
      explicit "tech-lead must request a gateway approval" decision
      and lists the L3+ ACTION_* ids in
      ``approval_required_actions``.
    """

    title = request.title or ""
    body = request.body or ""
    combined = (title + "\n" + body).strip()

    selection = recommend_active_roles(user_prompt=combined)

    # ------------------------------------------------------------------
    # Map selection → primary / support / excluded
    # ------------------------------------------------------------------

    selected = list(selection.selected_roles)
    # tech-lead is always primary by design — the selector also
    # always sticks tech-lead at the head of selected_roles, but be
    # defensive in case a future selector shape ships otherwise.
    primary_role = (
        ROLE_TECH_LEAD
        if ROLE_TECH_LEAD in selected
        else (selected[0] if selected else ROLE_TECH_LEAD)
    )

    if selection.selection_source == SOURCE_USER_ALL_TEAM:
        # Explicit user opt-in: every member role is primary alongside
        # tech-lead. Excluded list is empty.
        support = tuple(r for r in selected if r != primary_role)
        excluded = tuple(selection.excluded_roles)
    elif selection.selection_source == SOURCE_USER_EXPLICIT:
        # User named specific roles. Treat the named non-tech-lead
        # roles as primary support; everyone else is excluded.
        support = tuple(r for r in selected if r != primary_role)
        excluded = tuple(selection.excluded_roles)
    elif selection.selection_source == SOURCE_TECH_LEAD_RULE:
        # Keyword-driven scoring. Use the selector's primary_roles as
        # the *strong* support; reviewer_roles as the lighter support.
        strong = tuple(
            r
            for r in selection.primary_roles
            if r != primary_role
        )
        lighter = tuple(
            r
            for r in selection.reviewer_roles
            if r != primary_role and r not in strong
        )
        support = strong + lighter
        excluded = tuple(selection.excluded_roles)
    else:
        # SOURCE_FALLBACK or anything else — narrow to tech-lead only.
        # The selector's "legacy quartet" is a safety net for the live
        # runtime; the triage surface is stricter and refuses to
        # imply that ai/backend/qa "should" weigh in on a vague
        # request. The operator can still ask for "전체 팀 관점" if
        # they want broad coverage.
        support = ()
        excluded_set = set(all_agent_identities().keys())
        excluded_set.discard(primary_role)
        # Preserve a deterministic ordering — use the canonical role
        # tuple from identities.
        excluded = tuple(
            r for r in all_agent_identities().keys() if r in excluded_set
        )

    # ------------------------------------------------------------------
    # Rationale per role
    # ------------------------------------------------------------------

    rationale: dict[str, str] = {}
    rationale[primary_role] = (
        selection.reason_by_role.get(primary_role)
        or "primary owner — tech-lead always included"
    )
    for role in support:
        rationale[role] = selection.reason_by_role.get(role) or (
            f"{role} 관련 키워드가 본문에 잡혔다 — 활성 참여로 분류"
        )
    for role in excluded:
        rationale[role] = _excluded_rationale(role, selection)

    # ------------------------------------------------------------------
    # Coding-needed detection + risk + autonomy
    # ------------------------------------------------------------------

    coding_required = _is_coding_required(combined)
    request_type = _classify_request_type(
        combined, coding_required=coding_required
    )
    risk_level, autonomy_level = _risk_and_autonomy(
        coding_required=coding_required,
        request_type=request_type,
        selection=selection,
    )

    # ------------------------------------------------------------------
    # Approval-required actions
    # ------------------------------------------------------------------

    approval_actions: List[str] = []
    decisions: List[str] = []
    if coding_required:
        approval_actions.extend(
            [
                ACTION_BRANCH_PLAN,
                ACTION_CODE_DRAFT_PLAN,
                ACTION_TEST_PLAN,
                ACTION_DRAFT_PR_PLAN,
                ACTION_REAL_CODE_WRITE_REQUEST,
                ACTION_PUSH_COMMIT,
                ACTION_READY_PR,
            ]
        )
        decisions.append(
            "tech-lead 가 gateway approval 카드를 먼저 요청해야 실제 코드 변경 가능"
        )
        decisions.append(
            "approval 전까지 모든 role 은 plan / draft / 분석만 산출 — 실제 파일 수정 금지"
        )

    # Discord intake adds a hard rule: never let a Discord-only request
    # skip the approval gate even if the body looks tame.
    if request.kind == SourceKind.DISCORD_INTAKE and coding_required:
        decisions.append(
            "Discord 업무 접수에서 들어온 코드 변경 요청 — 승인 카드 필수"
        )

    # ------------------------------------------------------------------
    # Scope / non-scope / risks / assumptions / step list / test plan
    # ------------------------------------------------------------------

    scope: Tuple[str, ...] = (
        f"primary={primary_role}",
        "본 요청 본문에 명시된 surface 만 다룬다",
    )
    if support:
        scope = scope + (f"support 역할: {', '.join(support)}",)

    non_scope: Tuple[str, ...] = (
        "본 요청과 무관한 surface 의 lint / 리팩 / 리네이밍",
        "tech-lead 가 명시적으로 승인하지 않은 cross-cutting 변경",
    )
    if excluded:
        non_scope = non_scope + (
            f"excluded 역할 영역: {', '.join(excluded)}",
        )

    hidden_risks: Tuple[str, ...] = (
        "요청 본문이 누락한 호출자 / 의존 모듈 영향이 있을 수 있다",
        "테스트 / 회귀 범위가 본문에 명시되지 않았다 — qa 가 보강해야 한다",
    )
    if "devops-engineer" in selected:
        hidden_risks = hidden_risks + (
            "CI / 배포 surface 변경은 main / release 직접 push 금지",
        )

    assumptions: Tuple[str, ...] = (
        "본 요청은 단일 repository scope (cross-repo 변경 아님) 으로 가정",
        "L3 이상 작업은 사용자가 별도 승인 카드로 명시한다",
    )

    implementation_steps: Tuple[str, ...] = (
        "1. tech-lead 가 본 plan 을 검토 + 승인 / 보강",
        "2. primary + support 역할이 자기 work order 의 mission 을 수행",
        "3. qa-engineer 가 test_plan 으로 회귀 / 신규 테스트 범위 확정",
        "4. 코드 변경이 필요하면 approval 카드 → branch_plan → code_draft_plan",
        "5. 승인 후 push_commit / ready_pr — 이 단계는 별도 G3 executor 가 수행",
    )

    test_plan: Tuple[str, ...] = (
        "본 요청과 직접 관련된 단위 테스트 / 회귀 시나리오를 qa-engineer 가 정리",
        "기존 회귀 팩 (`tests/...`) 에 새 테스트를 추가할지, 신규 모듈에 분리할지 결정",
        "라이브 수동 검증이 필요한 경우 `policies/.../live-regression.md` 에 시나리오 추가",
    )
    if "frontend-engineer" in selected:
        test_plan = test_plan + (
            "frontend 변경은 브라우저에서 golden path + 주요 edge case 수동 확인",
        )
    if "devops-engineer" in selected:
        test_plan = test_plan + (
            "CI / workflow 변경은 dry-run 또는 별도 sandbox 에서 1회 확인",
        )

    # ------------------------------------------------------------------
    # Suggested branch
    # ------------------------------------------------------------------

    suggested_branch = _suggest_branch(request_type, title or combined[:60])

    # ------------------------------------------------------------------
    # Role work orders
    # ------------------------------------------------------------------

    active_roles = (primary_role,) + tuple(support)
    work_orders: List[RoleWorkOrder] = []
    for index, role in enumerate(active_roles):
        next_role = (
            active_roles[index + 1] if index + 1 < len(active_roles) else None
        )
        work_orders.append(_build_work_order(role, request_type, next_role))

    return TriagePlan(
        request_type=request_type,
        primary_role=primary_role,
        support_roles=tuple(support),
        excluded_roles=tuple(excluded),
        rationale_by_role=rationale,
        risk_level=risk_level,
        autonomy_level=autonomy_level,
        scope=scope,
        non_scope=non_scope,
        hidden_risks=hidden_risks,
        assumptions=assumptions,
        implementation_steps=implementation_steps,
        test_plan=test_plan,
        approval_required_actions=tuple(approval_actions),
        suggested_branch=suggested_branch,
        role_work_orders=tuple(work_orders),
        coding_required=coding_required,
        approval_required_before_write=coding_required,
        decisions=tuple(decisions),
    )


__all__ = ["senior_triage"]
