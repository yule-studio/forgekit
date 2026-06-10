"""Deterministic role-take fallback templates.

Extracted verbatim from :mod:`deliberation` (behavior-preserving split).
When no LLM runner is wired (or the runner raises / returns an
unstructured value), :func:`run_role_deliberation` falls back to these
deterministic templates so the loop always produces a usable, typed
role take grounded in session/pack/memory metadata.

Depends one-way on :mod:`deliberation` for the role-take dataclasses and
the shared pack / memory / signal helpers. ``deliberation`` imports
:func:`_deterministic_role_take` for its ``run_role_deliberation``
call-site wiring — the one explicit orchestrator→extracted edge.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Tuple

from .deliberation import (
    AiEngineerTake,
    BackendEngineerTake,
    DeliberationContext,
    DevOpsEngineerTake,
    FrontendEngineerTake,
    ProductDesignerTake,
    QaEngineerTake,
    RoleTake,
    TechLeadOpening,
    _excerpt,
    _first_line,
    _has_doc_or_code_signal,
    _has_qa_signal,
    _has_ui_signal,
    _has_visual_signal,
    _previous_field,
    _previous_tech_lead_decisions,
    _session_approved,
    _short_role,
    assign_citation_ids,
    evidence_lines_for_role,
    memory_evidence_lines,
    memory_hint_for_role,
    role_specific_attachments,
)


def _deterministic_role_take(context: DeliberationContext) -> RoleTake:
    # Normalize citation IDs once at the top so every fallback (and the
    # rendered evidence/risks/next_actions) sees the same labels.
    if context.memory_context:
        context = replace(
            context,
            memory_context=assign_citation_ids(context.memory_context),
        )
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
    if role_short == "devops-engineer":
        return _fallback_devops_engineer(context)
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


def _fallback_devops_engineer(ctx: DeliberationContext) -> DevOpsEngineerTake:
    """devops-engineer 관점의 CI/CD·배포·관측·롤백·릴리즈 체크 정리."""

    pack = ctx.research_pack
    role = ctx.role
    pack_evidence = evidence_lines_for_role(pack, role)
    memory_lines = memory_evidence_lines(ctx.memory_context, limit=2)
    evidence = tuple(list(pack_evidence) + list(memory_lines))

    risks: list[str] = [
        "배포 직후 롤백 경로 미정의 — 장애 시 복구 시간 지연",
        "secrets/권한 변경 누락 — 운영 환경에서만 노출되는 회귀 가능",
    ]
    if pack is None or not pack_evidence:
        risks.append("배포/관측 reference 부족 — 결정 근거가 약해 운영 사고 위험")
    policy_hit = memory_hint_for_role(ctx.memory_context, source="policy")
    if policy_hit:
        risks.append(f"기존 운영 정책({policy_hit}) 충돌 점검 필요")

    perspective = (
        "CI/CD 파이프라인, 배포 전략, 관측·롤백, secrets/권한, 릴리즈 체크리스트를 "
        "정리해 실행 후보가 운영 사고 없이 변경을 적용할 수 있는지 판단한다."
    )

    next_actions: list[str] = [
        "GitHub Actions 변경 영향 분석 — workflow yaml diff 정리",
        "deployment 단계별 rollback 시나리오 thread에 명시",
        "release checklist에 observability/alarm 항목 포함",
    ]
    decision_hit = memory_hint_for_role(ctx.memory_context, kind="decision")
    if decision_hit:
        next_actions.append(f"이전 결정({decision_hit}) 배포 영향 재확인")
    backend_data = _previous_field(ctx.previous_turns, BackendEngineerTake, "data_impact")
    if backend_data:
        next_actions.append(
            f"백엔드 데이터 영향({backend_data}) migration window·downtime 산정"
        )

    return DevOpsEngineerTake(
        cicd_strategy="GitHub Actions 기준 — main 머지 시 staging 배포, 수동 승인 후 prod",
        deployment_plan="기존 blue/green 또는 canary 패턴 유지 — 새 환경 변수만 검증 후 적용",
        rollback_plan="배포 직전 태그 보관 + revert PR 자동 생성 + alarm 30분 모니터",
        observability="기존 metrics/logs 스키마 유지 — 신규 지표만 추가, dashboard PR 동반",
        secrets_and_permissions="신규 secret/scope 변경은 .env.example 및 권한 정책 문서에 동기화",
        release_checklist=(
            "테스트 green + 회귀 묶음 통과 확인",
            "secrets/permissions 변경 점검",
            "rollback 시나리오 thread에 명시",
            "운영 알람/대시보드 갱신",
        ),
        risks=tuple(risks),
        perspective=perspective,
        evidence=evidence,
        next_actions=tuple(next_actions),
    )
