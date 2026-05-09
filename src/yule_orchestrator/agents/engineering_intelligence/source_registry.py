"""Per-role source registry seed + common-core merge.

Goal: the engineering-agent should know **a few** authoritative sources
per role from day one without us hardcoding hundreds of feeds. The
registry is intentionally seed-shaped — extend by adding a row, not by
rewriting the catalogue.

Layout:

  * :data:`COMMON_CORE_SOURCES` — sources every role benefits from
    (e.g. NIST CVE feed, MDN web platform docs cross-reference). Merged
    into every role's registry on demand.
  * :data:`_ROLE_SOURCES` — per-role tuples. Each role has at least 5
    entries seeded, with at least 1 standard / 1 docs / 1 changelog
    or release-notes / 1 engineering-blog where reasonable.
  * :func:`role_sources(role_id)` — returns the merged tuple
    (per-role + common-core, deduped on ``source_id``).
  * :func:`auto_collectable_sources(role_id)` — filters to entries
    where ``auto_collect=True`` and ``review_required=False``. Used
    by the collector as the default ingestion list.
  * :func:`prioritise_sources(...)` — orders by tier (Tier 1 first),
    then trust * freshness, so "official > community" is automatic.

Strictly offline. No URL is fetched here — base_url strings are kept
as the operator-facing identifier, the collector's adapter layer is
responsible for actual transport.
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple

from .models import (
    CollectionMode,
    SourceAxis,
    SourceEntry,
    SourceKind,
    SourceTier,
)


# ---------------------------------------------------------------------------
# Common core — applies to every role
# ---------------------------------------------------------------------------


COMMON_CORE_SOURCES: Tuple[SourceEntry, ...] = (
    SourceEntry(
        source_id="cve-nvd",
        name="NIST NVD Vulnerability Feed",
        base_url="https://nvd.nist.gov/feeds/json/cve/2.0",
        role_tags=(
            "tech-lead",
            "backend-engineer",
            "frontend-engineer",
            "devops-engineer",
            "qa-engineer",
            "ai-engineer",
            "product-designer",
        ),
        stack_tags=("security", "cve"),
        source_kind=SourceKind.SECURITY_ADVISORY,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.9,
        auto_collect=True,
        review_required=False,
        axes=(SourceAxis.SECURITY,),
    ),
    SourceEntry(
        source_id="mdn-web-platform",
        name="MDN Web Platform — Recent Changes",
        base_url="https://developer.mozilla.org/en-US/blog/rss.xml",
        role_tags=(
            "frontend-engineer",
            "qa-engineer",
            "tech-lead",
            "product-designer",
        ),
        stack_tags=("web-platform", "browser"),
        source_kind=SourceKind.DOCS,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_1,
        trust_weight=0.9,
        freshness_weight=0.85,
        auto_collect=True,
        axes=(SourceAxis.WEB_PLATFORM_FRAMEWORK, SourceAxis.OFFICIAL_DOCS),
    ),
    SourceEntry(
        source_id="github-engineering",
        name="GitHub Engineering Blog",
        base_url="https://github.blog/category/engineering/",
        role_tags=(
            "tech-lead",
            "backend-engineer",
            "devops-engineer",
            "ai-engineer",
        ),
        stack_tags=("github", "engineering"),
        source_kind=SourceKind.ENGINEERING_BLOG,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.8,
        freshness_weight=0.8,
        auto_collect=True,
        axes=(
            SourceAxis.ARCHITECTURE_ADR_TRADEOFF,
            SourceAxis.CI_CD_INFRA_OBSERVABILITY,
        ),
    ),
)


# ---------------------------------------------------------------------------
# Role-specific seeds
# ---------------------------------------------------------------------------
#
# Keep each role list small and high-signal (~5–7 entries). Operators
# can extend at runtime by passing additional registries to
# :func:`merge_registries`; we don't want to chase the long tail in
# code review.


_TECH_LEAD_SOURCES: Tuple[SourceEntry, ...] = (
    SourceEntry(
        source_id="iso-iec-25010",
        name="ISO/IEC 25010 — Systems and software quality models",
        base_url="https://www.iso.org/standard/35733.html",
        role_tags=("tech-lead",),
        stack_tags=("standards", "quality-models"),
        source_kind=SourceKind.STANDARD,
        collection_mode=CollectionMode.MANUAL,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.3,
        auto_collect=False,
        review_required=True,
        axes=(SourceAxis.ARCHITECTURE_ADR_TRADEOFF,),
    ),
    SourceEntry(
        source_id="adr-github",
        name="Architecture Decision Records (joelparkerhenderson/architecture-decision-record)",
        base_url="https://github.com/joelparkerhenderson/architecture-decision-record",
        role_tags=("tech-lead",),
        stack_tags=("adr", "rfc", "design-doc"),
        source_kind=SourceKind.REPO,
        collection_mode=CollectionMode.GITHUB_API,
        tier=SourceTier.TIER_3,
        trust_weight=0.7,
        freshness_weight=0.5,
        auto_collect=False,
        review_required=True,
        axes=(SourceAxis.ARCHITECTURE_ADR_TRADEOFF,),
    ),
    SourceEntry(
        source_id="cloudflare-engineering",
        name="Cloudflare Engineering Blog",
        base_url="https://blog.cloudflare.com/tag/engineering/rss/",
        role_tags=("tech-lead", "backend-engineer", "devops-engineer"),
        stack_tags=("cloudflare", "system-design"),
        source_kind=SourceKind.ENGINEERING_BLOG,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.85,
        freshness_weight=0.85,
        axes=(
            SourceAxis.ARCHITECTURE_ADR_TRADEOFF,
            SourceAxis.CI_CD_INFRA_OBSERVABILITY,
        ),
    ),
    SourceEntry(
        source_id="stripe-engineering",
        name="Stripe Engineering Blog",
        base_url="https://stripe.com/blog/engineering",
        role_tags=("tech-lead", "backend-engineer"),
        stack_tags=("stripe", "system-design", "api"),
        source_kind=SourceKind.ENGINEERING_BLOG,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.85,
        freshness_weight=0.8,
        axes=(
            SourceAxis.ARCHITECTURE_ADR_TRADEOFF,
            SourceAxis.API_SCHEMA_AUTH,
        ),
    ),
    SourceEntry(
        source_id="ietf-rfc-editor",
        name="IETF RFC Editor — Recent RFCs",
        base_url="https://www.rfc-editor.org/rfcrss.xml",
        role_tags=("tech-lead", "backend-engineer", "devops-engineer"),
        stack_tags=("rfc", "standards"),
        source_kind=SourceKind.STANDARD,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.7,
        axes=(
            SourceAxis.ARCHITECTURE_ADR_TRADEOFF,
            SourceAxis.OFFICIAL_DOCS,
        ),
    ),
)


_BACKEND_SOURCES: Tuple[SourceEntry, ...] = (
    SourceEntry(
        source_id="spring-framework-docs",
        name="Spring Framework Documentation",
        base_url="https://docs.spring.io/spring-framework/reference/index.html",
        role_tags=("backend-engineer",),
        stack_tags=("java", "spring", "spring-boot"),
        source_kind=SourceKind.DOCS,
        collection_mode=CollectionMode.SITEMAP,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.7,
        axes=(SourceAxis.OFFICIAL_DOCS, SourceAxis.API_SCHEMA_AUTH),
    ),
    SourceEntry(
        source_id="spring-blog",
        name="Spring Engineering Blog",
        base_url="https://spring.io/blog.atom",
        role_tags=("backend-engineer",),
        stack_tags=("java", "spring"),
        source_kind=SourceKind.ENGINEERING_BLOG,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.9,
        freshness_weight=0.85,
        axes=(SourceAxis.OFFICIAL_DOCS, SourceAxis.RELEASE_NOTES_CHANGELOG),
    ),
    SourceEntry(
        source_id="fastapi-changelog",
        name="FastAPI Release Notes",
        base_url="https://fastapi.tiangolo.com/release-notes/",
        role_tags=("backend-engineer",),
        stack_tags=("python", "fastapi"),
        source_kind=SourceKind.CHANGELOG,
        collection_mode=CollectionMode.HTML_LIST,
        tier=SourceTier.TIER_2,
        trust_weight=0.9,
        freshness_weight=0.85,
        axes=(
            SourceAxis.RELEASE_NOTES_CHANGELOG,
            SourceAxis.API_SCHEMA_AUTH,
        ),
    ),
    SourceEntry(
        source_id="postgresql-release-notes",
        name="PostgreSQL Release Notes",
        base_url="https://www.postgresql.org/docs/release/",
        role_tags=("backend-engineer", "devops-engineer"),
        stack_tags=("postgresql", "database"),
        source_kind=SourceKind.RELEASE_NOTES,
        collection_mode=CollectionMode.HTML_LIST,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.8,
        axes=(
            SourceAxis.RELEASE_NOTES_CHANGELOG,
            SourceAxis.API_SCHEMA_AUTH,
        ),
    ),
    SourceEntry(
        source_id="redis-release-notes",
        name="Redis Release Notes",
        base_url="https://github.com/redis/redis/releases.atom",
        role_tags=("backend-engineer",),
        stack_tags=("redis", "cache"),
        source_kind=SourceKind.RELEASE_NOTES,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.9,
        freshness_weight=0.85,
        axes=(SourceAxis.RELEASE_NOTES_CHANGELOG,),
    ),
    SourceEntry(
        source_id="owasp-top-10",
        name="OWASP Top 10",
        base_url="https://owasp.org/www-project-top-ten/",
        role_tags=("backend-engineer", "qa-engineer", "tech-lead"),
        stack_tags=("security", "owasp"),
        source_kind=SourceKind.STANDARD,
        collection_mode=CollectionMode.MANUAL,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.4,
        auto_collect=False,
        review_required=True,
        axes=(SourceAxis.SECURITY, SourceAxis.API_SCHEMA_AUTH),
    ),
    SourceEntry(
        source_id="nestjs-blog",
        name="NestJS Blog / Release Notes",
        base_url="https://docs.nestjs.com/migration-guide",
        role_tags=("backend-engineer",),
        stack_tags=("node", "nestjs"),
        source_kind=SourceKind.DOCS,
        collection_mode=CollectionMode.HTML_LIST,
        tier=SourceTier.TIER_2,
        trust_weight=0.85,
        freshness_weight=0.8,
        axes=(SourceAxis.OFFICIAL_DOCS, SourceAxis.API_SCHEMA_AUTH),
    ),
)


_FRONTEND_SOURCES: Tuple[SourceEntry, ...] = (
    SourceEntry(
        source_id="react-blog",
        name="React Blog",
        base_url="https://react.dev/blog",
        role_tags=("frontend-engineer",),
        stack_tags=("react",),
        source_kind=SourceKind.ENGINEERING_BLOG,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.85,
        axes=(SourceAxis.OFFICIAL_DOCS, SourceAxis.WEB_PLATFORM_FRAMEWORK),
    ),
    SourceEntry(
        source_id="nextjs-changelog",
        name="Next.js Releases",
        base_url="https://github.com/vercel/next.js/releases.atom",
        role_tags=("frontend-engineer",),
        stack_tags=("nextjs",),
        source_kind=SourceKind.RELEASE_NOTES,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.9,
        freshness_weight=0.9,
        axes=(
            SourceAxis.RELEASE_NOTES_CHANGELOG,
            SourceAxis.WEB_PLATFORM_FRAMEWORK,
        ),
    ),
    SourceEntry(
        source_id="typescript-changelog",
        name="TypeScript What's New",
        base_url="https://devblogs.microsoft.com/typescript/category/typescript/feed/",
        role_tags=("frontend-engineer", "backend-engineer"),
        stack_tags=("typescript",),
        source_kind=SourceKind.CHANGELOG,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.9,
        freshness_weight=0.85,
        axes=(
            SourceAxis.RELEASE_NOTES_CHANGELOG,
            SourceAxis.WEB_PLATFORM_FRAMEWORK,
        ),
    ),
    SourceEntry(
        source_id="web-dev-articles",
        name="web.dev Articles",
        base_url="https://web.dev/feed.xml",
        role_tags=("frontend-engineer",),
        stack_tags=("performance", "accessibility"),
        source_kind=SourceKind.DOCS,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.85,
        freshness_weight=0.85,
        axes=(SourceAxis.WEB_PLATFORM_FRAMEWORK, SourceAxis.OFFICIAL_DOCS),
    ),
    SourceEntry(
        source_id="wcag-spec",
        name="W3C WCAG 2 Specification",
        base_url="https://www.w3.org/TR/WCAG22/",
        role_tags=("frontend-engineer", "qa-engineer", "product-designer"),
        stack_tags=("accessibility", "wcag"),
        source_kind=SourceKind.STANDARD,
        collection_mode=CollectionMode.MANUAL,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.3,
        auto_collect=False,
        review_required=True,
        axes=(SourceAxis.WEB_PLATFORM_FRAMEWORK, SourceAxis.OFFICIAL_DOCS),
    ),
)


_DEVOPS_SOURCES: Tuple[SourceEntry, ...] = (
    SourceEntry(
        source_id="docker-release-notes",
        name="Docker Engine Release Notes",
        base_url="https://docs.docker.com/engine/release-notes/",
        role_tags=("devops-engineer",),
        stack_tags=("docker", "container"),
        source_kind=SourceKind.RELEASE_NOTES,
        collection_mode=CollectionMode.HTML_LIST,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.85,
        axes=(
            SourceAxis.CI_CD_INFRA_OBSERVABILITY,
            SourceAxis.RELEASE_NOTES_CHANGELOG,
        ),
    ),
    SourceEntry(
        source_id="kubernetes-release-notes",
        name="Kubernetes Release Notes",
        base_url="https://kubernetes.io/feed.xml",
        role_tags=("devops-engineer",),
        stack_tags=("kubernetes", "k8s"),
        source_kind=SourceKind.RELEASE_NOTES,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.9,
        axes=(
            SourceAxis.CI_CD_INFRA_OBSERVABILITY,
            SourceAxis.RELEASE_NOTES_CHANGELOG,
        ),
    ),
    SourceEntry(
        source_id="argo-cd-changelog",
        name="Argo CD CHANGELOG",
        base_url="https://github.com/argoproj/argo-cd/releases.atom",
        role_tags=("devops-engineer",),
        stack_tags=("argo-cd", "gitops"),
        source_kind=SourceKind.RELEASE_NOTES,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.9,
        freshness_weight=0.85,
        axes=(
            SourceAxis.CI_CD_INFRA_OBSERVABILITY,
            SourceAxis.RELEASE_NOTES_CHANGELOG,
        ),
    ),
    SourceEntry(
        source_id="github-actions-changelog",
        name="GitHub Actions Changelog",
        base_url="https://github.blog/changelog/label/actions/feed/",
        role_tags=("devops-engineer",),
        stack_tags=("github-actions", "ci"),
        source_kind=SourceKind.CHANGELOG,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.9,
        freshness_weight=0.9,
        axes=(
            SourceAxis.CI_CD_INFRA_OBSERVABILITY,
            SourceAxis.RELEASE_NOTES_CHANGELOG,
        ),
    ),
    SourceEntry(
        source_id="terraform-changelog",
        name="Terraform Changelog",
        base_url="https://github.com/hashicorp/terraform/releases.atom",
        role_tags=("devops-engineer",),
        stack_tags=("terraform", "iac"),
        source_kind=SourceKind.RELEASE_NOTES,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.9,
        freshness_weight=0.85,
        axes=(
            SourceAxis.CI_CD_INFRA_OBSERVABILITY,
            SourceAxis.RELEASE_NOTES_CHANGELOG,
        ),
    ),
)


_QA_SOURCES: Tuple[SourceEntry, ...] = (
    SourceEntry(
        source_id="playwright-release-notes",
        name="Playwright Release Notes",
        base_url="https://github.com/microsoft/playwright/releases.atom",
        role_tags=("qa-engineer", "frontend-engineer"),
        stack_tags=("playwright", "e2e"),
        source_kind=SourceKind.RELEASE_NOTES,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.9,
        freshness_weight=0.9,
        axes=(
            SourceAxis.REGRESSION_TEST_PLAN,
            SourceAxis.RELEASE_NOTES_CHANGELOG,
        ),
    ),
    SourceEntry(
        source_id="testing-library-blog",
        name="Testing Library Blog",
        base_url="https://github.com/testing-library/testing-library-docs/releases.atom",
        role_tags=("qa-engineer", "frontend-engineer"),
        stack_tags=("testing-library",),
        source_kind=SourceKind.CHANGELOG,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.85,
        freshness_weight=0.8,
        axes=(
            SourceAxis.REGRESSION_TEST_PLAN,
            SourceAxis.RELEASE_NOTES_CHANGELOG,
        ),
    ),
    SourceEntry(
        source_id="cypress-changelog",
        name="Cypress Changelog",
        base_url="https://docs.cypress.io/guides/references/changelog",
        role_tags=("qa-engineer",),
        stack_tags=("cypress",),
        source_kind=SourceKind.CHANGELOG,
        collection_mode=CollectionMode.HTML_LIST,
        tier=SourceTier.TIER_2,
        trust_weight=0.85,
        freshness_weight=0.85,
        axes=(
            SourceAxis.REGRESSION_TEST_PLAN,
            SourceAxis.RELEASE_NOTES_CHANGELOG,
        ),
    ),
    SourceEntry(
        source_id="iso-29119",
        name="ISO/IEC/IEEE 29119 — Software Testing",
        base_url="https://www.iso.org/standard/79430.html",
        role_tags=("qa-engineer", "tech-lead"),
        stack_tags=("standards", "test-strategy"),
        source_kind=SourceKind.STANDARD,
        collection_mode=CollectionMode.MANUAL,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.3,
        auto_collect=False,
        review_required=True,
        axes=(SourceAxis.REGRESSION_TEST_PLAN, SourceAxis.OFFICIAL_DOCS),
    ),
    SourceEntry(
        source_id="google-testing-blog",
        name="Google Testing Blog",
        base_url="https://testing.googleblog.com/feeds/posts/default",
        role_tags=("qa-engineer", "tech-lead"),
        stack_tags=("test-strategy",),
        source_kind=SourceKind.ENGINEERING_BLOG,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.85,
        freshness_weight=0.7,
        axes=(SourceAxis.REGRESSION_TEST_PLAN,),
    ),
)


_AI_SOURCES: Tuple[SourceEntry, ...] = (
    SourceEntry(
        source_id="openai-news",
        name="OpenAI News & Research",
        base_url="https://openai.com/news/rss.xml",
        role_tags=("ai-engineer",),
        stack_tags=("openai", "llm"),
        source_kind=SourceKind.ENGINEERING_BLOG,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.9,
        freshness_weight=0.95,
        axes=(SourceAxis.AI_FRAMEWORK, SourceAxis.OFFICIAL_DOCS),
    ),
    SourceEntry(
        source_id="anthropic-news",
        name="Anthropic News & Research",
        base_url="https://www.anthropic.com/news",
        role_tags=("ai-engineer",),
        stack_tags=("anthropic", "claude", "llm"),
        source_kind=SourceKind.ENGINEERING_BLOG,
        collection_mode=CollectionMode.HTML_LIST,
        tier=SourceTier.TIER_2,
        trust_weight=0.9,
        freshness_weight=0.95,
        axes=(SourceAxis.AI_FRAMEWORK, SourceAxis.OFFICIAL_DOCS),
    ),
    SourceEntry(
        source_id="huggingface-blog",
        name="Hugging Face Blog",
        base_url="https://huggingface.co/blog/feed.xml",
        role_tags=("ai-engineer",),
        stack_tags=("huggingface", "model-serving"),
        source_kind=SourceKind.ENGINEERING_BLOG,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.85,
        freshness_weight=0.9,
        axes=(SourceAxis.AI_FRAMEWORK,),
    ),
    SourceEntry(
        source_id="langchain-blog",
        name="LangChain Blog",
        base_url="https://blog.langchain.dev/rss/",
        role_tags=("ai-engineer",),
        stack_tags=("langchain", "rag"),
        source_kind=SourceKind.ENGINEERING_BLOG,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_3,
        trust_weight=0.7,
        freshness_weight=0.85,
        review_required=True,
        axes=(SourceAxis.AI_FRAMEWORK,),
    ),
    SourceEntry(
        source_id="pgvector-releases",
        name="pgvector Releases",
        base_url="https://github.com/pgvector/pgvector/releases.atom",
        role_tags=("ai-engineer", "backend-engineer"),
        stack_tags=("pgvector", "vector-db"),
        source_kind=SourceKind.RELEASE_NOTES,
        collection_mode=CollectionMode.RSS,
        tier=SourceTier.TIER_2,
        trust_weight=0.9,
        freshness_weight=0.85,
        axes=(
            SourceAxis.AI_FRAMEWORK,
            SourceAxis.RELEASE_NOTES_CHANGELOG,
        ),
    ),
    SourceEntry(
        source_id="ragas-eval-docs",
        name="Ragas Evaluation Docs",
        base_url="https://docs.ragas.io/",
        role_tags=("ai-engineer", "qa-engineer"),
        stack_tags=("rag-eval", "evaluation"),
        source_kind=SourceKind.DOCS,
        collection_mode=CollectionMode.SITEMAP,
        tier=SourceTier.TIER_2,
        trust_weight=0.8,
        freshness_weight=0.8,
        axes=(
            SourceAxis.AI_FRAMEWORK,
            SourceAxis.REGRESSION_TEST_PLAN,
        ),
    ),
)


_PRODUCT_DESIGN_SOURCES: Tuple[SourceEntry, ...] = (
    SourceEntry(
        source_id="apple-hig",
        name="Apple Human Interface Guidelines",
        base_url="https://developer.apple.com/design/human-interface-guidelines/",
        role_tags=("product-designer", "frontend-engineer"),
        stack_tags=("apple-hig", "ios", "macos"),
        source_kind=SourceKind.DESIGN_SYSTEM,
        collection_mode=CollectionMode.SITEMAP,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.6,
        axes=(SourceAxis.DESIGN_SYSTEM, SourceAxis.OFFICIAL_DOCS),
    ),
    SourceEntry(
        source_id="material-design",
        name="Material Design",
        base_url="https://m3.material.io/",
        role_tags=("product-designer", "frontend-engineer"),
        stack_tags=("material-design", "android"),
        source_kind=SourceKind.DESIGN_SYSTEM,
        collection_mode=CollectionMode.SITEMAP,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.6,
        axes=(SourceAxis.DESIGN_SYSTEM, SourceAxis.OFFICIAL_DOCS),
    ),
    SourceEntry(
        source_id="fluent-design",
        name="Microsoft Fluent Design",
        base_url="https://fluent2.microsoft.design/",
        role_tags=("product-designer", "frontend-engineer"),
        stack_tags=("fluent",),
        source_kind=SourceKind.DESIGN_SYSTEM,
        collection_mode=CollectionMode.SITEMAP,
        tier=SourceTier.TIER_1,
        trust_weight=0.9,
        freshness_weight=0.6,
        axes=(SourceAxis.DESIGN_SYSTEM, SourceAxis.OFFICIAL_DOCS),
    ),
    SourceEntry(
        source_id="atlassian-design",
        name="Atlassian Design System",
        base_url="https://atlassian.design/",
        role_tags=("product-designer", "frontend-engineer"),
        stack_tags=("atlassian",),
        source_kind=SourceKind.DESIGN_SYSTEM,
        collection_mode=CollectionMode.SITEMAP,
        tier=SourceTier.TIER_2,
        trust_weight=0.85,
        freshness_weight=0.7,
        axes=(SourceAxis.DESIGN_SYSTEM,),
    ),
    SourceEntry(
        source_id="carbon-design",
        name="IBM Carbon Design System",
        base_url="https://carbondesignsystem.com/",
        role_tags=("product-designer", "frontend-engineer"),
        stack_tags=("carbon", "ibm"),
        source_kind=SourceKind.DESIGN_SYSTEM,
        collection_mode=CollectionMode.SITEMAP,
        tier=SourceTier.TIER_2,
        trust_weight=0.85,
        freshness_weight=0.7,
        axes=(SourceAxis.DESIGN_SYSTEM,),
    ),
    SourceEntry(
        source_id="govuk-design",
        name="GOV.UK Design Patterns",
        base_url="https://design-system.service.gov.uk/patterns/",
        role_tags=("product-designer",),
        stack_tags=("gov-uk", "patterns"),
        source_kind=SourceKind.DESIGN_SYSTEM,
        collection_mode=CollectionMode.SITEMAP,
        tier=SourceTier.TIER_1,
        trust_weight=0.95,
        freshness_weight=0.7,
        axes=(SourceAxis.DESIGN_SYSTEM, SourceAxis.OFFICIAL_DOCS),
    ),
)


_ROLE_SOURCES: Mapping[str, Tuple[SourceEntry, ...]] = {
    "tech-lead": _TECH_LEAD_SOURCES,
    "backend-engineer": _BACKEND_SOURCES,
    "frontend-engineer": _FRONTEND_SOURCES,
    "devops-engineer": _DEVOPS_SOURCES,
    "qa-engineer": _QA_SOURCES,
    "ai-engineer": _AI_SOURCES,
    "product-designer": _PRODUCT_DESIGN_SOURCES,
}


SUPPORTED_ROLES: Tuple[str, ...] = (
    "tech-lead",
    "backend-engineer",
    "frontend-engineer",
    "devops-engineer",
    "qa-engineer",
    "ai-engineer",
    "product-designer",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def role_sources(role_id: str) -> Tuple[SourceEntry, ...]:
    """Return the merged source tuple for *role_id*.

    Per-role seed first, then any common-core entry whose
    ``role_tags`` covers the role. Dedup on ``source_id``. Raises
    ``KeyError`` for unknown roles so callers fail loudly.
    """

    if role_id not in _ROLE_SOURCES:
        raise KeyError(f"unknown role for source registry: {role_id!r}")
    own = _ROLE_SOURCES[role_id]
    merged: list[SourceEntry] = list(own)
    seen = {entry.source_id for entry in merged}
    for entry in COMMON_CORE_SOURCES:
        if role_id in entry.role_tags and entry.source_id not in seen:
            merged.append(entry)
            seen.add(entry.source_id)
    return tuple(merged)


def auto_collectable_sources(role_id: str) -> Tuple[SourceEntry, ...]:
    """Filter to entries the collector can scrape without review.

    Excludes ``auto_collect=False`` and ``review_required=True``
    rows. The collector can still surface those — the operator just
    has to opt in via the registry override.
    """

    return tuple(
        entry
        for entry in role_sources(role_id)
        if entry.auto_collect and not entry.review_required
    )


def find_source(role_id: str, source_id: str) -> Optional[SourceEntry]:
    for entry in role_sources(role_id):
        if entry.source_id == source_id:
            return entry
    return None


def prioritise_sources(
    sources: Tuple[SourceEntry, ...]
) -> Tuple[SourceEntry, ...]:
    """Sort by ``(tier, -trust*freshness)`` so Tier 1 official wins.

    Ties are broken by source_id alphabetical for determinism — handy
    for tests + audit replay.
    """

    tier_order = {
        SourceTier.TIER_1: 0,
        SourceTier.TIER_2: 1,
        SourceTier.TIER_3: 2,
        SourceTier.TIER_4: 3,
    }
    return tuple(
        sorted(
            sources,
            key=lambda entry: (
                tier_order.get(entry.tier, 99),
                -(entry.trust_weight * entry.freshness_weight),
                entry.source_id,
            ),
        )
    )


def daily_limit_for_role(role_id: str) -> int:
    """Default daily collection limit per role.

    Spec: 5 items per role per day. Kept as a function so a future
    operator-facing override hooks one place, not five.
    """

    if role_id not in _ROLE_SOURCES:
        raise KeyError(f"unknown role for source registry: {role_id!r}")
    return 5


# ---------------------------------------------------------------------------
# Axis-aware helpers
# ---------------------------------------------------------------------------


def axes_for_role(role_id: str) -> Tuple[SourceAxis, ...]:
    """Union of axes covered by *role_id*'s registry (sorted, deduped)."""

    seen: set[SourceAxis] = set()
    for entry in role_sources(role_id):
        for axis in entry.axes:
            seen.add(axis)
    return tuple(sorted(seen, key=lambda a: a.value))


