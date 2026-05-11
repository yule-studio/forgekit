---
title: "ECC (everything-claude-code) 구조 분석 — Yule 흡수용 diff 리서치"
kind: research
session_id: issue-25-ecc
project: yule-studio-agent
created_at: 2026-05-08T00:00:00+09:00
issue: https://github.com/yule-studio/yule-studio-agent/issues/25
references:
  - https://github.com/affaan-m/everything-claude-code
  - https://github.com/affaan-m/everything-claude-code/blob/main/skills/search-first/SKILL.md
  - https://github.com/affaan-m/everything-claude-code/blob/main/hooks/hooks.json
  - https://github.com/affaan-m/everything-claude-code/blob/main/agents/architect.md
  - https://github.com/affaan-m/everything-claude-code/blob/main/mcp-configs/mcp-servers.json
agent: engineering-agent/tech-lead
tags:
  - research
  - foundation
  - ecc
  - agent-platform
---

# 목표

ECC 의 4 외부 layer (`agents/` `skills/` `hooks/` `commands/`) + research-first 운영 방식을 분석해, 현재 Yule 이 이미 가진 구조와 *diff* 로 비교한다. 분석 결과는 `policies/runtime/agents/engineering-agent/ecc-foundation.md` 의 정책 결정 근거로 인용된다.

# 현재 Yule 기준선

[task-log 노트의 §"현재 Yule 기준선"](../task-logs/2026-05-08_task-log_25-ecc.md) 표 그대로. 핵심 요약:

- `agents/<dept>/<role>/agent.json` + role contract-v1
- 13 단계 lifecycle (intake → … → obsidian_recorded)
- session.extra 에 모든 lifecycle 상태 영속
- autonomy_policy L0~L4 + agent_ops_audit
- Obsidian export contract v0 + 7 가지 kind
- GitHub WorkOS G1~G6 + senior_triage
- self_improvement 신호 감지 skeleton (M10c)

# 참고한 외부 레퍼런스 — ECC 실관찰

ECC repo 의 top-level (분석 시점 기준 13 dir):

| dir | 한 줄 설명 |
| --- | --- |
| `agents/` | 48 개 markdown sub-agent (YAML frontmatter + body prompt) |
| `skills/` | 182 개 reusable workflow module — 각 디렉터리에 `SKILL.md` |
| `commands/` | slash command markdown (`description` 단일 frontmatter + 행동 명세) |
| `hooks/` | `hooks.json` registry + Node 스크립트 (`README.md` 포함) |
| `mcp-configs/` | `mcp-servers.json` 한 파일 (server registry) |
| `rules/` | language-agnostic / language-specific always-on guideline |
| `contexts/` | dynamic system-prompt injection snippet |
| `scripts/` | Node hook 구현체 + utilities |
| `manifests/`, `schemas/` | 플러그인 / 마켓플레이스 메타 + JSON 스키마 |
| `plugins/`, `legacy-command-shims/` | 플러그인 패키징 + 폐기 alias |
| `ecc2/` | Rust control-plane 프로토타입 (alpha) |
| `research/` | 리서치 산출물 저장소 (방법론 dir 아님) |
| `examples/`, `tests/`, `docs/`, `assets/` | 샘플 / 테스트 / 문서 / 미디어 |

여러 harness 어댑터 (`.claude/` `.codex/` `.cursor/` `.gemini/` `.kiro/` `.opencode/` `.trae/` `.codebuddy/`) 와 cross-harness root file (`CLAUDE.md` `AGENTS.md` `RULES.md` `SOUL.md` `WORKING-CONTEXT.md` `COMMANDS-QUICK-REF.md` `EVALUATION.md` `agent.yaml` `.mcp.json`) 가 같은 코어를 8 + 개 런타임에 노출.

## A. agents/ — single markdown per agent

`<name>.md`. YAML frontmatter:

