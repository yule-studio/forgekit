"""Coding job model + executor prompt generation.

After the user approves a :class:`CodingAuthorizationProposal`, the
gateway converts it into a :class:`CodingJob` so the executor role
gets a stable, persistable record of:

- which role is authorized to write,
- exactly what scope they may touch,
- what they must never touch,
- which safety rules they must obey,
- the executor prompt the role agent receives when it actually runs.

This module is pure-Python; the Discord layer wraps it for the
approval flow (next commit). No file is actually written here —
:class:`CodingJob` only carries the *intent* to run, and execution
itself stays out of scope for the MVP.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence, Tuple

from .authorization import CodingAuthorizationProposal, load_role_profile


# Coding job lifecycle states. Phase 1 only uses
# ``pending_approval`` → ``ready`` (user approved the proposal). Future
# phases will add ``in_progress`` / ``completed`` / ``failed`` once an
# executor actually runs the job.
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_READY = "ready"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


@dataclass(frozen=True)
class CodingJob:
    """A user-approved coding task assigned to a single executor role.

    ``status`` is a string (rather than enum) so the dataclass round-
    trips through JSON / SQLite cache without a custom serialiser.
    ``generated_prompt`` is the text the executor role receives when
    it starts work — it embeds session / scope / safety rules / role
    expertise context so the executor doesn't need to re-read the
    proposal at run time.
    """

    session_id: Optional[str]
    user_request: str
    executor_role: str
    review_roles: Tuple[str, ...]
    participant_roles: Tuple[str, ...]
    write_scope: Tuple[str, ...]
    forbidden_scope: Tuple[str, ...]
    safety_rules: Tuple[str, ...]
    reason: str
    status: str
    generated_prompt: str
    created_at: datetime
    approved_at: Optional[datetime] = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> Mapping[str, object]:
        """Serialise to a JSON-friendly mapping for ``session.extra``."""

        return {
            "session_id": self.session_id,
            "user_request": self.user_request,
            "executor_role": self.executor_role,
            "review_roles": list(self.review_roles),
            "participant_roles": list(self.participant_roles),
            "write_scope": list(self.write_scope),
            "forbidden_scope": list(self.forbidden_scope),
            "safety_rules": list(self.safety_rules),
            "reason": self.reason,
            "status": self.status,
            "generated_prompt": self.generated_prompt,
            "created_at": self.created_at.isoformat(),
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "metadata": dict(self.metadata),
        }

    def with_status(self, status: str, *, at: Optional[datetime] = None) -> "CodingJob":
        """Return a copy with the new status, leaving everything else intact."""

        if status == STATUS_READY and at is not None:
            return replace(self, status=status, approved_at=at)
        return replace(self, status=status)


def build_coding_job_from_proposal(
    proposal: CodingAuthorizationProposal,
    *,
    status: str = STATUS_PENDING_APPROVAL,
    approved_at: Optional[datetime] = None,
    role_profile: Optional[Mapping[str, object]] = None,
    now: Optional[datetime] = None,
) -> CodingJob:
    """Convert an authorization proposal into a coding job.

    Pass ``status=STATUS_READY`` together with ``approved_at`` to land a
    user-approved job; the default starts in ``pending_approval`` so
    the gateway can store the proposal first and only flip it to
    ``ready`` once the user types an approval phrase.

    Refuses to build a job when ``proposal.lifecycle_mode`` signals
    research-only — those proposals never select an executor, so an
    approval phrase against them must be preceded by a fresh
    implementation-mode proposal.
    """

    if getattr(proposal, "lifecycle_mode", "implementation") == "research_only":
        raise ValueError(
            "research-only proposal에는 executor가 없습니다. "
            "구현이 필요하면 '수정 권한 제안'을 다시 요청해 주세요."
        )

    profile = role_profile
    if profile is None:
        try:
            profile = load_role_profile(proposal.executor_role)
        except FileNotFoundError:
            profile = {}

    created_at = now or datetime.now(timezone.utc)
    prompt = generate_executor_prompt(proposal=proposal, role_profile=profile)
    return CodingJob(
        session_id=proposal.session_id,
        user_request=proposal.user_request,
        executor_role=proposal.executor_role,
        review_roles=tuple(proposal.review_roles),
        participant_roles=tuple(proposal.participant_roles),
        write_scope=tuple(proposal.write_scope),
        forbidden_scope=tuple(proposal.forbidden_scope),
        safety_rules=tuple(proposal.safety_rules),
        reason=proposal.reason,
        status=status,
        generated_prompt=prompt,
        created_at=created_at,
        approved_at=approved_at,
        metadata={
            "proposal_metadata": dict(proposal.metadata),
            "role_domain_focus": str(profile.get("domain_focus", "")),
        },
    )


def generate_executor_prompt(
    *,
    proposal: CodingAuthorizationProposal,
    role_profile: Mapping[str, object],
) -> str:
    """Render the prompt the executor role will receive when it runs.

    The prompt is structured so an LLM-backed runner (or a careful
    human reviewer) can act on it without re-reading the proposal:

    - what the user asked for,
    - which role they are and that role's domain expertise,
    - the exact write/forbidden scope,
    - the safety rules they must obey,
    - the workflow expectations (plan → confirm → run tests → report).
    """

    role = proposal.executor_role
    domain_focus = str(role_profile.get("domain_focus", "")).strip()
    decision_criteria = _string_list(role_profile.get("decision_criteria", ()))
    review_checklist = _string_list(role_profile.get("review_checklist", ()))
    risk_focus = _string_list(role_profile.get("risk_focus", ()))
    quality_bar = _string_list(role_profile.get("quality_bar", ()))

    blocks: list[str] = []
    blocks.append(f"# Coding Job — executor: {role}")
    if proposal.session_id:
        blocks.append(f"session_id: `{proposal.session_id}`")
    blocks.append("")

    blocks.append("## 사용자 요청")
    blocks.append(proposal.user_request.strip() or "(empty — clarify before writing)")

    if domain_focus:
        blocks.append("")
        blocks.append("## 너의 역할 / 전문성")
        blocks.append(f"- {role} — {domain_focus}")
        if decision_criteria:
            blocks.append("- 결정 기준:")
            for line in decision_criteria:
                blocks.append(f"  - {line}")

    blocks.append("")
    blocks.append("## write scope (이 영역만 수정 가능)")
    if proposal.write_scope:
        for scope in proposal.write_scope:
            blocks.append(f"- {scope}")
    else:
        blocks.append("- (명시된 write scope가 없습니다 — 사용자에게 다시 확인하세요)")

    blocks.append("")
    blocks.append("## forbidden scope (절대 수정 금지)")
    if proposal.forbidden_scope:
        for scope in proposal.forbidden_scope:
            blocks.append(f"- {scope}")
    else:
        blocks.append("- (forbidden scope 미지정 — 안전을 위해 destructive 명령은 모두 금지로 간주)")

    blocks.append("")
    blocks.append("## safety rules (절대 위반 금지)")
    for rule in proposal.safety_rules:
        blocks.append(f"- {rule}")

    if review_checklist:
        blocks.append("")
        blocks.append("## 검토 체크리스트 (작업 끝에 self-check)")
        for item in review_checklist:
            blocks.append(f"- {item}")
    if risk_focus:
        blocks.append("")
        blocks.append("## 리스크 포커스")
        for item in risk_focus:
            blocks.append(f"- {item}")
    if quality_bar:
        blocks.append("")
        blocks.append("## 품질 기준")
        for item in quality_bar:
            blocks.append(f"- {item}")

    blocks.append("")
    blocks.append("## 작업 절차 (반드시 이 순서)")
    blocks.append("1. 수정 전 현재 코드 구조와 의도를 짧게 요약한다.")
    blocks.append("2. 무엇을 어디에 어떻게 바꿀지 1~3 bullet 계획을 사용자에게 보여 준다.")
    blocks.append("3. 사용자 추가 승인이 필요하면 멈추고 묻는다.")
    blocks.append("4. write scope 안의 파일만 수정한다.")
    blocks.append("5. 변경 후 관련 단위/통합 테스트를 실행한다.")
    blocks.append("6. 결과(변경 파일 / 실행한 테스트 / 남은 위험)를 사용자에게 보고한다.")
    blocks.append("7. destructive 명령(파일 삭제 / git reset --hard / git push --force / 자동 deploy)은 절대 실행하지 않는다.")

    if proposal.review_roles:
        blocks.append("")
        blocks.append("## reviewer 통지 대상")
        for role_id in proposal.review_roles:
            blocks.append(f"- `{role_id}`")

    blocks.append("")
    blocks.append("## 참여 / 협업 role")
    for role_id in proposal.participant_roles:
        blocks.append(f"- `{role_id}`")

    blocks.append("")
    blocks.append(f"## 추천 사유\n{proposal.reason}")

    return "\n".join(blocks)


def _string_list(value: object) -> Tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value if item is not None)
    return ()


__all__ = (
    "CodingJob",
    "STATUS_CANCELLED",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_IN_PROGRESS",
    "STATUS_PENDING_APPROVAL",
    "STATUS_READY",
    "build_coding_job_from_proposal",
    "generate_executor_prompt",
)
