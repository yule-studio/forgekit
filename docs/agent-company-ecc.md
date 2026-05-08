# Agent Company — ECC ↔ Yule 비교 매트릭스 (Issue #25)

> **목적:** `affaan-m/everything-claude-code` (ECC) 의 외부 layer 패턴과 현재 Yule Studio Agent 구조를 영역 단위로 비교해, *어떤 패턴을 도입했고 어떤 것은 보류/거부했는지* 운영자가 한 화면에서 잡을 수 있게 한다.
> **단일 주체:** 본 문서의 작성·갱신·결정 발화 주체는 `engineering-agent / tech-lead` 이다. 다른 역할 (backend / frontend / devops / qa / ai-engineer / product-designer) 의 관점은 *분석 입력* 으로만 인용된다.
> **정책 본문은:** [`policies/runtime/agents/engineering-agent/ecc-foundation.md`](../policies/runtime/agents/engineering-agent/ecc-foundation.md). 본 문서는 *운영자 대상 요약 + 매트릭스* 다.

## 1. 한눈에 — 비교 매트릭스

| 영역 | 현재 Yule | ECC | 도입 여부 | 이유 | 구현 위치 | 리스크 |
| --- | --- | --- | --- | --- | --- | --- |
| **Agent 정의** | `agents/engineering-agent/<role>/agent.json` + role contract-v1 (JSON) | `agents/<name>.md` (YAML frontmatter + body system prompt) | **유지 (변경 없음)** | Yule 의 deterministic loader / role_profiles_data 와 호환. 단, agent.json 에 optional `skills:` `hooks:` `commands:` 선언 필드 추가 가능. | 기존 그대로 | JSON ↔ markdown 차이가 외부 reference 가독성에 영향 — `docs/engineering.md` 에서 cross-link |
| **Skill** | 없음 (역할 contract 안에 묻혀 있음) | `skills/<id>/SKILL.md` (디렉터리 + 4 섹션 본문) | **신설 — 단일 파일** | 가장 약한 영역. ECC 의 4 섹션 본문 + Yule 의 IO contract frontmatter 결합. | `agents/engineering-agent/skills/<id>.md` | dispatcher 미존재 → 정의만 있고 동작 안 한다는 오해. README 에 명시 |
| **Hook** | 없음 (lifecycle 코드에 분산) | `hooks.json` registry + Node script + 6 native event | **신설 — markdown spec 만** | Yule 의 13 lifecycle stage × {pre, post} 로 매핑. 본 PR 은 markdown 까지만, runtime registry / dispatcher 는 후속 PR | `agents/engineering-agent/hooks/<id>.md` | blocking 선언과 코드 가드 (autonomy_policy / github_writer) 의 책임 경계 — README §6 명시 |
| **Command** | slash + CLI 코드 직접 정의 (already 동작) | `commands/<name>.md` (행동 명세 markdown, primitive prompt) | **신설 — documentation layer** | Yule 의 기존 동작 entry 를 markdown 으로 등록만. dispatcher 자동화는 후속 PR. | `agents/engineering-agent/commands/<id>.md` | orphan markdown (코드 없는 entry) 금지 — README §8 |
| **MCP config** | 직접 클라이언트 (Discord / GitHub App / Obsidian / Tavily / Brave / Naver) | `mcp-configs/mcp-servers.json` (17 server registry) | **거부 (본 PR)** | 외부 통합은 별도 보안 검토 필요. 본 PR 은 디렉터리도 만들지 않음. | (없음) | 후속 PR 에서 MCP 표준 wiring 검토 시 기존 클라이언트와 중복 호출 가능 — 정책 우선 |
| **Agent identity 문서 triad** | `policies/runtime/...` + `agents/.../CLAUDE.md` + `obsidian-memory.md` | `SOUL.md` + `WORKING-CONTEXT.md` + `RULES.md` + `AGENTS.md` 등 root file 다수 | **거부** | 기존 분리가 동등 효과. 추가는 인플레이션. | — | — |
| **Multi-harness 어댑터** | 없음 (Discord + CLI + GitHub App 통합 런타임) | `.claude/` `.codex/` `.cursor/` `.gemini/` `.kiro/` `.opencode/` `.trae/` `.codebuddy/` 8+ | **거부** | Yule 은 단일 런타임. harness 분기 의미 없음. | — | — |
| **Research-first** | lifecycle 13 단계 + `compute_lifecycle_status` 가 *de facto* 강제 | `skills/search-first/SKILL.md` (연성) + `pre:edit-write:gateguard-fact-force` hook (강성) | **명시화 (정책 §3.1 게이트)** | 코드는 그대로, 정책 본문에서 *게이트 표* 로 격상. 강성 hook 은 후속 PR | `policies/runtime/agents/engineering-agent/ecc-foundation.md` §3 | 정책과 코드의 동기화 — `compute_lifecycle_status` 변경 시 §3 도 함께 갱신 |
| **GateGuard (research-before-write 강성 차단)** | 없음 (advisory 만) | `pre:edit-write:gateguard-fact-force` (file 별 첫 write 차단, exit code 2) | **후속 PR 검토** | 흥미로운 정책. autonomy_policy 의 새 action_id + hook dispatcher 합쳐 흡수 가능. | (후속) | autonomy_policy 강성화 시 false positive 위험 — 별도 PR 에서 검증 후 도입 |
| **Config-protection hook** | 없음 | `pre:config-protection` (lint/formatter config 편집 거부) | **후속 PR 검토** | autonomy_policy 의 새 action 으로 깔끔하게 흡수. | (후속) | — |
| **Continuous-learning hook (모든 tool call 캡처)** | `agent_ops_audit` (부분 동등) + `activity_log` | `pre:observe:continuous-learning` (async) | **후속 PR — 통합** | dispatcher 합류 시 `agent_ops_audit` 와 자연 통합. | (후속) | — |
| **research-first 의 Obsidian 자동 기록** | 자동 (kind=research / decision / task-log / report) | 없음 (`research/` 가 산출물 폴더일 뿐 ) | **유지** | Yule 가 더 강함. ECC 흡수 불필요. | 기존 그대로 | — |
| **GitHub App write actor** | `engineering-agent[bot]` (G6 smoke-pr 검증 통과) | (해당 없음 — local code-mod 위주) | **유지 + 강화** | 본 PR 은 *모든 write actor 라벨을 tech-lead 단일 주체* 로 강제 | `policies/runtime/agents/engineering-agent/ecc-foundation.md` §4 | actor 라벨 stamp 자동화는 후속 PR (audit / PR body 의 자동 stamp wiring) |
| **Skill 본문 IO** | (해당 없음) | 본문이 IO schema 아닌 prose | **거부 → strengthening** | Yule skill 은 frontmatter 로 `input_contract` `output_contract` `preconditions` `side_effects` 강제 | `agents/engineering-agent/skills/README.md` §3 | — |
| **autonomy_policy** | L0~L4 5 단계 + `agent_ops_audit` (M10a~c) | (없음 — hook 으로 분산) | **유지** | Yule 가 ECC 보다 명시적. ECC 의 GateGuard / config-protection 은 추후 autonomy action_id 로 흡수. | 기존 그대로 | — |
| **self-improvement 신호 감지** | M10c skeleton (`self_improvement.py`) + 4 신호 | 없음 (운영 정책 수준) | **유지** | Yule 가 더 코드화. ECC 의 pattern-extraction 은 자기 개선 후속에서 통합. | 기존 그대로 | — |
| **Rust control-plane** | 없음 (Python 단일) | `ecc2/` (alpha) | **거부** | 언어 추가는 별도 큰 결정. | — | — |