```yaml
---
name: architect
description: |
  Use PROACTIVELY when the user requests system design ...
tools: ["Read", "Grep", "Glob"]
model: opus
---
```

본문은 Role / Process (numbered phases) / Principles / Checklists / Red Flags 의 5 섹션 시스템 프롬프트. `Task(subagent_type=...)` 로 호출되거나 orchestrator 가 description 키워드로 auto-route.

## B. skills/ — directory + SKILL.md

`skills/<id>/SKILL.md` (디렉터리 단위!). frontmatter 는 `name` / `description` / `origin` 3 필드만. 본문은 고정 4 섹션:

- `## Trigger` — bullet list of "Use this skill when..."
- `## Workflow` — numbered phases (ASCII flowchart 가 자주 등장)
- `## Decision Matrix` — signal → action 표
- `## How to Use` — Quick Mode (inline) vs Full Mode (subagent 위임)

특이점: IO schema 미정의. 입출력은 free-form prompt. trigger 는 코드 enforcement 가 아니라 markdown prose. **agent-agnostic**: 어떤 agent 도 같은 skill 을 호출 가능.

## C. hooks/ — registry + scripts

`hooks.json` 한 파일이 source of truth. 항목 구조:

```json
{
  "id": "pre:edit-write:gateguard-fact-force",
  "matcher": "Edit|Write|MultiEdit",
  "hooks": [{"type": "command", "command": "node scripts/.../gateguard.js"}],
  "timeout": 30,
  "async": false
}
```

이벤트 6 종 (Claude Code native 이벤트와 동일):

- `PreToolUse` (block 가능 — exit code 2)
- `PostToolUse` (block 불가)
- `Stop` (세션 종료)
- `SessionStart` (직전 컨텍스트 로드)
- `SessionEnd`
- `PreCompact` (컨텍스트 축약 직전)

특이 hook 들:

- `pre:edit-write:gateguard-fact-force` — file 별 첫 Edit/Write 차단, "investigation" (importer / schema / user instruction) 이 기록되어야 통과. **research-before-write 의 강성 게이트.**
- `pre:config-protection` — lint / formatter config 편집 거부. "agent 가 코드를 고치지 config 를 약화시키지 못하게" 정책.
- `pre:bash:dispatcher` — quality / tmux / push / GateGuard preflight 통합.
- `pre:mcp-health-check` — MCP 서버 건강 점검.
- `pre:governance-capture` — secret / policy / approval 이벤트 (opt-in via `ECC_GOVERNANCE_CAPTURE=1`).
- `pre:observe:continuous-learning` — async 모든 tool call 관찰 → pattern extraction 입력.
- Stop 시: `pattern-extraction`, `cost-tracker`, `desktop-notify`, `console.log audit`.
- SessionStart 시: 이전 컨텍스트 로드.
- PreCompact 시: state 저장.

설치 시 `hooks.json` 의 절대경로는 plugin installer 가 사용자 `~/.claude` 기반으로 rewrite — checked-in JSON 은 템플릿이지 런타임 그대로가 아님.

## D. commands/ — primitive prompt markdown

`<name>.md`. frontmatter `description` 단일. 본문은 *행동 명세*:

- When to Use
- Usage examples
- numbered procedure
- Edge Cases
- Example outputs

특이점: command 는 *현재 agent 의 system prompt 에 prose 를 주입* 하는 primitive 다. subagent 호출 wrapper 가 아니다. 본문에서 "필요하면 researcher agent 를 호출하라" 식으로 권유는 하지만 dispatch 가 코드로 강제되지 않는다.

## E. mcp-configs/ — single JSON

`mcp-servers.json` 한 파일. 등록된 server 종류:

