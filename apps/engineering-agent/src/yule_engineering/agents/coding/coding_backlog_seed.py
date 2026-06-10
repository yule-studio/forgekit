"""P1-M C — deterministic ``coding_backlog`` seeder.

배경 — merge 후 자동 continuation 은 ``session.extra["coding_backlog"]``
가 채워져 있어야 동작. 옛 intake 경로는 backlog 를 절대 만들지 않아서
첫 merge 후 항상 session done 으로 마감됐다.

본 모듈은 두 가지 시점에 호출된다:
  * 새 intake (`/engineer_intake` slash 또는 채널 router) 직후
  * 옛 session recovery 시 (operator CLI)

backlog 가 이미 있으면 절대 덮어쓰지 않음 — idempotent. full-stack
single-repo + greenfield 의도 (또는 naver-search-clone 같은 known canary)
면 deterministic 8-slice 를 stamp. 그 외에는 빈 list 만 보장 (operator
가 수동으로 채울 수도 있게).

slice spec 포맷 (각 항목은 dict):
  * ``title``       — 사람이 한눈에 보는 제목 (한국어)
  * ``summary``     — 한 줄 요약
  * ``area``        — auth / search / blog / mail / runtime / polish 등
  * ``executor_role`` — backend-engineer | frontend-engineer | platform-engineer
  * ``prompt``      — coding executor 에게 넘길 한국어 프롬프트
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from ..lifecycle.session_mode import (
    EXTRA_SCOPE,
    EXTRA_TOPOLOGY,
    SCOPE_FULL_STACK,
    TOPOLOGY_SINGLE,
)


EXTRA_CODING_BACKLOG: str = "coding_backlog"
EXTRA_CODING_BACKLOG_SEEDED_BY: str = "coding_backlog_seeded_by"
EXTRA_CODING_BACKLOG_SEEDED_AT: str = "coding_backlog_seeded_at"


# canonical 8-slice plan for naver-search-clone 류 full-stack 검색 MVP.
# 각 slice 는 1 commit / 1 PR 단위로 굴러갈 수 있도록 좁게 끊었다.
FULL_STACK_SEARCH_MVP_PLAN: tuple = (
    {
        "title": "인증 백엔드 — 회원가입/로그인 API + 세션",
        "summary": "auth backend: 회원가입/로그인/세션 + 비밀번호 해시",
        "area": "auth",
        "executor_role": "backend-engineer",
        "prompt": (
            "네이버 검색 풀스택 MVP 의 인증 백엔드를 구현해줘. "
            "회원가입 / 로그인 API + 세션 (httpOnly cookie) + bcrypt 비밀번호 해시. "
            "Next.js + NestJS + Postgres 스택 가정. "
            "기존 scaffold 위에 services/auth 디렉터리에 추가."
        ),
    },
    {
        "title": "인증 프론트엔드 — 로그인/회원가입 화면 + 클라이언트 세션",
        "summary": "auth frontend: 로그인/회원가입 form + 세션 hooks",
        "area": "auth",
        "executor_role": "frontend-engineer",
        "prompt": (
            "네이버 검색 풀스택 MVP 의 인증 프론트엔드를 구현해줘. "
            "Next.js 페이지: /login, /signup. "
            "useSession hook + form validation. "
            "백엔드 API 와 fetch 연동."
        ),
    },
    {
        "title": "검색 홈 UI — 네이버 검색창 레퍼런스 (1:1 복제 금지)",
        "summary": "search home: 검색창 + 자동완성 placeholder",
        "area": "search",
        "executor_role": "frontend-engineer",
        "prompt": (
            "검색 홈 화면을 구현해줘. 네이버 검색 홈을 강하게 참고하되 로고/"
            "상표/문구의 1:1 복제는 피한다. 중앙 검색창 + 검색 버튼 + "
            "최근 검색어 placeholder. /search 페이지로 라우팅."
        ),
    },
    {
        "title": "검색 결과 탭 + 통합 검색 API",
        "summary": "search results: 통합/블로그/카페/이미지 탭 + API",
        "area": "search",
        "executor_role": "backend-engineer",
        "prompt": (
            "검색 결과 페이지와 통합 검색 API 를 구현해줘. /search?q=... "
            "탭: 통합 / 블로그 / 카페 / 이미지. "
            "백엔드 API: GET /api/search?q=&tab= → 모의 데이터 반환 (DB seed). "
            "다음 slice 의 블로그/메일 모듈 데이터와 호환되는 schema."
        ),
    },
    {
        "title": "블로그 코어 — 글 작성/조회/검색 노출",
        "summary": "blog core: 글 CRUD + 검색 결과 노출",
        "area": "blog",
        "executor_role": "backend-engineer",
        "prompt": (
            "블로그 코어 모듈을 구현해줘. 글 작성 / 조회 / 목록 / 단일 글. "
            "프론트엔드: /blog, /blog/[id], /blog/write. "
            "검색 결과 페이지의 '블로그' 탭이 본 모듈의 글을 노출하도록 연결."
        ),
    },
    {
        "title": "메일 코어 — 보낸 메일/받은 메일 + 작성",
        "summary": "mail core: 받은 메일함 + 작성",
        "area": "mail",
        "executor_role": "backend-engineer",
        "prompt": (
            "메일 코어 모듈을 구현해줘. 받은 메일함 / 보낸 메일함 / 메일 작성. "
            "API + 프론트엔드 /mail, /mail/compose. "
            "인증된 사용자만 접근. 외부 SMTP 없이 내부 DB 만 사용 (MVP)."
        ),
    },
    {
        "title": "Docker / dev runtime 안정화",
        "summary": "docker-compose up → 전체 스택 한 번에 기동",
        "area": "runtime",
        "executor_role": "platform-engineer",
        "prompt": (
            "docker-compose 와 dev runtime 안정화. "
            "docker-compose up 한 번으로 Postgres / NestJS / Next.js 모두 기동. "
            ".env.example 만 보존 (.env 는 hard-forbidden). "
            "README 에 로컬 개발 실행 절차 추가."
        ),
    },
    {
        "title": "테스트 커버리지 + 폴리시",
        "summary": "tests + polish: 핵심 흐름 회귀 + UX 다듬기",
        "area": "polish",
        "executor_role": "backend-engineer",
        "prompt": (
            "핵심 흐름 (인증 / 검색 / 블로그 / 메일) 회귀 테스트 추가. "
            "프론트엔드 UX 작은 폴리시 (loading state, error toast). "
            "마무리 PR."
        ),
    },
)


def detect_backlog_plan(
    session_extra: Optional[Mapping[str, Any]],
    *,
    prompt: Optional[str] = None,
) -> Sequence[Mapping[str, Any]]:
    """session 상태에 맞는 deterministic backlog plan 반환.

    full_stack_single_repo + 검색/포털 류 키워드 → FULL_STACK_SEARCH_MVP_PLAN.
    그 외에는 빈 tuple (의도가 명확하지 않으면 backlog 생성 안 함).
    """

    extra = session_extra or {}
    scope = str(extra.get(EXTRA_SCOPE) or "").strip()
    topology = str(extra.get(EXTRA_TOPOLOGY) or "").strip()
    text_lower = str(prompt or "").lower()

    full_stack_signal = scope == SCOPE_FULL_STACK and topology == TOPOLOGY_SINGLE
    search_signal = any(
        token in text_lower
        for token in (
            "네이버", "naver", "검색", "search",
            "포털", "portal", "blog", "블로그",
        )
    )

    if full_stack_signal and search_signal:
        return FULL_STACK_SEARCH_MVP_PLAN
    return ()


def seed_coding_backlog(
    *,
    session_id: Optional[str],
    force: bool = False,
    explicit_plan: Optional[Sequence[Mapping[str, Any]]] = None,
    seeded_by: str = "intake",
) -> Optional[Sequence[Mapping[str, Any]]]:
    """session.extra 에 ``coding_backlog`` stamp — idempotent.

    *force* 가 False (기본) 이면 backlog 가 이미 있고 비어있지 않으면 보존.
    *explicit_plan* 을 주면 detect 무시. 그 외에는 ``detect_backlog_plan``.

    Returns: stamp 된 backlog (없으면 None).
    """

    if not session_id:
        return None

    try:
        from dataclasses import replace as _replace
        from datetime import datetime, timezone
        from ..workflow_state import load_session, update_session
    except Exception:  # noqa: BLE001 - partial install
        return None

    try:
        session = load_session(session_id)
    except Exception:  # noqa: BLE001
        return None
    if session is None:
        return None

    extra = dict(getattr(session, "extra", None) or {})
    existing = extra.get(EXTRA_CODING_BACKLOG)
    if (
        not force
        and isinstance(existing, list)
        and len(existing) > 0
    ):
        return tuple(existing)

    if explicit_plan is not None:
        plan = tuple(dict(item) for item in explicit_plan)
    else:
        prompt_text = str(getattr(session, "prompt", "") or "")
        plan = tuple(dict(item) for item in detect_backlog_plan(extra, prompt=prompt_text))

    if not plan:
        # backlog 자체를 만들 의도가 없으면 stamp 도 하지 않음
        return None

    now_iso = datetime.now(tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    extra[EXTRA_CODING_BACKLOG] = [dict(item) for item in plan]
    extra[EXTRA_CODING_BACKLOG_SEEDED_BY] = seeded_by
    extra[EXTRA_CODING_BACKLOG_SEEDED_AT] = now_iso
    try:
        updated = _replace(session, extra=extra)
        update_session(updated, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        return None
    return plan


__all__ = (
    "EXTRA_CODING_BACKLOG",
    "EXTRA_CODING_BACKLOG_SEEDED_AT",
    "EXTRA_CODING_BACKLOG_SEEDED_BY",
    "FULL_STACK_SEARCH_MVP_PLAN",
    "detect_backlog_plan",
    "seed_coding_backlog",
)