## 2. 도입한 4 가지 (foundation)

본 PR 이 land 하는 외부 변경 layer:

### 2.1 `agents/engineering-agent/skills/` — Skill 등록 디렉터리

- 1 skill = 1 markdown (`<id>.md`).
- frontmatter: `id` / `title` / `owner_role` / `applicable_roles` / `autonomy_level` / `input_contract` / `output_contract` / `preconditions` / `side_effects` / `references`.
- 본문 4 섹션: `## Trigger` / `## Workflow` / `## Decision Matrix` / `## How to Use`.
- 작성 가이드: [`agents/engineering-agent/skills/README.md`](../agents/engineering-agent/skills/README.md).
- 1 reference manifest: [`research-collect.md`](../agents/engineering-agent/skills/research-collect.md).

### 2.2 `agents/engineering-agent/hooks/` — Lifecycle 정책 점

- Yule 의 13 lifecycle stage × {pre, post} = 26 점 + runtime 이벤트 별도.
- `sync: blocking | advisory` — blocking 은 *명시 선언 + autonomy_policy L2+ 사유* 필수.
- 작성 가이드: [`agents/engineering-agent/hooks/README.md`](../agents/engineering-agent/hooks/README.md).
- 1 reference manifest: [`research-first-gate.md`](../agents/engineering-agent/hooks/research-first-gate.md).

