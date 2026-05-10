# Role-axis knowledge feeds

How operators read **what each engineering-agent role keeps watching** and
**why a stored knowledge item came back** at request time. Companion piece
to `engineering-company-runtime-master-plan.md` — this doc covers only the
runtime knowledge-provider surface added on PR #77 (issue #73).

## Mental model

Each engineering-agent role has a small set of **source axes** it cares
about (the master plan §9.1 list, encoded as `SourceAxis` in
`engineering_intelligence.models`). Sources (RSS / Atom / sitemap / GitHub
releases / HTML pages) are tagged with the axes they cover. Treat this as
a per-role "GeekNews" board where the lanes are axes, not topics.

```
backend-engineer
├─ official_docs              (required)  → spring-framework-docs, fastapi-…
├─ api_schema_auth            (required)  → spring-framework-docs, owasp-top-10, …
├─ release_notes_changelog    (required)  → spring-blog, fastapi-changelog, …
├─ security                   (required)  → owasp-top-10, cve-nvd
└─ architecture_adr_tradeoff  (optional)  → stripe-engineering, github-engineering
```

The engineering-agent never *needs* to keep "all of the internet" in
range — five to seven feeds per role plus the common-core CVE/MDN feeds
covers the GeekNews-style "constant pulse" without the long tail.

## Operator surfaces

### 1. `RoleFeedDigest` — what is this role watching?

`engineering_intelligence.role_feed_digest(role_id)` returns a
`RoleFeedDigest` grouping every seeded source by axis:

```python
from yule_orchestrator.agents.engineering_intelligence import role_feed_digest

digest = role_feed_digest("backend-engineer")
print(digest.headline())
# backend-engineer: 8 feeds across 5 axes · 7 auto / 1 review

for group in digest.axes:
    flag = "★" if group.is_required else " "
    print(f"  {flag} {group.axis.value} ({len(group.feeds)} feeds)")
    for feed in group.feeds:
        print(f"      - {feed.source_id}  [{feed.tier.value}]  {feed.base_url}")
```

Required axes (declared in `_ROLE_REQUIRED_AXES`) are listed first. If
any required axis has zero feeds it shows up in
`digest.missing_required_axes` so adding a role without seeding the
right kinds of sources fails the digest immediately rather than
silently producing skinny role boards.

`multi_role_feed_digest()` returns the digest for every supported role
in one call — handy for an operator dashboard that wants the full grid.

### 2. `ProviderAvailabilitySummary` — will this tick fetch anything?

Already exists; see `provider_registry.KnowledgeProviderRegistry.availability_summary(env)`
and the routed `RefreshPlanStatus` from
`provider_routing.refresh_plan_status(plan, role_id=…, env=…)`. Two
sentences here for context: the digest above answers *which feeds the
role wants*; the availability summary answers *which transports are
actually live for the operator's env*. Together they answer "is the
backend role's `release_notes_changelog` axis going to refresh on this
tick?".

### 3. `RoleFeedProvenance` on retrieved items

When `ContextPackBuilder` retrieves knowledge at request time it now
carries two extra fields on each `EngineeringKnowledgeRef`:

| Field | Contents | Source |
|-------|----------|--------|
| `matched_axes` | The axes overlap between the row and the request's axis hints (e.g. `task_type=backend-feature` → API_SCHEMA_AUTH/OFFICIAL_DOCS/SECURITY hint). | `KnowledgeMatch.matched_axes` from `score_knowledge_record` |
| `relevance_reason` | One human sentence: `"role=backend-engineer (primary); axes=api_schema_auth,official_docs; freshness=fresh_7d"`. | `KnowledgeMatch.relevance_reason` |

These travel through `ContextPack.as_dict()` so the synthesizer (and
any debug dump) can quote *why this knowledge came back* without
re-deriving the score. The detailed signal list (`role_primary_match`,
`axis_overlap:…`, `topic_overlap:N`, `fresh_7d`, …) stays in `signals`
for tests / analytics; `relevance_reason` is the operator-readable
projection.

## Read-only & safe-live

The digest is built from the source registry only — no network, no env
read, no auth. Building it on every tick is cheap. The retrieval-side
provenance is computed inline by `score_knowledge_record` (deterministic
arithmetic on already-loaded vault rows) so adding `matched_axes` /
`relevance_reason` did not introduce any new I/O.

The live transport seam keeps the same dual gate from
`provider_registry.ProviderAuthRequirement` — required env keys *and*
the explicit `YULE_KNOWLEDGE_<TRANSPORT>_LIVE_ENABLED` flag must both
be set. The digest never inspects env, so it cannot accidentally
expose secrets or leak that a flag is set. Operators run it freely.

## Common questions

**"Why did this knowledge item show up in the discussion pack?"**
Look at `ref.relevance_reason`. If it says `role=backend-engineer
(primary); axes=api_schema_auth,security; freshness=fresh_7d`, the
record matched the request role exactly, two of the task's axis hints,
and was collected in the last 7 days.

**"Which feeds back the backend `security` axis?"**
`role_feed_digest("backend-engineer")`, find the
`SourceAxis.SECURITY` group, list `feed.source_id`/`feed.base_url`.

**"How do I know if a role is missing required coverage?"**
`digest.missing_required_axes` returns the axes that are declared
required for the role but have zero feeds seeded. Tests pin this empty
for every supported role.
