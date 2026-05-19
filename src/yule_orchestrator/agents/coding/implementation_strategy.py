"""P1-Z5 — full-stack-app / single_repo implementation strategy synthesizer.

배경
====
canonical session ``000f13fb121b`` 가 노출한 설계 결함:

  * role manifest 의 ``write_scope_candidates`` (예: ``src/<service>/api/**``)
    가 그대로 proposal 의 ``write_scope`` 로 복사
  * target repo ``naver-search-clone`` 은 ``apps/`` 중심 monorepo →
    write_scope 가 worktree 와 0 매칭 →
    ``write_scope_resolved_empty`` 로 종료
  * full-stack-app / single_repo 요청에서 tech-lead 가 **구현 전략을
    먼저 결정** 하는 단계가 없었음.  role keyword score → top executor →
    manifest scope 복사 단순 pipeline 만

본 모듈
========
tech-lead/orchestrator 의 책임을 코드로 명시:

  1. target repo layout 신호 (``apps/`` / ``src/`` / ``frontend/+backend/``
     ...) 수집
  2. user request 의 영역 신호 (full-stack / auth / search / UI / DB ...)
     수집
  3. 두 신호의 조합으로 :class:`ImplementationStrategy` synthesis —
     topology / frontend_root / backend_root / first_slice_owner /
     first_slice_scope / participant_roles / review_roles 결정
  4. 결과는 ``recommend_authorization`` 의 source of truth — manifest
     write_scope 는 strategy unresolved 일 때만 fallback

strategy 가 unresolved 면 placeholder scope 를 silently 내려보내지 않고
``tech_lead_strategy_unresolved`` blocker 로 surface — operator 가 원인
즉시 진단.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple


# Strategy id 상수 — operator surface / regression test 에서 매칭.
STRATEGY_MONOREPO_APPS: str = "monorepo_single_repo_apps"
STRATEGY_MONOREPO_NEXT_NEST: str = "monorepo_single_repo_next_nest"
STRATEGY_FRONTEND_BACKEND_SPLIT: str = "single_repo_frontend_backend_split"
STRATEGY_CLASSIC_SRC_LAYOUT: str = "single_repo_classic_src_layout"
STRATEGY_GREENFIELD_EMPTY: str = "single_repo_greenfield_empty"
STRATEGY_UNRESOLVED: str = "tech_lead_strategy_unresolved"


# scope_source 토큰 — operator 가 "왜 이 write_scope 가 만들어졌는지" 확인.
SCOPE_SOURCE_STRATEGY: str = "strategy"
SCOPE_SOURCE_MANIFEST_FALLBACK: str = "manifest_fallback"
SCOPE_SOURCE_UNRESOLVED: str = "tech_lead_strategy_unresolved"


# Role 토큰.
ROLE_BACKEND: str = "backend-engineer"
ROLE_FRONTEND: str = "frontend-engineer"
ROLE_FULLSTACK: str = "fullstack-engineer"
ROLE_DEVOPS: str = "devops-engineer"
ROLE_QA: str = "qa-engineer"
ROLE_TECH_LEAD: str = "tech-lead"


@dataclass(frozen=True)
class ImplementationStrategy:
    """tech-lead 의 구현 전략 결과 (single_repo / full-stack 한정).

    ``resolved == False`` 면 caller 는 placeholder scope 를 내려보내지
    않고 ``tech_lead_strategy_unresolved`` 로 명시 blocker surface.

    ``first_slice_scope`` 는 strategy 가 결정한 **실제 repo-aware path**
    들.  placeholder literal (``<service>`` 등) 절대 포함 금지.
    """

    strategy_id: str
    topology: str  # "single_repo" / "monorepo" 등 운영 용어
    frontend_root: Optional[str] = None
    backend_root: Optional[str] = None
    shared_roots: Tuple[str, ...] = ()
    first_slice_owner: Optional[str] = None
    first_slice_scope: Tuple[str, ...] = ()
    first_slice_forbidden: Tuple[str, ...] = ()
    participant_roles: Tuple[str, ...] = ()
    review_roles: Tuple[str, ...] = ()
    rationale: str = ""
    resolved: bool = False
    repo_layout_signals: Mapping[str, Any] = field(default_factory=dict)
    request_signals: Mapping[str, Any] = field(default_factory=dict)
    fallback_reason: Optional[str] = None

    def to_audit(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "topology": self.topology,
            "frontend_root": self.frontend_root,
            "backend_root": self.backend_root,
            "shared_roots": list(self.shared_roots),
            "first_slice_owner": self.first_slice_owner,
            "first_slice_scope": list(self.first_slice_scope),
            "first_slice_forbidden": list(self.first_slice_forbidden),
            "participant_roles": list(self.participant_roles),
            "review_roles": list(self.review_roles),
            "rationale": self.rationale,
            "resolved": self.resolved,
            "repo_layout_signals": dict(self.repo_layout_signals),
            "request_signals": dict(self.request_signals),
            "fallback_reason": self.fallback_reason,
        }


# ---------------------------------------------------------------------------
# Request signal extraction
# ---------------------------------------------------------------------------


_BACKEND_FIRST_HINTS: Tuple[str, ...] = (
    "인증", "로그인", "회원가입", "auth", "login", "sign up", "sign-up",
    "검색", "search", "api", "REST", "endpoint", "db ", "데이터베이스",
    "스키마", "migration", "마이그레이션", "백엔드 먼저", "백엔드부터",
    "backend first", "backend-first",
)
_FRONTEND_FIRST_HINTS: Tuple[str, ...] = (
    "ui 부터", "ui부터", "화면 부터", "화면부터", "프론트 먼저", "프론트엔드 먼저",
    "frontend first", "frontend-first", "디자인 시스템", "design system",
)
_FULLSTACK_HINTS: Tuple[str, ...] = (
    "풀스택", "fullstack", "full-stack", "full stack", "mvp", "monorepo",
    "단일 repo", "단일레포",
)


def detect_request_signals(user_request: str) -> Mapping[str, Any]:
    """user_request 에서 구현 전략 신호 추출.

    Returns dict with: ``full_stack``, ``backend_first_hints``,
    ``frontend_first_hints``, ``scope_hints``.
    """

    text = (user_request or "").lower()
    backend_hits = [kw for kw in _BACKEND_FIRST_HINTS if kw in text]
    frontend_hits = [kw for kw in _FRONTEND_FIRST_HINTS if kw in text]
    fullstack_hits = [kw for kw in _FULLSTACK_HINTS if kw in text]
    scope_hits: list[str] = []
    for label, keywords in (
        ("auth", ("인증", "회원가입", "로그인", "auth")),
        ("search", ("검색", "search")),
        ("blog", ("블로그", "blog")),
        ("mail", ("메일", "mail", "inbox")),
        ("ui", ("ui", "화면", "frontend", "디자인")),
        ("db", ("db ", "스키마", "schema", "데이터베이스")),
    ):
        for kw in keywords:
            if kw in text:
                scope_hits.append(label)
                break
    return {
        "full_stack": bool(fullstack_hits),
        "fullstack_hits": fullstack_hits[:3],
        "backend_first_hints": backend_hits[:3],
        "frontend_first_hints": frontend_hits[:3],
        "scope_hints": list(dict.fromkeys(scope_hits)),
    }


# ---------------------------------------------------------------------------
# Repo layout signal extraction
# ---------------------------------------------------------------------------


def detect_repo_layout_signals(
    toplevel_paths: Sequence[str] = (),
) -> Mapping[str, Any]:
    """toplevel directory 이름들로부터 repo 구조 신호 추출.

    *toplevel_paths* 는 ``repo_contract.toplevel_paths`` / worktree
    scan / 명시 hint 어느 source 든 받아들임 — caller 가 normalize.
    """

    norm = {str(p).strip("/").split("/")[0].lower() for p in (toplevel_paths or ()) if str(p).strip()}
    return {
        "toplevel": sorted(norm),
        "has_apps_dir": "apps" in norm,
        "has_packages_dir": "packages" in norm,
        "has_src_dir": "src" in norm,
        "has_frontend_dir": "frontend" in norm,
        "has_backend_dir": "backend" in norm,
        "has_server_dir": "server" in norm,
        "has_client_dir": "client" in norm,
        "has_web_dir": "web" in norm,
        "has_api_dir": "api" in norm,
        "has_migrations_dir": "migrations" in norm,
        "has_tests_dir": "tests" in norm,
        "is_empty_or_unknown": not norm,
    }


# ---------------------------------------------------------------------------
# Strategy synthesizer
# ---------------------------------------------------------------------------


def synthesize_implementation_strategy(
    *,
    user_request: str,
    toplevel_paths: Sequence[str] = (),
    lifecycle_mode: str = "implementation",
    task_type: Optional[str] = None,
    topology: str = "single_repo",
    explicit_first_slice_owner: Optional[str] = None,
) -> ImplementationStrategy:
    """tech-lead 가 구현 전략을 결정.

    *user_request* + *toplevel_paths* + *task_type* + *topology* 신호를
    조합해 ``ImplementationStrategy`` 산출.

    full-stack-app / single_repo 가 아닌 경우 (research_only / 다른 task
    type) 는 ``STRATEGY_UNRESOLVED`` 로 떨어져 caller 가 manifest fallback
    또는 명시 blocker 로 처리.

    *explicit_first_slice_owner* 가 주어지면 final first_slice_owner 결정에
    우선 — operator 가 명시한 경우만 사용.
    """

    if lifecycle_mode == "research_only":
        return ImplementationStrategy(
            strategy_id=STRATEGY_UNRESOLVED,
            topology=topology,
            resolved=False,
            fallback_reason="lifecycle_mode=research_only — no implementation strategy needed",
        )

    request_signals = detect_request_signals(user_request)
    repo_signals = detect_repo_layout_signals(toplevel_paths)

    # 1) layout 결정
    strategy_id: str
    frontend_root: Optional[str] = None
    backend_root: Optional[str] = None
    shared_roots: Tuple[str, ...] = ()
    rationale_parts: list[str] = []

    if repo_signals["has_apps_dir"]:
        # apps/ monorepo — Next/Nest 또는 일반 monorepo
        frontend_root = "apps/web"
        backend_root = "apps/api"
        shared_roots = ("packages/**",) if repo_signals["has_packages_dir"] else ()
        # frontend/backend 의 정확한 폴더는 unknown — 일반 apps/ monorepo 로
        strategy_id = STRATEGY_MONOREPO_APPS
        rationale_parts.append("repo top-level 에 apps/ 발견 → monorepo")
        if repo_signals["has_packages_dir"]:
            rationale_parts.append("packages/ 도 발견 → 공유 영역 포함")
    elif repo_signals["has_frontend_dir"] and repo_signals["has_backend_dir"]:
        frontend_root = "frontend"
        backend_root = "backend"
        strategy_id = STRATEGY_FRONTEND_BACKEND_SPLIT
        rationale_parts.append("frontend/ + backend/ top-level → 분리 split layout")
    elif repo_signals["has_client_dir"] and repo_signals["has_server_dir"]:
        frontend_root = "client"
        backend_root = "server"
        strategy_id = STRATEGY_FRONTEND_BACKEND_SPLIT
        rationale_parts.append("client/ + server/ top-level → 분리 split layout")
    elif repo_signals["has_src_dir"]:
        # src/ 단일 — 가장 흔한 monolith
        backend_root = "src"
        frontend_root = None  # src 안에 다 들어있을 수도
        strategy_id = STRATEGY_CLASSIC_SRC_LAYOUT
        rationale_parts.append("repo top-level 에 src/ → classic monolith")
    elif repo_signals["is_empty_or_unknown"]:
        strategy_id = STRATEGY_GREENFIELD_EMPTY
        rationale_parts.append("repo 비어있거나 미식별 → greenfield")
    else:
        # 미분류 → unresolved
        return ImplementationStrategy(
            strategy_id=STRATEGY_UNRESOLVED,
            topology=topology,
            resolved=False,
            rationale="repo top-level 구조 미식별 — apps/src/frontend/backend 어느 hint 도 없음",
            repo_layout_signals=dict(repo_signals),
            request_signals=dict(request_signals),
            fallback_reason="repo_layout_unclassified",
        )

    # 2) first_slice_owner 결정 — user-intent 우선
    first_slice_owner: Optional[str]
    if explicit_first_slice_owner:
        first_slice_owner = explicit_first_slice_owner
        rationale_parts.append(f"explicit first_slice_owner={explicit_first_slice_owner}")
    elif request_signals["frontend_first_hints"]:
        first_slice_owner = ROLE_FRONTEND
        rationale_parts.append(
            f"user 가 frontend first 신호 ({request_signals['frontend_first_hints'][0]})"
        )
    elif request_signals["backend_first_hints"]:
        first_slice_owner = ROLE_BACKEND
        rationale_parts.append(
            f"user 가 backend-domain 신호 ({request_signals['backend_first_hints'][0]})"
        )
    elif backend_root:
        # 기본 휴리스틱 — full-stack 일 때 backend 부터 (auth/DB/API 기반)
        first_slice_owner = ROLE_BACKEND
        rationale_parts.append("default: full-stack 첫 slice 는 backend (auth+API+DB)")
    elif frontend_root:
        first_slice_owner = ROLE_FRONTEND
        rationale_parts.append("default: frontend-only repo 로 추정")
    else:
        first_slice_owner = ROLE_TECH_LEAD
        rationale_parts.append("default: tech-lead 진단")

    # 3) first_slice_scope 결정 — strategy + owner
    first_slice_scope = _build_first_slice_scope(
        strategy_id=strategy_id,
        first_slice_owner=first_slice_owner,
        backend_root=backend_root,
        frontend_root=frontend_root,
        shared_roots=shared_roots,
        repo_signals=repo_signals,
    )

    # 4) participant + review roles — strategy 기반
    participants, reviewers = _resolve_participants_and_reviewers(
        first_slice_owner=first_slice_owner,
        strategy_id=strategy_id,
        request_signals=request_signals,
    )

    # forbidden — strategy 의 first_slice_owner 가 다른 영역 건드리지 않도록
    first_slice_forbidden = _build_first_slice_forbidden(
        first_slice_owner=first_slice_owner,
        backend_root=backend_root,
        frontend_root=frontend_root,
    )

    return ImplementationStrategy(
        strategy_id=strategy_id,
        topology=topology,
        frontend_root=frontend_root,
        backend_root=backend_root,
        shared_roots=shared_roots,
        first_slice_owner=first_slice_owner,
        first_slice_scope=first_slice_scope,
        first_slice_forbidden=first_slice_forbidden,
        participant_roles=participants,
        review_roles=reviewers,
        rationale=" · ".join(rationale_parts),
        resolved=bool(first_slice_owner) and bool(first_slice_scope),
        repo_layout_signals=dict(repo_signals),
        request_signals=dict(request_signals),
        fallback_reason=None,
    )


def _build_first_slice_scope(
    *,
    strategy_id: str,
    first_slice_owner: Optional[str],
    backend_root: Optional[str],
    frontend_root: Optional[str],
    shared_roots: Tuple[str, ...],
    repo_signals: Mapping[str, Any],
) -> Tuple[str, ...]:
    """strategy + owner 에 맞는 실제 repo-aware write_scope 생성."""

    scope: list[str] = []
    if strategy_id == STRATEGY_GREENFIELD_EMPTY:
        # greenfield 면 모든 영역 — bootstrap editor 가 scaffold 의 path 결정
        scope.extend(("apps/**", "packages/**", "**"))
        return tuple(scope)

    if first_slice_owner == ROLE_BACKEND and backend_root:
        scope.append(f"{backend_root}/**")
        # shared 도 backend 작업 시 함께
        scope.extend(shared_roots)
        # 일반적 backend artifact 영역 (있을 때만)
        if repo_signals.get("has_migrations_dir"):
            scope.append("migrations/**")
        # tests — backend 영역
        if repo_signals.get("has_tests_dir"):
            if backend_root == "apps/api":
                scope.append("tests/api/**")
            elif backend_root == "backend":
                scope.append("tests/backend/**")
            elif backend_root == "server":
                scope.append("tests/server/**")
            else:
                scope.append("tests/**")
    elif first_slice_owner == ROLE_FRONTEND and frontend_root:
        scope.append(f"{frontend_root}/**")
        scope.extend(shared_roots)
        if repo_signals.get("has_tests_dir"):
            scope.append("tests/**")
    elif first_slice_owner == ROLE_FULLSTACK:
        # 전체
        if backend_root:
            scope.append(f"{backend_root}/**")
        if frontend_root and frontend_root != backend_root:
            scope.append(f"{frontend_root}/**")
        scope.extend(shared_roots)
    # placeholder literal 절대 안 들어감
    return tuple(scope)


def _build_first_slice_forbidden(
    *,
    first_slice_owner: Optional[str],
    backend_root: Optional[str],
    frontend_root: Optional[str],
) -> Tuple[str, ...]:
    """first slice 에 한해서 반대 영역 수정 금지 — slice 분리 강제."""

    forbidden: list[str] = [".github/workflows/**"]
    if first_slice_owner == ROLE_BACKEND and frontend_root and frontend_root != backend_root:
        forbidden.append(f"{frontend_root}/**")
    elif first_slice_owner == ROLE_FRONTEND and backend_root and backend_root != frontend_root:
        forbidden.append(f"{backend_root}/**")
    return tuple(forbidden)


def _resolve_participants_and_reviewers(
    *,
    first_slice_owner: Optional[str],
    strategy_id: str,
    request_signals: Mapping[str, Any],
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """full-stack 이면 반대 영역 + devops + qa 가 살아있어야 함."""

    participants: list[str] = []
    if first_slice_owner == ROLE_BACKEND:
        participants = [ROLE_FRONTEND, ROLE_DEVOPS]
    elif first_slice_owner == ROLE_FRONTEND:
        participants = [ROLE_BACKEND, ROLE_DEVOPS]
    elif first_slice_owner == ROLE_FULLSTACK:
        participants = [ROLE_DEVOPS]
    else:
        participants = [ROLE_BACKEND, ROLE_FRONTEND]

    reviewers = [ROLE_TECH_LEAD, ROLE_QA]
    return tuple(participants), tuple(reviewers)


__all__ = (
    "ImplementationStrategy",
    "ROLE_BACKEND",
    "ROLE_DEVOPS",
    "ROLE_FRONTEND",
    "ROLE_FULLSTACK",
    "ROLE_QA",
    "ROLE_TECH_LEAD",
    "SCOPE_SOURCE_MANIFEST_FALLBACK",
    "SCOPE_SOURCE_STRATEGY",
    "SCOPE_SOURCE_UNRESOLVED",
    "STRATEGY_CLASSIC_SRC_LAYOUT",
    "STRATEGY_FRONTEND_BACKEND_SPLIT",
    "STRATEGY_GREENFIELD_EMPTY",
    "STRATEGY_MONOREPO_APPS",
    "STRATEGY_MONOREPO_NEXT_NEST",
    "STRATEGY_UNRESOLVED",
    "detect_repo_layout_signals",
    "detect_request_signals",
    "synthesize_implementation_strategy",
)
