# ForgeKit Company Model — 대규모 Agent 조직 SSoT

> 본 문서는 ForgeKit 을 **회사처럼 자율 운영되는 multi-agent 시스템** 으로
> 정의하는 **최상위 조직 모델 SSoT** 다. 누가 무엇을 책임지고(office), 무엇이
> 명시적으로 범위 밖인지(out-of-scope), 어떤 계층으로 의사결정이 흐르는지를
> 한 곳에 고정한다.
>
> **이 문서는 조직 *모델* 이지 런타임 *구현* 이 아니다.** dispatch 코드 / 폴더
> 이전 / validator 는 본 문서가 land 된 뒤 별도 stage 에서 다룬다
> ([`staged-agent-organization-roadmap.md`](staged-agent-organization-roadmap.md)).
>
> 동기 문서: 부서↔office 매핑은 [`agent-office-map.md`](agent-office-map.md),
> 네이밍 규칙은 [`naming-convention.md`](naming-convention.md), 단계 로드맵은
> [`staged-agent-organization-roadmap.md`](staged-agent-organization-roadmap.md).
> 기존 부서 매트릭스(현재 reality)는
> [`policies/runtime/agents/corporate-org-chart.md`](../../policies/runtime/agents/corporate-org-chart.md).

## 0. 한 줄 요약

- **operator(유찬)** = 인간 최종 승인자. 모든 L4(secret/deploy/merge) 게이트의 종착점.
- **CEO / forge-master** = 최상위 오케스트레이터. goal 을 office 로 라우팅하고
  일/주 단위로 종합한다. **직접 코드를 쓰지 않는다.**
- **C-level office** = 도메인 책임 단위(10개). 각 office 는 division → team →
  agent 로 내려간다.
- **Engineering(CTO) 만 현재 런타임 실재** — 나머지 office 는 본 모델에서 정의되되
  데이터/문서부터 채운다(roadmap 참조).

## 1. 계층 (Hierarchy)

```text
operator / 유찬                      인간. 최종 승인(L4). 방향 설정.
└── CEO / forge-master               최상위 오케스트레이터 (goal 라우팅·종합)
    └── C-level Office (10)           도메인 책임 단위
        └── Division                  office 내부 큰 책임 묶음
            └── Team                   실행 단위 (lead + member agents)
                └── Agent (role)       단일 도메인 실행/자문 주체
```

각 계층의 책임 경계:

| 계층 | 결정하는 것 | 하지 않는 것 |
|---|---|---|
| operator | 방향, L4 승인, 정책 변경 승인 | 일상 실행 |
| CEO/forge-master | goal→office 라우팅, 우선순위, 일/주 종합 | 코드 작성, office 내부 실행 |
| C-level office | 도메인 전략, division 간 조율, technical/operator 승인 표면 | 다른 office 의 owns 침범 |
| division | 팀 간 실행 순서, 산출물 통합 | 팀 전문 실행 대행 |
| team | 작업 분해, lead 라우팅, evidence 생산 | 다른 팀 owns 침범, 자동 머지 |
| agent (role) | 단일 도메인 draft/실행/리뷰 | 외부 직접 회신(gateway 경유), 단독 write 정책 위반 |

## 2. C-level Office 정의 (10)

각 office 의 mission / owned domains / out-of-scope / escalation. division·team·
role 의 상세 계약은 roadmap 의 Stage 1~2 에서 office 별로 확장한다. **Engineering
은 이미 4→8→12 성장 경로가 정의돼 있으므로 여기서 중복하지 않고 링크만 한다.**

### 2.1 CEO / forge-master
- **mission**: goal 을 받아 office 로 라우팅하고, office 산출물을 일/주 단위로
  종합해 operator 에게 보고. 자율 운영의 척추.
- **owned domains**: goal intake, office routing, daily/weekly synthesis, cross-office 우선순위.
- **out-of-scope**: 코드 작성, office 내부 실행 결정, operator approval 대행.
- **escalation**: → operator.

### 2.2 COO / operations-director
- **mission**: 24h 운영과 goal review 루프. scheduler/queue/alert 가 끊기지 않게.
- **owned domains**: 시간 tick 운영, goal periodic review, blocked queue triage, alert.
- **out-of-scope**: 제품 도메인 코드, 콘텐츠 생산.
- **escalation**: → CEO → operator.

### 2.3 CTO / engineering-director
- **mission**: 코드·런타임·플랫폼·품질. ForgeKit self-improvement 의 실행 엔진.
- **owned domains**: forgekit core, platform/runtime, skill R&D, QA/governance,
  reality-check (4→8→12 팀 — 아래 링크).
- **성장 경로(중복 금지, 링크)**: machine-readable SSoT =
  [`agents/engineering-agent/manifest.json`](../../agents/engineering-agent/manifest.json)
  의 `team_topology`, 사람용 =
  [`team-structure.md`](../../policies/runtime/agents/engineering-agent/team-structure.md),
  현재 land 중인 logical MVP topology = **PR #454** (4-core-team 한정).
- **out-of-scope**: 콘텐츠 생산, 투자 리서치, 법률 판단.
- **escalation**: technical=tech-lead, operator=gateway → operator.

### 2.4 CPO / product-goal-director
- **mission**: goal/PRD/제품 패턴/우선순위. 문제와 수용 기준을 고정.
- **owned domains**: discovery→PRD, PMBrief, 제품 우선순위, 제품 패턴 분석.
- **out-of-scope**: 기술 결정/구현(→CTO), 마케팅.
- **escalation**: → CTO(구현), CEO.

