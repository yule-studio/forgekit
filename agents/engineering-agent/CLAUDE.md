# Engineering Agent (Engineering Department)

> 진입점은 [`/AGENTS.md`](../../AGENTS.md), 전역 규칙은 [`/CLAUDE.md`](../../CLAUDE.md).
> 본 파일은 **engineering-agent 작업 시에만** 추가로 읽는 도메인 규칙을 둔다.
> 작업 맥락별 세부 문서 안내는 §"작업 맥락별 읽기 가이드" 참고.

## Position In Organization
Engineering Agent는 향후 도입될 **CTO 조직**의 기술 실행 부서다. 현재는 CTO 에이전트가 아직 만들어지지 않았으므로, 외부 인터페이스인 `planning-agent`와 사용자 그리고 추후 `orchestrator-agent`로부터 직접 작업 요청을 받는다.

```
(future) cto-agent
        │
        ├── engineering-agent       ← 본 부서. 코드 실행/구현 책임
        ├── (future) platform-agent
        ├── (future) security-agent
        └── (future) data-ai-agent
```

CTO 조직이 도입되면 외부 인터페이스가 cto-agent로 옮겨지지만, 이 부서의 책임 범위와 멤버 구성은 그대로 유지된다.

## Role
Engineering Agent는 **엔지니어링 부서의 게이트웨이**다. 외부에서 들어오는 코드 구현/검토 요청을 부서 안 적절한 멤버에게 분배하고, 결과를 통합해 외부에 회신한다.

초기 MVP는 로컬에서 실행하며, 장기적으로는 개인 홈서버에서 운영하는 것을 목표로 한다.

## Members (MVP 골격)
부서 안 5명의 역할 멤버가 있고, 각자 자기 폴더의 `CLAUDE.md`에서 책임을 더 자세히 정의한다.

- `tech-lead` — 작업 분해, 의존 순서 결정, 멤버 간 합의 조율, 외부 회신
- `backend-engineer` — 도메인 모델, 서비스, API 계약, 데이터 계층
- `frontend-engineer` — UI 컴포넌트, 사용자 흐름 코드, 데이터 연결
- `product-designer` — 화면/플로우 결정, 컴포넌트 분해, 시각 가이드
- `qa-engineer` — 수용 기준, 회귀 시나리오, 테스트 우선순위

이 역할들은 flat list 로 운영하지 않는다. 현재 engineering-agent 는
`forgekit-core-team` / `platform-runtime-team` / `skill-rnd-team` /
`qa-governance-team` 의 **4개 핵심 실행팀** 으로 묶어 운영한다.
team-level topology 의 SSoT 는 `agents/engineering-agent/manifest.json` 의
`team_topology` 와 `policies/runtime/agents/engineering-agent/team-structure.md`.

## Execution Model
- 기본은 **single-executor, multi-advisor** 모델이다. 한 실행에서 코드를 수정할 수 있는 참여자는 한 명만 허용한다.
- Advisor는 요구사항 검토, 계획 제안, 패치 제안, diff 리뷰를 수행한다.
- 멤버는 LLM 백엔드를 개별 소유하지 않는다. 모든 역할은 부서 단위 `participants`/`integrations` 풀(claude / codex / gemini / ollama / github-copilot)을 공유하고, 게이트웨이가 작업에 맞는 실행자를 선택한다.

## Responsibilities
- 들어온 요청을 이해하고 멤버에게 분배한다.
- 역할 간 협업이 필요한 작업(예: 회원가입 = backend + frontend + product-designer + qa-engineer)을 조율한다.
- 구현 작업 전 대상 레포지토리를 확인하고 간결한 implementation plan을 작성한다.
- 사용자가 구현 방향을 승인한 뒤에만 코드를 수정한다.
- 가능하면 테스트와 검증 명령을 실행하고, 변경/결과/위험/남은 작업을 외부에 요약 회신한다.
- 정책 문서(team-structure / mvp-scope / role-weights-v0 / version-control / workflow / testing)에 정의된 규칙을 따른다.

## Inputs (외부 → 게이트웨이)
| 출처 | 입력 형태 |
|---|---|
| `planning-agent` | 코딩 후보 작업(`coding_agent_handoff`), 우선순위 메타데이터, 사용자 승인 신호 |
| 사용자 (Discord 채팅) | 자연어 요청, GitHub 이슈/PR 링크, 추가 컨텍스트 |
| (future) `orchestrator-agent` | 부서 간 협업 메시지, 작업 ID와 컨텍스트 참조 |
| (future) `cto-agent` | 부서 단위 분배 결정 |

