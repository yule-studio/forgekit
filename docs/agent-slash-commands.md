# 에이전트 슬래시 명령어 · 스킬 · 플러그인 (harness 브리지)

> 이슈 #185 의 사람용 SSoT. 에이전트(부서/역할)가 슬래시 명령어를 쓰고, 스킬·플러그인을
> 직접 저작하는 구조를 설명한다. 코드/데이터 SSoT 는
> [`agents/grants/slash-command-grants.json`](../agents/grants/slash-command-grants.json) +
> 레지스트리 markdown(스킬은 최상위 `skills/<id>.md` SSoT, command/hook 은 `agents/<agent>/{commands,hooks}/*.md`).

## 1. 한눈에 — Bridge 구조

```
[SSoT]  skills/<id>.md  (스킬 단일 SSoT, cross-agent)
        agents/<agent>/{commands,hooks}/*.md   (ECC v0 markdown, 런타임 비의존)
          + agents/grants/slash-command-grants.json   (agent ↔ 명령어/스킬 grant)
                              │
                   scripts/sync_harness_skills.py      (단방향 생성)
                              │
        ┌─────────────────────┴─────────────────────┐
   [Claude Code]                                [Codex]
   .claude/skills/<id>/SKILL.md                 .agents/skills/<id>/SKILL.md
   .claude-plugin/plugin.json                   .codex-plugin/plugin.json
```

- **레지스트리 markdown + grant JSON 이 단일 SSoT.** `.claude/`·`.agents/`·`*-plugin/` 은
  **생성물**이다. 손으로 두 곳을 고치지 않는다(레포 전역 원칙: 한 곳에만 두고 나머지는 generate).
- 에이전트는 harness 실행자(`ClaudeCodeRunner` = `claude -p`, `CodexRunner` = `codex`)로 돌기
  때문에, harness 아티팩트가 실제로 존재해야 슬래시 명령어/스킬을 쓸 수 있다.
- 안전 게이트(autonomy L0~L4 / approval / secret redaction / protected branch / do-not-merge)는
  그대로. 어떤 harness 패턴도 이를 우회하지 않는다.

### 용어 충돌 주의

- **harness 플러그인** (`.claude-plugin/`, `.codex-plugin/`) = Claude Code/Codex 의 스킬 번들.
- **Yule 런타임 플러그인** (`plugins/<id>/manifest.json`) = hook-provider 파이썬 모듈(별개 개념).

## 2. 슬래시 명령어 카탈로그 (built-in)

`grantable=false` 는 대화형 UI 전용이라 에이전트에 부여하지 않는다.

| 명령어 | 용도 | grant 가능 | 비고 |
| --- | --- | --- | --- |
| `/compact` | 대화 압축으로 컨텍스트 정리 | ✅ | compact→vault 의 기반. SDK `query("/compact …")` 로 호출 가능, `compact_boundary` 로 토큰 반환 |
| `/context` | 컨텍스트 사용량 표시 | ✅ | |
| `/clear` | 대화 기록 초기화 | ✅ | |
| `/memory` | CLAUDE.md/메모리 편집 | ✅ | 변경성 — L2 기록 필수 |
| `/export` | 대화 내보내기 | ✅ | read 성격 |
| `/init` | 가이드 초기화 | ✅ | engineering tech-lead |
| `/security-review` | 보안 취약점 분석 | ✅ | engineering(qa/devops/ai), legal |
| `/diff` | 변경 사항 확인 | ✅ | |
| `/cost` | 토큰 사용량 통계 | ✅ | finance 추적 |
| `/model` `/config` `/permissions` `/agents` `/plugin` `/mcp` | 세션/설정 UI | ❌ | 운영자 대화형 전용 |

## 3. agent ↔ 명령어/스킬 grant 매트릭스

부서 단위로 grant 하고, 필요 시 `<agent>/<role>` 오버라이드를 얹는다. 전체는
[`slash-command-grants.json`](../agents/grants/slash-command-grants.json) 이 SSoT.

| 부서 | C-level | built-in (요약) | custom 스킬 |
| --- | --- | --- | --- |
| engineering-agent | CTO | /compact /context /clear /memory /export /init /security-review /diff /cost | compact-to-vault, vault-curate, skill-author, research-collect |
| product-agent | CPO | /compact /context /export /diff | compact-to-vault, vault-curate |
| marketing-agent | CMO | /compact /context /export | compact-to-vault |
| hr-agent | CHRO | /compact /context /export | compact-to-vault, vault-curate |
| finance-agent | CFO | /compact /context /cost /export | compact-to-vault |
| sales-cs-agent | CRO | /compact /context /export | compact-to-vault |
| legal-agent | GC | /compact /context /security-review /export | compact-to-vault, vault-curate |
| planning-agent | Planning Ops | /compact /context /export | compact-to-vault |

