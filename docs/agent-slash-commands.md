# 에이전트 슬래시 명령어 · 스킬 · 플러그인 (harness 브리지)

> 이슈 #185 의 사람용 SSoT. 에이전트(부서/역할)가 슬래시 명령어를 쓰고, 스킬·플러그인을
> 직접 저작하는 구조를 설명한다. 코드/데이터 SSoT 는
> [`agents/grants/slash-command-grants.json`](../agents/grants/slash-command-grants.json) +
> 레지스트리 markdown(`agents/<agent>/{skills,commands,hooks}/*.md`).

## 1. 한눈에 — Bridge 구조

```
[SSoT]  agents/<agent>/{skills,commands,hooks}/*.md   (ECC v0 markdown, 런타임 비의존)
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
- 스킬 spec: [`compact-to-vault.md`](../agents/engineering-agent/skills/compact-to-vault.md).
- 자동 트리거는 `YULE_COMPACT_TO_VAULT_ENABLED`(기본 off) 뒤에서만. live `/compact` 토큰 캡처는 후속 PR.

## 5. 에이전트가 스킬/플러그인을 직접 저작하는 절차

harness 디렉터리는 생성물이므로 직접 만들지 않는다. 항상 **SSoT → 생성** 경로:

1. 레지스트리에 spec 1개 저작 — `agents/<agent>/<layer>/<id>.md` (layer = skill|command|hook, v0 frontmatter).
2. grant 선언 — `slash-command-grants.json` 의 `custom_skills` 등록 + `grants` 에 부서/autonomy 추가.
3. 인벤토리 표 갱신 — 해당 layer README 행 추가.
4. 생성기 재실행 — `python3 scripts/sync_harness_skills.py`.
5. 회귀 0 확인 — `test_slash_command_grants` + `test_harness_projection`.

이 절차를 캡슐화한 메타 스킬: [`skill-author.md`](../agents/engineering-agent/skills/skill-author.md).

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
- grant 매트릭스 런타임 강제 — RoleRunner dispatch 시 미부여 슬래시 차단.
- MCP 서버 표준 wiring(보안 검토) + Codex 멀티에이전트 연동.

## 10. 관련 문서

- 결정 근거 / ECC: [`ecc-foundation.md`](../policies/runtime/agents/engineering-agent/ecc-foundation.md) (A.3 개정)
- compact 정책: [`context-compression.md`](../policies/runtime/agents/engineering-agent/context-compression.md)
- 레지스트리 가이드: [`skills/README.md`](../agents/engineering-agent/skills/README.md) · [`commands/README.md`](../agents/engineering-agent/commands/README.md) · [`hooks/README.md`](../agents/engineering-agent/hooks/README.md)
- 자율/승인: [`autonomy-policy.md`](autonomy-policy.md) · [`approval-matrix.md`](approval-matrix.md)
