# Engineering Agent — Team Architecture Patterns

본 문서는 issue #48 의 결과물로, Harness 의 6 가지 팀 아키텍처 패턴을 Yule engineering-agent 에 매핑하고, Yule 이 실제로 채택할 **회사형 시니어 개발팀 구조** 의 운영 규칙을 정의한다. 본 정책은 2026-05-08 에 `engineering-agent/tech-lead` 단일 주체로 작성됐다.

이 문서는 **policy 단일 source of truth** 다. 코드 차원의 selector / dispatcher / runtime 변경은 이 문서가 아니라 후속 PR (`#25 선행 의존` 또는 본 정책의 §11 다음 액션 목록) 에서 다룬다.

## 1. 목표

- Harness 의 팀 아키텍처 팩토리 관점을 **개념 레퍼런스**로 받아들이되, Yule 의 현재 baseline (lifecycle 13 단계, 7 역할 RoleProfile, 5 단계 ParticipationLevel, fallback 6 종, ObsidianWriterWorker, GitHub WorkOS) 과 충돌 없이 흡수한다.
- "회사형 시니어 개발팀" 의 핵심 약속 두 가지를 명문화한다.
  - **단일 write 주체**: GitHub issue / branch / PR / worklog 작성 주체는 항상 `engineering-agent/tech-lead` 1 명.
  - **다중 분석 관점**: backend / frontend / devops / qa / ai-engineer / product-designer 는 분석 관점만 제공하고, 실제 실행 / 외부 회신 / 커밋 / PR 작성에 직접 손대지 않는다.
- 6 패턴 중 어떤 것을 어떻게 결합해 Yule gateway 의 기본 패턴 조합으로 둘지를 결정한다.

## 2. 현재 Yule 기준선 (2026-05-08)

| 항목 | 현재 baseline 모듈 / 문서 | 핵심 surface |
| --- | --- | --- |
| Department gateway | `src/yule_orchestrator/discord/engineering_channel_router.py`, `team-structure.md` | `route_engineering_message`, intake → triage → role_selection → … 13 단계 |
| Role 정의 | `src/yule_orchestrator/agents/role_profiles_data.py`, `role-profiles.md` | RoleProfile 7 종 + ParticipationLevel 5 + fallback 6 |
| Selector | `src/yule_orchestrator/agents/lifecycle/role_selection.py` | `recommend_active_roles`, `apply_role_selection_to_extra` |
| Tech-lead aggregator | `src/yule_orchestrator/agents/tech_lead_aggregator.py` | `aggregate_role_outputs`, research-only 자동 코딩 차단 |
| Lifecycle status / gate | `src/yule_orchestrator/agents/lifecycle/lifecycle_status.py` | `compute_report_status`, `can_write_obsidian_record` |
| Coding authorization | `src/yule_orchestrator/agents/coding/authorization.py` | executor 추천 + 사용자 승인 게이트 |
| GitHub workspace | `src/yule_orchestrator/agents/github_workos/` (G2~G6) | `senior_triage`, `decide_permission`, branch/commit/PR/audit 어댑터 |
| Engineering intelligence | `src/yule_orchestrator/agents/engineering_intelligence/` | RAG/CAG 학습 지식 수집 + Obsidian L1 자동 저장 |
| Supervisor / health | `src/yule_orchestrator/agents/job_queue/worker_loop.py`, `runtime/run_service.py` | heartbeat sweep, lease reaper, self-improvement detect hook |

Yule 은 이미 Pipeline (lifecycle 13 단계), Fan-out (multi-role research), Producer-Reviewer (coding authorization 의 executor + reviewer split), Supervisor (`run_supervisor_watch_loop`) 의 **부분 구현**을 갖고 있다. Harness 6 패턴은 이 파편들을 한 회사 모델 안에서 일관성 있게 묶기 위한 어휘다.

## 3. Harness 6 패턴 — 매핑 + 도입 결정

