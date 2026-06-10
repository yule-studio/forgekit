"""Tech-lead triage — self-improvement runtime.

gateway 가 문제를 발견하면 무조건 사람에게 던지지 않고 **tech-lead 가
먼저 triage** 한다. 여기서 triage 는 다음을 결정한다:

* ``owner_role`` — 어느 역할 에이전트가 가장 잘 처리할 것인가
* ``problem_kind`` — runtime/config / code-bug / approval-flow /
  discord-surface / memory-vault / external / unclear
* ``suggested_next_action`` — autonomy_policy 의 action 한 개로 표현
* ``approval_scope_hint`` — "delegated_ok" / "needs_human" / "blocked"
* ``confidence`` — 0~1 사이 (decision-quality 자기 평가)
* ``rationale`` — 사람이 읽을 한 줄 설명

본 모듈은 **순수 함수** — Discord, LLM, DB 접근 없음. 시그널 ID 기반
heuristic 매핑이라 testable. 향후 LLM 백엔드 (techn-lead 가 실제로
deliberation 함) 가 추가될 때 주입 seam 으로 교체 가능.

매핑 원칙 (사용자가 §J 에서 지정한 owner heuristic 그대로):
* approval / reply / router 문제 → tech-lead + backend-engineer
* Discord surface 문제 → tech-lead + frontend/backend (codepath 에 따라)
* vault / retrieval 문제 → tech-lead + ai-engineer
* workflow / runtime status 문제 → tech-lead + devops-engineer
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .autonomy_policy import (
    ACTION_AGENT_OPS_RECORD,
    ACTION_DRAFT_PR_CREATE,
    ACTION_FAILURE_POSTMORTEM_CREATE,
    ACTION_FEATURE_BRANCH_CREATE,
    ACTION_LOW_RISK_TEST_EDIT,
    ACTION_RUNTIME_CODE_CHANGE,
    ACTION_RUNTIME_RESTART,
    ACTION_SELF_IMPROVEMENT_PROPOSAL,
    ACTION_TEST_EXECUTE,
)
from .self_improvement import (
    SIGNAL_DUPLICATE_TOPIC_APPROVAL,
    SIGNAL_EMPTY_KNOWLEDGE_NOTE,
    SIGNAL_FAILED_RETRYABLE_PILEUP,
    SIGNAL_REPEATED_USER_COMPLAINT,
    SIGNAL_STALE_HEARTBEAT,
)
from .self_improvement_seed_detectors import (
    SIGNAL_APPROVAL_NO_MATCHING_REPLY,
    SIGNAL_CODING_CONTINUATION_STALLED,
    SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH,
    SIGNAL_ISSUELESS_BOOTSTRAP_FAILURE,
    SIGNAL_MEMBER_BOT_PRESENCE_CONFUSION,
    SIGNAL_OBSIDIAN_RENDER_FAILURE,
    SIGNAL_QA_TEST_MISCLASSIFICATION,
    SIGNAL_SUPERVISOR_WATCH_UNKNOWN,
)


# ---------------------------------------------------------------------------
# Triage taxonomy
# ---------------------------------------------------------------------------


PROBLEM_KIND_RUNTIME_CONFIG: str = "runtime_config"
PROBLEM_KIND_CODE_BUG: str = "code_bug"
PROBLEM_KIND_APPROVAL_FLOW: str = "approval_flow"
PROBLEM_KIND_DISCORD_SURFACE: str = "discord_surface"
PROBLEM_KIND_MEMORY_VAULT: str = "memory_vault"
PROBLEM_KIND_EXTERNAL_FACT: str = "external_fact"
PROBLEM_KIND_CLASSIFICATION: str = "classification"
PROBLEM_KIND_UNCLEAR: str = "unclear"


SCOPE_DELEGATED_OK: str = "delegated_ok"
SCOPE_NEEDS_HUMAN: str = "needs_human"
SCOPE_BLOCKED: str = "blocked"


ROLE_TECH_LEAD: str = "tech-lead"
ROLE_BACKEND: str = "backend-engineer"
ROLE_FRONTEND: str = "frontend-engineer"
ROLE_QA: str = "qa-engineer"
ROLE_DEVOPS: str = "devops-engineer"
ROLE_AI: str = "ai-engineer"
ROLE_DESIGN: str = "product-designer"


# ---------------------------------------------------------------------------
# Verdict object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriageVerdict:
    """One tech-lead triage decision.

    ``co_owner_roles`` is the recommended set of additional roles to
    bring in (e.g. tech-lead always present, plus backend for an
    approval/router issue). ``primary_owner`` is the single role the
    executor handoff should be wired to.
    """

    problem_kind: str
    primary_owner: str
    co_owner_roles: tuple
    suggested_action: str
    approval_scope_hint: str
    confidence: float
    rationale: str
    needs_external_fact: bool = False

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "problem_kind": self.problem_kind,
            "primary_owner": self.primary_owner,
            "co_owner_roles": list(self.co_owner_roles),
            "suggested_action": self.suggested_action,
            "approval_scope_hint": self.approval_scope_hint,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "needs_external_fact": self.needs_external_fact,
        }


# ---------------------------------------------------------------------------
# Heuristic table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Heuristic:
    problem_kind: str
    primary_owner: str
    co_owners: tuple
    suggested_action: str
    approval_scope: str
    rationale: str
    confidence: float = 0.85
    needs_external_fact: bool = False


_SIGNAL_HEURISTICS: Mapping[str, _Heuristic] = {
    # Approval / reply / router issues — backend handles router bugs.
    SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH: _Heuristic(
        problem_kind=PROBLEM_KIND_APPROVAL_FLOW,
        primary_owner=ROLE_BACKEND,
        co_owners=(ROLE_TECH_LEAD,),
        suggested_action=ACTION_RUNTIME_CODE_CHANGE,
        approval_scope=SCOPE_DELEGATED_OK,
        rationale=(
            "engineering_write 카드의 reply_router posted_message_id 매칭이 "
            "회귀했을 가능성 — backend 가 reply_router.py 를 수정해 fix."
        ),
        confidence=0.9,
    ),
    SIGNAL_APPROVAL_NO_MATCHING_REPLY: _Heuristic(
        problem_kind=PROBLEM_KIND_APPROVAL_FLOW,
        primary_owner=ROLE_BACKEND,
        co_owners=(ROLE_TECH_LEAD,),
        suggested_action=ACTION_RUNTIME_CODE_CHANGE,
        approval_scope=SCOPE_DELEGATED_OK,
        rationale=(
            "approval card 가 posting 후 reply 매칭에 실패 — reply_router 또는 "
            "channel router 의 token 인식 회귀 후보."
        ),
        confidence=0.85,
    ),
    SIGNAL_DUPLICATE_TOPIC_APPROVAL: _Heuristic(
        problem_kind=PROBLEM_KIND_APPROVAL_FLOW,
        primary_owner=ROLE_BACKEND,
        co_owners=(ROLE_TECH_LEAD,),
        suggested_action=ACTION_RUNTIME_CODE_CHANGE,
        approval_scope=SCOPE_DELEGATED_OK,
        rationale="topic ledger dedup 회귀 — backend 가 ledger 일관성을 복구.",
    ),
    # Classification / dispatcher issues — backend + tech-lead.
    SIGNAL_QA_TEST_MISCLASSIFICATION: _Heuristic(
        problem_kind=PROBLEM_KIND_CLASSIFICATION,
        primary_owner=ROLE_BACKEND,
        co_owners=(ROLE_TECH_LEAD, ROLE_QA),
        suggested_action=ACTION_RUNTIME_CODE_CHANGE,
        approval_scope=SCOPE_DELEGATED_OK,
        rationale=(
            "dispatcher.classify / stack_detector 가 코딩 intent 를 qa-test "
            "로 오분류 — backend 가 분류기 룰을 보정, QA 가 회귀 케이스 추가."
        ),
    ),
    # Coding continuation — runtime/dispatcher concern, backend + devops.
    SIGNAL_CODING_CONTINUATION_STALLED: _Heuristic(
        problem_kind=PROBLEM_KIND_APPROVAL_FLOW,
        primary_owner=ROLE_BACKEND,
        co_owners=(ROLE_TECH_LEAD, ROLE_DEVOPS),
        suggested_action=ACTION_RUNTIME_CODE_CHANGE,
        approval_scope=SCOPE_DELEGATED_OK,
        rationale=(
            "approval → coding_execute dispatch bridge 가 멈춤 — "
            "work_order_coding_continuation / dispatcher 점검."
        ),
        confidence=0.8,
    ),
    # Supervisor / runtime status — devops territory.
    SIGNAL_SUPERVISOR_WATCH_UNKNOWN: _Heuristic(
        problem_kind=PROBLEM_KIND_RUNTIME_CONFIG,
        primary_owner=ROLE_DEVOPS,
        co_owners=(ROLE_TECH_LEAD,),
        suggested_action=ACTION_FAILURE_POSTMORTEM_CREATE,
        approval_scope=SCOPE_DELEGATED_OK,
        rationale=(
            "supervisor / gateway 상태 보고 surface 가 UNKNOWN stuck — "
            "devops 가 status_poster / heartbeat 진단."
        ),
        confidence=0.75,
    ),
    SIGNAL_STALE_HEARTBEAT: _Heuristic(
        problem_kind=PROBLEM_KIND_RUNTIME_CONFIG,
        primary_owner=ROLE_DEVOPS,
        co_owners=(ROLE_TECH_LEAD,),
        suggested_action=ACTION_FAILURE_POSTMORTEM_CREATE,
        approval_scope=SCOPE_NEEDS_HUMAN,
        rationale=(
            "서비스 heartbeat 정체 — restart 가 필요할 수 있어 사람 승인 권장."
        ),
        confidence=0.8,
    ),
    SIGNAL_FAILED_RETRYABLE_PILEUP: _Heuristic(
        problem_kind=PROBLEM_KIND_RUNTIME_CONFIG,
        primary_owner=ROLE_DEVOPS,
        co_owners=(ROLE_TECH_LEAD,),
        suggested_action=ACTION_FAILURE_POSTMORTEM_CREATE,
        approval_scope=SCOPE_DELEGATED_OK,
        rationale="failed_retryable 누적 — 원인 분류 후 자동 requeue 또는 fix.",
    ),
    # Vault / retrieval — ai-engineer.
    SIGNAL_EMPTY_KNOWLEDGE_NOTE: _Heuristic(
        problem_kind=PROBLEM_KIND_MEMORY_VAULT,
        primary_owner=ROLE_AI,
        co_owners=(ROLE_TECH_LEAD,),
        suggested_action=ACTION_SELF_IMPROVEMENT_PROPOSAL,
        approval_scope=SCOPE_DELEGATED_OK,
        rationale="hydration 누락 — ai-engineer 가 snapshot/synthesis 점검.",
    ),
    SIGNAL_OBSIDIAN_RENDER_FAILURE: _Heuristic(
        problem_kind=PROBLEM_KIND_MEMORY_VAULT,
        primary_owner=ROLE_AI,
        co_owners=(ROLE_TECH_LEAD, ROLE_BACKEND),
        suggested_action=ACTION_RUNTIME_CODE_CHANGE,
        approval_scope=SCOPE_DELEGATED_OK,
        rationale="vault renderer 회귀 — ai-engineer + backend 가 renderer 수정.",
    ),
    # Discord surface — frontend / backend depending on codepath.
    SIGNAL_MEMBER_BOT_PRESENCE_CONFUSION: _Heuristic(
        problem_kind=PROBLEM_KIND_DISCORD_SURFACE,
        primary_owner=ROLE_FRONTEND,
        co_owners=(ROLE_TECH_LEAD, ROLE_BACKEND),
        suggested_action=ACTION_SELF_IMPROVEMENT_PROPOSAL,
        approval_scope=SCOPE_DELEGATED_OK,
        rationale=(
            "member bot 이 online 이지만 closure 상태 표시가 누락 — "
            "frontend (presence) + backend (workflow_state) 합의 필요."
        ),
        confidence=0.65,
    ),
    # Issue-less bootstrap — backend.
    SIGNAL_ISSUELESS_BOOTSTRAP_FAILURE: _Heuristic(
        problem_kind=PROBLEM_KIND_CODE_BUG,
        primary_owner=ROLE_BACKEND,
        co_owners=(ROLE_TECH_LEAD,),
        suggested_action=ACTION_RUNTIME_CODE_CHANGE,
        approval_scope=SCOPE_DELEGATED_OK,
        rationale=(
            "issue 없이 부트스트랩하는 work order 가 실패 — "
            "github_work_order 의 anchor 로직 수정."
        ),
    ),
    # Repeated user complaint — unclear domain; tech-lead first.
    SIGNAL_REPEATED_USER_COMPLAINT: _Heuristic(
        problem_kind=PROBLEM_KIND_UNCLEAR,
        primary_owner=ROLE_TECH_LEAD,
        co_owners=(),
        suggested_action=ACTION_SELF_IMPROVEMENT_PROPOSAL,
        approval_scope=SCOPE_NEEDS_HUMAN,
        rationale="repeated user complaint — tech-lead 가 사람에게 우선 보고.",
        confidence=0.6,
        needs_external_fact=True,
    ),
}


_FALLBACK_HEURISTIC: _Heuristic = _Heuristic(
    problem_kind=PROBLEM_KIND_UNCLEAR,
    primary_owner=ROLE_TECH_LEAD,
    co_owners=(),
    suggested_action=ACTION_SELF_IMPROVEMENT_PROPOSAL,
    approval_scope=SCOPE_NEEDS_HUMAN,
    rationale="알 수 없는 시그널 — tech-lead 가 사람에게 escalate.",
    confidence=0.4,
    needs_external_fact=True,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def triage_problem(
    *,
    signal_id: str,
    severity: str = "medium",
    evidence: Mapping[str, Any] = (),
    summary: str = "",
    deliberation_fn: Optional[Callable[..., Optional[TriageVerdict]]] = None,
) -> TriageVerdict:
    """Map a signal id (+ evidence) to a :class:`TriageVerdict`.

    ``deliberation_fn`` is a seam for plugging in a real LLM-based
    tech-lead later. If provided and it returns a verdict, that
    overrides the heuristic table. Returning ``None`` falls back to the
    heuristic (so the LLM can decline to triage and the deterministic
    table still answers).

    *severity* and *evidence* are accepted but not used by the
    heuristic right now — they're forwarded to ``deliberation_fn`` so a
    future tech-lead implementation has the full context.
    """

    if deliberation_fn is not None:
        try:
            verdict = deliberation_fn(
                signal_id=signal_id,
                severity=severity,
                evidence=evidence,
                summary=summary,
            )
        except Exception:  # noqa: BLE001 - never crash triage
            verdict = None
        if isinstance(verdict, TriageVerdict):
            return verdict

    heuristic = _SIGNAL_HEURISTICS.get(signal_id, _FALLBACK_HEURISTIC)

    # Severity adjustment: high-severity signals push down confidence-
    # constrained fallbacks toward needs_human.
    approval_scope = heuristic.approval_scope
    if heuristic.problem_kind == PROBLEM_KIND_UNCLEAR and severity == "high":
        approval_scope = SCOPE_NEEDS_HUMAN

    return TriageVerdict(
        problem_kind=heuristic.problem_kind,
        primary_owner=heuristic.primary_owner,
        co_owner_roles=heuristic.co_owners,
        suggested_action=heuristic.suggested_action,
        approval_scope_hint=approval_scope,
        confidence=heuristic.confidence,
        rationale=heuristic.rationale,
        needs_external_fact=heuristic.needs_external_fact,
    )


__all__ = (
    "PROBLEM_KIND_APPROVAL_FLOW",
    "PROBLEM_KIND_CLASSIFICATION",
    "PROBLEM_KIND_CODE_BUG",
    "PROBLEM_KIND_DISCORD_SURFACE",
    "PROBLEM_KIND_EXTERNAL_FACT",
    "PROBLEM_KIND_MEMORY_VAULT",
    "PROBLEM_KIND_RUNTIME_CONFIG",
    "PROBLEM_KIND_UNCLEAR",
    "ROLE_AI",
    "ROLE_BACKEND",
    "ROLE_DESIGN",
    "ROLE_DEVOPS",
    "ROLE_FRONTEND",
    "ROLE_QA",
    "ROLE_TECH_LEAD",
    "SCOPE_BLOCKED",
    "SCOPE_DELEGATED_OK",
    "SCOPE_NEEDS_HUMAN",
    "TriageVerdict",
    "triage_problem",
)
