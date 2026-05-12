# Engineering Agent Team Structure

## Position In Company-wide Agent Platform
engineering-agent는 회사 전체 agent platform의 **첫 번째 실행 부서(reference department)** 다. CTO 조직의 1차 구현 대상이다.

```
(future) cto-agent
        │
        ├── engineering-agent       ← 본 부서. 코드 실행 책임
        ├── (future) platform-agent     (devops, infra, observability)
        ├── (future) security-agent     (review, threat modeling)
        ├── (future) data-ai-agent      (analytics, model ops)
        ├── (future) product-agent      (PM, research, discovery)
        ├── (future) design-agent       (visual design, brand, system) — product-designer가 분기될 위치
        ├── (future) marketing-agent    (content, ad, growth)
        └── (future) operations-agent   (legal, finance, hr)
```

CTO 조직이 도입되면 engineering-agent의 외부 인터페이스는 cto-agent로 이양된다. 그러나 부서 책임 범위와 멤버 구성은 그대로 유지된다.

## Why a Department, Not a Flat List of Agents
1. **요청 라우팅이 자연스럽다.** planning-agent나 사용자가 "엔지니어링 작업 필요"라고 하면 게이트웨이(engineering-agent)에게만 보내면 된다. 게이트웨이가 어떤 멤버에게 분배할지 결정한다.
2. **크로스팀 작업이 한 곳에서 조율된다.** 회원가입 같은 기능은 backend + frontend + product-designer + qa가 모두 움직이는데, 게이트웨이가 분배·합의·회신을 한 번에 책임진다.
3. **외부 인터페이스가 단순하다.** 다른 시스템(Discord, planning-agent, 미래의 cto-agent)은 게이트웨이와만 대화한다. 멤버 구성을 알 필요가 없다.
4. **확장이 쉽다.** 같은 부서 패턴으로 platform/security/data-ai/product/design/marketing/operations 부서를 단계적으로 추가한다.

## File Layout

```
agents/
└── engineering-agent/             ← Department gateway (게이트웨이)
    ├── manifest.json                  type=department, members=[...], 부서 단위 LLM 풀
    ├── CLAUDE.md                   게이트웨이 책임/입출력 계약 정의
    ├── tech-lead/                  Role member: 작업 분해 / 합의 조율
    ├── ai-engineer/                LLM/RAG/research collector/agent eval 자문
    ├── product-designer/           장기적으로 design-agent로 분기 가능
    ├── backend-engineer/
    ├── frontend-engineer/
    └── qa-engineer/

policies/runtime/agents/engineering-agent/
├── team-structure.md          (이 문서)
├── mvp-scope.md               부서 MVP 범위 / Out of scope
├── mvp-operating-policy.md    작업 범위·트리거·통신·봇 운영 4가지 결정
├── role-weights-v0.md         역할별 기본 모델 가중치
├── reference-pack.md          역할별 1순위 레퍼런스 소스
├── version-control.md         브랜치/커밋 규칙
├── workflow.md                작업 흐름
└── testing.md                 테스트 정책
```

## How Members Share LLM Backends
멤버 폴더의 `manifest.json`은 **개별 LLM 백엔드를 직접 소유하지 않는다**. 모든 멤버는 부서 단위 `manifest.json`의 `participants`/`integrations` 풀을 공유한다.

- 게이트웨이가 작업 성격과 `role-weights-v0.md`의 가중치를 보고 어떤 executor(Claude / Codex / Gemini / Ollama / GitHub Copilot)를 쓸지 결정한다.
- 멤버는 자기 책임 범위(responsibilities)와 입력/출력 계약만 정의한다.
- 멤버 폴더의 `runner: null`은 "현재 골격 단계라 실행 백엔드는 비어 있음"을 뜻한다.

## Role Members (MVP 1차 골격)