def sources_for_axis(
    role_id: str, axis: SourceAxis
) -> Tuple[SourceEntry, ...]:
    """Entries belonging to *role_id* that mention *axis*.

    Used by retrieval-side filtering when the request hints a specific
    axis (e.g. backend-feature task → API_SCHEMA_AUTH preference).
    Returns the prioritised order so Tier 1 sits first.
    """

    matching = tuple(
        entry for entry in role_sources(role_id) if axis in entry.axes
    )
    return prioritise_sources(matching)


def role_axis_coverage_report(role_id: str) -> Mapping[SourceAxis, int]:
    """Per-axis source count for *role_id* — used by audit + tests.

    The map is total-coverage focused: it counts every entry whether
    auto-collectable or review-required. Operators look at this to
    spot a bare axis (count == 0) before adding new seeds.
    """

    counts: dict[SourceAxis, int] = {}
    for entry in role_sources(role_id):
        for axis in entry.axes:
            counts[axis] = counts.get(axis, 0) + 1
    return counts


# Per-role minimum axes contract. A role must cover at least these axes
# for the registry to be considered "operationally seeded". Tests pin
# this so that adding a role without seeding the right kinds of sources
# fails loudly instead of silently producing skinny digests.
_ROLE_REQUIRED_AXES: Mapping[str, Tuple[SourceAxis, ...]] = {
    "tech-lead": (
        SourceAxis.ARCHITECTURE_ADR_TRADEOFF,
        SourceAxis.OFFICIAL_DOCS,
    ),
    "backend-engineer": (
        SourceAxis.OFFICIAL_DOCS,
        SourceAxis.API_SCHEMA_AUTH,
        SourceAxis.RELEASE_NOTES_CHANGELOG,
        SourceAxis.SECURITY,
    ),
    "frontend-engineer": (
        SourceAxis.WEB_PLATFORM_FRAMEWORK,
        SourceAxis.OFFICIAL_DOCS,
        SourceAxis.RELEASE_NOTES_CHANGELOG,
    ),
    "devops-engineer": (
        SourceAxis.CI_CD_INFRA_OBSERVABILITY,
        SourceAxis.RELEASE_NOTES_CHANGELOG,
        SourceAxis.SECURITY,
    ),
    "qa-engineer": (
        SourceAxis.REGRESSION_TEST_PLAN,
        SourceAxis.SECURITY,
    ),
    "ai-engineer": (SourceAxis.AI_FRAMEWORK,),
    "product-designer": (SourceAxis.DESIGN_SYSTEM,),
}


