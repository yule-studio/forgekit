---
title: "ECC foundation 도입 결정 — Yule 4-layer + research-first 게이트"
kind: decision
session_id: issue-25-ecc
project: yule-studio-agent
created_at: 2026-05-08T00:00:00+09:00
issue: https://github.com/yule-studio/yule-studio-agent/issues/25
agent: engineering-agent/tech-lead
status: decided
related:
  - ../research/2026-05-08_research_ecc-foundation.md
  - ../task-logs/2026-05-08_task-log_25-ecc.md
  - ../../../../../../policies/runtime/agents/engineering-agent/ecc-foundation.md
tags:
  - decision
  - foundation
  - ecc
---

# 목표

ECC (`affaan-m/everything-claude-code`) 의 외부 layer 패턴을 Yule 의 회사형 구조 위에 흡수할지를 *layer 단위로 결정* 한다. 본 노트는 **결정 그 자체** — 분석 raw 는 research note 가, 정책 본문은 `policies/runtime/agents/engineering-agent/ecc-foundation.md` 가 책임진다.

# 현재 Yule 기준선

이미 갖춘 회사형 구조: 13 단계 lifecycle / role contract-v1 / autonomy_policy L0~L4 / agent_ops_audit / Obsidian export contract v0 / GitHub WorkOS G1~G6 / self_improvement signal detector. 외부에서 정책을 *얹어 변경* 할 수 있는 markdown layer 만 비어 있는 상태.

# 참고한 외부 레퍼런스

[../research/2026-05-08_research_ecc-foundation.md](../research/2026-05-08_research_ecc-foundation.md) — ECC 의 4 layer 실관찰 + 강성 GateGuard + multi-harness 구조.

# 결정

| ID | 영역 | 결정 | 근거 |
| --- | --- | --- | --- |
| D-25-1 | `agents/engineering-agent/skills/` 디렉터리 신설 | **도입** (foundation 단계) | 가장 약한 영역. ECC 의 `## Trigger`/`## Workflow`/`## Decision Matrix`/`## How to Use` 4 섹션 구조 + Yule 의 frontmatter (id / owner_role / autonomy_level / IO contract) 결합. |
| D-25-2 | Skill 단위 = 디렉터리 vs 단일 파일 | **단일 파일** (`<id>.md`) 사용 | Yule 정책 markdown 의 기존 패턴 일관. asset 동반이 필요해지면 디렉터리로 전환 (후속 결정). |
| D-25-3 | `agents/engineering-agent/hooks/` 디렉터리 신설 | **도입** (foundation 단계, markdown spec 만) | 13 lifecycle stage × {pre, post} × {advisory, blocking} 의 명시 점. ECC 의 6 native 이벤트 그대로 도입은 거부 — Yule 의 lifecycle 단계 명시가 더 정확. |
| D-25-4 | `hooks.json` registry + Node script | **거부 (현 단계)** | 본 PR 은 markdown spec 까지만. JSON registry 와 dispatcher 는 후속 PR 에서 loader 와 함께. |
| D-25-5 | `agents/engineering-agent/commands/` 디렉터리 신설 | **도입** (foundation 단계) | Yule 의 기존 slash / CLI 와 1:1 매핑 가능한 markdown 등록 layer. `surface` 필드로 분기. |
| D-25-6 | Command = "행동 명세 markdown" (dispatcher 없음) | **도입** | ECC 의 primitive 패턴 그대로. dispatcher 자동화는 후속 PR — 본 PR 은 정책·매핑까지만. |
| D-25-7 | research-first 강성 게이트 (코드 enforcement) | **명시화 (코드는 그대로)** | Yule 은 이미 `compute_lifecycle_status` 가 research_status 를 강제. 본 PR §3.1 표가 정책 명시화. ECC 의 GateGuard 류 hook 은 후속 PR 에서 markdown hook + autonomy_policy action 으로 흡수. |
| D-25-8 | tech-lead = 모든 GitHub WorkOS write 의 단일 actor | **도입 (정책 강제)** | 본 issue 의 명시 요구. PR body / commit author / issue comment 의 actor 라벨 = `engineering-agent/tech-lead`, git committer = `yule-studio-engineering-agent[bot]`. backend / frontend / 등은 *분석 입력* 으로만 인용. |
| D-25-9 | Multi-harness 어댑터 (`.claude/`, `.codex/`, ...) | **거부** | Yule 은 통합 런타임 (Discord + CLI + GitHub App). harness 분기 의미 없음. |
| D-25-10 | ECC 의 `SOUL.md` / `WORKING-CONTEXT.md` / `RULES.md` triad | **거부** | 기존 `policies/runtime/...` + 각 role 의 `CLAUDE.md` + `obsidian-memory.md` 가 동등. 추가는 인플레이션. |
| D-25-11 | `mcp-configs/` 디렉터리 + MCP server registry | **거부 (본 PR)** / **후속 PR 검토** | 외부 통합은 별도 보안 검토 필요. 본 PR 은 디렉터리도 만들지 않음. |
| D-25-12 | Continuous-learning hook (모든 tool call 캡처) | **후속 PR** | Yule 의 `agent_ops_audit` 가 부분 동등. dispatcher 합류 시 통합. |
| D-25-13 | Config-protection hook (lint config 편집 거부) | **후속 PR** | autonomy_policy 의 새 action_id 로 자연 흡수 가능. 본 PR 범위 밖. |
| D-25-14 | Rust `ecc2/` control-plane | **거부** | Yule 은 Python 단일 런타임. 언어 추가는 별도 큰 결정. |

