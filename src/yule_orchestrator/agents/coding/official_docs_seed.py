"""Official docs seed for detected engineering stacks — P0-J (#145).

When the user's coding request mentions a stack (Next.js / NestJS /
PostgreSQL / Docker Compose / ...), seed the official documentation
URL as a ResearchSource-shaped reference so the gateway can answer
"official_docs 부족" by *itself* instead of asking the user.

The mapping is intentionally narrow — only first-party canonical
docs. If a stack has multiple canonical sources (e.g. Next.js docs
vs App Router docs), we pick the top-level entry. No third-party
tutorials, no Stack Overflow.

This module is **pure / network-free** — it produces seed metadata
(title / url / source_type). The actual web fetch is the collector
loop's responsibility; the seed lets the gateway short-circuit
"NEEDS_USER_INPUT" when stack mentions are present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence, Tuple


@dataclass(frozen=True)
class OfficialDocsSource:
    """Seed entry for the collector / status surface.

    Shape matches the ``ResearchSource`` used elsewhere — title,
    url, domain, snippet, source_type. The caller composes the
    actual ``ResearchSource`` when persisting; this dataclass
    keeps the seed table independent of the heavier import.
    """

    canonical: str  # stack canonical name (matches stack_detector)
    title: str
    url: str
    domain: str
    snippet: str
    source_type: str = "official_docs"


# Mapping by canonical stack name from
# ``agents.coding.stack_detector._LEXICON``. Keep entries narrow —
# top-level entry per stack.
_DOCS: Mapping[str, OfficialDocsSource] = {
    # Frontend
    "Next.js": OfficialDocsSource(
        canonical="Next.js",
        title="Next.js — Official Docs",
        url="https://nextjs.org/docs",
        domain="nextjs.org",
        snippet="The official Next.js documentation — App Router, Pages Router, deployment, data fetching.",
    ),
    "React": OfficialDocsSource(
        canonical="React",
        title="React — Reference",
        url="https://react.dev/reference/react",
        domain="react.dev",
        snippet="Official React reference for hooks, components, and APIs.",
    ),
    "Vue": OfficialDocsSource(
        canonical="Vue",
        title="Vue.js — Guide",
        url="https://vuejs.org/guide/introduction.html",
        domain="vuejs.org",
        snippet="Official Vue 3 guide.",
    ),
    "Svelte": OfficialDocsSource(
        canonical="Svelte",
        title="Svelte — Tutorial / Docs",
        url="https://svelte.dev/docs",
        domain="svelte.dev",
        snippet="Official Svelte / SvelteKit docs.",
    ),
    "Tailwind": OfficialDocsSource(
        canonical="Tailwind",
        title="Tailwind CSS — Docs",
        url="https://tailwindcss.com/docs",
        domain="tailwindcss.com",
        snippet="Official Tailwind CSS installation, configuration, utility reference.",
    ),
    "Vite": OfficialDocsSource(
        canonical="Vite",
        title="Vite — Guide",
        url="https://vitejs.dev/guide/",
        domain="vitejs.dev",
        snippet="Official Vite build tool guide.",
    ),
    "Angular": OfficialDocsSource(
        canonical="Angular",
        title="Angular — Docs",
        url="https://angular.dev/overview",
        domain="angular.dev",
        snippet="Official Angular docs (v17+).",
    ),
    # Backend
    "NestJS": OfficialDocsSource(
        canonical="NestJS",
        title="NestJS — Documentation",
        url="https://docs.nestjs.com",
        domain="docs.nestjs.com",
        snippet="Official NestJS framework documentation — modules, providers, controllers, auth, TypeORM/Prisma.",
    ),
    "Express": OfficialDocsSource(
        canonical="Express",
        title="Express.js — Guide",
        url="https://expressjs.com/en/guide/routing.html",
        domain="expressjs.com",
        snippet="Express.js official guide (routing / middleware / error handling).",
    ),
    "FastAPI": OfficialDocsSource(
        canonical="FastAPI",
        title="FastAPI — Documentation",
        url="https://fastapi.tiangolo.com",
        domain="fastapi.tiangolo.com",
        snippet="Official FastAPI documentation.",
    ),
    "Django": OfficialDocsSource(
        canonical="Django",
        title="Django — Documentation",
        url="https://docs.djangoproject.com/en/stable/",
        domain="docs.djangoproject.com",
        snippet="Official Django documentation.",
    ),
    "Flask": OfficialDocsSource(
        canonical="Flask",
        title="Flask — Documentation",
        url="https://flask.palletsprojects.com",
        domain="flask.palletsprojects.com",
        snippet="Official Flask documentation.",
    ),
    "Spring Boot": OfficialDocsSource(
        canonical="Spring Boot",
        title="Spring Boot — Reference",
        url="https://docs.spring.io/spring-boot/docs/current/reference/html/",
        domain="docs.spring.io",
        snippet="Official Spring Boot reference documentation.",
    ),
    "Rails": OfficialDocsSource(
        canonical="Rails",
        title="Ruby on Rails — Guides",
        url="https://guides.rubyonrails.org",
        domain="guides.rubyonrails.org",
        snippet="Official Ruby on Rails guides.",
    ),
    "Node.js": OfficialDocsSource(
        canonical="Node.js",
        title="Node.js — Docs",
        url="https://nodejs.org/docs/latest/api/",
        domain="nodejs.org",
        snippet="Official Node.js API documentation.",
    ),
    # Database
    "PostgreSQL": OfficialDocsSource(
        canonical="PostgreSQL",
        title="PostgreSQL — Documentation",
        url="https://www.postgresql.org/docs/current/",
        domain="postgresql.org",
        snippet="Official PostgreSQL documentation — SQL syntax, indexing, replication.",
    ),
    "MySQL": OfficialDocsSource(
        canonical="MySQL",
        title="MySQL — Reference Manual",
        url="https://dev.mysql.com/doc/refman/8.0/en/",
        domain="dev.mysql.com",
        snippet="Official MySQL reference manual.",
    ),
    "MongoDB": OfficialDocsSource(
        canonical="MongoDB",
        title="MongoDB — Manual",
        url="https://www.mongodb.com/docs/manual/",
        domain="mongodb.com",
        snippet="Official MongoDB manual.",
    ),
    "SQLite": OfficialDocsSource(
        canonical="SQLite",
        title="SQLite — Documentation",
        url="https://sqlite.org/docs.html",
        domain="sqlite.org",
        snippet="Official SQLite documentation.",
    ),
    "Prisma": OfficialDocsSource(
        canonical="Prisma",
        title="Prisma — Documentation",
        url="https://www.prisma.io/docs",
        domain="prisma.io",
        snippet="Official Prisma ORM documentation.",
    ),
    "TypeORM": OfficialDocsSource(
        canonical="TypeORM",
        title="TypeORM — Documentation",
        url="https://typeorm.io",
        domain="typeorm.io",
        snippet="Official TypeORM documentation.",
    ),
    # Infra
    "Docker": OfficialDocsSource(
        canonical="Docker",
        title="Docker — Documentation",
        url="https://docs.docker.com",
        domain="docs.docker.com",
        snippet="Official Docker documentation.",
    ),
    "Docker Compose": OfficialDocsSource(
        canonical="Docker Compose",
        title="Docker Compose — Overview",
        url="https://docs.docker.com/compose/",
        domain="docs.docker.com",
        snippet="Official Docker Compose docs — multi-container apps, services, networks, volumes.",
    ),
    "Kubernetes": OfficialDocsSource(
        canonical="Kubernetes",
        title="Kubernetes — Documentation",
        url="https://kubernetes.io/docs/home/",
        domain="kubernetes.io",
        snippet="Official Kubernetes documentation.",
    ),
    "Terraform": OfficialDocsSource(
        canonical="Terraform",
        title="Terraform — Documentation",
        url="https://developer.hashicorp.com/terraform/docs",
        domain="developer.hashicorp.com",
        snippet="Official Terraform documentation.",
    ),
    "GitHub Actions": OfficialDocsSource(
        canonical="GitHub Actions",
        title="GitHub Actions — Documentation",
        url="https://docs.github.com/en/actions",
        domain="docs.github.com",
        snippet="Official GitHub Actions documentation.",
    ),
    "Vercel": OfficialDocsSource(
        canonical="Vercel",
        title="Vercel — Documentation",
        url="https://vercel.com/docs",
        domain="vercel.com",
        snippet="Official Vercel platform docs.",
    ),
    # Cache / Queue / Testing
    "Redis": OfficialDocsSource(
        canonical="Redis",
        title="Redis — Documentation",
        url="https://redis.io/docs/",
        domain="redis.io",
        snippet="Official Redis docs — data structures, persistence, replication.",
    ),
    "RabbitMQ": OfficialDocsSource(
        canonical="RabbitMQ",
        title="RabbitMQ — Documentation",
        url="https://www.rabbitmq.com/documentation.html",
        domain="rabbitmq.com",
        snippet="Official RabbitMQ documentation.",
    ),
    "Kafka": OfficialDocsSource(
        canonical="Kafka",
        title="Apache Kafka — Documentation",
        url="https://kafka.apache.org/documentation/",
        domain="kafka.apache.org",
        snippet="Official Apache Kafka documentation.",
    ),
    "Jest": OfficialDocsSource(
        canonical="Jest",
        title="Jest — Documentation",
        url="https://jestjs.io/docs/getting-started",
        domain="jestjs.io",
        snippet="Official Jest testing framework docs.",
    ),
    "Vitest": OfficialDocsSource(
        canonical="Vitest",
        title="Vitest — Guide",
        url="https://vitest.dev/guide/",
        domain="vitest.dev",
        snippet="Official Vitest guide.",
    ),
    "pytest": OfficialDocsSource(
        canonical="pytest",
        title="pytest — Documentation",
        url="https://docs.pytest.org/en/stable/",
        domain="docs.pytest.org",
        snippet="Official pytest documentation.",
    ),
    "Cypress": OfficialDocsSource(
        canonical="Cypress",
        title="Cypress — Documentation",
        url="https://docs.cypress.io",
        domain="docs.cypress.io",
        snippet="Official Cypress E2E testing docs.",
    ),
    "Playwright": OfficialDocsSource(
        canonical="Playwright",
        title="Playwright — Documentation",
        url="https://playwright.dev/docs/intro",
        domain="playwright.dev",
        snippet="Official Playwright docs.",
    ),
    # Auth
    "JWT": OfficialDocsSource(
        canonical="JWT",
        title="JWT — Introduction (jwt.io)",
        url="https://jwt.io/introduction",
        domain="jwt.io",
        snippet="JSON Web Tokens introduction.",
    ),
    "OAuth": OfficialDocsSource(
        canonical="OAuth",
        title="OAuth 2.0 — RFC 6749",
        url="https://datatracker.ietf.org/doc/html/rfc6749",
        domain="datatracker.ietf.org",
        snippet="The OAuth 2.0 Authorization Framework (RFC 6749).",
    ),
    "Auth0": OfficialDocsSource(
        canonical="Auth0",
        title="Auth0 — Documentation",
        url="https://auth0.com/docs",
        domain="auth0.com",
        snippet="Official Auth0 documentation.",
    ),
    "NextAuth": OfficialDocsSource(
        canonical="NextAuth",
        title="NextAuth.js — Documentation",
        url="https://next-auth.js.org/getting-started/introduction",
        domain="next-auth.js.org",
        snippet="Official NextAuth.js documentation.",
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def seed_official_docs(
    detected_stacks: Sequence[str],
) -> Tuple[OfficialDocsSource, ...]:
    """Return docs entries for *detected_stacks*.

    *detected_stacks* matches canonical names from
    :func:`stack_detector.detect_stacks`. Unknown canonicals are
    silently skipped (no fake docs). Order preserved.
    """

    if not detected_stacks:
        return ()
    out: list[OfficialDocsSource] = []
    seen: set = set()
    for canonical in detected_stacks:
        if canonical in seen:
            continue
        entry = _DOCS.get(canonical)
        if entry is None:
            continue
        seen.add(canonical)
        out.append(entry)
    return tuple(out)


def known_canonicals() -> Tuple[str, ...]:
    """For tests / debugging — list of stacks with an official docs entry."""

    return tuple(_DOCS.keys())


__all__ = (
    "OfficialDocsSource",
    "known_canonicals",
    "seed_official_docs",
)