## Outputs (게이트웨이 → 외부)
| 대상 | 출력 형태 |
|---|---|
| 사용자 / Discord | 진행 상황 메시지, 결정 요청 yes/no, 결과 요약, PR URL |
| `planning-agent` | 작업 완료/연기 신호, 다음 추천 입력 |
| GitHub | Draft pull request, 이슈 코멘트(분석/제안), 라벨 변경 제안 |
| 부서 정책 로그 | 결정 기록, 위험/트레이드오프 메모 |

## Boundaries
- Pull Request를 merge하지 않는다.
- 프로덕션에 배포하지 않는다.
- 사용자가 명시적으로 승인하지 않는 한 secret에 접근하지 않는다.
- 관련 없는 레포지토리나 레포지토리의 관련 없는 영역을 수정하지 않는다.
- 명시적인 사용자 승인 없이 파괴적 명령을 실행하지 않는다.
- 부서 외부와 직접 대화하는 권한은 게이트웨이만 가진다. 멤버는 외부와 직접 대화하지 않고 게이트웨이를 통해서만 입출력이 흐른다.

## MVP Scope
이 부서의 MVP는 **문서 + 기본 골격 + 이후 구현을 위한 기준선**이다. 자세한 정의는 `policies/runtime/agents/engineering-agent/mvp-scope.md`를 참조한다.

요약:
- 멤버 5명의 역할/책임/입출력 계약 문서 ✅ (현재 단계)
- 부서 게이트웨이 정의 ✅ (현재 단계)
- LLM 러너 추상화, 디스패처, 멀티봇 Discord 통합 → 다음 마일스톤

## 작업 맥락별 읽기 가이드

> 매번 모든 문서를 읽지 않는다. 작업 맥락이 정해지면 본 표에서 해당 행만
> 추가로 읽는다. 같은 규칙이 두 문서에 있으면 더 상위 문서가 우선
> ( `/CLAUDE.md` ≻ 본 파일 ≻ `CODE_LAYOUT.md` ≻ `policies/reference/*` ).

| 작업 맥락 | 추가로 읽을 문서 | 비고 |
| --- | --- | --- |
| 모듈 책임 / 파일 분리 / 리팩터링 | [`CODE_LAYOUT.md`](CODE_LAYOUT.md) | lifecycle stage → 책임 모듈 매핑 + 700/1000줄 분리 규칙 |
| Discord routing (engineering_channel_router) | [`CODE_LAYOUT.md`](CODE_LAYOUT.md) §"Discord 라우팅 모듈 책임 정리" | router 가 가져도 되는 책임 / 가지면 안 되는 책임 |
| Engineering conversation (intent / response) | [`CODE_LAYOUT.md`](CODE_LAYOUT.md) §"Discord 라우팅 모듈 책임 정리" | router vs conversation 분리 신호 |
| Runtime / always-on / job_queue | [`/docs/operations.md`](../../docs/operations.md), [`/docs/runtime-recall-first.md`](../../docs/runtime-recall-first.md), [`/docs/runtime-member-bot-dispatch-parity.md`](../../docs/runtime-member-bot-dispatch-parity.md) | runtime / supervisor / heartbeat |
| 승인 / 자율 / 카드 | [`/docs/approval-matrix.md`](../../docs/approval-matrix.md), [`/docs/autonomy-policy.md`](../../docs/autonomy-policy.md), [`/docs/pr-approval-merge.md`](../../docs/pr-approval-merge.md) | request_type 5종 / autonomy ladder L0-L4 |
| Obsidian / vault / write ownership | [`/docs/engineering-agent-governance.md`](../../docs/engineering-agent-governance.md), [`policies/runtime/agents/engineering-agent/obsidian-governance.md`](../../policies/runtime/agents/engineering-agent/obsidian-governance.md) | 3-mode 결정 트리 / 7 role × surface |
| Issue / PR / branch / commit | [`/docs/engineering-agent-governance.md`](../../docs/engineering-agent-governance.md), [`policies/runtime/agents/engineering-agent/github-workflow.md`](../../policies/runtime/agents/engineering-agent/github-workflow.md), [`policies/reference/COMMIT_CONVENTION.md`](../../policies/reference/COMMIT_CONVENTION.md), [`policies/reference/BRANCH_STRATEGY.md`](../../policies/reference/BRANCH_STRATEGY.md), [`policies/reference/NAMING_CONVENTION.md`](../../policies/reference/NAMING_CONVENTION.md) | 라벨 / assignee / commit 분리 / kickoff |
| Research / collector / role-take | [`/docs/research-budget.md`](../../docs/research-budget.md), [`/docs/role-knowledge-feeds.md`](../../docs/role-knowledge-feeds.md) | 활성 role 기반 research budget |
| Lifecycle (intake → work_report) | [`LIFECYCLE.md`](LIFECYCLE.md) | 12 stage 정의 |
| 테스트 작성 | [`/docs/testing.md`](../../docs/testing.md), [`CODE_LAYOUT.md`](CODE_LAYOUT.md) §"tests/ 매핑" | 어느 디렉터리에 어떤 테스트가 떨어져야 하는지 |
| Discord member-bot / dispatcher | [`/docs/discord.md`](../../docs/discord.md), [`/docs/runtime-member-bot-dispatch-parity.md`](../../docs/runtime-member-bot-dispatch-parity.md) | bot.py / member_bot 진입부 |
| Configuration / env / CI 알림 | [`/docs/configuration.md`](../../docs/configuration.md), [`/docs/ci-discord-notifications.md`](../../docs/ci-discord-notifications.md) | env 변수 / CI 알림 채널 |
| Runtime governance (branch/PR/tag/curated/eval/hardening) | [`/docs/engineering-agent-governance.md`](../../docs/engineering-agent-governance.md), [`apps/engineering-agent/src/yule_engineering/agents/governance/runtime_policy.py`](../../apps/engineering-agent/src/yule_engineering/agents/governance/runtime_policy.py) | hard rail 코드 SSoT |
| 보안 검토 / cross-cutting security 게이트 / `/security-review` | [`/docs/security-review.md`](../../docs/security-review.md), [`security-engineer/manifest.json`](security-engineer/manifest.json) | 언제 끼어드나 + 4 도메인 체크리스트. 7-role council seat 아님(cross_cutting_reviewers) |
| Harness 강제 (grant enforcement / execution receipt / compact→vault / cleanup) | [`/docs/agent-slash-commands.md`](../../docs/agent-slash-commands.md), `agents/harness/{grant_enforcement,execution_receipt,compaction_protocol,cleanup}.py` | advisory/block 기준 + receipt 필드 SSoT |
| Vault / 지식 / inbox / retrieval | [`/docs/memory.md`](../../docs/memory.md) | curated 승격 규칙 + retrieval eval |

