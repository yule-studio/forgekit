"""Product shaping — raw user ask → :class:`ProductIntentPacket`.

The intake gate's core: detect feature families, auto-fill implied features +
safe defaults + baseline cross-cutting concerns, surface ≤3 high-value decision
questions, derive acceptance criteria + non-goals, and judge readiness. Pure and
deterministic so the "영상 업로드" example is fully testable.
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple

from . import families as fam
from . import question_policy
from .models import (
    GAP_IMPLIED,
    READINESS_CLARIFICATION,
    READINESS_IMPLEMENTATION_CANDIDATE,
    READINESS_RESEARCH_ONLY,
    READINESS_SPEC_READY,
    FeatureGap,
    ProductIntentPacket,
    ProductReadinessVerdict,
)

# implied feature → acceptance criterion text (service-readiness, not UI clone).
_ACCEPTANCE = {
    "processing_state": "업로드/처리 상태(진행·완료·실패)가 사용자에게 표시된다",
    "failure_retry": "업로드 실패 시 에러를 보여주고 재시도할 수 있다",
    "thumbnail_fallback": "썸네일이 없으면 기본 썸네일로 대체된다",
    "visibility_state": "비공개 항목은 비인가 사용자에게 노출되지 않는다",
    "ordering_display": "정의된 노출 순서대로 목록이 표시된다",
    "draft_state": "공개 전 draft 상태로 저장·검토할 수 있다",
    "validation": "잘못된 입력은 검증되어 거부되고 사유가 표시된다",
    "audit_trail": "쓰기·삭제 등 민감 작업은 audit 로 남는다",
    "session_management": "로그인 세션이 만료·갱신 정책대로 관리된다",
    "role_scope": "권한 밖 작업은 차단된다",
    "empty_state": "데이터가 없을 때 빈 상태 화면이 표시된다",
    "payment_failure_handling": "결제 실패가 안전하게 처리되고 재시도 경로가 있다",
    "refund_flow": "환불 요청이 정책대로 처리된다",
    "publish_failure": "예약 발행 실패가 감지되고 알림/재시도된다",
}

_RESEARCH_MARKERS = ("조사", "리서치", "분석", "경쟁사", "research", "investigate", "compare")
_VAGUE_MAX_WORDS = 4


def _core_flow(family_keys: Tuple[str, ...]) -> Tuple[str, ...]:
    flow = []
    if "auth_and_permission" in family_keys:
        flow.append("사용자 인증")
    if "media_upload" in family_keys:
        flow += ["콘텐츠 업로드", "처리/검증", "공개 정책 적용", "목록/상세 노출"]
    if "admin_crud" in family_keys:
        flow += ["관리자 작성", "검증/draft", "공개", "목록/상세 노출"]
    if "list_detail_catalog" in family_keys and not flow:
        flow += ["목록 조회", "상세 보기"]
    if "payment_or_billing" in family_keys:
        flow += ["결제 시작", "결제 처리", "영수증/상태"]
    return tuple(dict.fromkeys(flow))  # de-dup, keep order


def shape_product_intent(
    raw_text: str,
    *,
    target_user: str = "일반 사용자",
    metadata: Optional[Mapping[str, object]] = None,
) -> ProductIntentPacket:
    """Shape a raw ask into a structured product packet."""

    text = (raw_text or "").strip()
    family_keys = fam.detect_families(text)
    detected = [fam.FAMILY_BY_KEY[k] for k in family_keys]

    implied: list[FeatureGap] = []
    recommended: list[str] = []
    decision_keys: list[str] = []
    suggested_roles: list[str] = ["tech-lead"]
    for family in detected:
        for feat in family.implied:
            implied.append(FeatureGap(feat, GAP_IMPLIED, family.key))
        recommended.extend(family.recommended_defaults)
        decision_keys.extend(family.ask)
        suggested_roles.extend(family.suggested_roles)

    questions = question_policy.select_questions(decision_keys)
    deferred = question_policy.deferred_keys(decision_keys, questions)

    # baseline cross-cutting concerns are auto-filled, never asked
    recommended_defaults = tuple(dict.fromkeys(recommended)) + fam.BASELINE_DEFAULTS
    assumptions = []
    if any(f.key in ("media_upload", "admin_crud", "payment_or_billing") for f in detected):
        assumptions.append(fam.BASELINE_OBSERVABILITY)
    # deferred (budget-dropped) decisions become explicit assumptions w/ the default
    for key in deferred:
        tmpl = fam.TEMPLATE_BY_KEY.get(key)
        if tmpl:
            rec = next((o.label for o in tmpl.options if o.recommended), "")
            assumptions.append(f"{tmpl.prompt} → 기본값 '{rec}' 가정")

    acceptance = tuple(dict.fromkeys(
        _ACCEPTANCE[g.name] for g in implied if g.name in _ACCEPTANCE
    ))

    non_goals = []
    if "payment_or_billing" not in family_keys:
        non_goals.append("결제/과금은 이번 범위 밖")
    if "notification" not in family_keys:
        non_goals.append("알림/푸시는 이번 범위 밖")
    non_goals.append("기존 화면 그대로 클론(미보강 복제)은 범위 밖")

    suggested_roles = tuple(dict.fromkeys(suggested_roles))
    user_decisions = tuple(q.prompt for q in questions)
    readiness = _judge_readiness(text, family_keys, questions)

    return ProductIntentPacket(
        user_goal=text or "(빈 요청)",
        target_user=target_user,
        problem_statement=_problem_statement(text, detected),
        core_flow=_core_flow(family_keys),
        required_features=tuple(f"{f.label} 기본 기능" for f in detected),
        implied_features=tuple(implied),
        recommended_defaults=recommended_defaults,
        assumptions=tuple(assumptions),
        user_decisions_needed=user_decisions,
        decision_questions=questions,
        non_goals=tuple(non_goals),
        acceptance_criteria=acceptance,
        suggested_roles=suggested_roles,
        detected_families=family_keys,
        readiness=readiness,
    )


def _problem_statement(text: str, detected) -> str:
    if not detected:
        return f"요청 '{text}' 의 제품 맥락이 아직 불명확 — 보강 필요"
    labels = " / ".join(f.label for f in detected)
    return f"'{text}' 를 실제 서비스 수준({labels})으로 만들기 위한 요구 정리"


def _judge_readiness(text, family_keys, questions) -> ProductReadinessVerdict:
    low = text.lower()
    signals = [f"families={list(family_keys)}", f"questions={len(questions)}"]
    if any(m in low for m in _RESEARCH_MARKERS) and not family_keys:
        return ProductReadinessVerdict(READINESS_RESEARCH_ONLY, "조사/분석성 요청", tuple(signals))
    if not family_keys and len(text.split()) <= _VAGUE_MAX_WORDS:
        return ProductReadinessVerdict(
            READINESS_CLARIFICATION, "feature family 미검출 + 짧은 요청 — 무엇을 만들지 보강 필요", tuple(signals)
        )
    if questions:
        return ProductReadinessVerdict(
            READINESS_CLARIFICATION,
            f"중요 비즈니스 결정 {len(questions)}개 미정 (visibility/permission/billing 등)",
            tuple(signals),
        )
    if family_keys:
        return ProductReadinessVerdict(
            READINESS_IMPLEMENTATION_CANDIDATE,
            "feature family 검출 + 미해결 결정 없음 — packet 기반 구현 후보", tuple(signals)
        )
    return ProductReadinessVerdict(READINESS_SPEC_READY, "spec 정리됨", tuple(signals))


__all__ = ("shape_product_intent",)
