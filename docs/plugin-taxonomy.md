# Plugin / Hook / Skill / MCP / Backend — 분류 체계 (taxonomy)

> 이 레포는 Claude 만 쓰지 않는다 — Codex / Gemini / Ollama 를 함께 쓴다. 따라서
> "Claude 전용 plugin" 관점이 아니라 **vendor-neutral SSoT → provider projection**
> 관점으로 5 개념을 분리한다. 본 문서는 그 분류의 SSoT.
>
> 짝 문서: [`provider-capability-matrix.md`](provider-capability-matrix.md) (어떤 capability 를 어느 provider 에 배치).

## 0. 한 문장 요약

**capability/skill/hook 는 vendor-neutral SSoT 에 한 번 정의하고, 각 provider(harness)
로 *투영*한다. backend(runner)는 그 위에서 실행만 하는 LLM 엔진이며 plugin host 가 아니다.**

## 1. 5 개념 — 절대 섞지 않는다

| 개념 | 정의 | SSoT 위치 | vendor-neutral? |
| --- | --- | --- | --- |
| **backend (runner)** | 작업을 실제로 실행하는 LLM 엔진 | `agents/<agent>/manifest.json` `participants`/`integrations` + `agents/runners/<id>.py` | n/a (provider 자체) |
| **skill** | 재사용 가능한 에이전트 절차(작업 레시피) | `skills/<id>.md` + `agents/grants/slash-command-grants.json` | ✅ (harness 로 투영) |
| **hook** | lifecycle 개입점(pre/post/gate) | runtime plugin 의 `hooks_provided` + `agents/<agent>/hooks/*.md` | ✅ (runtime 가 실행) |
| **MCP** | 외부 도구 서버(Model Context Protocol) | (현재 SSoT 없음 — §6 제안) | ✅ (서버는 중립, 연결은 provider별) |
| **plugin** | **중의어 — 아래 2개로 분리** | — | — |
| ├ Yule runtime plugin | hook-provider 파이썬 모듈 | `plugins/<id>/manifest.json` (`kind`+`hooks_provided`+`module_path`) | ✅ |
| └ harness plugin | Claude Code/Codex 의 skill 번들(생성물) | `.claude-plugin/` · `.codex-plugin/` | ❌ (provider projection) |

> "plugin" 단어 충돌이 혼란의 근원이다. **Yule runtime plugin**(중립, runtime 안에서
> 실행) 과 **harness plugin**(provider별 생성 번들)은 다른 것이다. 본 문서 이후로는
> 두 이름을 구분해서 쓴다.

## 2. backend(runner) ≠ plugin host

`agents/<agent>/manifest.json` 의 `participants`(claude/codex/gemini/ollama) +
`integrations`(github-copilot)가 backend 목록이고, 구현은 `agents/runners/<id>.py`.

- backend 는 **"무엇으로 실행하나"** — LLM 엔진/CLI.
- plugin/hook/skill 은 **"무엇을 실행하나"** — 절차/개입.
- 한 backend 가 여러 skill 을 실행하고, 한 skill 이 여러 backend 로 투영된다(N:N).

**Ollama 는 backend 다, plugin host 가 아니다** — 이유는 [matrix 문서 §4](provider-capability-matrix.md) 참조.

## 3. vendor-neutral SSoT 계층

```
[SSoT — vendor-neutral]
  plugins/<id>/manifest.json          ← runtime hook providers (kind/hooks_provided/module_path)
  skills/<id>.md                      ← skill 절차 (단일 SSoT)
  agents/<agent>/{commands,hooks}/*.md ← command/hook 스펙(ECC v0)
  agents/grants/slash-command-grants.json ← agent↔skill/command grant + `harness`(투영 대상)
  agents/<agent>/manifest.json        ← backend 목록(participants/integrations)
        │
        │  scripts/sync_harness_skills.py  (단방향 생성, 투영)
        ▼
[Projection — provider별 생성물 (손 편집 금지)]
  .claude/skills/<id>/SKILL.md   .claude-plugin/plugin.json   ← Claude Code
  .agents/skills/<id>/SKILL.md   .codex-plugin/plugin.json    ← Codex
  (.gemini/...                                                ← Gemini, 후속 §5)
```

- **`harness` 필드 = 투영 대상 목록**(현재 `["claude","codex"]`). 이게 사실상
  `supports_providers` 다 — §5 에서 의미를 고정한다.
- 투영기는 `HARNESS_TARGETS` 레지스트리(스크립트 상단)로 대상↔레이아웃을 매핑한다.
  미등록 대상은 **조용히 Codex 로 오라벨하지 않고 명시적으로 skip** 한다.

## 4. runtime plugin 분류 (kind × hooks_provided)

`plugins/<id>/manifest.json` 의 `kind` 어휘와 `hooks_provided` 어휘:

| kind | 의미 | 예 |
| --- | --- | --- |
| `guard` | outbound/preflight 차단·검열 | paste-guard, lsp-preflight, tool-call-gate |
| `learning` | preflight/postmortem 기억·preflight 주입 | claude-mem, hookify |
| `delivery` | 완료/외부 출력 발행 | obsidian-vault-push, discussion-response, auto-merge-decider, live-llm-editor, live-research-provider |
| `exploration` | preflight 컨텍스트 수집 | repo-map |

| hook 시점 | 의미 |
| --- | --- |
| `PREFLIGHT` | 작업 진입 전(컨텍스트 주입/차단) |
| `COMPLETION` | 작업 완료 시 |
| `POSTMORTEM` | 사후 회고 |
| `OUTBOUND_{LLM,DISCORD,GITHUB,VAULT}` | 외부로 나가기 직전 검열/발행 |

이 plugin 들은 **모두 vendor-neutral** — Yule runtime 이 어떤 backend 를 쓰든 실행된다.
일부는 특정 provider 의 *native* hook 으로도 표현 가능하다(예: paste-guard ↔ Claude Code
pre-tool hook). 그 매핑이 [capability matrix](provider-capability-matrix.md) 의 핵심.

## 5. `supports_providers` / `harness` 의미 고정 (최소 스키마 제안)

현재 grant 스펙의 `harness` 필드를 **projection-target 목록의 정식 이름**으로 고정한다.
별도 신규 필드를 만들기보다 기존 필드의 의미를 명문화하는 게 최소 변경이다.

- `harness: ["claude", "codex"]` = "이 skill 을 Claude Code 와 Codex 로 투영한다".
- 예약: `"gemini"` 는 Gemini projection 대상(현재 미생성 — §아래 Gemini projection).
- 검증: `harness` 토큰은 `HARNESS_TARGETS` 의 키(투영 대상) 집합 안이어야 한다.
  vendor-neutral 인 backend 이름(`ollama` 등)은 **harness 가 아니다** — Ollama 는
  투영 대상이 아니라 backend 이므로 skill 의 harness 에 절대 넣지 않는다.

> runtime plugin 매니페스트에는 별도 `supports_providers` 가 필요 없다 — 중립이기 때문.
> 대신 선택적 `capability_class`(아래) 로 matrix 자동 도출을 준비할 수 있다(후속).

### Gemini projection (✅ 1차 구현됨)

투영 경로가 실제로 결선됐다 — `research-collect` skill 이 Gemini 로 투영된다:

- `HARNESS_TARGETS["gemini"]` 등록(`.gemini/commands` / `.gemini-plugin`, `fmt: toml`).
- `_render_skill_toml` 가 Gemini custom command(`description` + `prompt`, SSoT pointer)를 생성.
- grant `harness` 에 `"gemini"` 포함 시 `.gemini/commands/<id>.toml` + `.gemini-plugin/plugin.json` 생성.
- `.gitignore` 가 `.gemini/` 로컬 state 는 무시하되 `.gemini/commands/` 는 추적(.claude/skills 패턴과 동일).
- `GEMINI.md` 가 생성 command 를 참조. 회귀 `test_provider_capability_taxonomy.GeminiProjectionTests`.

남은 후속: research 외 Gemini 적합 skill 확대, GEMINI.md `@include` 자동화, custom command
argument(`{{args}}`) 매핑.

## 6. MCP 의 자리 (현재 공백)

MCP(외부 도구 서버: figma/drive/browser 등)는 현재 **SSoT 가 없다**(docs 에 개념만).
plugin/hook/skill 과 섞지 말 것 — MCP 는 *backend 가 붙는 외부 도구 채널*이다.

제안(후속): `integrations/mcp/<id>.json`(서버 url/auth env/도구 목록) 을 vendor-neutral
SSoT 로 두고, provider별 연결(Claude `.mcp`, Codex `mcp_servers`, Gemini MCP)은
projection 으로 생성. 본 PR 범위 밖 — [matrix 문서](provider-capability-matrix.md) 후속 참조.

## 7. 승격 / provider-specific 판단 기준 (질문 4 답)

| 판단 | 규칙 |
| --- | --- |
| **공통(vendor-neutral)으로 승격** | 안전/감사/거버넌스/기억처럼 backend 무관하게 항상 돌아야 하는 것 → runtime plugin(`plugins/`) 또는 skill(`skills/`). 예: paste-guard, claude-mem, compact-to-vault. |
| **provider projection 으로 유지** | 특정 harness 의 native 기능에 의존(예: Claude pre-tool hook, Codex computer-use, Gemini token cache) → SSoT 는 중립이되 `harness` 로 그 provider 에만 투영. |
| **backend 로 분리** | LLM 추론 엔진 자체(Ollama local inference) → plugin 아님, `participants` + runner. |

## 8. 관련
- 짝 문서: [`provider-capability-matrix.md`](provider-capability-matrix.md)
- harness 브리지/grant/생성기: [`agent-slash-commands.md`](agent-slash-commands.md)
- 생성기 코드: `scripts/sync_harness_skills.py` · 드리프트 가드: `tests/agents/test_harness_projection.py`
- 거버넌스 회귀: `tests/governance/test_provider_capability_taxonomy.py`