오버라이드 예: `engineering-agent/tech-lead` 는 `skill-author` 를 강조 부여(부서 스킬 저작 owner).

## 4. compact→vault — 압축을 지식 적립으로

긴 세션은 LLM 입력 비용을 키우고 맥락을 흐린다. `/compact` 만으로는 압축 결과가 휘발한다.
`compact-to-vault` 는 압축 요약을 **버리지 않고 vault 의 curated task-log 노트로 적립**한다.

- 결정형 코어: [`apps/engineering-agent/src/yule_engineering/agents/harness/context_compaction.py`](../apps/engineering-agent/src/yule_engineering/agents/harness/context_compaction.py)
  - `build_compaction_summary()` — 보호 영역(첫 3 / 최근 5 / decision / synthesis / focus)을 보존하며
    중간 turn 을 한 줄 placeholder(`audit_id` 역참조)로 접는다.
  - `write_compaction_note()` — `10-projects/<project>/task-logs/task-log-compact-<session>.md`
    (날짜 prefix 금지, F8/#99 컨벤션). **working tree 만 작성, git commit/push 는 별도 L3 게이트.**
- 정책 근거: [`context-compression.md`](../policies/runtime/agents/engineering-agent/context-compression.md)
  (모델별 비율 threshold, 보호 영역, 절대 금지 영역).
- 스킬 spec: [`compact-to-vault.md`](../skills/compact-to-vault.md).
- 자동 트리거는 `YULE_COMPACT_TO_VAULT_ENABLED`(기본 off) 뒤에서만. live `/compact` 토큰 캡처는 후속 PR.

## 4b. grant 강제 (runtime enforcement)

grant table 은 "X 가 Y 에 부여됐나" 의 SSoT 이고, 런타임 강제는
[`agents/harness/grant_enforcement.py`](../apps/engineering-agent/src/yule_engineering/agents/harness/grant_enforcement.py)
가 책임진다. actor(부서 또는 `<부서>/<role>`)가 슬래시 명령어/스킬을 쓰려 할 때 세 판정 중 하나를 낸다.

| 상황 | 판정 |
| --- | --- |
| actor 에 grant 됨 | **ALLOW** |
| 미부여, 카탈로그에 존재 + grantable built-in | **ADVISORY** (경고 surface, 게이트웨이/운영자가 grant 확장 결정) |
| 미부여, 등록된 custom skill | **ADVISORY** |
| 미부여, grantable=false built-in (운영자 대화형 전용) | **BLOCK** |
| 카탈로그에 없는 명령어/스킬 | **BLOCK** |
| grant table 에 없는 actor | **BLOCK** |

advisory vs block 의 경계는 **코드에 고정**(`grant_enforcement.evaluate_command/skill`)되고
[`tests/agents/test_grant_enforcement.py`](../tests/agents/test_grant_enforcement.py) 가 잠근다.
원칙: *알려진(=카탈로그에 있고 부여 가능한) 능력의 미보유*는 over-block 하지 않고 advisory 로
surface; *알 수 없거나 grant 자체가 금지된 능력*은 block.

**hot-path 결선(실 dispatch).** role-runner dispatch 가 provider 를 부르기 직전,
`role_runner.build_role_runner_dispatcher(pre_dispatch_gate=…)` 가 gate 를 실행한다 — BLOCK
이면 provider 호출 없이 `STATUS_BLOCKED` take 를 반환(`agents/harness/hot_path.build_capability_block_gate`).
게이트웨이 결선은 `bootstrap.build_role_runner_dispatch_from_env(grant_table=…, receipt_sink=…)`
이며 `YULE_GRANT_ENFORCEMENT_ENABLED` 로 opt-in(기본 off, 미설정 시 기존 동작 그대로). capability 는
`RoleRunnerInput.metadata['capabilities']` 로 선언한다. ADVISORY 는 차단하지 않고 receipt 에 남는다.
회귀: [`tests/runners/test_role_runner_gate.py`](../tests/runners/test_role_runner_gate.py) ·
[`tests/runners/test_runner_bootstrap_enforcement.py`](../tests/runners/test_runner_bootstrap_enforcement.py) ·
[`tests/agents/test_hot_path.py`](../tests/agents/test_hot_path.py).

## 4c. execution receipt (실행 증명)

이번 run 이 무엇을 로드했고 무엇이 허용됐는지를
[`agents/harness/execution_receipt.py`](../apps/engineering-agent/src/yule_engineering/agents/harness/execution_receipt.py)
가 `ExecutionReceipt` 로 묶는다. 필드: loaded docs / loaded policies / selected agent · role /
granted skills · commands / blocked or missing / selected runner / warnings / compaction status /
cleanup status / security status. CLI: `yule harness receipt [--role <r>] [--runner <id>] [--capability …]
[--change-path …] [--change-summary …] [--json]`.

**매 run 결선.** enforcement opt-in(`YULE_GRANT_ENFORCEMENT_ENABLED`) 시 게이트웨이 dispatch 가
끝날 때마다 `hot_path.dispatch_receipt` 가 receipt 를 만들어 `session.extra['execution_receipts']`
(append-only, cap 50 — audit 는 트리밍하지 않음)에 적립한다.

**live `/compact` canary.** `compact_canary.run_compact_canary` 가 deterministic 추정과
live `compact_boundary`(`ClaudeCodeRunner.compact()`)를 같은 run 에서 측정해 **estimate vs live
오차**를 보고한다(`YULE_COMPACT_LIVE_CANARY_ENABLED` 기본 off, CLI `--live` 강제). `--output-format
stream-json` 의 `compact_boundary` 이벤트에서 pre/post 토큰을 캡처. 파싱
실패/비live 면 deterministic 추정치로 graceful fallback 하고 receipt 에 `token_source=estimate`
+ warning 을 남긴다. CLI: `yule harness compact --live …`.

## 4d. cleanup (용량/잔여 artifact 안전 정리)

[`agents/harness/cleanup.py`](../apps/engineering-agent/src/yule_engineering/agents/harness/cleanup.py)
는 allowlist 기반으로만 정리한다. 분류: `DELETABLE`(transient/regeneratable) ·
`PRESERVE`(audit/canonical — `*.sqlite3` 세션, agent_ops_audit, vault canonical, 원문
prompt/decision/synthesis/approval, 소스/정책/테스트) · `APPROVAL_NEEDED`(generated-but-tracked
harness 디렉터리, 대용량). **PRESERVE 가 항상 우선**, default 도 PRESERVE. dry-run 이 기본이며
execute 는 `--execute --yes` 둘 다 필요. CLI: `yule harness cleanup [--root …] [--execute --yes] [--json]`.

## 5. 에이전트가 스킬/플러그인을 직접 저작하는 절차

harness 디렉터리는 생성물이므로 직접 만들지 않는다. 항상 **SSoT → 생성** 경로:

1. 레지스트리에 spec 1개 저작 — 스킬은 `skills/<id>.md`(단일 SSoT), command/hook 은 `agents/<agent>/<layer>/<id>.md` (v0 frontmatter).
2. grant 선언 — `slash-command-grants.json` 의 `custom_skills` 등록 + `grants` 에 부서/autonomy 추가.
3. 인벤토리 표 갱신 — 해당 layer README 행 추가.
4. 생성기 재실행 — `python3 scripts/sync_harness_skills.py`.
5. 회귀 0 확인 — `test_slash_command_grants` + `test_harness_projection`.

이 절차를 캡슐화한 메타 스킬: [`skill-author.md`](../skills/skill-author.md).

## 6. 생성기 사용법

```bash
python3 scripts/sync_harness_skills.py          # 아티팩트 생성/갱신
python3 scripts/sync_harness_skills.py --check    # SSoT 와 어긋나면 exit 1 (CI/테스트)
```

생성물(전부 DO NOT EDIT 마커 포함):

- `.claude/skills/<id>/SKILL.md` — Claude Code 프로젝트 스킬
- `.agents/skills/<id>/SKILL.md` — Codex 프로젝트 스킬
- `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` — harness 플러그인 매니페스트

drift 방지: [`tests/agents/test_harness_projection.py`](../tests/agents/test_harness_projection.py).

## 7. Codex 설정

Codex 는 `CLAUDE.md` 가 아니라 **`AGENTS.md`** 를 읽는다. 그리고 스킬 디스커버리는
**설정 없이** repo 의 `.agents/skills` 를 자동 인식한다(cwd→repo→`~`→`/etc` 우선순위).
따라서 본 PR 의 커밋만으로 Codex 의 스킬/플러그인 설정은 사실상 끝난다.

로컬 `~/.codex/config.toml` 또는 repo `.codex/config.toml`(신뢰 프로젝트, gitignore)에서
추가로 둘 수 있는 것(선택):

```toml
model = "gpt-5.4"
approval_policy = "untrusted"          # 파괴적 작업 전 승인 — 본 레포 안전 정책과 일치

# (선택) MCP 서버 — Claude 쪽 MCP 와 평행하게
# [mcp_servers.figma]
# url = "https://mcp.figma.com/mcp"
# bearer_token_env_var = "FIGMA_OAUTH_TOKEN"
```

- `.codex/` 는 로컬 실행 설정이며 공유 정책으로 다루지 않는다([`AGENTS.md`](../AGENTS.md)). 따라서
  본 레포에는 커밋하지 않는다 — 공유 대상은 `.agents/skills` 와 `.codex-plugin/`.
- `AGENTS.md` 의 프로젝트 규칙(전역 안전/컨벤션)은 Codex 의 1차 컨텍스트가 된다.

## 8. Claude Code 쪽 사용

- 프로젝트 스킬은 `.claude/skills/` 에서 자동 로드된다. `/skills` 로 목록 확인, `/<skill-id>` 로 호출.
- `ClaudeCodeRunner`(`claude -p`)가 이 repo 에서 돌면 동일 스킬을 헤드리스로 쓴다(추후 grant 강제 wiring).
- 플러그인 배포(marketplace 패키징, 스킬을 플러그인 루트로 복사)는 후속 — 현재는 프로젝트 스킬 +
  매니페스트 스캐폴드까지.

## 9. 후속 PR (본 PR 비범위)

- 8개 부서 전체 custom 스킬 spec 확충(현재는 cross-cutting 위주).
- live `/compact` wiring — `ClaudeCodeRunner` 구현(현재 stub) 후 port 주입 + `compact_boundary` 캡처.
- ✅ grant 매트릭스 런타임 강제 — `grant_enforcement.py` (§4b) + **RoleRunner dispatch hot-path 결선**(`role_runner.pre_dispatch_gate` + `bootstrap`, `YULE_GRANT_ENFORCEMENT_ENABLED`).
- ✅ execution receipt(§4c) — 매 run 결선(`session.extra['execution_receipts']`) · ✅ compact→vault 프로토콜 + `/clear` 가드 · ✅ cleanup(§4d, hardened).
- ✅ live `/compact` 토큰 캡처 — `claude_code.ClaudeCodeRunner.{submit,compact}`(`YULE_CLAUDE_LIVE_ENABLED`, compact_boundary + graceful fallback).
- ✅ security review cross-cutting 게이트 + **auto-dispatch 판정**(`security_gate.assess_security_review`) — `docs/security-review.md`.
- 남은 후속: provider 별 live submit(codex/gemini) · MCP 서버 표준 wiring · Codex 멀티에이전트 연동 · 변경 metadata 를 dispatch 입력에 자동 채우는 producer 결선.

## 9b. provider projection 확장 (Claude/Codex → Gemini)

본 문서의 harness 브리지는 `harness` 필드(투영 대상 목록)로 Claude Code/Codex 에 투영한다.
이 필드의 의미(=projection target, backend 이름이 아님)와 Gemini projection 추가 절차,
plugin/hook/skill/MCP/backend 의 개념 분리는 [`plugin-taxonomy.md`](plugin-taxonomy.md) +
[`provider-capability-matrix.md`](provider-capability-matrix.md) 가 SSoT. 생성기
`scripts/sync_harness_skills.py` 의 `HARNESS_TARGETS` 레지스트리가 확장 지점이다.

## 10. 관련 문서

- 결정 근거 / ECC: [`ecc-foundation.md`](../policies/runtime/agents/engineering-agent/ecc-foundation.md) (A.3 개정)
- compact 정책: [`context-compression.md`](../policies/runtime/agents/engineering-agent/context-compression.md)
- 레지스트리 가이드: [`skills/README.md`](../skills/README.md) · [`commands/README.md`](../agents/engineering-agent/commands/README.md) · [`hooks/README.md`](../agents/engineering-agent/hooks/README.md)
- 자율/승인: [`autonomy-policy.md`](autonomy-policy.md) · [`approval-matrix.md`](approval-matrix.md)
