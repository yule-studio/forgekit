---
id: skill-author
title: 스킬/플러그인 저작 (skill author)
owner_role: tech-lead
applicable_roles:
  - tech-lead
  - ai-engineer
  - backend-engineer
cross_department: true   # 부서 게이트웨이가 자기 도메인 스킬을 저작 가능
autonomy_level: L2_AUTO_RECORD_REQUIRED
input_contract:
  - layer            # skill | command | hook
  - id               # kebab-case 신규 식별자
  - owner_role
  - intent           # 무엇을 하는 capability 인지
output_contract:
  - spec_path        # agents/<agent>/<layer>/<id>.md
  - grant_patch      # slash-command-grants.json 에 추가할 grant (제안)
  - projection       # 재생성된 harness 아티팩트 (.claude / .agents / plugin)
preconditions:
  - id 가 동일 layer 에서 미사용
  - intent 가 기존 skill 과 중복 아님 (단일 책임)
side_effects:
  - 레지스트리에 신규 markdown spec 1개 생성
  - (선택) slash-command-grants.json grant 추가
  - scripts/sync_harness_skills.py 재실행으로 harness 투영물 갱신
  - agent_ops_audit entry (action=skill_author)
references:
  - policies/runtime/agents/engineering-agent/ecc-foundation.md
  - agents/engineering-agent/skills/README.md
  - scripts/sync_harness_skills.py
  - docs/agent-slash-commands.md
related_hooks: []
---

# Skill: skill-author

> **이 skill 이 "에이전트가 플러그인을 직접 만든다"의 경로다.**
> 에이전트는 harness 디렉터리(.claude / .agents)를 직접 손대지 않는다 — **레지스트리에 spec 1개를 저작**하고 grant 를 선언한 뒤 **생성기를 재실행**하면 harness 플러그인이 갱신된다. SSoT 는 항상 레지스트리.

## Trigger

- 부서/역할에 기존에 없는 반복 작업 패턴이 생겼고, 이를 재사용 단위로 굳히고 싶을 때.
- 사용자가 "이 작업을 스킬로 만들어줘 / 명령어로 등록해줘" 발화.
- 새 lifecycle 정책 점이 필요할 때(hook).

## Workflow

```
intent (+ layer, id, owner_role)
        │
        ▼
[1] 중복/단일책임 검사        ← 기존 레지스트리 인벤토리 대조
        │
        ▼
[2] spec 작성                 ← layer 별 v0 frontmatter + 본문 섹션
        │   skill: Trigger/Workflow/Decision Matrix/How to Use
        │   command: When to Use/Trigger/Inputs/Outputs/Edge/Examples
        │   hook: 트리거/동작/차단·통과/실패 routing/audit
        ▼
[3] grant 선언               ← slash-command-grants.json 의 custom_skills + grants 패치
        │
        ▼
[4] 인벤토리 표 갱신          ← 해당 README 의 인벤토리 행 추가
        │
        ▼
[5] 생성기 재실행             ← scripts/sync_harness_skills.py
        │   → .claude/skills, .agents/skills, .claude-plugin/plugin.json, .codex-plugin/plugin.json
        ▼
[6] 검증                      ← governance test + drift-guard test 0 회귀
```

1. 동일 layer 에 같은 id/책임이 이미 있는지 인벤토리로 확인한다(단일 책임 원칙).
2. layer 별 v0 frontmatter 스키마와 본문 섹션을 채운 spec markdown 을 만든다.
3. custom 스킬이면 `slash-command-grants.json` 의 `custom_skills` 에 등록하고, 어느 부서가 어떤 autonomy 로 쓸지 `grants` 에 추가한다.
4. 해당 layer README 의 인벤토리 표에 행을 추가한다.
5. `python3 scripts/sync_harness_skills.py` 로 harness 투영물을 재생성한다 — **harness 파일을 손으로 만들지 않는다**.
6. governance test(`test_slash_command_grants.py`) + drift-guard test(`test_harness_projection.py`)로 0 회귀를 확인한다.

## Decision Matrix

| signal | action |
| --- | --- |
| 기존 skill 과 70%+ 겹침 | 신규 생성 금지 — 기존 spec 보강 |
| 부작용이 secret/merge/deploy 포함 | autonomy L4 + required_approval=human, 본문에 사유 명시 |
| harness 파일을 직접 수정하려는 충동 | ✋ 금지 — 항상 spec→생성 경로 |
| grant 없이 spec 만 추가 | orphan skill — governance test 가 차단 |
| id 가 기존과 충돌 | 거부 — 다른 id 사용 |

## How to Use

### Quick Mode

```bash
# 1) spec 작성 (예: agents/<agent>/skills/<id>.md)
# 2) grant 등록 (agents/grants/slash-command-grants.json)
# 3) 투영물 재생성
python3 scripts/sync_harness_skills.py
# 4) 회귀 확인
python3 -m unittest tests.agents.test_slash_command_grants tests.agents.test_harness_projection
```

### Full Mode (후속 PR)

dispatcher 가 본 skill 의 frontmatter 를 읽어 `layer` 별 템플릿을 스캐폴드하고, grant 패치 제안 + 생성기 실행 + 테스트를 한 번에 수행한다.

## Hard rails

- **레지스트리가 SSoT.** harness 디렉터리(.claude/.agents/plugin)는 생성물 — 직접 편집 금지.
- **grant 없는 spec 금지(orphan).** spec 추가 시 grant 도 함께.
- **L4(merge/deploy/secret)는 사람 승인 강제.**
- **단일 책임.** 1 skill = 1 capability.

## Obsidian 기록

- 저작 결정: `10-projects/yule-studio-agent/decisions/<date>_decision_<id>.md` (kind=decision)

## 검증 / 회귀

| 테스트 | 위치 | 상태 |
| --- | --- | --- |
| grant ↔ spec 일관성 | `tests/agents/test_slash_command_grants.py` | ✅ (본 PR) |
| harness 투영 drift-guard | `tests/agents/test_harness_projection.py` | ✅ (본 PR) |
| dispatcher 자동 스캐폴드 | (후속 PR) | ⏳ |