### 2.3 `agents/engineering-agent/commands/` — 운영자 entry registry

- 기존 slash + CLI 의 *문서화 layer*. 본 PR 은 코드 변경 0.
- frontmatter `surface: discord-slash | cli | both` + `slash:` / `cli:` + `allowed_roles` + `autonomy_level` + `required_approval`.
- 작성 가이드: [`agents/engineering-agent/commands/README.md`](../agents/engineering-agent/commands/README.md).
- 1 reference manifest: [`engineer-show.md`](../agents/engineering-agent/commands/engineer-show.md).

### 2.4 `policies/runtime/agents/engineering-agent/ecc-foundation.md` — 정책 본문

- 4 layer 정의 + research-first 게이트 + tech-lead orchestration 보강 + Appendix A (ECC 실관찰 컨벤션 + 도입/거부 이유).

## 3. 본 PR 이 닫지 않는 것 (후속 작업)

| 항목 | 후속 PR | 근거 |
| --- | --- | --- |
| skill / hook / command markdown loader + dispatcher | #25 의 후속 PR (또는 #48 e2e harness 와 병합) | foundation 정착 후 분리 |
| `hooks.json` runtime registry | 같은 후속 PR | dispatcher 와 한 묶음 |
| `mcp-configs/` 표준 wiring | 별도 PR (외부 통합 보안 검토 필요) | — |
| Discord slash 자동 등록 (markdown-first) | #59 hermes 와 협의 | bot.py 변경 충돌 가능 |
| LLM runner 의 skill 자동 호출 | M11 RoleRunner 합쳐서 | dispatcher 와 연결 |
| autonomy_policy / agent_ops_audit 의 hook 통합 | M10d 또는 후속 | hook dispatcher 와 한 묶음 |
| GateGuard 류 강성 hook (config-protection / continuous-learning) | autonomy_policy 새 action 추가 + hook | — |

## 4. 운영자 체크리스트

새 skill / hook / command 추가 시:

- [ ] markdown 파일 작성 (frontmatter + 본문 필수 섹션).
- [ ] 해당 README 의 인벤토리 표에 행 추가.
- [ ] [`policies/runtime/agents/engineering-agent/ecc-foundation.md`](../policies/runtime/agents/engineering-agent/ecc-foundation.md) 의 §2 / §3 정책과 충돌 없는지 확인.
- [ ] 코드와 매핑 (command 의 경우 기존 slash / CLI 에 실제로 존재하는지) 확인.
- [ ] `python3 -m unittest discover -s tests -t .` 회귀 0 확인.
- [ ] `yule memory reindex` 실행해 vault 인덱스 갱신 (정책 markdown 이 SOURCE_POLICY 로 픽업).
- [ ] Obsidian mirror note 갱신 (research / decision / task-log).

## 5. 원본 분석 / 결정 노트

- ECC 분석 (raw): [`notes/vault-mirror/.../research/2026-05-08_research_ecc-foundation.md`](../notes/vault-mirror/10-projects/yule-studio-agent/research/2026-05-08_research_ecc-foundation.md)
- 도입/보류 결정: [`notes/vault-mirror/.../decisions/2026-05-08_decision_ecc-foundation.md`](../notes/vault-mirror/10-projects/yule-studio-agent/decisions/2026-05-08_decision_ecc-foundation.md)
- 작업 로그: [`notes/vault-mirror/.../task-logs/2026-05-08_task-log_25-ecc.md`](../notes/vault-mirror/10-projects/yule-studio-agent/task-logs/2026-05-08_task-log_25-ecc.md)
- 정책 본문: [`policies/runtime/agents/engineering-agent/ecc-foundation.md`](../policies/runtime/agents/engineering-agent/ecc-foundation.md)
- 외부 레퍼런스: https://github.com/affaan-m/everything-claude-code
- GitHub issue: https://github.com/yule-studio/yule-studio-agent/issues/25
