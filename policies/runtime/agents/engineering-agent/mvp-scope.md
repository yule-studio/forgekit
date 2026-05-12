# Engineering Agent MVP Scope

## Position
Engineering Agent는 회사 전체 agent platform의 **첫 번째 실행 부서(reference department)** 다. CTO 조직의 1차 구현 대상이며, 이 부서를 완성하는 과정에서 만들어지는 패턴이 이후 product / design / marketing / operations 부서 구축의 템플릿이 된다.

```
(future) cto-agent
        │
        ├── engineering-agent       ← MVP 대상. 이 부서를 레퍼런스로 완성한다.
        ├── (future) platform-agent     (devops, infra, observability)
        ├── (future) security-agent     (review, threat modeling)
        ├── (future) data-ai-agent      (analytics, model ops)
        ├── (future) product-agent      (PM, research, discovery)
        ├── (future) design-agent       (visual design, brand, system)
        ├── (future) marketing-agent    (content, ad, growth)
        └── (future) operations-agent   (legal, finance, hr)
```

`product-designer`는 현재 engineering-agent 안에 두지만, 장기적으로 `design-agent`로 분리될 수 있도록 책임 경계와 입출력 계약을 미리 명확히 정의한다.

## In Scope (MVP에서 끝내는 범위)

### S1. 정체성 문서 ✅
- 부서 게이트웨이 역할/책임/경계/I·O 계약 명시 (`agents/engineering-agent/CLAUDE.md`)
- 5명 멤버(tech-lead, backend-engineer, frontend-engineer, product-designer, qa-engineer)의 책임 범위와 입력/출력 계약 (`agents/engineering-agent/<member>/CLAUDE.md`)
- 부서 단위 LLM participant 풀(claude / codex / gemini / ollama / github-copilot) 정의 (`agents/engineering-agent/manifest.json`)

### S2. 운영 정책 문서 ✅
- 작업 범위, 트리거, 통신 방식, Discord 봇 운영 방식 4가지 결정 명문화 (`mvp-operating-policy.md`)
- 역할별 기본 모델 가중치 v0 (`role-weights-v0.md`)
- 역할별 reference pack 정의 (`reference-pack.md`)

### S3. 협업 흐름 정책 ✅
- 멤버 간 호출 순서, 외부 회신 흐름 (`team-structure.md`)
- 브랜치/커밋/테스트 정책 (기존 `version-control.md`, `workflow.md`, `testing.md` 유지)

### S4. 미래 확장 지점 문서화 ✅
- cto-agent / platform / security / data-ai 도입 시 어디가 변경되는지 표기
- product-designer → design-agent 분기 시점과 절차 메모

## Scope Correction (2026-05-01)

초기 MVP 문서는 "single executor + multi advisor"를 안전한 시작점으로 잡았지만, 실제 제품 목표는 **직원형 autonomous engineering team**이다. 따라서 Discord member-bot은 단순 순번표를 따라 말하는 보조자가 아니라, gateway가 게시한 작업을 보고 각자 조사/검토/실행 후보를 제안하는 부서 구성원으로 진화해야 한다.

현재 반영된 조정:
- 운영-리서치 forum은 `[research-open:<session_id>]` 공개 호출을 기본으로 사용한다.
- 각 멤버 봇은 open call을 보고 자기 role profile에 맞춰 추가 조사 후 독립 take를 남긴다.
- 기존 `team-turn` 순차 체인은 작업 thread handoff/복구용 legacy protocol로 남긴다.

아직 남은 autonomous-team 마일스톤:
- GitHub issue 생성, branch 생성, PR 생성/리뷰 요청을 멤버 봇 실행 루프에 연결한다.
- 역할별 장기 메모리/정책/성과 기록을 두어 다음 작업에서 자기 경험을 재사용한다.
- 코드 write는 user approval과 PR review gate를 지키되, 승인 후에는 지정된 봇이 직접 실행할 수 있게 한다.

## Out of Scope (초기 MVP에서 하지 않는 일)

다음은 별도 마일스톤 (Phase 2)에서 다룬다:

- **LLM 러너 본문 구현** — 각 멤버 `manifest.json`은 `runner: null` 상태로 둔다. claude-code/codex/gemini/ollama subprocess wrapper는 다음 단계에서 별도 추상화 레이어로 만든다.
- **멤버 간 메시지 디스패처** — `tech-lead`가 자동으로 다른 멤버를 호출하는 자동화는 아직 없다. MVP 단계에서는 사용자/게이트웨이가 직접 멤버를 지정해 호출한다.
- **멀티봇 Discord 인프라** — 멤버별 별도 Discord 봇 토큰, `yule discord bot --agent <name>` CLI는 다음 단계.
- **자동 머지 / 자동 배포 / secrets 자동 접근** — 어떤 단계에서도 사용자 명시 승인 없이 수행하지 않는다.
- **외부 모델 랭킹 자동 반영** — MVP 단계에서는 외부 평가가 모델 선택의 강한 자동 근거로 쓰이지 않는다. 참고 신호일 뿐.
- **product/design/marketing/operations 부서 구축** — engineering-agent를 레퍼런스로 완성한 뒤 같은 패턴으로 단계적 확장.

## 완료 조건 (DoD)

이 MVP는 다음 세 조건이 모두 만족되면 완료로 간주한다.

1. `agents/engineering-agent/`와 `policies/runtime/agents/engineering-agent/` 안의 문서가 외부 사람/에이전트가 읽고 부서의 책임 경계와 운영 방식을 이해할 수 있는 수준으로 정리되어 있다.
2. MVP 범위 안 일과 범위 밖 일이 분리되어 있어, 다음 이슈가 어디부터 시작해야 하는지 명확하다.
3. 이후 다른 부서가 같은 패턴으로 추가될 때 이 부서를 템플릿으로 복제할 수 있다(폴더 구조, manifest.json 스키마, 멤버 CLAUDE.md 양식, mvp-scope/operating-policy/role-weights/reference-pack 4종 문서 양식).

## Boundaries Reminder

부서가 어떤 단계에서도 지키는 안전선:

- 사용자 승인 없이 파괴적 명령 실행 금지
- secrets 접근 금지
- 자동 배포 / 자동 머지 금지
- 단일 write executor 원칙 (한 실행에서 코드 수정자는 한 명만)
- 부서 외부와의 직접 대화는 게이트웨이만 가능. 멤버는 게이트웨이를 거쳐 입출력
