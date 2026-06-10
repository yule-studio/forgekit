# ECC Foundation — Yule 흡수 정책 (Issue #25)

> **단일 주체:** 본 정책의 작성·갱신·적용 결정은 모두 `engineering-agent / tech-lead` 가 책임진다.
> **목적:** `affaan-m/everything-claude-code` (이하 ECC) 가 채택한 4 개 외부 layer (`agents/` `skills/` `hooks/` `commands/`) + research-first 운영 방식을, Yule 의 기존 회사형 구조 (Discord gateway → role profile → lifecycle 13 stage → Obsidian → GitHub WorkOS) 위에 *디프 기반으로* 흡수한다.
> **단순 복사 금지.** 정책으로 흡수, 디렉터리·코드는 minimal scaffold 까지만.

본 문서는 issue #25 결정의 **운영자용 통합 정책** 이다. 비교 분석의 raw 데이터는 [`docs/agent-company-ecc.md`](../../../../docs/agent-company-ecc.md) 에, 결정 근거 노트는 vault mirror (`notes/vault-mirror/10-projects/yule-studio-agent/decisions/`) 에 둔다.

## 1. 핵심 원칙

1. **Yule 구조가 기준선** — 외부 레퍼런스는 정책 단위로만 흡수한다. 기존 모듈 / SQLite 키 / lifecycle 단계 / manifest.json 스키마는 본 정책으로 변경되지 않는다.
2. **정책 우선, 코드 점진** — 본 PR 은 정책 + 디렉터리 골격까지 land. dispatcher / runtime wiring 은 후속 PR (#48, #59 가 공통 기반으로 활용).
3. **tech-lead orchestration 강화** — gateway 가 분배·합의·외부 회신을 책임지는 기존 구조를 ECC 의 "research-first / skill 단위 호출 / hook 기반 확장" 패턴으로 보강한다.
4. **safety gate 유지** — autonomy_policy L0~L4, approval gate, secret redaction, protected branch 차단, smoke do-not-merge 정책은 그대로. 어떤 ECC 패턴도 이 gate 를 우회할 수 없다.

## 2. 4 개 외부 layer 의 Yule 정의

각 layer 는 **markdown frontmatter + 명세 본문** 의 1 파일 = 1 단위로 등록한다. 코드는 markdown 을 읽어 등록·디스패치만 한다 (코드가 layer 의 source of truth 가 되지 않는다).

### 2.1 agents/ — 이미 존재 (재정의)

Yule 은 이미 `agents/engineering-agent/<role>/manifest.json` + role contract-v1 으로 agent 를 정의한다. 본 정책은 *재정의 없음*. 다만:

- ECC 의 `agents/` 가 markdown 인 것과 달리 Yule 은 JSON 이다 — 이는 Yule 의 deterministic loader / role_profiles_data 와의 호환성을 위한 의도된 차이다. **본 PR 에서 markdown 으로 전환하지 않는다.**
- 단, manifest.json 에 `skills:` `hooks:` `commands:` 필드를 *선언만* 추가해 외부 layer 를 가리킬 수 있게 한다. 필드는 optional, 기존 contract 비파괴.

### 2.2 skills/ — 신설 (foundation)

**Skill = 한 역할이 수행 가능한 단일 capability 명세.**

위치: `skills/<skill-id>.md`

Frontmatter 스키마 (v0):

```yaml
---
id: <kebab-case-id>          # e.g. "research-collect"
title: <human readable>
owner_role: <tech-lead|backend-engineer|...>
applicable_roles: [<role list>]
autonomy_level: L0 | L1 | L2 | L3 | L4
input_contract:
  - <required field>
output_contract:
  - <required field>
preconditions:
  - <precondition>
side_effects:
  - <side effect | none>
references:
  - <doc path or URL>
---
```

본문은 운영자가 읽는 markdown 명세 (목적 / 트리거 / 흐름 / 예외 / Obsidian 기록 위치 / 테스트 hook).

운영 규칙:

- 1 skill = 1 markdown. 같은 capability 의 변형은 별도 파일로.
- `owner_role` 만 작성 권한을 가진다. 다른 역할은 `applicable_roles` 로 참여.
- skill 변경은 정책 변경 — `policies/runtime/agents/engineering-agent/CHANGELOG.md` 에 기록 (별도 작업).
- 등록 / 검증 코드는 후속 PR — 본 PR 은 디렉터리 + 1~2 개 reference manifest 까지만.

### 2.3 hooks/ — 신설 (foundation)

**Hook = lifecycle 13 단계 사이에서 실행되는 외부 정책 점.**

위치: `agents/engineering-agent/hooks/<hook-id>.md`

Frontmatter 스키마 (v0):

```yaml
---
id: <kebab-case-id>
title: <human readable>
fires_on: <lifecycle stage>          # intake | triage | role_selection | research_planning | role_scoped_research | sufficiency_check | deliberation | synthesis | interim_report | insufficient_report | final_report | obsidian_preview | obsidian_recorded | coding_authorization_pending | coding_job_ready
phase: pre | post                    # before / after the stage
sync: blocking | advisory            # blocking → 실패 시 stage 중단
owner_role: tech-lead
output_contract:
  - <required field>
side_effects:
  - <side effect>
---
```

본문은 hook 의 책임 / 입출력 / 실패 시 routing / agent_ops_audit 기록 형식.

운영 규칙:

- hook 은 default 로 `advisory` (실패 = 로그). `blocking` 은 명시 선언 + autonomy_policy L2 이상 사유 첨부.
- pre-hook 은 입력 검증 / 사용자 승인 게이트, post-hook 은 audit / Obsidian 기록 / Discord 통지.
- **하드 레일 위반 hook 은 실패 시 작업을 중단하지 않고 audit 로 기록 후 진행** — secret 출력 / protected branch / merge / push 같은 가드는 여전히 코드 수준 가드 (autonomy_policy + github_writer) 가 1 차 책임. hook 은 정책 추적용.
- skill 과 같은 markdown 트리, dispatcher 는 후속 PR.

### 2.4 commands/ — 신설 (foundation)

**Command = 운영자가 manual 로 호출 가능한 entry point 의 표준 매핑.**

위치: `agents/engineering-agent/commands/<command-id>.md`

Frontmatter 스키마 (v0):

```yaml
---
id: <kebab-case-id>
title: <human readable>
surface: discord-slash | cli | both
slash: /<name>                       # surface 가 discord-slash 이면 필수
cli: yule <subcommand>               # surface 가 cli 이면 필수
allowed_roles: [<role list>]
autonomy_level: L0 | L1 | L2 | L3 | L4
required_approval: false | role-approver | human
references:
  - <doc path>
---
```

본문은 트리거 phrase / 입력 schema / 출력 schema / 거부 조건 / 호환 hook.

운영 규칙:

- 기존 slash command (`/engineer_intake`, `/engineer_show`, …) 와 CLI (`yule engineer ...`, `yule github ...`) 는 본 PR 이전부터 코드에 존재한다. 본 정책은 *문서화 layer* 로서 markdown 등록만 추가, 코드는 손대지 않는다.
- 새 command 추가는 markdown 만 만들고, 동작 시점에 코드와 1:1 매핑이 가능해야 한다 (검증은 후속 PR 의 등록기 책임).

## 3. research-first 운영 기준

Yule 은 이미 lifecycle 13 단계의 `intake → triage → role_selection → research_planning → role_scoped_research → sufficiency_check → deliberation` 흐름을 통해 *de facto* research-first 를 구현한다. 본 정책은 이를 **명시적 게이트** 로 격상한다.

### 3.1 research-first 게이트 (강제)

다음 표에 해당하는 작업 입력은 deliberation / coding 단계로 진입하기 전에 **research_status ∈ {`ready`, `insufficient`} 가 session.extra 에 박혀야 한다**.

| 작업 분류 | research-first 강제 |
| --- | --- |
| 사용자가 `[Research]` prefix 포함 | ✅ 강제 |
| 사용자가 "조사해줘" / "자료 수집" / "리서치만" 키워드 포함 | ✅ 강제 (research-only 모드) |
| coding_required = True 인 작업 | ✅ 강제 (research_pack 또는 명시적 user 결정 필요) |
| 단순 status 응답 / `/engineer_show` / `/engineer_progress` | ❌ 면제 |
| Obsidian 직접 입력 (사용자 명시 메모) | ❌ 면제 |

검증: `agents/lifecycle_status.compute_lifecycle_status(session)` 가 `coding_authorization_pending` 또는 `coding_job_ready` 단계로 진입하기 전 `research_status` 가 missing 이면 `INSUFFICIENT` 로 떨어뜨린다. 본 동작은 이미 코드에 존재 — 본 정책은 이를 명시화.

### 3.2 research-first 의 stop conditions

다음이 되면 추가 research 를 멈추고 deliberation 으로 진입한다:

- `score_research_sufficiency()` 가 모든 active role 에 대해 `coverage >= threshold` 를 보고
- `ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS` 예산 초과
- 4 round 연속 새 자료 수 0
- 사용자가 명시적으로 "여기까지" / "deliberation 으로" 발화

### 3.3 research-first 의 Obsidian 기록

research-first 게이트 통과 시 자동 기록되는 vault 노트:

- `10-projects/<project>/research/<date>_research_<slug>.md` (ResearchPack)
- 미흡 시 `10-projects/<project>/task-logs/<date>_task-log_<session>.md` 에 stop_reason / missing_roles 추가

## 4. tech-lead orchestration 보강안

ECC 의 패턴을 흡수하면서 tech-lead 의 역할을 재정의:

| 책임 | 현재 위치 | 강화 방향 |
| --- | --- | --- |
| 작업 분해 | `tech_lead_aggregator.aggregate_role_outputs` | 변경 없음 |
| 역할 분배 | `role_selection.recommend_active_roles` | skill 매트릭스 (`skills/`) 를 입력에 추가 가능 (후속 PR) |
| 합의 조율 | `tech_lead_aggregator.build_tech_lead_summary_context` | hook (`hooks/synthesis-postcheck.md`) 으로 외부 정책 추가 가능 (후속 PR) |
| 외부 회신 | gateway router | command markdown 의 `surface` 매핑이 외부 회신 채널을 명시 (본 PR) |
| **GitHub WorkOS write 주체** | `senior_triage` + `LiveGithubAppClient` | **모든 write 의 actor 는 `engineering-agent / tech-lead`. 본 정책에서 강제** |

본 PR 의 정책 영역에서 강제하는 것:

- 모든 issue comment / PR body 의 actor 라벨은 `engineering-agent/tech-lead` 로 고정.
- backend / frontend / devops / qa / ai-engineer / product-designer 의 take 는 PR body 에 `## 역할별 검토 (분석 입력)` 섹션으로만 인용. write actor 라벨은 전파되지 않음.
- senior_triage 결과의 `primary_role` 은 *분석 관점 표시* 일 뿐, 실제 git author / committer / PR author 는 항상 `yule-studio-engineering-agent[bot]` (= tech-lead 가 위임받은 단일 GitHub App 계정).

## 5. 디렉터리 + 영향 받는 위치

```
agents/engineering-agent/
├── skills/                    ← 신설 (foundation, 본 PR)
│   ├── README.md              ← skill 작성 가이드
│   └── <reference-skill>.md   ← 1~2 개 sample
├── hooks/                     ← 신설 (foundation, 본 PR)
│   ├── README.md
│   └── <reference-hook>.md
├── commands/                  ← 신설 (foundation, 본 PR)
│   ├── README.md
│   └── <reference-command>.md
└── (기존 role 폴더는 손대지 않음)

policies/runtime/agents/engineering-agent/
├── ecc-foundation.md          ← 본 정책 (이 파일)
└── (기존 정책 8 종은 손대지 않음)

docs/
├── agent-company-ecc.md       ← 운영자용 비교 통합 문서
└── (기존 docs 는 손대지 않음)

notes/vault-mirror/10-projects/yule-studio-agent/
├── task-logs/2026-05-08_task-log_25-ecc.md
├── research/2026-05-08_research_ecc-foundation.md
└── decisions/2026-05-08_decision_ecc-foundation.md
```

## 6. 본 PR 이 닫지 않는 것 (후속 PR 분리 영역)

| 후속 작업 | 이유 | 예상 PR / 이슈 |
| --- | --- | --- |
| skill / hook / command 의 markdown loader + dispatcher 코드 | foundation 정착 후 분리 | #48 e2e harness 또는 신규 |
| MCP config 표준 wiring | 외부 통합은 별도 보안 검토 필요 | 별도 |
| Discord slash command 의 markdown 등록 자동화 | bot.py 변경 충돌 가능 | #59 hermes 와 협의 |
| LLM runner 의 skill 호출 자동화 | M11 RoleRunner 와 합쳐서 | M11 후속 |
| autonomy_policy / agent_ops_audit 의 hook 통합 | M10 마무리 단계에서 | M10d 후속 |

## 7. 검증

본 PR 의 정책·문서·markdown 추가는 *runtime 거동을 변경하지 않는다*. 따라서 회귀 위험은 다음 두 가지에 한정:

1. `yule memory reindex` 가 새 정책 markdown 을 SOURCE_POLICY 로 픽업 → retrieval 우선순위 변동.
2. `policies/runtime/agents/engineering-agent/role-profiles.md` 같은 기존 정책과 충돌하는 표현이 없어야 함.

검증 절차:

- 전체 unit 테스트 실행 (`python3 -m unittest discover -s tests -t .`) — 0 회귀 기대.
- `yule github plan-pr --dry-run --json <issue=25>` (가능 시) 로 PR body 가 repo template 형식을 따르는지 확인.
- `git diff --stat` 으로 코드 변경 0 확인 (정책 / 노트 / docs / 디렉터리 골격만).

## 8. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-08 | 초안 작성 (foundation 정책 + 4 layer 정의 + research-first 게이트 + tech-lead orchestration 보강) |
| 2026-05-27 | #185: A.3 "multi-harness 비도입" 부분 개정 — 레지스트리 SSoT 를 `.claude/` · `.agents/` · harness 플러그인으로 단방향 생성 투영하는 bridge 도입. 손 편집 대상이 아니라 생성물. grant SSoT(`agents/grants/slash-command-grants.json`) + `compact-to-vault` 결정형 코어 추가. 상세 [`docs/agent-slash-commands.md`](../../../../docs/agent-slash-commands.md) |

---

## Appendix A — ECC 실관찰 컨벤션 (2026-05-08 분석)

본 PR 의 결정 근거가 된 ECC 의 실제 구조 (분석은 별도 sub-agent 가 ECC repo 의 raw 파일을 fetch 해 정리). 본 부록은 *증거 기록* 이며 정책 본문이 아니다.

### A.1 ECC 의 4 layer 실제 형태

| layer | 단위 | frontmatter 필드 | 본문 형식 | 주의점 |
| --- | --- | --- | --- | --- |
| `agents/` | 단일 `<name>.md` | `name` / `description` / `tools` / `model` | Role / Process (numbered phases) / Principles / Checklists / Red Flags | `description` 안의 "Use PROACTIVELY when..." 가 auto-routing hint. `tools` 는 allowlist 배열. |
| `skills/<id>/SKILL.md` | **디렉터리** + `SKILL.md` | `name` / `description` / `origin` | `## Trigger` / `## Workflow` (numbered) / `## Decision Matrix` / `## How to Use` (Quick / Full) | Quick = inline, Full = `Task(subagent_type=...)` 로 위임. IO schema 는 명시 안 함. |
| `hooks/` | `hooks.json` registry + Node 스크립트 | (json: `id` / `matcher` / `hooks: [{type, command}]` / `timeout` / `async`) | external script | 이벤트: PreToolUse / PostToolUse / Stop / SessionStart / SessionEnd / PreCompact. Block 은 PreToolUse 의 exit code 2 만. |
| `commands/<name>.md` | 단일 markdown | `description` (단일 필드) | 행동 명세 (When to Use / Usage / numbered procedure / Edge cases / Example) | dispatcher 가 아니라 *primitive prompt injection*. subagent 호출은 본문 prose 로 권유. |

### A.2 ECC 의 research-first 강제 방식

**전용 doc 없음.** 두 가지 경로로 연성/강성 강제:

- **연성:** `skills/search-first/SKILL.md` — "Research-before-coding workflow" 자체 규율. trigger 가 markdown 으로 적혀 있을 뿐 코드 enforcement 없음.
- **강성:** `hooks/hooks.json` 의 `pre:edit-write:gateguard-fact-force` — `Edit|Write|MultiEdit` 의 *첫 호출* 을 file-by-file 로 blocking. "investigation 기록 (importers / data schemas / user instruction)" 이 없으면 exit code 2 로 차단.

GateGuard 패턴은 Yule 의 autonomy_policy (advisory) 보다 **강성** 이다. 본 PR 은 이를 즉시 도입하지 않는다 — Yule 의 lifecycle 13 단계가 이미 *de facto* research-first 를 강제하므로 (research_status 가 missing 이면 work_report INSUFFICIENT) 동등 효과. 본 정책 §3.1 이 그 강제를 **명시적 게이트** 로 격상한다.

### A.3 흡수하지 않는 ECC 패턴 (이유 명시)

| ECC 패턴 | 흡수 여부 | 이유 |
| --- | --- | --- |
| Multi-harness 모노레포 (.claude / .codex / .cursor / ...) | ⚠️ **#185 에서 부분 개정** | 당시 근거(통합 런타임)는 유효하나, 실제로 에이전트는 `ClaudeCodeRunner`(`claude -p`) / `CodexRunner`(`codex`) 로 harness CLI 를 호출한다 — 이 점이 과소평가됨. 따라서 harness 디렉터리를 *직접 운영*하지는 않되, 레지스트리 SSoT 를 `.claude/` · `.agents/`(Codex) · `*-plugin/` 으로 **단방향 생성 투영**한다(`scripts/sync_harness_skills.py`). SSoT 는 여전히 레지스트리 markdown + grant JSON. 상세: [`docs/agent-slash-commands.md`](../../../../docs/agent-slash-commands.md). |
| `SOUL.md` + `WORKING-CONTEXT.md` + `RULES.md` triad | ❌ 비도입 | Yule 이미 `policies/runtime/...` + `agents/.../CLAUDE.md` + `obsidian-memory.md` 로 동등 분리. 추가 파일은 인플레이션. |
| Continuous-learning hook (모든 tool call 캡처) | ❌ 비도입 (현 단계) | Yule 의 agent_ops_audit / activity_log 가 동등 역할. 후속 PR 에서 hook 등록기 합치면 자연 통합. |
| Config-protection hook (lint/formatter config 편집 거부) | ⏳ 후속 PR 검토 | 흥미로운 정책이나 본 PR 범위 밖. autonomy_policy 의 새 action_id 로 추가 가능. |
| `Plugin installer` 가 `hooks.json` 의 절대경로를 install 시점에 rewrite | ❌ 비도입 | Yule 은 systemd unit + `yule run-service` 로 absolute path 를 이미 일관되게 관리. |
| Skill = 디렉터리 (`<id>/SKILL.md`) | ❌ 비도입 (초기 단계) | 단일 파일 (`<id>.md`) 가 Yule 의 기존 정책 markdown 패턴과 일관. asset 이 늘어 디렉터리화가 필요해지면 그 때 전환. |
| `mcp-servers.json` registry (15+ servers) | ⏳ 후속 PR | 외부 통합은 별도 보안 검토 필요. 본 PR 은 placeholder 디렉터리 (`mcp-configs/`) 도 만들지 않는다. |
| Rust `ecc2/` control-plane 프로토타입 | ❌ 비도입 | Yule 은 Python 단일 런타임. 언어 추가는 별도 큰 결정. |

### A.4 흡수하는 ECC 패턴 (이유 명시)

| ECC 패턴 | 본 PR 도입 형태 |
| --- | --- |
| Skill = `## Trigger` / `## Workflow` / `## Decision Matrix` / `## How to Use` 의 4 섹션 markdown | **도입.** Yule 의 frontmatter (id / owner_role / autonomy_level / IO contract) 와 ECC 의 4 섹션 본문을 결합. (§2.2) |
| Hook = lifecycle 단계별 사전/사후 정책 점 | **도입.** ECC 의 6 이벤트 (PreToolUse 등) 대신 Yule 의 13 lifecycle stage × {pre, post} 로 매핑. (§2.3) |
| Command = "행동 명세 markdown" (dispatcher 가 아닌 primitive) | **도입.** 단, Yule 의 기존 slash / CLI 와 1:1 매핑되는 `surface` 필드 추가. (§2.4) |
| `description` 의 "Use PROACTIVELY when..." 패턴으로 auto-routing hint | **간접 도입.** Yule 은 이미 `RoleProfile.activation_keywords` 로 동등 효과. skill / hook / command markdown 의 frontmatter `applicable_roles` + 본문 trigger 가 같은 역할. |
| Research-before-code 의 강성 게이트 | **도입 (명시화).** §3.1 의 "research-first 게이트" 표가 Yule 기존 코드의 명시적 정책화. |
| Skill 본문이 IO schema 가 아니라 prose | **거부 → strengthening.** Yule 은 `input_contract` / `output_contract` / `preconditions` / `side_effects` 를 frontmatter 로 강제. ECC 의 prose-only 보다 deterministic 검증 가능. |