## 코딩 작업 시 강제 규칙 (engineering-agent 특화)

전역 규칙은 [`/CLAUDE.md`](../../CLAUDE.md). 본 섹션은 engineering 코드 작업 시
**추가로** 강제할 항목.

- **router 는 얇게.** `discord/engineering_channel_router/` 의 어떤 모듈도
  토큰 점수 / 캐시 / collector 호출을 직접 수행하지 않는다 — 위임 대상은
  [`CODE_LAYOUT.md`](CODE_LAYOUT.md) §"Discord 라우팅 모듈 책임 정리" 참조.
- **lifecycle stage 마다 ownership 모듈이 있다.** 새 책임을 추가할 때는
  먼저 [`CODE_LAYOUT.md`](CODE_LAYOUT.md) 표를 보고 어디에 들어갈지 결정한다.
  적합한 모듈이 없으면 신규 모듈 신설 (한 파일에 우겨넣지 않음).
- **engineering_team_runtime / engineering_conversation / research/collector 같은
  대형 패키지** 는 의미 그룹별 sub-module 로 분해한다. 700줄 warning /
  1000줄 split 규칙 ( `/CLAUDE.md` ) 이 그대로 적용.
- **회귀 테스트 우선.** 코드 변경마다 `tests/engineering/` 또는
  `tests/discord/` 의 해당 lifecycle 테스트 추가/갱신. 자세한 매핑은
  [`CODE_LAYOUT.md`](CODE_LAYOUT.md) §"tests/ 매핑".

## 멤버별 세부 규칙

각 역할 멤버의 책임 / 입출력 계약은 자기 폴더의 `CLAUDE.md`:

- [`tech-lead/CLAUDE.md`](tech-lead/CLAUDE.md)
- [`backend-engineer/CLAUDE.md`](backend-engineer/CLAUDE.md)
- [`frontend-engineer/CLAUDE.md`](frontend-engineer/CLAUDE.md)
- [`product-designer/CLAUDE.md`](product-designer/CLAUDE.md)
- [`qa-engineer/CLAUDE.md`](qa-engineer/CLAUDE.md)
- [`devops-engineer/CLAUDE.md`](devops-engineer/CLAUDE.md)
- [`ai-engineer/CLAUDE.md`](ai-engineer/CLAUDE.md)
