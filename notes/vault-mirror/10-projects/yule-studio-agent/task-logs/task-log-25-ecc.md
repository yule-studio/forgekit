---
title: "[#25] everything-claude-code 기반 Yule 강화 — 작업 로그"
kind: task-log
session_id: issue-25-ecc
project: yule-studio-agent
created_at: 2026-05-08T00:00:00+09:00
status: in-progress
issue: https://github.com/yule-studio/yule-studio-agent/issues/25
branch: feature/agent-company-ecc-25
worktree: /Users/masterway/local-dev/yule-studio-agent-worktrees/25-ecc
agent: engineering-agent/tech-lead
references:
  - https://github.com/affaan-m/everything-claude-code
tags:
  - task-log
  - foundation
  - ecc
  - agent-platform
---

> **본 노트의 모든 발화 / 결정 / 진행 기록 주체는 `engineering-agent / tech-lead` 단일.** 다른 역할(backend / frontend / devops / qa / ai-engineer / product-designer)은 분석 입력으로만 차용한다.

# 목표

ECC (`affaan-m/everything-claude-code`) 의 `agents` / `skills` / `hooks` / `mcp-configs` / `commands` / research-first 운영 구조를 분석하고, 현재 Yule (Discord gateway → engineering-agent → Obsidian → GitHub App WorkOS) 흐름에 자연스럽게 흡수 가능한 **foundation** 레이어를 설계·문서화·가능 범위 내에서 구현한다.

목표 분해:

