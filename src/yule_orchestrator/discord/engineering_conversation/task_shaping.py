"""engineering_conversation — task_type classification + write-intent heuristics.

Stateless helpers that interpret a free-form message and emit hints used
by the response formatter:

- :func:`_suggest_task_type` — string ``TaskType`` value or ``None``.
  P0-J (#145) ordering: stack_detector first (full-stack / pure infra),
  then keyword table fallback. Bug-fix surface for new task types lives
  here only.
- :func:`_looks_like_write_request` — does the message say *change /
  build / fix* something? Used to tag intake candidates so the gateway
  knows it can move toward a coding role rather than research-only.
- :func:`_looks_like_multiple_tasks` — does the message wedge multiple
  asks into one sentence (``"X 그리고 Y"``)? Triggers split proposal.

Dependencies: ``_normalize`` and ``split_task_branches`` live in
``intent_detection`` (audit doc §2). Until that module is extracted in
step 4, both are imported from ``._legacy``.
"""

from __future__ import annotations

from typing import Optional

from ...agents.messaging.dispatcher import TaskType


def _looks_like_multiple_tasks(message_text: str) -> bool:
    from .intent_detection import split_task_branches

    branches = split_task_branches(message_text)
    if len(branches) < 2:
        return False
    # Require each fragment to look "task-like" (>=2 words). Otherwise we
    # mis-fire on "음 그리고 좋아".
    return all(len(part.split()) >= 2 for part in branches)


def _looks_like_write_request(message_text: str) -> bool:
    from .intent_detection import _normalize

    normalized = _normalize(message_text)
    write_signals = (
        "구현",
        "만들",
        "추가",
        "수정",
        "고쳐",
        "고치",
        "리팩",
        "refactor",
        "implement",
        "build",
        "create",
        "fix",
        "패치",
        "patch",
        "PR",
        "pull request",
        "draft",
        "짜야",
        "짜줘",
        "짜자",
        "작성",
        "쓸게",
        "써줘",
    )
    review_signals = ("어떻게 생각", "분석", "리뷰", "review", "검토", "조사")
    if any(signal.lower() in normalized for signal in review_signals):
        return False
    return any(signal.lower() in normalized for signal in write_signals)


_TASK_TYPE_KEYWORDS: tuple[tuple[TaskType, tuple[str, ...]], ...] = (
    (
        TaskType.VISUAL_POLISH,
        ("visual ", "polish", "리디자인", "redesign", "시각 정리", "visual cleanup"),
    ),
    (
        TaskType.ONBOARDING_FLOW,
        ("onboarding", "온보딩", "signup flow", "가입 흐름", "first-run"),
    ),
    (
        TaskType.EMAIL_CAMPAIGN,
        ("email", "이메일", "campaign", "캠페인", "광고", "ad creative"),
    ),
    (TaskType.LANDING_PAGE, ("landing", "랜딩", "marketing page", "히어로")),
    (TaskType.QA_TEST, ("regression", "회귀", "qa", "test plan", "테스트 시나리오")),
    # P0-J (#145): PLATFORM_INFRA 키워드에서 단독으로 흔히 등장하는 "docker"
    # 제거. Docker / Docker Compose / K8s 가 *full-stack 요청 안에서* 언급되면
    # 본 매칭 전에 stack_detector 의 is_full_stack 가 우선해 FULL_STACK_APP 분류.
    # 본 platform-infra 매칭은 deploy/terraform/github actions 같은 *genuine
    # infra* 신호만 남김.
    (
        TaskType.PLATFORM_INFRA,
        ("infra", "deploy", "ci ", " ci", "terraform", "github action"),
    ),
    (
        TaskType.FRONTEND_FEATURE,
        ("frontend", "ui ", "component", "컴포넌트", "react", "next.js", "vue"),
    ),
    (
        TaskType.BACKEND_FEATURE,
        ("backend", "api ", "schema", "database", "migration", "도메인", "service layer"),
    ),
)


def _suggest_task_type(message_text: str) -> Optional[str]:
    """Classify task type — P0-J (#145) refined.

    Order:

      1. **Stack detector** — if message mentions ≥2 distinct
         application tiers (frontend / backend / database / cache /
         queue / auth) → ``full-stack-app``. This precedes the
         keyword table so "Docker Compose + Next.js + NestJS +
         Postgres" never falls into ``platform-infra``.
      2. **Stack detector — pure infra** — when *only* infra tier is
         detected (terraform / k8s / github actions / docker alone)
         → ``platform-infra``. Keeps the existing classification for
         genuine infra requests.
      3. **Keyword table** — legacy fallback for short messages.
      4. None.
    """

    from .intent_detection import _normalize

    normalized = _normalize(message_text)
    if not normalized:
        return None

    try:
        from ...agents.coding.stack_detector import detect_stacks
    except Exception:  # noqa: BLE001 - partial install fallback
        detect_stacks = None  # type: ignore[assignment]

    if detect_stacks is not None:
        detection = detect_stacks(message_text)
        if detection.is_full_stack:
            return TaskType.FULL_STACK_APP.value
        if detection.is_infra_only:
            return TaskType.PLATFORM_INFRA.value

    for task_type, keywords in _TASK_TYPE_KEYWORDS:
        for keyword in keywords:
            if keyword in normalized:
                return task_type.value
    return None


__all__ = (
    "_looks_like_multiple_tasks",
    "_looks_like_write_request",
    "_suggest_task_type",
    "_TASK_TYPE_KEYWORDS",
)
