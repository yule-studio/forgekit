"""Per-role take renderers (Discord-friendly multi-line strings).

Extracted verbatim from :mod:`deliberation` (behavior-preserving split).
:func:`render_role_take` in :mod:`deliberation` stays the thin dispatch
entry point; the per-role rendering bodies live here. Depends one-way on
:mod:`deliberation` for the role-take dataclasses and the ``_short_role``
/ ``_bullet_block`` helpers.
"""

from __future__ import annotations

from .deliberation import (
    AiEngineerTake,
    BackendEngineerTake,
    DevOpsEngineerTake,
    FrontendEngineerTake,
    ProductDesignerTake,
    QaEngineerTake,
    TechLeadOpening,
    _bullet_block,
    _short_role,
)


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


def _render_devops_engineer(t: DevOpsEngineerTake) -> str:
    short = _short_role(t.role)
    lines = [f"**[{short}]**"]
    if t.perspective:
        lines.append(f"관점: {t.perspective}")
    if t.cicd_strategy:
        lines.append(f"CI/CD: {t.cicd_strategy}")
    if t.deployment_plan:
        lines.append(f"배포 계획: {t.deployment_plan}")
    if t.rollback_plan:
        lines.append(f"롤백 계획: {t.rollback_plan}")
    if t.observability:
        lines.append(f"관측성: {t.observability}")
    if t.secrets_and_permissions:
        lines.append(f"secrets/권한: {t.secrets_and_permissions}")
    lines.append(_bullet_block("릴리즈 체크리스트", t.release_checklist))
    lines.append(_bullet_block("근거", t.evidence))
    lines.append(_bullet_block("리스크", t.risks))
    lines.append(_bullet_block("다음 행동", t.next_actions))
    return "\n".join(line for line in lines if line)