- 단순 디렉터리 복사가 아니라 **diff 기반 흡수** — 현재 Yule 에 이미 있는 / 없는 / 약한 영역을 분리해 도입 우선순위를 정한다.
- 다른 이슈 (#48 e2e harness, #59 hermes) 가 같은 foundation 위에 올라탈 수 있게 **공통 기반** 으로 설계한다.
- 본 세션은 **foundation 정의와 정책화** 가 1순위. 실제 dispatcher / runtime 코드는 후속 PR.

# 현재 Yule 기준선 (2026-05-08 시점)

### 이미 갖춘 것

| 영역 | 상태 | 위치 |
| --- | --- | --- |
| Discord gateway + 7 멤버 봇 | ✅ 운영 중 | `src/yule_orchestrator/discord/`, `agents/engineering-agent/<role>/` |
| Role profile (mission / contract / activation_keywords / fallback_policy) | ✅ contract-v1 단계 | `agents/role_profiles_data.py`, `policies/runtime/agents/engineering-agent/role-profiles.md` |
| Lifecycle MVP (intake → triage → role_selection → research → deliberation → synthesis → work_report → obsidian) | ✅ 13 단계 정의 + persistence | `policies/runtime/agents/engineering-agent/lifecycle-mvp.md` |
| Obsidian export + sync (kind: research / decision / reference / task-log / meeting / report / knowledge) | ✅ contract v0 + CLI | `agents/obsidian_export.py`, `cli/obsidian.py`, `policies/runtime/agents/engineering-agent/obsidian-memory.md` |
| GitHub App WorkOS (G1~G6) — config / auth / triage / executor / Discord bridge / e2e harness / CLI | ✅ live smoke-pr 검증 통과 | `src/yule_orchestrator/github_app/`, `agents/github_workos/`, `cli/github_workos.py` |
| Autonomy policy L0~L4 + agent_ops_audit | ✅ M10a~M10c | `agents/lifecycle/autonomy_policy.py`, `agent_ops_log.py`, `self_improvement.py` |
| Self-improvement signal detector skeleton | ✅ M10c | `agents/lifecycle/self_improvement.py` |
| Research-only 게이트 (Phase 2 stab) | ✅ keyword 기반 분기 | router 안의 `lifecycle_mode = research_only` |
| Reference budget / multi-provider research | ✅ tier 4종 | `policies/runtime/agents/engineering-agent/research-budget.md` |

### 약한 영역 (정의는 있지만 명시 단위가 없음)

- **Skill 단위** — 역할별 capability 가 contract 안에 묻혀 있고, "이 역할이 무엇을 할 수 있는가" 를 외부에서 점검할 단일 단위가 없다. ECC 의 `skills/` 와 매핑 가능.
- **Hook 표면** — 작업 lifecycle 의 13 단계 사이 사이에 외부 확장점이 없다. 모든 확장은 코드 수정으로만 가능.
- **Slash command registry** — Discord slash 는 `bot.py` 안에 하드코딩. 외부 markdown 등록 layer 없음.
- **MCP config** — 현재 외부 통합은 Discord / GitHub App / Obsidian / Tavily / Brave / Naver 6 종 직접 클라이언트. MCP 표준 wiring 은 없음.

### 명시적 비도입 영역 (이번 세션에서)

- 실제 LLM runner 호출 (별도 PR — M11 진행 중)
- vault git push / merge 자동화
- production deploy 자동화

# 참고한 외부 레퍼런스

- ECC repo: https://github.com/affaan-m/everything-claude-code (분석은 별도 sub-agent 가 진행 중, 결과는 research note 에 기록)
- Yule 의 자체 lifecycle / role-profiles / autonomy / GitHub WorkOS 운영 정책 (위 표 참조)

# 진행 단계 / 결정

| 시점 | 단계 | 상세 |
| --- | --- | --- |
| 2026-05-08 kickoff | branch + worktree 생성 | `feature/agent-company-ecc-25` @ `../yule-studio-agent-worktrees/25-ecc` |
| 2026-05-08 kickoff | 로컬 정책 문서 8 종 + issue #25 본문 + ISSUE_TEMPLATE 통독 완료 | 본 노트의 "현재 Yule 기준선" 표가 그 결과물 |
| 2026-05-08 kickoff | issue #25 kickoff 코멘트 게시 | tech-lead 가 단일 주체로 진입한다는 선언 |
| 2026-05-08 kickoff | ECC 분석 sub-agent dispatch | 결과는 research note 로 흡수 |

# 도입한 부분 / 보류한 부분

(본 task-log 는 진행 중 — 최종 progress comment 시점에 갱신)

도입 후보 (분석 결과에 따라 결정):

1. `skills/` markdown 디렉터리 (foundation) — Yule 에서 가장 약한 영역.
2. `hooks/` 정책 문서 + 13 단계 lifecycle 의 명시적 hook 점 정의.
3. `commands/` 디렉터리 (slash + CLI 매핑) — 운영자가 manual 로 호출 가능한 entry point 표.
4. research-first 운영 정책 문서.
5. `mcp-configs/` 정책 placeholder — wiring 은 후속 PR.

보류 후보:

- ECC 의 dispatcher / runtime python 그대로 import — Yule 의 SQLite + session_extra 모델과 충돌 가능. 정책 흡수만 진행.

# 왜 시니어 개발팀 회사형 구현에 필요한가

ECC 는 "Claude 한 명이 도구 더미를 갖고 매번 즉흥 작업하는" 패턴을 "역할별 markdown 정의 + skill 호출 + hook 으로 lifecycle 자동화" 패턴으로 전환한 사례다. Yule 은 이미 *역할은 있고 lifecycle 도 있는* 회사형 구조이지만, 외부에서 정책을 **얹어 변경** 할 수 있는 layer 가 없다 — `skills/` `hooks/` `commands/` markdown 트리는 그 외부 변경 layer 를 표준화한다. 시니어 팀이 새 도메인을 도입할 때 코드 수정 없이 "이 역할이 이 skill 도 가능합니다" 만 추가하는 운영 모델이 가능해진다.

# 구현 위치 / 설계 위치

- 정책: `policies/runtime/agents/engineering-agent/{ecc-foundation,research-first,skills,hooks,commands}.md` 후보
- 비교 매트릭스: `docs/agent-company-ecc.md` 후보
- Obsidian mirror notes: `notes/vault-mirror/10-projects/yule-studio-agent/{task-logs,research,decisions}/`
- 코드 (foundation 단계만): 디렉터리 골격 (`agents/skills/`, `agents/hooks/`, `agents/commands/`) + 로더 / 등록기 (있으면 후속 PR 의 첫 줄에 import 가능한 정도)

# 리스크와 다음 액션

리스크:

- **공통 기반 변경 충돌** — #48 / #59 가 같은 worktree 패밀리에서 동시에 진행될 가능성. 본 PR 은 추가 only 로 좁히고 기존 모듈은 손대지 않는다.
- **정책 인플레이션** — 새 markdown 트리가 늘어나면 `yule memory reindex` 가 모든 정책을 SOURCE_POLICY 로 잡는다. retrieval 우선순위에 영향 가능 — research note 에 영향 분석.
- **secret / token 노출** — kickoff 부터 종료까지 production secret 출력 금지 (이미 hard rail). 코드 scaffolding 이 env 를 읽으면 redact_secret_like 적용.

다음 액션:

1. ECC sub-agent 결과 수신 → research note 작성.
2. 비교 매트릭스 + 도입/보류 결정 (decision note).
3. `docs/agent-company-ecc.md` (운영자용 통합 문서).
4. `policies/runtime/agents/engineering-agent/ecc-foundation.md` (정책 본문).
5. foundation 디렉터리 골격 (`agents/skills/`, `agents/hooks/`, `agents/commands/`) — README + 1~2 개의 reference manifest.
6. 테스트 (가능 범위) + 진행 코멘트 + draft PR.

---

> 본 노트는 자동 작성된 task-log 가 아니라 tech-lead 가 직접 갱신하는 working document. 변경 시 `created_at` 보존 + 본문 시간 stamp 추가.

## 관련 문서

- [[CLAUDE]]
- [[2026-05-08_research_ecc-foundation]]
- [[2026-05-08_decision_ecc-foundation]]
- [[2026-05-08_issue-69-research-engineering-agent-governance-synthesis]]
- [[2026-05-08_issue-69-decision-engineering-agent-authoring-policy]]
- [[2026-05-08_issue-69-task-log-governance-integration]]