| Member | 책임 요약 | 향후 분리 가능성 |
|---|---|---|
| `tech-lead` | 작업 분해, 의존 순서, 멤버 간 합의 조율, 외부 회신 | engineering-agent에 유지 |
| `ai-engineer` | autonomous research collector 설계, LLM runner / model routing / prompt 정책, ResearchPack 품질, RAG/memory/Obsidian export 자문, hallucination 방지와 source grounding, token/cost/latency 최적화, agent evaluation 기준 제안 | 장기적으로 `data-ai-agent` 분리 가능 |
| `product-designer` | 화면 흐름·구조·시각 가이드 결정 | **장기적으로 `design-agent`로 분기 가능** |
| `backend-engineer` | 도메인 모델, 서비스, API, 데이터 계층 | engineering-agent에 유지 |
| `frontend-engineer` | UI 컴포넌트, 사용자 흐름 코드, 데이터 연결 | engineering-agent에 유지 |
| `qa-engineer` | 수용 기준, 회귀 시나리오, 테스트 우선순위 | engineering-agent에 유지 (또는 `quality-agent`로 분기 가능) |

향후 추가 가능 멤버:
- `platform/devops` — 인프라, CI/CD, observability
- `security-review` — 보안 리뷰, threat modeling
- `data-ai` — 데이터 분석, ML/AI 모델 운영

이런 추가 멤버는 부서가 커지면 별도 부서(`platform-agent`, `security-agent`, `data-ai-agent`)로 분리한다.

## Typical Collaboration Flow

> MVP 1단계에서는 자동화되지 않고, 게이트웨이/사용자가 멤버를 직접 지정해 호출한다.
> Phase 2 디스패처가 만들어지면 자동 분배가 가능해진다.

1. 게이트웨이가 요청 수신 (planning-agent / 사용자 / 미래 orchestrator) → `tech-lead`에게 작업 분해 요청
2. `tech-lead`가 의존 순서 결정 → 필요한 멤버 호출 순서 결정 (예: ai-engineer → product-designer → backend → frontend → qa)
3. `ai-engineer`가 자료 수집 전략, 모델/프롬프트, RAG 관점에서 검토 (research collector 우선순위, ResearchPack 품질, hallucination 가드)
4. UI/UX 영역이 포함되면 `reference-pack.md` 기준 레퍼런스 조사 (3~5개) 결과를 PR/이슈에 첨부
5. `product-designer`가 화면/흐름 결정 산출
6. `backend-engineer`가 도메인/API 작업
7. `frontend-engineer`가 UI 옮김
8. `qa-engineer`가 수용 기준과 회귀 시나리오로 검증
9. `tech-lead`가 종합 → 게이트웨이가 외부에 회신

## Phase

| Phase | 상태 | 결과물 |
|---|---|---|
| **1. MVP 골격** | 진행 중 / 마무리 | 부서 정체성 문서, 멤버 책임 정의, 운영 정책 4가지 결정, 역할 가중치 v0, reference pack |
| **2. 본격 구현** | 다음 마일스톤 | LLM 러너 추상화, 메시지 프로토콜, 디스패처, Discord 멀티봇 |
| **3. 확장** | 그 이후 | platform/security/data-ai 부서 분기, design-agent 분기 검토 |

## Boundaries
- 모든 코드 변경은 부서 단위 `write_policy`를 따른다(사용자 승인 필수, write executor 1명).
- 사용자 승인 없이 파괴적 명령, secrets 접근, 자동 배포, 자동 머지는 하지 않는다.
- 멤버는 부서 외부와 직접 대화하지 않는다. 모든 외부 회신은 게이트웨이를 거친다.
- engineering-agent 외 다른 부서가 만들어지면, 부서 간 협업은 `orchestrator-agent` 또는 `cto-agent` 같은 상위 조율자를 통해 이루어진다.

## Reference Department Template

이 부서가 다른 부서 구축의 템플릿이 되도록 다음 세 가지를 보장한다:

1. **폴더 구조**: `agents/<department-name>/<member-id>/{manifest.json, CLAUDE.md}` 패턴 유지
2. **정책 4종**: `mvp-scope`, `mvp-operating-policy`, `role-weights-v0`, `reference-pack` (해당되는 경우)
3. **manifest.json 스키마**: `type=department` 부서장 + `type=role` 멤버, `runner: null`로 시작해 단계적 채움

이 패턴이 정착되면 product/design/marketing/operations agent 도입 시 폴더 복사 + 책임 재정의만으로 빠르게 출범 가능하다.
