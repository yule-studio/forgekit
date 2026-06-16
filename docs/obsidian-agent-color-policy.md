# Obsidian agent color + metadata policy — 공통 vault, 역할별 색/네임스페이스 (SSoT)

> **공통 Obsidian vault 하나를 유지**한다. agent 별 별도 위키를 만들지 않는다. 대신 모든
> note 가 `frontmatter 메타데이터 + write namespace(lane) + color token` 으로 구분된다.
> **색은 사람용 시각 구분**이고, **retrieval 은 색이 아니라 메타데이터**(agent/role/kind/
> status/topic/project/retrieval_weight)를 읽는다. 코드 SSoT 는
> [`agent_color_registry.py`](../apps/engineering-agent/src/yule_engineering/agents/governance/agent_color_registry.py)
> + [`note_frontmatter.py`](../apps/engineering-agent/src/yule_engineering/agents/governance/note_frontmatter.py).

## 1. 원칙
1. 공통 vault 1개 — 별도 위키 분리 금지.
2. 역할 구분은 **lane(write namespace) + frontmatter 메타데이터**.
3. **색(color_token/hex)은 사람용 보조 신호** — retrieval 의 key 가 아니다.
4. retrieval 우선순위: `agent · role · kind · status · topic · project · retrieval_weight`.
5. 색은 **부서 hue 공유 + 역할 shade 변이** — 같은 부서는 비슷한 색군, 역할마다 distinct.
6. starter/shared pack 은 read-only — 쓰기는 각 역할의 lane(개인 brain) 으로만.

## 2. frontmatter 스키마 (필수 15 키)
`title · department · agent · role · kind · status · project · topic · tags · created_at ·
related · write_owner · obsidian_lane · color_token · retrieval_weight`.
`retrieval_weight` 기본값은 kind 로 결정(canonical 2 / reusable·decision 1 / retrospective·status 0.5 / 그 외 0).

## 3. note 템플릿 예시
```markdown
---
title: 영상 업로드 PRD
department: product-agent
agent: product-agent/product-manager
role: product-manager
kind: decision
status: draft
project: bkurs
topic: media-upload
tags: [prd, upload]
created_at: 2026-06-16T00:00:00Z
related: []
write_owner: product-manager
obsidian_lane: 20-product/product-manager
color_token: prod-product-manager
retrieval_weight: 1.0
cssclasses: [forgekit-agent, agent-prod-product-manager]
---

## 핵심 요약
...
```
`obsidian_lane` 경로(`20-product/product-manager/`)에 저장하고, `cssclasses` 로 색 snippet 을 적용한다.

## 4. Obsidian 색 구현 (현실적)
Obsidian 은 frontmatter 만으로 note 색을 자동 칠하지 않는다. **repo-managed CSS snippet +
`cssclasses`** 로 적용한다:
1. [`vault-assets/forgekit-agent-colors.css`](../vault-assets/forgekit-agent-colors.css) 를 `<vault>/.obsidian/snippets/` 에 복사 후 활성화.
2. note frontmatter 에 `cssclasses: [forgekit-agent, agent-<color_token>]`.
3. snippet 이 `.agent-<token> { --fk-accent: <hex> }` 로 inline-title 좌측 accent 를 칠한다.

(색이 미지원 환경이면 메타데이터는 그대로 동작 — 색은 보조이므로 깨지지 않는다.)

## 5. retrieval 은 metadata 중심 (색 아님)
`yule memory search` 등은 frontmatter 의 `agent/role/kind/status/topic/project` 로 필터하고
`retrieval_weight` 로 가중한다. `color_token` 은 결과에 같이 실려도 **정렬·필터의 key 가 아니다**.
knowledge-engineer 가 이 정책의 owner — [`agents/engineering-agent/knowledge-engineer/CLAUDE.md`](../agents/engineering-agent/knowledge-engineer/CLAUDE.md).

## 6. 역할별 lane + color token (레지스트리 생성 — 28 역할)