## 결정 요약 — 한 표

| layer | 본 PR 결정 | 후속 PR 검토 |
| --- | --- | --- |
| `agents/` (기존) | 변경 없음. agent.json 에 optional `skills:` `hooks:` `commands:` 선언 필드만 추가. | — |
| `skills/` (신규) | foundation directory + README + 1~2 reference markdown | loader / dispatcher / runtime wiring |
| `hooks/` (신규) | foundation directory + README + 1~2 reference markdown | `hooks.json` registry / dispatcher / autonomy_policy 통합 |
| `commands/` (신규) | foundation directory + README + 1~2 reference markdown | slash command / CLI 자동 등록 |
| `mcp-configs/` | **본 PR 비도입** | 외부 통합 보안 검토 후 별도 PR |
| research-first 명시 게이트 | 정책 본문 §3.1 로 명시 | GateGuard 류 markdown hook + autonomy_policy action |
| tech-lead orchestration 강화 | 정책 §4 로 명시 (write actor 강제) | runtime 레이어에서 actor 라벨 자동 stamp |

# 도입한 부분 (본 PR 이 land 하는 것)

1. `policies/runtime/agents/engineering-agent/ecc-foundation.md` — 정책 본문.
2. `docs/agent-company-ecc.md` — 운영자용 비교 매트릭스 + 통합 안내.
3. `agents/engineering-agent/{skills,hooks,commands}/README.md` — 각 layer 의 작성 가이드.
4. `agents/engineering-agent/skills/research-collect.md` — reference skill (분석된 ECC `search-first` 와 Yule 의 `auto_collect_or_request_more_input` 매핑).
5. `agents/engineering-agent/hooks/research-first-gate.md` — reference hook (research_status 강제 정책 점).
6. `agents/engineering-agent/commands/engineer-show.md` — reference command (기존 `/engineer_show` slash 의 markdown 등록).
7. `notes/vault-mirror/10-projects/yule-studio-agent/{task-logs,research,decisions}/2026-05-08_*` — 본 작업의 Obsidian mirror.

# 보류 / 비도입 부분 (본 PR 이 닫지 않는 것)

[ecc-foundation.md §6 후속 PR 표](../../../../../../policies/runtime/agents/engineering-agent/ecc-foundation.md) 그대로:

- skill / hook / command markdown loader + dispatcher 코드
- `hooks.json` runtime registry
- `mcp-configs/` 표준 wiring
- Discord slash command markdown 자동 등록
- LLM runner 의 skill 자동 호출
- autonomy_policy / agent_ops_audit 의 hook 통합
- ECC 의 GateGuard 류 강성 hook (config-protection / continuous-learning)

# 왜 시니어 개발팀형 회사 구현에 필요한가

본 결정은 *외부 변경 layer 의 표준화* 다. 시니어 팀이 운영 중 새 도메인을 도입할 때 다음이 가능해진다:

1. 새 skill 추가 = markdown 1 파일 (코드 없음)
2. 새 정책 점 추가 = hook markdown 1 파일 (lifecycle 단계만 지정)
3. 새 entry point 추가 = command markdown 1 파일 (slash / CLI 매핑)

이는 ECC 가 "한 사람이 도구 더미" → "역할 + skill + hook 으로 자동화" 로 전환한 사례를 *회사형 구조에 맞게* 재구성한 것이다. Yule 은 이미 회사형 lifecycle / role contract 가 있으므로 ECC 를 그대로 복사하는 게 아니라 **diff 만 흡수** 한다.

# 구현 위치 / 설계 위치

[ecc-foundation.md §5 디렉터리 표](../../../../../../policies/runtime/agents/engineering-agent/ecc-foundation.md) 그대로.

# 리스크와 다음 액션

리스크:

- **dispatcher 미존재 → "정의만 있고 동작 안 함"** 오해. 각 markdown 의 README 에 *현재 단계는 정의 layer, runtime wiring 은 후속 PR* 표기.
- **Markdown 인플레이션** — `yule memory reindex` 가 새 정책을 SOURCE_POLICY 로 픽업. 본 PR 의 신규 markdown 은 4~6 개 (정책 1 + READMEs 3 + samples 2~3) 로 제한.
- **#48 / #59 와 충돌** — 본 PR 은 add-only. 변경 0 인 기존 파일을 보호한다.

다음 액션 (본 세션 안):

1. `docs/agent-company-ecc.md` (운영자 비교 매트릭스) 작성.
2. `agents/engineering-agent/{skills,hooks,commands}/README.md` + 각 1 sample manifest.
3. agent.json 에 optional `skills:` `hooks:` `commands:` 선언 필드 (본 PR 은 *선언만 허용*, 미선언 시 비파괴).
4. `python3 -m unittest discover -s tests -t .` 회귀 0 확인.
5. `git diff --stat` 으로 코드 변경 0 확인.
6. issue #25 progress comment.
7. draft PR 생성 (squash merge / do-not-merge 정책).

## 관련 문서

- [[CLAUDE]]
- [[research-ecc-foundation]]
- [[task-log-25-ecc]]
- [[research-engineering-agent-governance-synthesis-issue-69]]
- [[decision-engineering-agent-authoring-policy-issue-69]]
- [[task-log-governance-integration-issue-69]]
