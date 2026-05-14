"""Engineering-agent gateway dispatcher.

Decides, for one incoming request, the participant candidates, the execution
candidate / reviewer runner picks, the reference pack to consult, and whether a write may proceed.

Single-executor, multi-advisor: at most one role writes; everyone else
proposes patches or reviews. Source-of-truth for default weights is
``policies/runtime/agents/engineering-agent/role-weights-v0.md``; for
reference packs, ``reference-pack.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence

from .registry import ParticipantsPool


class TaskType(str, Enum):
    BACKEND_FEATURE = "backend-feature"
    FRONTEND_FEATURE = "frontend-feature"
    # P0-J (#145) — Next.js+NestJS+Postgres+Docker Compose 같은 full-stack
    # 요청을 Docker 한 단어로 platform-infra 분류하던 회귀 차단용. 다중
    # application tier (frontend+backend+database 등) 동시 발견 + write
    # intent 시 분류.
    FULL_STACK_APP = "full-stack-app"
    LANDING_PAGE = "landing-page"
    ONBOARDING_FLOW = "onboarding-flow"
    VISUAL_POLISH = "visual-polish"
    EMAIL_CAMPAIGN = "email-campaign"
    QA_TEST = "qa-test"
    PLATFORM_INFRA = "platform-infra"
    UNKNOWN = "unknown"


# role × runner default weights mirroring role-weights-v0.md
ROLE_DEFAULT_WEIGHTS: Mapping[str, Mapping[str, int]] = {
    "tech-lead": {"claude": 9, "gemini": 7, "codex": 5, "ollama": 3},
    "ai-engineer": {"claude": 9, "gemini": 7, "codex": 5, "ollama": 4},
    "backend-engineer": {"claude": 9, "codex": 7, "gemini": 5, "ollama": 3},
    "frontend-engineer": {"claude": 8, "codex": 8, "gemini": 5, "ollama": 3},
    "product-designer": {"gemini": 9, "claude": 8, "codex": 4, "ollama": 3},
    "qa-engineer": {"codex": 9, "claude": 8, "gemini": 5, "ollama": 3},
    "devops-engineer": {"claude": 9, "codex": 7, "gemini": 6, "ollama": 3},
}


# tech-lead always leads; ai-engineer follows for LLM/RAG/research collector
# perspective; implementing roles follow per task type. tech-lead also closes
# the deliberation in the higher-level role_sequence used by run_research_loop.
TASK_ROLE_SEQUENCE: Mapping[TaskType, Sequence[str]] = {
    TaskType.BACKEND_FEATURE: (
        "tech-lead",
        "ai-engineer",
        "product-designer",
        "backend-engineer",
        "qa-engineer",
    ),
    TaskType.FRONTEND_FEATURE: (
        "tech-lead",
        "ai-engineer",
        "product-designer",
        "frontend-engineer",
        "qa-engineer",
    ),
    TaskType.LANDING_PAGE: (
        "tech-lead",
        "ai-engineer",
        "product-designer",
        "frontend-engineer",
        "qa-engineer",
    ),
    TaskType.ONBOARDING_FLOW: (
        "tech-lead",
        "ai-engineer",
        "product-designer",
        "backend-engineer",
        "frontend-engineer",
        "qa-engineer",
    ),
    TaskType.VISUAL_POLISH: (
        "tech-lead",
        "ai-engineer",
        "product-designer",
        "frontend-engineer",
    ),
    TaskType.EMAIL_CAMPAIGN: (
        "tech-lead",
        "ai-engineer",
        "product-designer",
        "frontend-engineer",
        "qa-engineer",
    ),
    TaskType.QA_TEST: (
        "tech-lead",
        "ai-engineer",
        "product-designer",
        "qa-engineer",
        "backend-engineer",
    ),
    TaskType.PLATFORM_INFRA: (
        "tech-lead",
        "ai-engineer",
        "product-designer",
        "backend-engineer",
        "qa-engineer",
        "devops-engineer",
    ),
    TaskType.FULL_STACK_APP: (
        "tech-lead",
        "ai-engineer",
        "product-designer",
        "backend-engineer",
        "frontend-engineer",
        "devops-engineer",
        "qa-engineer",
    ),
    TaskType.UNKNOWN: (
        "tech-lead",
        "ai-engineer",
        "product-designer",
        "backend-engineer",
        "frontend-engineer",
        "qa-engineer",
        "devops-engineer",
    ),
}


# The single role allowed to write per task type. tech-lead writes only when
# the task is unclassified (planning-only).
TASK_EXECUTOR_ROLE: Mapping[TaskType, str] = {
    TaskType.BACKEND_FEATURE: "backend-engineer",
    TaskType.FRONTEND_FEATURE: "frontend-engineer",
    TaskType.LANDING_PAGE: "frontend-engineer",
    TaskType.ONBOARDING_FLOW: "frontend-engineer",
    TaskType.VISUAL_POLISH: "frontend-engineer",
    TaskType.EMAIL_CAMPAIGN: "frontend-engineer",
    TaskType.QA_TEST: "qa-engineer",
    TaskType.PLATFORM_INFRA: "backend-engineer",
    TaskType.UNKNOWN: "tech-lead",
}


# task_type → reference sources (per spec).
TASK_REFERENCE_SOURCES: Mapping[TaskType, Sequence[str]] = {
    TaskType.LANDING_PAGE: ("Wix Templates", "Awwwards", "Behance", "Pinterest Trends"),
    TaskType.ONBOARDING_FLOW: ("Mobbin", "Page Flows"),
    TaskType.VISUAL_POLISH: ("Pinterest Trends", "Notefolio", "Behance", "Canva Design School"),
    TaskType.EMAIL_CAMPAIGN: (
        "Really Good Emails",
        "Meta Ad Library",
        "TikTok Creative Center",
        "Google Trends",
    ),
}


# Small, conservative bonuses on top of role defaults. Negative bonuses are
# allowed; final negative scores collapse to 0 (= excluded).
TASK_BONUSES: Mapping[TaskType, Mapping[str, Mapping[str, int]]] = {
    TaskType.LANDING_PAGE: {
        "product-designer": {"gemini": 2},
        "frontend-engineer": {"codex": 1},
    },
    TaskType.VISUAL_POLISH: {
        "product-designer": {"gemini": 3},
        "frontend-engineer": {"gemini": 1},
    },
    TaskType.ONBOARDING_FLOW: {
        "tech-lead": {"gemini": 1},
        "product-designer": {"gemini": 1},
    },
    TaskType.EMAIL_CAMPAIGN: {
        "product-designer": {"gemini": 2},
        "frontend-engineer": {"codex": 1},
    },
    TaskType.QA_TEST: {"qa-engineer": {"codex": 2}},
    TaskType.PLATFORM_INFRA: {
        "backend-engineer": {"claude": 1},
        "qa-engineer": {"codex": 1},
    },
    TaskType.BACKEND_FEATURE: {"backend-engineer": {"codex": 1}},
}


# Keyword fallback when DispatchRequest.task_type is not provided.
# Order matters: more specific intents (polish/onboarding/email) are checked
# before generic surface keywords (landing/frontend/backend) so that a prompt
# like "히어로 visual polish 정리" classifies as VISUAL_POLISH.
_KEYWORD_RULES: Sequence[tuple[TaskType, Sequence[str]]] = (
    (TaskType.VISUAL_POLISH, ("polish", "visual ", "리디자인", "redesign", "시각 정리", "visual cleanup")),
    (TaskType.ONBOARDING_FLOW, ("onboarding", "온보딩", "signup flow", "가입 흐름", "first-run")),
    (TaskType.EMAIL_CAMPAIGN, ("email", "이메일", "campaign", "캠페인", "광고", "ad creative")),
    (TaskType.LANDING_PAGE, ("landing", "랜딩", "marketing page")),
    (TaskType.QA_TEST, ("regression", "회귀", "qa", "test plan", "테스트 시나리오")),
    (
        TaskType.PLATFORM_INFRA,
        ("infra", "deploy", "ci ", " ci", "docker", "k8s", "terraform", "github action"),
    ),
    (TaskType.FRONTEND_FEATURE, ("frontend", "ui ", "component", "컴포넌트", "react", "next.js", "vue")),
    (
        TaskType.BACKEND_FEATURE,
        ("backend", "api ", "schema", "database", "migration", "도메인", "service layer"),
    ),
)


@dataclass(frozen=True)
class DispatchRequest:
    """Request handed to the gateway dispatcher.

    *task_type* is optional: when ``None``, the prompt is classified by
    keyword. *user_approved* is the explicit operator approval needed
    before any writing role is allowed to execute.
    """

    prompt: str
    task_type: Optional[TaskType] = None
    write_requested: bool = False
    user_approved: bool = False
    repository: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoleAssignment:
    role: str
    runner_id: Optional[str]
    is_executor: bool
    score: int
    rationale: str


@dataclass(frozen=True)
class DispatchPlan:
    task_type: TaskType
    role_sequence: Sequence[str]
    assignments: Sequence[RoleAssignment]
    reference_sources: Sequence[str]
    write_blocked: bool
    write_block_reason: Optional[str]
    notes: Sequence[str] = field(default_factory=tuple)

    def executor(self) -> Optional[RoleAssignment]:
        for assignment in self.assignments:
            if assignment.is_executor:
                return assignment
        return None

    def advisors(self) -> Sequence[RoleAssignment]:
        return tuple(a for a in self.assignments if not a.is_executor)


class RankingSignal(Protocol):
    """Optional external signal: returns a small additive nudge for one (role, runner).

    MVP applies the result with weight 0 by default — see
    ``Dispatcher.dispatch(ranking_weight=...)``. The slot exists so a future
    score source (LMSys, local success rate) can be plugged in without
    touching the dispatcher.
    """

    def score(self, role: str, runner_id: str, request: DispatchRequest) -> float:
        ...


class Dispatcher:
    """Stateless dispatcher; instances hold only configuration / hooks."""

    def __init__(
        self,
        pool: ParticipantsPool,
        *,
        ranking_signal: Optional[RankingSignal] = None,
    ) -> None:
        self.pool = pool
        self.ranking_signal = ranking_signal

    def classify(self, request: DispatchRequest) -> TaskType:
        if request.task_type is not None:
            return request.task_type
        prompt = request.prompt.lower()
        for task_type, keywords in _KEYWORD_RULES:
            for keyword in keywords:
                if keyword in prompt:
                    return task_type
        return TaskType.UNKNOWN

    def dispatch(
        self,
        request: DispatchRequest,
        *,
        ranking_weight: float = 0.0,
    ) -> DispatchPlan:
        task_type = self.classify(request)
        role_sequence = TASK_ROLE_SEQUENCE[task_type]
        executor_role = TASK_EXECUTOR_ROLE[task_type]
        bonuses = TASK_BONUSES.get(task_type, {})

        assignments: list[RoleAssignment] = []
        notes: list[str] = []

        for role in role_sequence:
            base_weights = ROLE_DEFAULT_WEIGHTS.get(role, {})
            role_bonus = bonuses.get(role, {})
            scored = self._score_runners_for_role(
                role=role,
                request=request,
                base_weights=base_weights,
                role_bonus=role_bonus,
                ranking_weight=ranking_weight,
            )
            best = scored[0] if scored else (None, 0, "no candidate runner in pool")
            runner_id, score, rationale = best
            assignments.append(
                RoleAssignment(
                    role=role,
                    runner_id=runner_id,
                    is_executor=(role == executor_role),
                    score=int(score),
                    rationale=rationale,
                )
            )
            if runner_id is None:
                notes.append(f"role '{role}' has no eligible runner in the pool")

        write_blocked, write_block_reason = self._evaluate_write_gate(request, executor_role)
        if write_blocked and write_block_reason:
            notes.append(write_block_reason)

        return DispatchPlan(
            task_type=task_type,
            role_sequence=tuple(role_sequence),
            assignments=tuple(assignments),
            reference_sources=tuple(TASK_REFERENCE_SOURCES.get(task_type, ())),
            write_blocked=write_blocked,
            write_block_reason=write_block_reason,
            notes=tuple(notes),
        )

    def _score_runners_for_role(
        self,
        *,
        role: str,
        request: DispatchRequest,
        base_weights: Mapping[str, int],
        role_bonus: Mapping[str, int],
        ranking_weight: float,
    ) -> Sequence[tuple[Optional[str], int, str]]:
        candidates: list[tuple[Optional[str], int, str]] = []
        for runner_id, base in base_weights.items():
            if runner_id not in self.pool.runners:
                continue
            bonus = role_bonus.get(runner_id, 0)
            ranking_score = 0.0
            if self.ranking_signal is not None and ranking_weight > 0:
                ranking_score = float(self.ranking_signal.score(role, runner_id, request)) * ranking_weight
            score = max(0, int(round(base + bonus + ranking_score)))
            if score == 0:
                continue
            rationale = self._format_rationale(base, bonus, ranking_score)
            candidates.append((runner_id, score, rationale))

        candidates.sort(key=lambda item: (-item[1], item[0] or ""))
        return candidates

    def _evaluate_write_gate(
        self,
        request: DispatchRequest,
        executor_role: str,
    ) -> tuple[bool, Optional[str]]:
        if not request.write_requested:
            return False, None
        if request.user_approved:
            return False, None
        return (
            True,
            (
                f"write is requested for {executor_role} but user_approved=False. "
                f"Block until the operator confirms."
            ),
        )

    @staticmethod
    def _format_rationale(base: int, bonus: int, ranking_score: float) -> str:
        parts = [f"base={base}"]
        if bonus:
            parts.append(f"task_bonus={bonus:+d}")
        if ranking_score:
            parts.append(f"ranking={ranking_score:+.1f}")
        return ", ".join(parts)


def render_plan_summary(plan: DispatchPlan) -> str:
    """Operator-facing summary of a dispatch decision."""

    lines: list[str] = [
        f"task_type: {plan.task_type.value}",
        f"participant candidates: {', '.join(plan.role_sequence)}",
    ]
    for assignment in plan.assignments:
        marker = "[exec]" if assignment.is_executor else "[advisor]"
        runner = assignment.runner_id or "<no runner>"
        lines.append(
            f"  {marker} {assignment.role}: {runner} (score={assignment.score}, {assignment.rationale})"
        )
    if plan.reference_sources:
        lines.append(f"references: {', '.join(plan.reference_sources)}")
    if plan.write_blocked:
        lines.append(f"write blocked: {plan.write_block_reason}")
    for note in plan.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)