| Harness 패턴 | 패턴 요약 | Yule 현재 대응 | Yule 도입 여부 | 이유 | 리스크 / 비도입 부분 |
| --- | --- | --- | --- | --- | --- |
| **Pipeline** | 단계가 직렬로 묶이고 한 단계의 산출이 다음 단계의 입력. | lifecycle 13 단계 (`intake → triage → role_selection → research_planning → role_scoped_research → sufficiency_check → deliberation → synthesis → interim/insufficient/final_report → obsidian_preview → obsidian_recorded → coding_authorization_pending → coding_job_ready`) | **채택 (이미 운영 중)** | gateway 의 기본 흐름. session.extra persistence + lifecycle gate 가 이미 단계별 status 를 보장. | 단계 사이의 retry / branch 가 hard-coded 라 신규 단계를 끼울 때 회귀 위험. → §11 next action: lifecycle stage 등록 helper 분리 (#25 선행 의존). |
| **Fan-out / Fan-in** | 한 입력을 여러 분석가에게 동시에 보내고, 결과를 단일 합의안으로 모은다. | research_planning 후 active role 별 `role_take` 잡 fan-out → `tech_lead_aggregator.aggregate_role_outputs` 가 fan-in. | **채택 (이미 운영 중) — tech-lead 단일 fan-in 으로 강화** | 이미 fan-out 자체는 작동. 본 정책으로 **모든 fan-in 출력은 tech-lead 가 1 회 다시 정리한 뒤에야 외부 회신** 으로 못박는다. | 단일 fan-in 이 SPOF — tech-lead degrade 시 보고서가 멈춘다. → fallback policy: tech-lead 가 degrade 면 `interim_report` 로 lifecycle stop, 사용자에게 즉시 통지. |
| **Expert Pool** | 다수 전문가에서 한 명을 선택해 답변을 받는다. (= dispatcher) | RoleSelector + activation_keywords + ParticipationLevel + Coding executor priority. | **부분 채택** | 7 역할 RoleProfile + selector 가 사실상 expert pool. 단, Yule 은 일반 작업에서 **단일 expert 응답을 그대로 외부에 노출하지 않고** tech-lead aggregator 를 거치게 강제한다. | 단일 expert 응답이 외부로 직출되는 우회 경로가 없는지 §6 routing matrix 로 못박는다. |
| **Producer-Reviewer** | 한 에이전트가 산출하고 다른 에이전트가 검토 후 승인. | Coding authorization MVP (executor 추천 + reviewer + 사용자 승인 게이트), GitHub WorkOS senior_triage 의 `approval_required_actions`. | **부분 채택** | 코드 변경 surface 에는 이미 적용. 다만 **issue / PR / Obsidian write 에도 동일 producer-reviewer 형식을 일관 적용**: producer = 분석 관점 역할, reviewer = tech-lead, write subject = tech-lead 1 명. | 모든 글에 reviewer 한 단계가 강제되면 latency 증가. → §6 routing matrix 로 reviewer 강제 단계와 fast-path 단계를 분리. |
| **Supervisor** | 장기 실행 모니터가 하위 에이전트의 heartbeat / 실패를 감시하고 재기동 / circuit-break / 자기 개선을 트리거. | `run_supervisor_watch_loop`, heartbeat sweep, lease reaper, self-improvement detect+dispatch hook (M12 `d083446`). | **채택 (이미 운영 중)** — 본 정책이 추가로 Harness evolution feedback loop 를 명문화. | self_improvement detector → planner → ObsidianWriteRequest → 운영자 검토 흐름이 이미 합류. | M12 의 `run_service.py` 분기 wiring 한 줄은 아직 미완 (M13 readiness 문서 §2 G-M12-01). 본 정책은 이 wiring 을 **회사형 팀의 "team retrospective" 메커니즘** 으로 분류해 우선순위를 한 단계 올린다. |
| **Hierarchical Delegation** | 상위 에이전트가 하위 에이전트에게 작업을 위임하고, 결과를 받아 더 상위에 보고. | `agents/engineering-agent/` (department gateway) → `tech-lead` → 6 멤버 역할 → executor (`coding_job`). team-structure.md §"Position In Company-wide Agent Platform" 에서 `(future) cto-agent → engineering-agent → 6 역할` 로 2 단 계층 명시. | **채택** | Yule 의 부서 모델 자체가 hierarchical delegation. 본 정책은 **현 단계 = 1 부서 (engineering)** 이고, cto-agent 도입 시 외부 인터페이스가 cto-agent 로 이양된다는 baseline 을 그대로 유지. | 2 단 계층을 깊게 (sub-team 분기) 만드는 시도는 보류. 1 부서 + 6 역할 + 1 게이트웨이 구조가 안정화되기 전까지는 새 sub-team 추가 금지. |

### 3.1 Yule gateway 기본 패턴 조합

> **Pipeline (외곽) + Fan-out·Fan-in (research/synthesis 내부) + Expert Pool (selector) + Producer-Reviewer (코드/PR/외부 회신) + Supervisor (런타임 watcher) + 1 단 Hierarchical Delegation (부서 → 6 역할).**

`gateway 가 받은 요청 → Pipeline 단계 진행 → 단계 안에서 Fan-out·Fan-in 으로 역할별 분석 수집 → 모든 외부 발화 / 코드 / Obsidian write 는 Producer-Reviewer 로 tech-lead 가 reviewer + write subject → Supervisor 가 heartbeat / 실패 / 자기개선 감시 → 부서 외부와는 gateway 만 대화 (1 단 계층).` 이 조합이 본 정책의 기본형이다.

## 4. Gateway 책임 범위 (재정의)

`engineering-channel-router → tech-lead aggregator → 외부 회신` 으로 이어지는 흐름에서 **gateway 의 책임은 5 가지로 좁힌다**.

1. **Discord 메시지 수신 + intake** — 채널 / 스레드 / continuation 결정. (이 단계는 #25 와 가장 겹치므로, 본 정책은 책임 정의만 두고 코드는 #25 PR 가 잡는다.)
2. **routing 분류** — `decide_routing` 4 가지 (`join_existing_work` / `create_new_work` / `ask_for_clarification` / `append_context_only`). research-only 모드 자동 감지.
3. **role selection 호출** — `recommend_active_roles`. selector 자체는 일반 정책 엔진 — 본 정책은 selector 결과를 그대로 신뢰한다.
4. **fan-out / fan-in 조율** — 활성 역할에 `role_take` job dispatch, 결과는 `tech_lead_aggregator.aggregate_role_outputs` 로 한 번 정리.
5. **외부 회신 / 영속화 위임** — Discord, GitHub, Obsidian 으로 나가는 모든 텍스트 / 커밋 / PR / vault note 의 author / commenter / committer 는 tech-lead 1 명. gateway 자체는 이 발화의 **transport** 일 뿐 author 가 아니다.

다음 항목은 gateway 책임에서 **명시적으로 제외**한다.

- 단일 역할의 take 를 그대로 외부 회신에 통과시키기 (반드시 tech-lead aggregator 를 거친다).
- 사용자 승인 없이 코드 변경 / PR ready / vault canonical knowledge 저장 / GitHub merge / production deploy.
- 부서 외부 시스템 (planning-agent, future cto-agent, marketing-agent 등) 과 직접 대화 (반드시 부서 gateway → 외부 인터페이스 한 곳).

## 5. 역할별 책임 — 실행 주체 vs 분석 관점 분리 기준

본 정책은 두 단어를 분리해서 쓴다.

- **실행 주체 (write subject)**: 어떤 텍스트 / 코드 / 노트 / 외부 메시지가 누구의 이름으로 발화되는가.
- **분석 관점 (analysis lens)**: 어떤 기술 관점에서 의견을 보탰는가. 발화 주체와는 별개.

| 역할 | 실행 주체 권한 | 분석 관점 권한 | 비고 |
| --- | --- | --- | --- |
| `tech-lead` | **유일한 write 주체.** issue comment / PR body / vault note / Discord 회신 / commit 메시지 / approval 카드의 author. | 모든 역할의 take 를 종합. 자기 take 도 함께 작성. | 모든 외부 surface 의 1 인. |
| `backend-engineer` | **없음.** 자기 분석 결과를 직접 issue comment / PR / vault 에 쓰지 않는다. | 도메인 모델 / API 계약 / DB / 트랜잭션 / 보안. | take 는 `agent_ops_audit` + `role_take` 잡 결과로만 표현. |
| `frontend-engineer` | **없음.** | UI / 사용자 흐름 / 접근성 / 컴포넌트 구조 / 상태. | 동상. |
| `devops-engineer` | **없음.** | CI / 배포 / 런타임 / 시크릿 / 모니터링 / 롤백. | 동상. |
| `qa-engineer` | **없음.** | 인수 조건 / 회귀 / 테스트 우선순위. | 동상. |
| `ai-engineer` | **없음.** | LLM / RAG / agent runtime / evaluation / 비용. | 동상. |
| `product-designer` | **없음.** | 사용자 흐름 / UX copy / 디자인 시스템 / 정보 구조. | 동상. |

이 분리의 의미:

- 6 분석 관점은 자유롭게 **틀려도 된다**. tech-lead aggregator 가 충돌 / 의문문 / 사용자 결정 필요를 분리하므로, 분석 관점이 보수적으로 발화되어 외부에 직접 노출될 위험이 없다.
- 외부 surface 에서는 항상 "tech-lead 가 6 관점을 종합해 다음과 같이 정리했다" 는 단일 author tone. **GitHub 커밋의 commit author** 는 별개로 `owner-as-author` 정책 (`COMMIT_AUTHOR_POLICY_OWNER_AS_AUTHOR`) 을 따른다. tech-lead 는 commit 의 **committer** 로만 등장하고 author 자리는 사람이다.
- Yule 의 GitHub WorkOS 가 senior_triage 의 결과를 단일 PR 본문으로 집약하는 흐름은 본 정책의 자연스러운 확장 — `agents/github_workos/triage.py` 가 이미 primary_role / support_roles / excluded_roles 를 한 본문 안에 묶고 있다.

## 6. Orchestration Contract — Routing Matrix / Review Gate / Approval Gate

본 절은 어떤 surface 에서 어떤 review / approval gate 가 필수인지의 **계약** 이다.

### 6.1 Routing Matrix (입력 → 활성 역할 + write subject)

| 입력 신호 | selector source | 활성 역할 (분석 관점) | write subject | review gate | approval gate |
| --- | --- | --- | --- | --- | --- |
| user_explicit (역할 명시) | `user_explicit` | tech-lead + 명시된 역할 | tech-lead | tech-lead aggregator | code change 시 사용자 승인 카드 |
| 키워드 hit | `tech_lead_rule` | tech-lead + primary 역할 + reviewer 역할 | tech-lead | tech-lead aggregator | 동상 |
| 빈 prompt | `fallback (empty_prompt)` | tech-lead only | tech-lead | tech-lead 자체 | (없음 — 정보 부족 안내) |
| infra hint | `fallback (vague_infra)` | tech-lead + devops + backend | tech-lead | tech-lead aggregator | 동상 |
| ai/rag hint | `fallback (vague_ai_research)` | tech-lead + ai + backend | tech-lead | 동상 | 동상 |
| product hint | `fallback (vague_product)` | tech-lead + product + frontend | tech-lead | 동상 | 동상 |
| eng hint | `fallback (vague_engineering)` | tech-lead + backend + qa | tech-lead | 동상 | 동상 |
| 모두 미스 | `fallback (legacy_quartet)` | tech-lead + ai + backend + qa | tech-lead | 동상 | 동상 |
| 전체 팀 명시 | `user_all_team` | tech-lead + 6 역할 모두 | tech-lead | 동상 | 동상 |
| 코드 변경 시그널 | (위와 결합) | 위 + executor 추천 | tech-lead | tech-lead aggregator + executor pre-write 검토 | **사용자 승인 카드 필수.** approval 없이 push / ready PR 금지. |
| 라이브 배포 시그널 | (관련 없음) | 활성 안 함 | (없음 — gateway 거부) | — | **L4 — 본 정책 범위 밖. 사람이 직접 수행.** |

### 6.2 Review Gate

모든 외부 surface 텍스트는 다음 review gate 를 거친 뒤에만 외부로 나간다.

1. **Aggregator gate (필수)** — `tech_lead_aggregator.aggregate_role_outputs` 호출. 충돌 / 의문문 / 사용자 결정 필요 분리.
2. **Lifecycle gate (필수)** — `compute_report_status` 가 `interim` / `insufficient` / `ready` / `final` 분류. `interim` 이하면 final 회신 차단.
3. **Forbidden-actions gate (필수)** — RoleProfile 의 `forbidden_actions` 확인 후 위반 시 차단.
4. **Obsidian write gate (필수, vault write 한정)** — `can_write_obsidian_record`. `research_status != "ready"` 면 차단.
5. **GitHub permission gate (필수, GitHub write 한정)** — `agents.github_workos.policy.decide_permission`. L3 이상은 approval 없으면 deny, main / master / prod / release 직접 변경 deny.

### 6.3 Approval Gate

사용자 명시적 승인 카드가 필요한 surface 는 다음 5 종이다.

1. **코드 변경** — `coding_proposal` → `coding_job` 전환.
2. **canonical knowledge / decision-record vault save** — 기존 ObsidianWriterWorker `_APPROVAL_REQUIRED_KINDS` 정책 그대로.
3. **GitHub PR ready 전환 / push** — G2 senior_triage 의 `approval_required_actions` (BRANCH_PLAN / CODE_DRAFT_PLAN / TEST_PLAN / DRAFT_PR_PLAN / REAL_CODE_WRITE_REQUEST / PUSH_COMMIT / READY_PR).
4. **secret / 시크릿 변경** — 본 정책 범위 밖 (사람 직접 수행).
5. **production deploy / merge to main / force push** — 본 정책 범위 밖 (사람 직접 수행).

`engineering-knowledge` 노트 (G-engineering-intelligence) 는 L1 자동 저장 — quality gate 를 통과한 경우에 한해 사용자 승인 없이 저장. 단 GitHub sync 는 `pending_git_sync` plan 으로만 남고 직접 push 하지 않는다 (이미 G `engineering_intelligence/github_sync.py` 가 강제).

## 7. Progressive Disclosure Skill 구조

본 절은 #25 와 가장 겹치는 영역이다. 본 정책에서는 **policy 만** 명시하고, 실제 skill 파일 / hook 파일 / MCP wiring 은 #25 PR 가 잡는다 (선행 의존).

### 7.1 design intent

- skill / prompt 는 **3 단 disclosure** 로 제공한다.
  1. **L0 — overview**: tech-lead 가 자기 작업 범위를 결정할 때 한 줄 요약 + 입력/출력 계약 + activation 조건.
  2. **L1 — operating mode**: tech-lead 가 모드 (design / implementation / review / debugging / migration) 를 골랐을 때 mode 별 reasoning_flow + required_questions.
  3. **L2 — contract standards**: 결정에 필요한 deep contract (예: backend 의 7 standard, frontend 의 컴포넌트 contract). 토큰 비용이 큼 — 정말 필요할 때만 펼친다.
- 6 역할 분석 관점도 동일한 3 단 disclosure 를 따라 tech-lead aggregator 가 필요한 단까지만 펼친다. 본 정책은 disclosure 단계의 권위 정의만 하고, 단계별 toggle 신호는 후속 PR 에서 선택한다.

### 7.2 Yule 와의 매핑

- L0 = `RoleProfile.mission` + `activation_keywords` + `output_sections` (이미 존재).
- L1 = `manifest.json` 의 `operating_modes` + `reasoning_flow` + `required_questions` (backend 는 이미 v1 contract 로 노출, 다른 역할은 phase 1~7 점진).
- L2 = `manifest.json` 의 `contract_standards` (api / data / error / security / transaction / observability / test_handoff). 현재 backend 만 7 종 노출.

다른 역할로 동일 패턴을 확장하는 절차는 `role-profiles.md` §"다른 role 로 확장할 때 재사용할 패턴" 이 이미 7 단계로 정리해 두었다 — 본 정책은 그 절차를 **회사형 팀의 표준 onboarding** 으로 채택한다.

### 7.3 #25 선행 의존

skill 파일 / hook 파일 / MCP server wiring / Discord-side disclosure 토글은 #25 PR 가 잡는다. 본 정책은 다음 한 줄을 #25 의 acceptance 기준에 추가하기를 권고한다.

> "skill / prompt 는 3 단 disclosure (L0 overview / L1 operating mode / L2 contract standard) 로 제공되고, tech-lead aggregator 가 활성 역할의 disclosure 단계를 결정한다."

## 8. Harness Evolution — Feedback Loop

Harness 의 또 다른 핵심은 "팀이 자기 자신을 개선하는 retrospective loop" 다. Yule 은 이미 다음 두 메커니즘을 갖추고 있다.

1. **self-improvement signals** (M12 `agents/lifecycle/self_improvement.py`) — failed_retryable / duplicate_topic_approval / stale_heartbeat / empty_knowledge_note 검출 + `plan_self_improvement_proposal` → ObsidianWriteRequest L2 자동 저장.
2. **engineering-intelligence collector** (위 G-engineering-intelligence 패키지) — 각 역할이 자기 분야 공식 문서 변화를 5 개 / 일 까지 수집해 RAG/CAG 학습 surface 로 누적.

본 정책은 이 두 surface 를 **팀 retrospective 의 두 축** 으로 정의한다.

- self-improvement = **부서 내부 회귀**. 운영 중 발견한 실패 패턴을 자기 운영 정책으로 환류.
- engineering-intelligence = **부서 외부 학습**. 외부 표준 / 공식 문서 변경을 RAG/CAG 에 적재해 다음 작업의 판단 근거로 사용.

후속 작업으로 둘을 잇는 surface 가 필요하다 — 본 정책 §11 next action 참조.

## 9. SPOF / 리스크

- **tech-lead degrade**: 모든 외부 발화의 단일 author 이므로 tech-lead 백엔드 (Claude / Codex / Ollama / deterministic) 가 모두 fail 하면 lifecycle 이 `interim_report` 에서 멈춘다. 운영자에게 즉시 알림 (`#봇-상태`) 가 이미 구현됨. 본 정책은 이를 **수용**: 정확성 > 가용성.
- **fan-in 누락**: 6 관점 중 하나가 fail_terminal 이어도 fan-in 은 진행되어야 한다 (이미 `degrade 규칙` §5 가 정의). 본 정책은 fail_terminal 한 역할의 의견을 **누락 명시 ("X 역할 의견 없음 — fallback 사유 표기")** 로 외부 회신에 노출하기로 한다.
- **routing matrix 와 selector 의 불일치**: selector 가 새 fallback 정책을 추가하면 본 §6.1 표가 즉시 stale. 단위 테스트로 두 표의 정합성을 확인 (§10 self-check 참조).
- **#25 와 중복 변경**: 본 정책은 코드 변경을 0 으로 두어 충돌 표면을 최소화. 후속 PR 가 coordinator-agent / orchestrator-agent / hook 시스템을 구현할 때 본 정책의 §6 / §7 을 입력으로 받기를 권고.

## 10. Self-check / 검증

본 정책은 코드 변경을 동반하지 않으므로 검증은 **문서 정합성 + 인접 회귀 무결성** 으로 수행한다.

1. **policy doc 존재 + 필수 섹션 보유** — `tests/engineering/test_team_architecture_patterns_doc.py` 가 본 문서의 §1~§11 헤더를 검사.
2. **인접 회귀 무결성** — `python3 -m unittest discover -s tests -t .` 가 0 FAIL 인지 확인. 본 정책 추가가 import / lint / 인덱싱 surface 를 깨지 않는지 안전망.
3. **policy → code 매핑 확인** — `role_profiles_data.py` 의 7 역할 + `role_selection.py` 의 fallback 6 종이 본 §6.1 routing matrix 의 8 행과 1:1 대응하는지 매뉴얼 검토 (회귀가 발생하면 본 문서 stale 로 간주).

## 11. 다음 액션

본 정책이 합의되면 다음 작업이 후속 PR 에서 진행된다.

| 액션 | 의존 | 우선순위 |
| --- | --- | --- |
| `run_supervisor_watch_loop` 의 `self_improvement_*` 3 인자를 `run_service.py` 에서 채워, supervisor 가 self-improvement 를 production 에서 실제 tick 하게 한다. | M13 readiness §2 G-M12-01 | 높음 |
| coordinator agent / orchestrator agent / sub-agent runtime 도입 (Hierarchical Delegation 2 단 확장) | #25 선행 의존 | 중간 |
| skill / hook / MCP wiring 으로 progressive disclosure 실 구현 | #25 선행 의존 | 중간 |
| engineering-intelligence 의 daily 결과를 self-improvement detector 의 `empty_knowledge_note` 신호와 연결하는 retrospective loop | M12 + G-engineering-intelligence | 낮음 |
| frontend / devops / qa / ai / product-designer 역할의 `manifest.json` 을 backend v1 패턴으로 phase 1~7 점진 강화 (operating_modes / reasoning_flow / contract_standards) | role-profiles.md §"다른 role 로 확장할 때 재사용할 패턴" | 낮음 |
| 본 정책 §6.1 routing matrix 와 `role_selection.py` 의 fallback 정책 사이의 일관성을 자동 점검하는 단위 테스트 | 본 정책 통과 후 | 낮음 |

## 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-08 | issue #48 — Harness 6 패턴 매핑 + Yule gateway 기본 패턴 조합 + tech-lead 단일 write 주체 모델 + orchestration contract (routing matrix / review gate / approval gate) + progressive disclosure skill 정책 + Harness evolution feedback loop 정의. |