| department | role | contract | commit | lane | color_token | hex |
| --- | --- | --- | :--: | --- | --- | --- |
| engineering-agent | tech-lead | coordinator | — | `30-engineering/tech-lead` | `eng-tech-lead` | `#1e61ad` |
| engineering-agent | backend-engineer | executor | ✅ | `30-engineering/backend-engineer` | `eng-backend-engineer` | `#2362ba` |
| engineering-agent | frontend-engineer | executor | ✅ | `30-engineering/frontend-engineer` | `eng-frontend-engineer` | `#2862c6` |
| engineering-agent | devops-engineer | executor | ✅ | `30-engineering/devops-engineer` | `eng-devops-engineer` | `#2f63d1` |
| engineering-agent | ai-engineer | executor | ✅ | `30-engineering/ai-engineer` | `eng-ai-engineer` | `#4067d2` |
| engineering-agent | qa-engineer | reviewer | — | `30-engineering/qa-engineer` | `eng-qa-engineer` | `#516dd3` |
| engineering-agent | security-engineer | reviewer | — | `30-engineering/security-engineer` | `eng-security-engineer` | `#6175d5` |
| engineering-agent | product-designer | advisory | — | `30-engineering/product-designer` | `eng-product-designer` | `#717dd7` |
| engineering-agent | platform-runtime-engineer | platform | ✅ | `30-engineering/platform-runtime-engineer` | `eng-platform-runtime-engineer` | `#8187d9` |
| engineering-agent | knowledge-engineer | curator | — | `30-engineering/knowledge-engineer` | `eng-knowledge-engineer` | `#9091dc` |
| engineering-agent | ops-observer | observer | — | `40-ops/ops-observer` | `eng-ops-observer` | `#a19fdf` |
| product-agent | product-manager | product | — | `20-product/product-manager` | `prod-product-manager` | `#1ead90` |
| product-agent | user-researcher | advisory | — | `20-product/user-researcher` | `prod-user-researcher` | `#23baa3` |
| product-agent | growth-analyst | advisory | — | `20-product/growth-analyst` | `prod-growth-analyst` | `#28c6b7` |
| planning-agent | planning-agent | advisory | — | `50-planning/planning-agent` | `plan-planning-agent` | `#ad7d1e` |
| marketing-agent | brand-manager | advisory | — | `60-marketing/brand-manager` | `mkt-brand-manager` | `#ad1e79` |
| marketing-agent | content-strategist | advisory | — | `60-marketing/content-strategist` | `mkt-content-strategist` | `#ba237b` |
| marketing-agent | growth-marketer | advisory | — | `60-marketing/growth-marketer` | `mkt-growth-marketer` | `#c6287d` |
| marketing-agent | seo-specialist | advisory | — | `60-marketing/seo-specialist` | `mkt-seo-specialist` | `#d12f7e` |
| marketing-agent | example | advisory | — | `60-marketing/example` | `mkt-example` | `#d24080` |
| hr-agent | culture-coach | advisory | — | `70-people/culture-coach` | `hr-culture-coach` | `#741ead` |
| hr-agent | people-ops | advisory | — | `70-people/people-ops` | `hr-people-ops` | `#8523ba` |
| hr-agent | recruiter | advisory | — | `70-people/recruiter` | `hr-recruiter` | `#9728c6` |
| finance-agent | budget-analyst | advisory | — | `80-finance/budget-analyst` | `fin-budget-analyst` | `#ad901e` |
| sales-cs-agent | customer-success | advisory | — | `90-revenue/customer-success` | `rev-customer-success` | `#ad571e` |
| sales-cs-agent | sales-rep | advisory | — | `90-revenue/sales-rep` | `rev-sales-rep` | `#ba6723` |
| legal-agent | contract-reviewer | advisory | — | `95-legal/contract-reviewer` | `legal-contract-reviewer` | `#1e82ad` |
| legal-agent | privacy-officer | advisory | — | `95-legal/privacy-officer` | `legal-privacy-officer` | `#2385ba` |

## 7. 관련
- [`agent-invocation-contract.md`](agent-invocation-contract.md) ·
  [`memory.md`](memory.md) · [`forgekit-console.md`](forgekit-console.md) ·
  [`vault-assets/forgekit-agent-colors.css`](../vault-assets/forgekit-agent-colors.css)
