"""Engineering tech-stack detector — P0-J (#145).

When a user pastes a coding request like "Next.js + NestJS + PostgreSQL
+ Docker Compose 회원가입/로그인/검색", the previous
``_suggest_task_type`` matched on the single word "docker" and
classified the request as ``platform-infra``. That blocked the
gateway with the "official_docs / code_context 부족" insufficiency
gate even though the user clearly described a coding-capable
full-stack app.

This module recognises engineering stacks across multiple **tiers**
(frontend / backend / database / infra-tool / cache / queue /
testing) so the caller can:

  1. classify a request as **full-stack** when ≥2 distinct tiers
     are mentioned (commit 4 wires this into ``_suggest_task_type``),
  2. seed official-docs URLs per detected stack (commit 3),
  3. surface the detected stack list in the gateway status surface
     and the coding handoff packet.

The detector is **pure / network-free**. It uses case-insensitive
substring matching against a curated lexicon. Each stack carries its
*tier* + its *canonical display name* so callers don't have to
re-derive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence, Tuple


# Tier constants — used by ``classify_full_stack`` to decide whether
# enough distinct tiers were mentioned.
TIER_FRONTEND = "frontend"
TIER_BACKEND = "backend"
TIER_DATABASE = "database"
TIER_INFRA = "infra"
TIER_CACHE = "cache"
TIER_QUEUE = "queue"
TIER_TESTING = "testing"
TIER_AUTH = "auth"

TIERS = (
    TIER_FRONTEND,
    TIER_BACKEND,
    TIER_DATABASE,
    TIER_INFRA,
    TIER_CACHE,
    TIER_QUEUE,
    TIER_TESTING,
    TIER_AUTH,
)


@dataclass(frozen=True)
class StackEntry:
    """One row in the stack lexicon — display name, tier, match aliases."""

    canonical: str
    tier: str
    aliases: Tuple[str, ...]


# Curated engineering lexicon. Each entry's aliases are matched as
# **whole tokens or known compounds** so "docker" alone fires the
# infra tier but the combo logic (commit 4) still allows the request
# to be classified as full-stack when other tiers are also present.
_LEXICON: Tuple[StackEntry, ...] = (
    # --- Frontend ----------------------------------------------------------
    StackEntry("Next.js", TIER_FRONTEND, ("next.js", "nextjs", "next js")),
    StackEntry("React", TIER_FRONTEND, ("react", "리액트")),
    StackEntry("Vue", TIER_FRONTEND, ("vue.js", "vuejs", "vue")),
    StackEntry("Svelte", TIER_FRONTEND, ("svelte", "sveltekit")),
    StackEntry("Tailwind", TIER_FRONTEND, ("tailwind", "tailwindcss")),
    StackEntry("Vite", TIER_FRONTEND, ("vite", "vitejs")),
    StackEntry("Angular", TIER_FRONTEND, ("angular",)),
    # --- Backend -----------------------------------------------------------
    StackEntry("NestJS", TIER_BACKEND, ("nest.js", "nestjs", "nest js")),
    StackEntry("Express", TIER_BACKEND, ("express.js", "expressjs", "express ")),
    StackEntry("FastAPI", TIER_BACKEND, ("fastapi", "fast api")),
    StackEntry("Django", TIER_BACKEND, ("django",)),
    StackEntry("Flask", TIER_BACKEND, ("flask",)),
    StackEntry("Spring Boot", TIER_BACKEND, ("spring boot", "springboot", "spring-boot")),
    StackEntry("Rails", TIER_BACKEND, ("rails", "ruby on rails")),
    StackEntry("Gin", TIER_BACKEND, ("gin ", "gin-gonic")),
    StackEntry("Node.js", TIER_BACKEND, ("node.js", "nodejs", "node js")),
    # --- Database ----------------------------------------------------------
    StackEntry("PostgreSQL", TIER_DATABASE, ("postgresql", "postgres", "psql")),
    StackEntry("MySQL", TIER_DATABASE, ("mysql", "mariadb")),
    StackEntry("MongoDB", TIER_DATABASE, ("mongodb", "mongo")),
    StackEntry("SQLite", TIER_DATABASE, ("sqlite",)),
    StackEntry("Prisma", TIER_DATABASE, ("prisma",)),  # ORM but binds DB tier
    StackEntry("TypeORM", TIER_DATABASE, ("typeorm",)),
    StackEntry("DynamoDB", TIER_DATABASE, ("dynamodb",)),
    # --- Infra -------------------------------------------------------------
    StackEntry("Docker", TIER_INFRA, ("docker",)),
    StackEntry("Docker Compose", TIER_INFRA, ("docker compose", "docker-compose")),
    StackEntry("Kubernetes", TIER_INFRA, ("kubernetes", "k8s")),
    StackEntry("Terraform", TIER_INFRA, ("terraform",)),
    StackEntry("GitHub Actions", TIER_INFRA, ("github actions", "github-actions")),
    StackEntry("Vercel", TIER_INFRA, ("vercel",)),
    StackEntry("Netlify", TIER_INFRA, ("netlify",)),
    StackEntry("AWS", TIER_INFRA, ("aws ", " aws", "amazon web services")),
    # --- Cache -------------------------------------------------------------
    StackEntry("Redis", TIER_CACHE, ("redis",)),
    StackEntry("Memcached", TIER_CACHE, ("memcached",)),
    # --- Queue -------------------------------------------------------------
    StackEntry("RabbitMQ", TIER_QUEUE, ("rabbitmq",)),
    StackEntry("Kafka", TIER_QUEUE, ("kafka",)),
    StackEntry("SQS", TIER_QUEUE, ("sqs ", "amazon sqs")),
    # --- Testing -----------------------------------------------------------
    StackEntry("Jest", TIER_TESTING, ("jest",)),
    StackEntry("Vitest", TIER_TESTING, ("vitest",)),
    StackEntry("pytest", TIER_TESTING, ("pytest",)),
    StackEntry("Cypress", TIER_TESTING, ("cypress",)),
    StackEntry("Playwright", TIER_TESTING, ("playwright",)),
    # --- Auth --------------------------------------------------------------
    StackEntry("JWT", TIER_AUTH, ("jwt ", "jwt token", " jwt")),
    StackEntry("OAuth", TIER_AUTH, ("oauth", "oauth2", "oauth 2")),
    StackEntry("Auth0", TIER_AUTH, ("auth0",)),
    StackEntry("Clerk", TIER_AUTH, ("clerk",)),
    StackEntry("NextAuth", TIER_AUTH, ("nextauth", "next-auth")),
    # --- Korean tier hints (P0-W) -----------------------------------------
    # 한국어로만 작성된 prompt 도 full-stack 으로 분류될 수 있게 한국어
    # tier hint 를 추가한다. 카테고리 단어 (`프론트`, `백엔드`) 는 특정
    # 도구가 아니라 tier 자체를 가리키므로 canonical 은 "한국어 tier"
    # 라벨로 둠. ``is_full_stack`` 은 tier 단위로 계산하므로 동일 효과.
    StackEntry("한국어 프론트", TIER_FRONTEND, ("프론트엔드", "프론트")),
    StackEntry("한국어 백엔드", TIER_BACKEND, ("백엔드", "백앤드")),
    StackEntry(
        "한국어 데이터베이스",
        TIER_DATABASE,
        ("데이터베이스", "디비 ", " 디비", "디비를", "디비에"),
    ),
    StackEntry("한국어 도커", TIER_INFRA, ("도커",)),
    StackEntry("한국어 쿠버네티스", TIER_INFRA, ("쿠버네티스",)),
    StackEntry(
        "한국어 인증",
        TIER_AUTH,
        ("회원가입", "로그인", "소셜 로그인", "인증/인가", "auth/인증"),
    ),
)


# 명시적 한국어 full-stack 키워드 — 단독으로 등장해도 ``is_full_stack``
# True 가 되도록 ``detect_stacks`` 결과의 explicit 신호로 반영한다.
# tier 한 종만 매칭되더라도 본 키워드가 같이 있으면 full-stack 으로
# 분류해야 mvp / 풀스택 / single repo 요청이 qa-test 로 흘러내리지 않는다.
_EXPLICIT_FULL_STACK_HINTS: Tuple[str, ...] = (
    "풀스택",
    "fullstack",
    "full stack",
    "full-stack",
    "mvp 풀스택",
)


@dataclass(frozen=True)
class StackDetection:
    """Output of :func:`detect_stacks`.

    ``stacks`` is the ordered tuple of canonical names we found.
    ``tiers_present`` is the set of tier identifiers covered.
    ``mentioned_aliases`` is the raw alias substring that matched —
    useful for surfacing back to the user ("Next.js detected via
    'next.js'") or seeding queries.
    ``explicit_full_stack_hint`` is True when the prompt 자체에 "풀스택"
    / "fullstack" 같이 application 전체를 함께 만든다는 의도가 명시되어
    있을 때. tier 가 한 종만 잡혀도 본 hint 와 함께면 full-stack 으로
    분류 (qa-test 같은 약한 fallback 으로 떨어지지 않게).
    """

    stacks: Tuple[str, ...]
    tiers_present: Tuple[str, ...]
    mentioned_aliases: Mapping[str, str]  # canonical → alias-as-matched
    explicit_full_stack_hint: bool = False

    @property
    def has_any(self) -> bool:
        return bool(self.stacks)

    @property
    def is_full_stack(self) -> bool:
        """``True`` if 둘 중 하나라도 만족:

          1. 2 이상의 application tier 가 동시 등장 (기존 동작), OR
          2. 명시적 풀스택 hint (`풀스택` / `fullstack` 등) 가 있고 app
             tier 가 최소 1 개 존재 — 한국어 prompt 가 tier 토큰을 하나만
             담아도 의도가 분명하면 full-stack 으로 분류한다.
        """

        app_tiers = {
            TIER_FRONTEND,
            TIER_BACKEND,
            TIER_DATABASE,
            TIER_CACHE,
            TIER_QUEUE,
            TIER_AUTH,
        }
        present_app_tiers = set(self.tiers_present) & app_tiers
        if len(present_app_tiers) >= 2:
            return True
        if self.explicit_full_stack_hint and len(present_app_tiers) >= 1:
            return True
        return False

    @property
    def is_infra_only(self) -> bool:
        """All matched stacks are infra-tier (deploy / terraform / k8s).
        Used to keep PLATFORM_INFRA classification for genuine infra
        requests like "GitHub Actions만 셋업" or "terraform module만".
        """

        if not self.tiers_present:
            return False
        return all(tier == TIER_INFRA for tier in self.tiers_present)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_stacks(text: str) -> StackDetection:
    """Scan *text* for engineering stack mentions.

    Returns :class:`StackDetection`. Empty input → no detections.
    Match is case-insensitive whole-or-substring; punctuation
    boundaries are honored so "Docker." still matches "docker".
    """

    if not text:
        return StackDetection((), (), {}, False)
    lowered = text.lower()
    found: list[Tuple[str, str, str]] = []  # (canonical, tier, matched alias)
    seen_canonical: set = set()
    for entry in _LEXICON:
        for alias in entry.aliases:
            normalized_alias = alias.lower()
            if normalized_alias in lowered:
                if entry.canonical not in seen_canonical:
                    seen_canonical.add(entry.canonical)
                    found.append((entry.canonical, entry.tier, alias.strip()))
                break
    stacks = tuple(name for name, _, _ in found)
    tiers = tuple(_unique_preserve(tier for _, tier, _ in found))
    mentioned = {name: alias for name, _, alias in found}
    explicit_full_stack = any(
        hint.lower() in lowered for hint in _EXPLICIT_FULL_STACK_HINTS
    )
    return StackDetection(
        stacks=stacks,
        tiers_present=tiers,
        mentioned_aliases=mentioned,
        explicit_full_stack_hint=explicit_full_stack,
    )


def classify_full_stack(text: str) -> bool:
    """Convenience wrapper for the gateway's task-type hint.

    True iff the message mentions ≥2 distinct *application* tiers
    (frontend / backend / database / cache / queue / auth). Pure
    infra mentions never trip this.
    """

    return detect_stacks(text).is_full_stack


def has_write_intent(text: str) -> bool:
    """Heuristic — does the message describe a *write/implement* request?

    Looks for build-verbs (만들 / 구현 / 작성 / build / implement /
    setup / 세팅 / 셋업 / scaffold / spin up) and excludes pure
    review verbs (검토 / 분석 / 리뷰 / 어떻게 생각).
    """

    if not text:
        return False
    lowered = text.lower()
    review_signals = ("검토", "분석", "review", "리뷰", "어떻게 생각", "조사")
    if any(signal in lowered for signal in review_signals):
        return False
    write_signals = (
        "만들",
        "구현",
        "작성",
        "build",
        "implement",
        "scaffold",
        "spin up",
        "set up",
        "setup",
        "셋업",
        "세팅",
        "개발",
        "create",
    )
    return any(signal in lowered for signal in write_signals)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _unique_preserve(seq) -> list:
    seen: set = set()
    out: list = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


__all__ = (
    "StackDetection",
    "StackEntry",
    "TIERS",
    "TIER_AUTH",
    "TIER_BACKEND",
    "TIER_CACHE",
    "TIER_DATABASE",
    "TIER_FRONTEND",
    "TIER_INFRA",
    "TIER_QUEUE",
    "TIER_TESTING",
    "classify_full_stack",
    "detect_stacks",
    "has_write_intent",
)