- `jira` (Atlassian — uvx mcp-atlassian)
- `github`
- `firecrawl` (web scraping)
- `supabase`
- `memory` (basic KV)
- `omega-memory` (semantic search + multi-agent + KG)
- `sequential-thinking`
- `vercel`, `railway`
- Cloudflare 4 endpoint (`cloudflare-docs`, `-workers-builds`, `-workers-bindings`, `-observability`)
- `clickhouse`
- `exa-web-search` (neural search)
- `context7` (라이브 라이브러리 / docs lookup)
- `magic` (UI components)
- `filesystem`
- `playwright`, `browserbase`
- `fal-ai` (멀티미디어 생성)

총 ~17 서버. fine-grained capability split 이 두드러진다 (Cloudflare 만 4 endpoint).

## F. research-first 운영 방식

**전용 정책 doc 없음.** 두 가지 layer 로 강제:

- 연성: `skills/search-first/SKILL.md` ("Research-before-coding workflow")
- 강성: `pre:edit-write:gateguard-fact-force` hook (file 별 첫 write 를 블록)

`SOUL.md` 는 "test-before-trust" / "deliberate phase planning" 표현으로 철학 수준에서만 강조.

# 도입한 부분

[ecc-foundation.md §2 / §3](../../../../policies/runtime/agents/engineering-agent/ecc-foundation.md) 의 4 layer 정의 + research-first 게이트 + tech-lead orchestration 보강. 핵심 요약:

| ECC 패턴 | Yule 도입 형태 |
| --- | --- |
| skill 의 4 섹션 본문 | 그대로 도입 (`## Trigger` / `## Workflow` / `## Decision Matrix` / `## How to Use`). frontmatter 는 Yule 만의 IO contract 추가 |
| hook 의 lifecycle 분기 | Yule 의 13 stage × {pre, post} 로 재매핑. ECC 의 6 이벤트 대신 Yule lifecycle 의 명시 단계 사용 |
| command 의 primitive markdown | 도입. 단 `surface: discord-slash | cli | both` + `slash:` / `cli:` 명시 |
| research-first 강성 게이트 | 정책 §3.1 표로 격상. 코드 enforcement 는 기존 `compute_lifecycle_status` + `can_write_obsidian_record` 가 이미 수행 |

# 보류 / 비도입 부분

| ECC 패턴 | 결정 | 이유 |
| --- | --- | --- |
| Skill = 디렉터리 | 보류 (단일 파일 사용) | Yule 의 기존 정책 markdown 컨벤션 일관성. asset 동반이 필요해지면 디렉터리로 전환. |
| `hooks.json` 단일 registry + Node script | 보류 | 본 PR 은 markdown 단위만 정의. JSON registry / dispatcher 는 후속 PR (loader 와 함께). |
| Multi-harness 어댑터 (.claude / .codex / ...) | 비도입 | Yule 은 Discord + CLI + GitHub App 의 통합 런타임. harness 분기 의미 없음. |
| `SOUL.md` triad | 비도입 | 기존 `policies/runtime/...` + `agents/.../CLAUDE.md` + `obsidian-memory.md` 가 동등 역할. |
| 17 개 MCP server registry | 후속 PR | 외부 통합은 별도 보안 검토. 본 PR 은 `mcp-configs/` 디렉터리도 만들지 않는다. |
| Continuous-learning hook (모든 tool call 캡처) | 후속 PR | Yule 의 `agent_ops_audit` 가 부분 동등. hook dispatcher 합쳐 통합. |
| Config-protection hook | 후속 PR | autonomy_policy 의 새 action 으로 깔끔하게 흡수 가능. 본 PR 범위 밖. |
| Rust `ecc2/` 프로토타입 | 비도입 | Python 단일 런타임 정책. 언어 추가는 별도 결정. |

# 왜 시니어 개발팀형 회사 구현에 필요한가

ECC 가 보여준 본질적 가치는 **"agent 의 작업 단위 (skill) / 정책 점 (hook) / 외부 호출 entry (command) 를 코드 밖 markdown 으로 분리"** 했다는 것이다. 이는 회사형 구조에서:

1. **새 도메인 추가 비용을 정책 변경으로 한정** — Yule 의 활성 키워드 매트릭스와 같은 효과가 더 큰 단위 (skill = 1 파일) 로 확장.
2. **운영자 / 비코드 인력의 진입점을 markdown 으로** — 정책 작성·갱신을 권한 분리한 사람도 가능.
3. **테스트 가능성** — markdown 의 frontmatter 가 schema 검증 가능하면 정책 회귀를 자동화.
4. **GateGuard 류 hook 의 hard rail** — autonomy_policy 가 advisory 인 영역을 *코드 없이* hook 정책으로 강성화 가능.

Yule 이 이미 회사형 lifecycle 13 stage + role contract-v1 까지 발전한 만큼, 본 PR 의 foundation 은 *그 위에 외부 정책 layer 를 얹는 작업* 이다. 다른 부서 (platform / security / data-ai) 분기 시에도 같은 4 layer 만 복사하면 된다.

# 구현 위치 / 설계 위치

| 산출물 | 위치 |
| --- | --- |
| 정책 본문 | `policies/runtime/agents/engineering-agent/ecc-foundation.md` |
| 운영자용 비교 매트릭스 | `docs/agent-company-ecc.md` |
| 디렉터리 골격 (skills / hooks / commands) | `agents/engineering-agent/{skills,hooks,commands}/README.md` + 1~2 sample manifest |
| Obsidian mirror notes (research / decision / task-log) | `notes/vault-mirror/10-projects/yule-studio-agent/{research,decisions,task-logs}/` |
| dispatcher / loader / runtime wiring | **본 PR 비범위.** 후속 PR (#48 / #59 와 합쳐서) |

# 리스크와 다음 액션

리스크:

- **Markdown layer 의 인플레이션** — `yule memory reindex` 가 모두 SOURCE_POLICY 로 픽업. 본 PR 의 신규 markdown 은 4~6 개로 제한. retrieval 영향은 작지만 후속 PR 합류 시 재평가 필요.
- **dispatcher 미존재 → "정의만 있고 동작 안 함"** — 본 PR 은 명시적으로 *foundation 만* 도입. README 와 정책 본문에 "현재는 정의 layer, runtime wiring 은 후속 PR" 표기.
- **#48 / #59 의 worktree 가 같은 디렉터리에 변경** — 본 PR 은 add-only. 충돌 없으나, 머지 순서가 dependent 면 리베이스 부담. 사전 의존을 issue body 에 명시.

다음 액션:

1. `docs/agent-company-ecc.md` 작성 (운영자용 매트릭스).
2. decision note 작성 (왜 무엇을 도입 / 보류했는지의 압축 요약).
3. `agents/engineering-agent/{skills,hooks,commands}/` 디렉터리 + README + sample manifest.
4. unit 테스트 (가능한 범위) + `python3 -m unittest discover -s tests -t .` 회귀 0 확인.
5. 진행 코멘트 (#25) + draft PR 생성.

# 검증 / 신뢰도

- ECC 분석은 sub-agent 의 GitHub web fetch 결과를 1 차 소스로 사용. raw 파일 경로는 본 노트의 `references` frontmatter 에 기록.
- ECC 의 hook 동작 (exit code 2 block 등) 은 정책 인용 수준이며, Yule 도입 시점에는 markdown 정책 + (후속 PR) loader 로 분리해 검증할 예정.
- 본 PR 자체는 runtime 거동을 변경하지 않으므로 회귀 위험은 `yule memory reindex` 의 retrieval 우선순위 변동에 한정.

## 관련 문서

- [[CLAUDE]]
- [[decision-ecc-foundation]]
- [[task-log-25-ecc]]
- [[research-engineering-agent-governance-synthesis-issue-69]]
- [[decision-engineering-agent-authoring-policy-issue-69]]
- [[task-log-governance-integration-issue-69]]
