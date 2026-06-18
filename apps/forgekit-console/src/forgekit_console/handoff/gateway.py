"""Gateway + tech-lead split — reuse the product-intake engine, derive role tasks.

``intake_packet`` bridges to ``yule_engineering.agents.product_intake`` (importable
via the root install) to shape a raw ask into a real ProductIntentPacket. The
gateway forwards it; tech-lead splits it into per-role tasks by the detected feature
families. Areas with no execution permission (infra / deploy / IAM / secret) are
marked ``blocked`` with a runbook hint — surfaced honestly, never faked as done.
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple

from .packet import (
    PHASE_GATEWAY,
    PHASE_INTAKE,
    PHASE_TECH_LEAD,
    ROLE_TASK_BLOCKED,
    ROLE_TASK_READY,
    Handoff,
    HandoffTrace,
    RoleTask,
    TechLeadSplit,
)

# Why a blocked area cannot be executed here + what to produce instead.
_BLOCKED_REASON = "실행 권한 없음 (infra/IAM/deploy/secret) — operator 승인 + Terraform/ops runbook 필요"
_RUNBOOK_HINT = "Terraform/ops/approval runbook note 산출 (vault) 후 operator 승인 요청"

# Per-family → role tasks. (role, role_label, title, blocked?)
_R = ROLE_TASK_READY
_B = ROLE_TASK_BLOCKED
_FAMILY_TASKS: Mapping[str, Tuple[Tuple[str, str, str, str], ...]] = {
    "media_upload": (
        ("be", "Backend", "업로드/처리(processing)/썸네일 파이프라인 구현", _R),
        ("fe", "Frontend", "업로드 UI + 상태(processing/실패/재시도) 표면", _R),
        ("security", "Security", "업로드 검증·파일타입·권한 게이트 리뷰", _R),
        ("devops", "DevOps", "미디어 스토리지/CDN 프로비저닝", _B),
    ),
    "auth_and_permission": (
        ("be", "Backend", "인증/권한/세션 구현", _R),
        ("security", "Security", "authz·토큰·IDOR 리뷰 (cross-cutting 게이트)", _R),
    ),
    "admin_crud": (
        ("be", "Backend", "CRUD + 입력 검증 구현", _R),
        ("fe", "Frontend", "관리자 CRUD 화면 + 빈/에러/로딩 상태", _R),
    ),
    "list_detail_catalog": (
        ("fe", "Frontend", "목록/상세 + 정렬/페이지네이션 UI", _R),
        ("be", "Backend", "목록/상세 API + 정렬", _R),
    ),
    "search_filter": (
        ("be", "Backend", "검색/필터 쿼리 구현", _R),
        ("fe", "Frontend", "검색/필터 UI", _R),
    ),
    "notification": (
        ("be", "Backend", "알림 트리거/템플릿 구현", _R),
        ("devops", "DevOps", "알림 전송 인프라/자격 (이메일·푸시)", _B),
    ),
    "payment_or_billing": (
        ("be", "Backend", "결제/구독 도메인 구현", _R),
        ("security", "Security", "결제 보안·PCI 경계 리뷰", _R),
        ("devops", "DevOps", "결제 provider 키/웹훅 인프라", _B),
    ),
    "scheduling_or_publish": (
        ("be", "Backend", "스케줄/발행 상태머신 구현", _R),
        ("fe", "Frontend", "draft/발행 UI", _R),
    ),
}

# Operational signals in the raw ask that imply a deploy/infra task (often blocked).
_OPS_SIGNALS = ("운영", "배포", "deploy", "infra", "인프라", "rollout", "ci/cd", "ci ", "devops")


def intake_packet(raw_ask: str, *, target_user: str = "일반 사용자"):
    """Shape *raw_ask* into a ProductIntentPacket (reuse the product-intake engine).

    Falls back to a minimal packet-like object if the engine is unavailable, so the
    handoff contract still works in a minimal environment (never raises).
    """

    try:
        from yule_engineering.agents.product_intake.shaping import shape_product_intent
    except Exception:  # noqa: BLE001 - engine absent → minimal fallback
        return _FallbackPacket(raw_ask)
    try:
        return shape_product_intent(raw_ask, target_user=target_user)
    except Exception:  # noqa: BLE001 - never let intake crash the handoff
        return _FallbackPacket(raw_ask)


class _FallbackPacket:
    """A minimal ProductIntentPacket-shaped object for env without the engine."""

    def __init__(self, raw_ask: str) -> None:
        self.user_goal = (raw_ask or "").strip()
        self.detected_families: Tuple[str, ...] = ()
        self.implied_features: Tuple = ()
        self.suggested_roles: Tuple[str, ...] = ("tech-lead",)
        self.acceptance_criteria: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "user_goal": self.user_goal,
            "detected_families": [],
            "implied_features": [],
            "suggested_roles": list(self.suggested_roles),
            "acceptance_criteria": [],
            "fallback": True,
        }


def _detected_families(packet) -> Tuple[str, ...]:
    return tuple(getattr(packet, "detected_families", ()) or ())


def tech_lead_split(packet, *, raw_ask: str = "") -> TechLeadSplit:
    """Tech-lead breaks the packet into per-role tasks (FE/BE/DevOps/QA/Security).

    Deterministic: families → role tasks (deduped), QA + tech-lead always added, and
    any infra/deploy area is a BLOCKED task with a runbook hint (no fake execution).
    """

    seen = set()
    tasks: list[RoleTask] = []

    def add(role, role_label, title, state):
        key = (role, title)
        if key in seen:
            return
        seen.add(key)
        blocked = state == ROLE_TASK_BLOCKED
        tasks.append(RoleTask(
            role=role, role_label=role_label, title=title, state=state,
            blocked_reason=_BLOCKED_REASON if blocked else "",
            needs_approval=blocked,
            runbook_hint=_RUNBOOK_HINT if blocked else "",
        ))

    for fam in _detected_families(packet):
        for role, label, title, state in _FAMILY_TASKS.get(fam, ()):  # type: ignore[arg-type]
            add(role, label, title, state)

    # operational/deploy ask → an explicit blocked DevOps task (honest).
    text = (raw_ask or getattr(packet, "user_goal", "") or "").lower()
    if any(sig in text for sig in _OPS_SIGNALS) and not any(t.role == "devops" for t in tasks):
        add("devops", "DevOps", "배포/인프라 rollout (권한 필요)", ROLE_TASK_BLOCKED)

    # baselines tech-lead always assigns.
    add("qa", "QA", "acceptance criteria 기반 회귀/스모크 테스트 작성", ROLE_TASK_READY)
    add("tech-lead", "Tech Lead", "범위/리스크/순서 조율 + role handoff 승인", ROLE_TASK_READY)

    return TechLeadSplit(tasks=tuple(tasks))


def forward_to_tech_lead(packet) -> HandoffTrace:
    """The gateway hop — records that the gateway forwarded the packet to tech-lead."""

    return HandoffTrace(
        phase=PHASE_GATEWAY, author="gateway", author_role="Engineering Gateway",
        handoff_from="product-agent", handoff_to="tech-lead",
        note="ProductIntentPacket 검증 후 tech-lead 로 전달",
    )


def run_handoff(raw_ask: str, *, project: str = "", target_user: str = "일반 사용자") -> Handoff:
    """Full path: raw ask → packet (PM) → gateway → tech-lead split, with trace."""

    packet = intake_packet(raw_ask, target_user=target_user)
    split = tech_lead_split(packet, raw_ask=raw_ask)
    trace = (
        HandoffTrace(
            PHASE_INTAKE, "product-agent", "Product (PM)",
            handoff_from="operator", handoff_to="gateway",
            note="raw ask → implied features 보강 + 결정/기본값 정리",
        ),
        forward_to_tech_lead(packet),
        HandoffTrace(
            PHASE_TECH_LEAD, "tech-lead", "Tech Lead",
            handoff_from="gateway", handoff_to="engineers",
            note=f"{len(split.tasks)} role tasks ({len(split.blocked)} blocked)",
        ),
    )
    return Handoff(raw_ask=raw_ask, packet=packet, split=split, trace=trace, project=project)


__all__ = ("intake_packet", "tech_lead_split", "forward_to_tech_lead", "run_handoff")
