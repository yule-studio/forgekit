"""Runbook generator — when forgekit can't execute (deploy / IAM / infra / secret),
it does NOT fake success; it produces an operator-facing runbook note instead.

A :class:`RunbookNote` is a markdown artifact (Terraform skeleton + approval steps)
that an operator follows to do the privileged step themselves. Pure string building
→ testable; the note's authorship frontmatter is added by the vault layer (WT5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

AREA_DEPLOY = "deploy"
AREA_IAM = "iam"
AREA_INFRA = "infra"
AREA_SECRET = "secret"

# Per-area: (why it's privileged, what the operator must provide, a terraform hint).
_AREA_KB = {
    AREA_DEPLOY: (
        "프로덕션 배포는 forgekit 의 자율 범위 밖 (approval matrix: production action).",
        ("배포 대상 환경/리전", "rollout 전략(blue-green/canary) 승인", "롤백 기준"),
        'resource "null_resource" "deploy" { # CI/CD 파이프라인 트리거로 대체 }',
    ),
    AREA_IAM: (
        "IAM 권한 부여/변경은 최소권한·감사 대상이라 사람 승인 필수.",
        ("필요한 역할/정책 범위", "부여 대상 principal", "만료/회수 정책"),
        'resource "aws_iam_policy" "x" { # 최소권한 JSON, 사람 검토 후 apply }',
    ),
    AREA_INFRA: (
        "인프라 provisioning(apply)은 비용/상태 변경이라 operator apply 가 필요.",
        ("리소스 종류/규모", "예산 한도", "상태(state) 백엔드 위치"),
        'terraform { backend "s3" {} } # plan 은 자동, apply 는 사람',
    ),
    AREA_SECRET: (
        "secret 생성/회전은 자격 노출 위험이라 forgekit 가 직접 수행하지 않음.",
        ("secret 이름/범위", "저장 위치(secret manager)", "회전 주기"),
        '# secret 값은 Terraform state 에 남기지 말 것 — secret manager 참조만',
    ),
}
_DEFAULT_KB = (
    "이 작업은 forgekit 의 실행 권한 밖입니다.",
    ("필요 권한", "승인자", "범위"),
    "# 권한 확보 후 수동 apply",
)


@dataclass(frozen=True)
class RunbookNote:
    """An operator runbook for a blocked privileged action (markdown artifact)."""

    title: str
    area: str
    why_blocked: str
    requires: Tuple[str, ...]
    terraform_hint: str
    context: str = ""
    next_action: str = "operator 가 위 절차로 직접 수행 후 forgekit 에 결과를 알려주세요."

    def to_markdown(self) -> str:
        lines = [
            f"# Runbook — {self.title}",
            "",
            f"- **area**: {self.area}",
            f"- **왜 막혔나**: {self.why_blocked}",
        ]
        if self.context:
            lines.append(f"- **맥락**: {self.context}")
        lines += ["", "## operator 가 제공/결정해야 할 것"]
        lines += [f"- [ ] {r}" for r in self.requires]
        lines += [
            "",
            "## Terraform / ops skeleton",
            "```hcl",
            self.terraform_hint,
            "```",
            "",
            "## 승인 / 다음 단계",
            f"- {self.next_action}",
            "- 승인 경로: `#승인-대기` 카드 (request_type=ACCESS/SECRET) — approval matrix 참조.",
        ]
        return "\n".join(lines) + "\n"


def build_runbook(area: str, *, title: str = "", context: str = "") -> RunbookNote:
    """Build a runbook note for a blocked *area* (deploy/iam/infra/secret)."""

    why, requires, hint = _AREA_KB.get(area, _DEFAULT_KB)
    return RunbookNote(
        title=title or f"{area} (권한 필요)",
        area=area,
        why_blocked=why,
        requires=requires,
        terraform_hint=hint,
        context=context,
    )


def infer_area(text: str) -> str:
    """Classify a blocked-task description into a runbook area (best-effort)."""

    t = (text or "").lower()
    if any(k in t for k in ("secret", "키", "자격", "credential", "token")):
        return AREA_SECRET
    if any(k in t for k in ("iam", "권한", "role", "policy")):
        return AREA_IAM
    if any(k in t for k in ("deploy", "배포", "rollout", "release")):
        return AREA_DEPLOY
    return AREA_INFRA


__all__ = (
    "AREA_DEPLOY", "AREA_IAM", "AREA_INFRA", "AREA_SECRET",
    "RunbookNote", "build_runbook", "infer_area",
)