def required_axes_for_role(role_id: str) -> Tuple[SourceAxis, ...]:
    """Hard-coded "must-cover" axis list for *role_id*."""

    if role_id not in _ROLE_REQUIRED_AXES:
        raise KeyError(f"unknown role for required axes: {role_id!r}")
    return _ROLE_REQUIRED_AXES[role_id]


# ---------------------------------------------------------------------------
# task_type → axis hint matrix (used by retrieval to weight)
# ---------------------------------------------------------------------------


_TASK_TYPE_AXIS_HINTS: Mapping[str, Tuple[SourceAxis, ...]] = {
    "backend-feature": (
        SourceAxis.API_SCHEMA_AUTH,
        SourceAxis.OFFICIAL_DOCS,
        SourceAxis.SECURITY,
    ),
    "frontend-feature": (
        SourceAxis.WEB_PLATFORM_FRAMEWORK,
        SourceAxis.OFFICIAL_DOCS,
    ),
    "landing-page": (
        SourceAxis.DESIGN_SYSTEM,
        SourceAxis.WEB_PLATFORM_FRAMEWORK,
    ),
    "onboarding-flow": (
        SourceAxis.DESIGN_SYSTEM,
        SourceAxis.WEB_PLATFORM_FRAMEWORK,
    ),
    "visual-polish": (SourceAxis.DESIGN_SYSTEM,),
    "email-campaign": (SourceAxis.DESIGN_SYSTEM,),
    "qa-test": (
        SourceAxis.REGRESSION_TEST_PLAN,
        SourceAxis.SECURITY,
    ),
    "platform-infra": (
        SourceAxis.CI_CD_INFRA_OBSERVABILITY,
        SourceAxis.ARCHITECTURE_ADR_TRADEOFF,
    ),
}


def axis_hints_for_task_type(task_type: Optional[str]) -> Tuple[SourceAxis, ...]:
    """Axes that retrieval should weight up for *task_type*.

    Unknown / None task types return an empty tuple — retrieval falls
    back to role + topic match alone. Kept tiny on purpose: the
    weighting math should not branch on a long taxonomy.
    """

    if not task_type:
        return ()
    return _TASK_TYPE_AXIS_HINTS.get(str(task_type).strip().lower(), ())


__all__ = [
    "COMMON_CORE_SOURCES",
    "SUPPORTED_ROLES",
    "auto_collectable_sources",
    "axes_for_role",
    "axis_hints_for_task_type",
    "daily_limit_for_role",
    "find_source",
    "prioritise_sources",
    "required_axes_for_role",
    "role_axis_coverage_report",
    "role_sources",
    "sources_for_axis",
]
