# Agent Office Map — Office ↔ 부서/역할 매핑

> 본 문서는 [`forgekit-company-model.md`](forgekit-company-model.md) 의 10개
> C-level office 를, **현재 실재하는** `agents/<dept>/` 부서/역할에 매핑하는 SSoT 다.
> "이상적 office 모델" 과 "현재 런타임 reality" 사이의 간극을 정직하게 표시한다.
>
> 현재 부서 reality 의 1차 매트릭스는
> [`policies/runtime/agents/corporate-org-chart.md`](../../policies/runtime/agents/corporate-org-chart.md)
> (CTO/CPO/CMO/CHRO/CFO/CRO/GC + Planning Ops). 본 문서는 그것을 **office 모델로
> 확장·재배치** 한다 — 부서를 옮기지 않고 매핑만.

## 1. Office ↔ 부서 매핑표

| Office | director (제안) | 현재 부서(`agents/`) | 부서 manifest 상태 | 비고 |
|---|---|---|---|---|
| CEO / forge-master | forge-master | **없음(신규)** | — | 최상위 오케스트레이터, 신규 |
| COO / operations | operations-director | `planning-agent` | manifest 있음(type/members 미설정) | cross-dept 운영 |
| CTO / engineering | engineering-director | `engineering-agent` | ✅ 완비(type=department, members 7+aux 3) | **유일하게 런타임 실재** |
| CPO / product-goal | product-goal-director | `product-agent` | ❌ 부서 manifest 없음 | 역할 폴더만 존재 |
| CDO / knowledge-data | knowledge-data-director | **없음(신규)** | — | 현재 knowledge-engineer 가 engineering 내부 |
| CMO / content-growth | content-growth-director | `marketing-agent` | ❌ 부서 manifest 없음 | 역할 폴더만 존재 |
| CRO / revenue-opportunity | revenue-opportunity-director | `sales-cs-agent` | ❌ 부서 manifest 없음 | 역할 폴더만 존재 |
| CFO / finance-investment | finance-investment-director | `finance-agent` | ❌ 부서 manifest 없음 | 역할 폴더만 존재 |
| CHRO / personal-growth | personal-growth-director | `hr-agent` | ❌ 부서 manifest 없음 | 역할 폴더만 존재 |
| CLO / legal-risk | legal-risk-director | `legal-agent` | ❌ 부서 manifest 없음 | 역할 폴더만 존재 |
| CSO / strategy-intelligence | strategy-intelligence-director | **없음(신규)** | — | discovery/Nexus 는 forgekit-console 코드에 존재 |

> **정직한 reality**: 부서 레벨 `manifest.json` 을 가진 건 `engineering-agent`
> 하나뿐이다. 나머지는 역할 폴더 또는 문서상 정의만 있다. office 모델은 목표이고,
> 매핑을 채우는 작업은 roadmap Stage 1(I-2)에서 부서 manifest 부터 시작한다.

## 2. 현재 역할 → office 배치

corporate-org-chart 의 현재 역할들을 office 로 배치(부서 폴더는 유지, 논리 배치만):

| office | 현재 역할(`agents/`) |
|---|---|
| CTO/engineering | engineering-agent/{tech-lead, backend-engineer, frontend-engineer, qa-engineer, devops-engineer, ai-engineer, product-designer, security-engineer, platform-runtime-engineer, knowledge-engineer*, ops-observer} |
| CPO/product-goal | product-agent/{product-manager, user-researcher, growth-analyst} |
| CMO/content-growth | marketing-agent/{growth-marketer, content-strategist, seo-specialist, brand-manager} |
| CHRO/personal-growth | hr-agent/{recruiter, people-ops, culture-coach} |
| CFO/finance-investment | finance-agent/{budget-analyst} |
| CRO/revenue-opportunity | sales-cs-agent/{sales-rep, customer-success} |
| CLO/legal-risk | legal-agent/{contract-reviewer, privacy-officer} |
| COO/operations | planning-agent/* |
| CDO/knowledge-data | (목표) knowledge-engineer 가 장기적으로 여기로 분기 가능 |
| CSO/strategy-intelligence | (목표) discovery sweep 로직(forgekit-console)이 office 로 형식화 |

`*` knowledge-engineer: 현재 engineering 내부 auxiliary. CDO 신설 시 분기 후보
(company-model §2.5). **지금 옮기지 않는다** — 매핑 의도만 표시.

## 3. 교차 관계 (cross-office)

| 협업 축 | office 흐름 |
|---|---|
| goal → 구현 | CEO → CPO(brief) → CTO(구현) |
| signal → 아이디어 → 제품 | CSO → CDO(지식화) → CPO/CRO |
| 콘텐츠 파이프라인 | CSO(트렌드) → CMO(제작) |
| 자율 운영 루프 | CEO → COO(tick) → CTO(실행) → reality-check |
| 도입 리스크 | CSO/CTO(후보) → CLO(리스크 verdict) → operator |
| 비용/투자 | COO(/cost) → CFO(예산·리서치) → operator |

## 4. Engineering Office 내부 (링크, 중복 금지)

CTO/engineering 의 4→8→12 팀 구조는 **본 문서에서 재정의하지 않는다.** SSoT:

- machine-readable: [`agents/engineering-agent/manifest.json`](../../agents/engineering-agent/manifest.json) `team_topology`
- 사람용: [`team-structure.md`](../../policies/runtime/agents/engineering-agent/team-structure.md)
- 현재 land 중(logical MVP only): **PR #454**

reality-check-team 은 이 office 의 **required future team**(company-model §3 역량 9,
roadmap I-7). qa-governance 산하에서 출발해 Target 단계에서 독립.

## 5. 동기화

- office↔부서 매핑이 바뀌면 본 문서 + [`forgekit-company-model.md`](forgekit-company-model.md) §2.
- 부서 manifest 가 채워지면 [`corporate-org-chart.md`](../../policies/runtime/agents/corporate-org-chart.md) 와 일치 확인.