### 2.5 CDO / knowledge-data-director
- **mission**: 지식 데이터화. vault/Nexus/retrieval 품질.
- **owned domains**: vault schema, Nexus ingest, retrieval eval, canonical knowledge,
  compact→vault.
- **out-of-scope**: 기능 코드, 배포.
- **escalation**: → CTO/CSO.

### 2.6 CMO / content-growth-director
- **mission**: 콘텐츠·YouTube 자동화·그로스.
- **owned domains**: 영상 기획, 스크립트, SEO, 배포 자동화, 그로스 실험.
- **out-of-scope**: 코어 코드, 투자/법률.
- **escalation**: → CEO.

### 2.7 CRO / revenue-opportunity-director
- **mission**: 수익 클론 프로젝트·제품 기회·시장 적합 분석.
- **owned domains**: opportunity scout, 클론 후보 spec, 제품 패턴→수익 가설.
- **out-of-scope**: 코어 보안 게이트, 법률 최종 판단.
- **escalation**: → CPO/CTO.

### 2.8 CFO / finance-investment-director
- **mission**: 예산·토큰 비용·투자(주식/시장/부동산) 리서치.
- **owned domains**: 비용 추적(/cost), 예산 리포트, 시장/부동산 리서치 brief.
- **out-of-scope**: 실거래 실행, 코드 작성.
- **escalation**: → operator.

### 2.9 CHRO / personal-growth-director
- **mission**: operator 의 백엔드/DevOps 커리어 성장.
- **owned domains**: 학습 경로, 커리큘럼, 1:1 코칭 산출물, 스킬 갭 분석.
- **out-of-scope**: 코어 실행, 외부 채용(개인 성장 한정).
- **escalation**: → operator.

### 2.10 CLO / legal-risk-director
- **mission**: 라이선스·프라이버시·리스크 게이트.
- **owned domains**: OSS 라이선스 검토, 프라이버시, 외부 도입 리스크 verdict.
- **out-of-scope**: 실행, 구현.
- **escalation**: → operator.

### 2.11 CSO / strategy-intelligence-director
- **mission**: 시그널 수집·아이디어 생성·전략 인텔리전스.
- **owned domains**: discovery sweep, 트렌드 digest, IdeaBrief, 전략 가설.
- **out-of-scope**: 실행, 구현.
- **escalation**: → CEO.

## 3. goal 역량 → office 매핑

operator 가 요구한 9개 역량이 어느 office 로 흐르는지(primary/보조):

| goal 역량 | primary | 보조 |
|---|---|---|
| 1. ForgeKit self-improvement | CTO | CSO |
| 2. 24h goal review / 자율 운영 | COO | CEO |
| 3. signal 수집 / 아이디어 생성 | CSO | CDO |
| 4. 백엔드/DevOps 커리어 성장 | CHRO | CTO |
| 5. 주식/시장/부동산 리서치 | CFO | CSO |
| 6. 콘텐츠/YouTube 자동화 | CMO | CDO |
| 7. skill/tool 리서치(Claude Code·Codex·MCP·OpenClaw·Hermes·Harness·Ponytail) | CTO(skill-rnd) | CDO |
| 8. 수익 클론/제품 패턴 분석 | CRO | CPO |
| 9. strict reality-check | CTO(reality-check-team) | CLO |

## 4. 명시적 Out-of-Scope (이 문서 / 이 단계)

- ❌ 런타임 dispatch 구현 (office→team→role resolver) — Stage 4.
- ❌ 폴더 physical migration (role→team/office 폴더) — Stage 5, 별도 제안서.
- ❌ CODEOWNERS / GitHub native enforcement — 후속 단계.
- ❌ 신규 validator / ownership engine / registry 프레임워크.
- ❌ goal runtime 수정 — 본 문서는 **target behavior 만 기술**(§5), 구현 안 함.
- ❌ Engineering 성장 경로 중복 정의 — manifest `team_topology` + PR #454 가 SSoT, 본 문서는 링크.
- ❌ 비-engineering office 의 즉시 런타임화 — 데이터/문서부터.

## 5. Target Behavior — Goal Closed-Loop (구현 아님, 목표 기술)

reality-check 의 기준선. goal 은 "저장+조회" 가 아니라 아래 **closed-loop** 여야
한다. 본 문서는 이를 **목표 동작** 으로 고정하되 구현하지 않는다 — 실제 어디까지
닫혔는지는 reality-check-team 의 evidence 감사가 판정한다([roadmap I-7](staged-agent-organization-roadmap.md)).

```text
goal 생성
  → scheduler 등록
  → periodic goal review
  → next action 생성
  → agent assignment (office→team→role)
  → agent run
  → evidence 저장
  → blocked / approval queue
  → daily / weekly report
```

**reality-check 원칙**: "저장됐지만 실행 안 됨"(stored-not-executed)은 done 이
아니다. 각 단계는 evidence 로 증명돼야 한다(코드 존재 ≠ 동작).

## 6. 동기화

본 문서를 변경하면 다음만 갱신(중복 회피):

- [`agent-office-map.md`](agent-office-map.md) — office↔부서 매핑이 바뀌면
- [`naming-convention.md`](naming-convention.md) — 네이밍 규칙이 바뀌면
- [`staged-agent-organization-roadmap.md`](staged-agent-organization-roadmap.md) — 단계/issue 가 바뀌면
- [`AGENTS.md`](../../AGENTS.md) §2 / root [`CLAUDE.md`](../../CLAUDE.md) — 새 SSoT 등록(읽기 우선순위)
- [`policies/runtime/agents/corporate-org-chart.md`](../../policies/runtime/agents/corporate-org-chart.md) — 현재 부서 reality 와 어긋나면
