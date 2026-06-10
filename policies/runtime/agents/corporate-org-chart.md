# Yule Studio Corporate Org Chart

본 문서는 yule-studio-agent 의 모든 부서 (department) 와 역할 (role) 의 단일 진실 매트릭스. 새 부서 / 역할 추가 시 본 문서 + `agents/<dept>/manifest.json` 둘 다 갱신.

## C-level 매트릭스

| C-level | 부서 (department) | 부서 ID (`agents/<id>/`) | 1차 책임 |
| --- | --- | --- | --- |
| **CTO** | Engineering | `engineering-agent` | 코드 / 인프라 / 보안 / 품질 |
| **CPO** | Product | `product-agent` | 제품 발견 / PRD / OKR / 데이터 분석 |
| **CMO** | Marketing | `marketing-agent` | 그로스 / 콘텐츠 / SEO / 브랜드 |
| **CHRO** | People (HR) | `hr-agent` | 채용 / 온보딩 / 문화 / 코칭 |
| **CFO** | Finance | `finance-agent` | 예산 / 비용 추적 / 재무 보고 |
| **CRO** | Revenue (Sales/CS) | `sales-cs-agent` | 영업 파이프라인 / 고객 성공 |
| **GC** | Legal | `legal-agent` | 계약 검토 / 프라이버시 / 컴플라이언스 |
| _(별도)_ | Planning Ops | `planning-agent` | 일정 / 알림 / 운영 자동화 (cross-dept) |

## 부서 × 역할 매트릭스

### Engineering (CTO 산하)
- `engineering-agent/tech-lead`
- `engineering-agent/backend-engineer`
- `engineering-agent/frontend-engineer`
- `engineering-agent/qa-engineer`
- `engineering-agent/devops-engineer`
- `engineering-agent/ai-engineer`
- `engineering-agent/product-designer` *(현재 engineering 에 있지만 CPO 산하 product 와 cross-functional)*

### Product (CPO 산하)
- `product-agent/product-manager` — PRD / 발견 / 우선순위
- `product-agent/user-researcher` — 인터뷰 / 페르소나
- `product-agent/growth-analyst` — SQL / 코호트 / A/B

### Marketing (CMO 산하)
- `marketing-agent/growth-marketer` — funnel / 실험
- `marketing-agent/content-strategist` — 블로그 / 영상 기획
- `marketing-agent/seo-specialist` — 검색 / 키워드
- `marketing-agent/brand-manager` — 브랜드 일관성

### People (CHRO 산하)
- `hr-agent/recruiter` — JD / 인터뷰 진행
- `hr-agent/people-ops` — 온보딩 / 정책
- `hr-agent/culture-coach` — 1:1 / 피드백

### Finance (CFO 산하)
- `finance-agent/budget-analyst`

### Revenue (CRO 산하)
- `sales-cs-agent/sales-rep`
- `sales-cs-agent/customer-success`

### Legal (GC 산하)
- `legal-agent/contract-reviewer`
- `legal-agent/privacy-officer`

## 부서 간 협업 매트릭스

| 협업 축 | 부서 |
| --- | --- |
| PRD → 코드 | product-agent → engineering-agent |
| 그로스 실험 | marketing-agent + product-agent |
| 채용 → 온보딩 | hr-agent + engineering-agent (또는 해당 부서) |
| 가격 결정 | product-agent + finance-agent + marketing-agent |
| 계약 검토 | legal-agent + sales-cs-agent |

## 새 부서 / 역할 추가 절차

1. `agents/<dept>/manifest.json` 작성 (AgentManifest schema 준수)
2. `agents/<dept>/<role>/manifest.json` 추가
3. 본 doc 의 매트릭스에 row 추가
4. (옵션) `skills/<domain>/` 에 skill `.md` 추가
5. governance test (`test_corporate_structure_governance.py`) 가 자동 검증

## 운영 정책

- **단일 책임 원칙**: 각 역할은 한 axis 만 owns. 여러 axis 가 필요하면 cross-functional thread 를 운영-리서치 forum 에 연다.
- **HIGH risk plugin (live LLM call / 외부 API write)** 은 모든 부서에서 동일하게 운영자 명시 opt-in.
- **prompt-template ref**: 모든 role manifest 는 `prompt_template_ref` 를 채우거나 명시적으로 빈 값 (skeleton 상태) 표시.
- **plugins_required** 는 F11 plugins/ 에 실제 등록된 manifest id 만 가리킨다.

## 참고

- F11 extension architecture: `policies/runtime/agents/engineering-agent/extension-architecture.md` (있는 경우)
- pm-skills: github.com/phuryn/pm-skills (PM lifecycle 65 skills 카탈로그)
- gstack: github.com/garrytan/gstack (skill template + codegen 패턴)
