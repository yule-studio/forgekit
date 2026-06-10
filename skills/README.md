# skills — Yule 스킬 단일 SSoT

> 본 디렉터리는 **모든 스킬의 단일 출처(SSoT)** 다. 부서/역할별로 흩어져 있던
> 스킬 정의(구 `agents/engineering-agent/skills/` · 구 `prompts/skills/`)를 한 곳으로
> 통합했다. 스킬은 cross-cutting(여러 에이전트 공용)이므로 특정 에이전트 밑이 아니라
> 최상위 `skills/` 에 둔다.

## 0. 두 종류의 스킬 (둘 다 본 디렉터리에 산다)

| 종류 | 파일 | 소비 방식 | 투영 |
| --- | --- | --- | --- |
| **A. Harness registry skill** | `skills/<id>.md` (frontmatter + 4 섹션) | `agents/grants/slash-command-grants.json` 의 grant + `scripts/sync_harness_skills.py` 로 `.claude/skills`·`.agents/skills`·플러그인에 **생성 투영** → Claude Code/Codex 슬래시 단위 | ✅ 자동 생성 (harness 디렉터리 손 편집 금지) |
| **B. Portable prompt-recipe skill** | `skills/pm/<verb-noun>.md` (5 섹션 recipe) | 역할 `prompt.md` 의 "참고 skills" 섹션이 `skills/<domain>/<skill>.md` 로 referencing → 런타임에 inline 첨부 | ❌ 투영 안 함 (프롬프트 재료) |

둘은 보완 관계다 — **B 는 프롬프트 재료**, **A 는 harness 가 실제로 호출하는 슬래시 단위**.
harness 쪽 상세: [`docs/agent-slash-commands.md`](../docs/agent-slash-commands.md).

---

## A. Harness registry skill

**Skill = 한 역할(또는 여러 역할)이 수행 가능한 단일 capability 의 명세.** input/output
contract · owner · autonomy(L0~L4) · lifecycle 연동을 한 파일에 모은다.

### frontmatter 스키마 v0

```yaml
---
id: research-collect            # kebab-case 고정
title: 자료 수집 (research collect)
owner_role: tech-lead
applicable_roles:
  - tech-lead
  - ai-engineer
  - backend-engineer
autonomy_level: L1_AUTO_RECORD_REQUIRED
input_contract: [prompt, active_research_roles]
output_contract: [research_pack, research_status, research_source_count]
preconditions:
  - session.extra has research_forum_thread_id (when forum mode)
side_effects:
  - session.extra.research_pack written
  - agent_ops_audit entry recorded
references:
  - policies/runtime/agents/engineering-agent/lifecycle-mvp.md
---
```

### 본문 4 섹션 (필수, 순서 고정)

`## Trigger` → `## Workflow` → `## Decision Matrix` → `## How to Use` (Quick/Full Mode).
ECC (`affaan-m/everything-claude-code`) 의 `skills/<id>/SKILL.md` 패턴 차용. 결정 근거:
[`policies/runtime/agents/engineering-agent/ecc-foundation.md`](../policies/runtime/agents/engineering-agent/ecc-foundation.md) §2.2.

### 작성 가이드라인

- **단일 책임** (1 skill = 1 capability). **autonomy_level 명시 필수** (L3+ 는 왜 사람 승인이
  필요한지 본문에). input/output contract 는 frontmatter 에서 deterministic. side_effects 전부
  나열. 변경 시 `references` 갱신.

### 인벤토리

| skill | owner | autonomy | 상태 |
| --- | --- | --- | --- |
| [`research-collect`](research-collect.md) | tech-lead | L1 | 정의 |
| [`compact-to-vault`](compact-to-vault.md) | tech-lead | L2 (vault commit L3) | 정의 + 결정형 코어 + tests (#185) |
| [`vault-curate`](vault-curate.md) | tech-lead | L3 | 정의 (#185) |
| [`skill-author`](skill-author.md) | tech-lead | L2 | 정의 (#185, 메타 — 스킬/플러그인 저작) |

새 registry skill 추가: ① `<id>.md` 작성 → ② 본 인벤토리 행 추가 → ③ `slash-command-grants.json`
에 grant + `python3 scripts/sync_harness_skills.py` 재실행(투영) → ④ `python3 -m unittest discover
-s tests -t .` (특히 `test_slash_command_grants` · `test_harness_projection`) 회귀 0.

---

## B. Portable prompt-recipe skill

모든 부서/역할이 참조하는 **portable skill markdown**. 어떤 런타임(Claude/Gemini/Cursor)에서든
그대로 inline 으로 붙여 쓸 수 있어야 한다.

### 디자인 원칙

1. **Portable**: front-matter 외 도구-specific 토큰 금지. 어느 런타임에서든 inline 사용.
2. **Single-purpose**: 한 skill 은 한 작업만 (PRD 작성 / OKR 정렬 / 회고 등 단일 단위).
3. **Recipe 5 섹션**: `When to use` / `Inputs` / `Steps` / `Output` / `Quality bar`.
4. **No live secret**: 환경 변수/키/토큰 직접 인용 금지 (PasteGuard·hookify 차단).
5. **`github.com/phuryn/pm-skills` 패턴** 차용 — PM 도메인부터 land.

### 구조

```
skills/
├── README.md             ← 본 문서
├── <id>.md               ← A. harness registry skill (compact-to-vault 등)
├── agent_spawn.md.tmpl   ← agent spawn 프롬프트 템플릿
└── pm/                   ← B. Product Management lifecycle recipes (14)
```

### 역할 manifest 연결

각 role 의 `prompt.md` "참고 skills" 섹션이 `skills/pm/<skill>.md` 로 referencing.
governance test (`tests/engineering/test_corporate_structure_governance.py`) 가 referencing
누락/깨진 링크를 차단. 새 portable skill 추가: ① `skills/pm/<verb-noun>.md`(5 섹션) →
② 관련 role `prompt.md` "참고 skills" 에 항목 추가 → ③ governance test 자동 검증.

---

## 참고

- `github.com/phuryn/pm-skills` — PM lifecycle reference 카탈로그.
- `policies/runtime/agents/corporate-org-chart.md` — 부서/역할 매트릭스.
- [`docs/agent-slash-commands.md`](../docs/agent-slash-commands.md) — harness 슬래시 명령어/스킬/플러그인 (#185).
