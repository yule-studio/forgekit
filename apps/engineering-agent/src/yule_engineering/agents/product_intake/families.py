"""Feature-family heuristics — the PM's domain knowledge as data.

A raw ask maps to one or more *feature families*; each family declares the
features a real service implies, the business decisions a user must make, and the
safe defaults the PM auto-fills. Decision templates render those decisions as
short option-shaped questions (with a recommended answer). Keeping this as data
(not code branches) keeps the shaping logic tiny and the rules reviewable.
"""

from __future__ import annotations

from typing import Mapping, Tuple

from .models import DecisionQuestion, FeatureFamily, QuestionOption

# --- families ---------------------------------------------------------------

FAMILIES: Tuple[FeatureFamily, ...] = (
    FeatureFamily(
        "media_upload", "미디어 업로드",
        implied=("processing_state", "failure_retry", "thumbnail_fallback",
                 "visibility_state", "ordering_display"),
        ask=("who_can_upload_view", "visibility_policy", "ordering_policy"),
        recommended_defaults=("admin-only upload", "default private until publish",
                              "manual ordering when an admin surface exists"),
        suggested_roles=("backend-engineer", "frontend-engineer"),
    ),
    FeatureFamily(
        "admin_crud", "관리자 CRUD",
        implied=("draft_state", "validation", "audit_trail", "list_pagination"),
        ask=("publish_visibility", "ordering_policy", "draft_support"),
        recommended_defaults=("draft before publish", "newest first", "admin-only edit/delete"),
        suggested_roles=("backend-engineer", "frontend-engineer"),
    ),
    FeatureFamily(
        "auth_and_permission", "인증·권한",
        implied=("session_management", "password_reset", "role_scope"),
        ask=("auth_method", "session_policy", "role_scope"),
        recommended_defaults=("email+password", "server session/JWT", "single default role"),
        suggested_roles=("backend-engineer", "security-engineer"),
    ),
    FeatureFamily(
        "list_detail_catalog", "목록·상세",
        implied=("empty_state", "pagination", "detail_view"),
        ask=("ordering_policy",),
        recommended_defaults=("newest first", "basic filter"),
        suggested_roles=("frontend-engineer", "backend-engineer"),
    ),
    FeatureFamily(
        "notification", "알림",
        implied=("delivery_channel", "read_state", "opt_out"),
        ask=("notification_channels",),
        recommended_defaults=("in-app by default", "immediate delivery"),
        suggested_roles=("backend-engineer",),
    ),
    FeatureFamily(
        "payment_or_billing", "결제·과금",
        implied=("payment_provider", "receipt", "refund_flow", "payment_failure_handling"),
        ask=("billing_model", "refund_policy"),
        recommended_defaults=("Stripe", "manual refund first"),
        suggested_roles=("backend-engineer", "security-engineer"),
    ),
    FeatureFamily(
        "search_filter", "검색·필터",
        implied=("empty_results", "query_validation"),
        ask=("search_scope",),
        recommended_defaults=("title search", "basic filters"),
        suggested_roles=("backend-engineer", "frontend-engineer"),
    ),
    FeatureFamily(
        "scheduling_or_publish", "예약·발행",
        implied=("scheduled_state", "timezone_handling", "publish_failure"),
        ask=("schedule_policy",),
        recommended_defaults=("immediate or scheduled", "user timezone"),
        suggested_roles=("backend-engineer",),
    ),
)

FAMILY_BY_KEY: Mapping[str, FeatureFamily] = {f.key: f for f in FAMILIES}

# keyword → family. Lowercased substring match against the raw ask (ko + en).
FAMILY_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("media_upload", ("업로드", "영상", "비디오", "이미지", "사진", "파일", "upload", "video", "media")),
    ("admin_crud", ("관리자", "공지", "게시", "crud", "admin", "글 작성", "글작성", "post", "관리")),
    ("auth_and_permission", ("로그인", "회원가입", "인증", "권한", "auth", "login", "signup", "permission", "role")),
    ("list_detail_catalog", ("목록", "리스트", "카탈로그", "피드", "list", "catalog", "feed", "상세")),
    ("notification", ("알림", "푸시", "notification", "notify", "push")),
    ("payment_or_billing", ("결제", "구독", "과금", "billing", "payment", "subscribe", "checkout", "요금")),
    ("search_filter", ("검색", "필터", "search", "filter", "정렬")),
    ("scheduling_or_publish", ("예약", "발행", "스케줄", "schedule", "publish", "예약 공개")),
)

# --- decision templates -----------------------------------------------------
# decision key → DecisionQuestion (prompt + category + option list w/ recommend).

_TEMPLATES: Tuple[DecisionQuestion, ...] = (
    DecisionQuestion(
        "who_can_upload_view", "업로드/조회 주체는 누구로 할까요?", "permission",
        (QuestionOption("관리자만 업로드", True), QuestionOption("일반 유저도 업로드"),
         QuestionOption("조회만 공개, 업로드는 관리자")),
    ),
    DecisionQuestion(
        "visibility_policy", "공개 정책은 어떤 걸로 갈까요?", "visibility",
        (QuestionOption("비공개 후 수동 공개", True), QuestionOption("즉시 공개"),
         QuestionOption("예약 공개")),
    ),
    DecisionQuestion(
        "ordering_policy", "노출 순서는 어떻게 할까요?", "ordering",
        (QuestionOption("최신순", True), QuestionOption("수동 정렬"), QuestionOption("인기순")),
    ),
    DecisionQuestion(
        "publish_visibility", "작성 후 공개 방식은?", "visibility",
        (QuestionOption("draft 후 수동 공개", True), QuestionOption("즉시 공개")),
    ),
    DecisionQuestion(
        "draft_support", "임시저장(draft)을 지원할까요?", "publish",
        (QuestionOption("지원", True), QuestionOption("미지원")),
    ),
    DecisionQuestion(
        "auth_method", "인증 방식은 무엇으로 할까요?", "permission",
        (QuestionOption("이메일+비밀번호", True), QuestionOption("소셜 로그인(OAuth)"),
         QuestionOption("매직링크")),
    ),
    DecisionQuestion(
        "session_policy", "세션 정책은?", "permission",
        (QuestionOption("서버 세션/JWT 만료 7일", True), QuestionOption("단기 만료+refresh")),
    ),
    DecisionQuestion(
        "role_scope", "권한 범위(role)는?", "permission",
        (QuestionOption("단일 기본 role", True), QuestionOption("admin/user 분리"),
         QuestionOption("세분화된 RBAC")),
    ),
    DecisionQuestion(
        "billing_model", "과금 모델은 무엇인가요?", "billing",
        (QuestionOption("구독(subscription)", True), QuestionOption("단건 결제"),
         QuestionOption("사용량 기반")),
    ),
    DecisionQuestion(
        "refund_policy", "환불 정책은?", "billing",
        (QuestionOption("수동 환불부터", True), QuestionOption("자동 환불")),
    ),
    DecisionQuestion(
        "notification_channels", "알림 채널은?", "external_integration",
        (QuestionOption("인앱만", True), QuestionOption("인앱+이메일"), QuestionOption("푸시 포함")),
    ),
    DecisionQuestion(
        "search_scope", "검색 범위는?", "ordering",
        (QuestionOption("제목 검색", True), QuestionOption("제목+본문"), QuestionOption("전체 메타")),
    ),
    DecisionQuestion(
        "schedule_policy", "예약 발행 정책은?", "publish",
        (QuestionOption("즉시 또는 예약 선택", True), QuestionOption("즉시만"), QuestionOption("항상 예약")),
    ),
)

TEMPLATE_BY_KEY: Mapping[str, DecisionQuestion] = {t.id: t for t in _TEMPLATES}

# --- baseline cross-cutting augments (auto-filled, never asked) --------------
BASELINE_DEFAULTS: Tuple[str, ...] = (
    "로딩 / 빈 상태 / 에러 상태 처리",
    "입력 검증(validation)",
    "권한·노출 체크(permission/visibility guard)",
    "모바일/반응형 고려",
)
BASELINE_OBSERVABILITY = "주요 액션 audit/observability 훅(쓰기·삭제 등 민감 작업)"


def detect_families(text: str) -> Tuple[str, ...]:
    """Return the family keys whose keywords appear in *text* (stable order)."""

    low = (text or "").lower()
    hits = []
    for key, words in FAMILY_KEYWORDS:
        if any(w in low for w in words):
            hits.append(key)
    return tuple(hits)


__all__ = (
    "FAMILIES", "FAMILY_BY_KEY", "FAMILY_KEYWORDS", "TEMPLATE_BY_KEY",
    "BASELINE_DEFAULTS", "BASELINE_OBSERVABILITY", "detect_families",
)
