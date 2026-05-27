---
id: vault-curate
title: inbox→curated 승격 (vault curate)
owner_role: tech-lead
applicable_roles:
  - tech-lead
  - ai-engineer
  - product-designer
cross_department: true   # grant table 이 product/hr/legal 에도 부여
autonomy_level: L3_HUMAN_OR_ROLE_APPROVER
input_contract:
  - source_note          # 00-inbox 의 raw 자료 경로
  - home_hub             # 승격 후 소속 hub (20-areas / 10-projects/<project>)
output_contract:
  - curated_note_path    # 새로 생성된 curated 노트 경로
  - related_links        # 연결한 관련 노트
preconditions:
  - source_note 가 00-inbox 에 존재
  - retrieval eval fixture 가 최소 50 (대량 생성 시)
side_effects:
  - vault 에 신규 curated 노트 생성 (inbox 내 이동 아님)
  - agent_ops_audit entry (action=vault_curate)
  - vault git commit/push (승인 게이트)
references:
  - docs/memory.md
  - src/yule_orchestrator/agents/obsidian/knowledge_writer.py
  - agents/engineering-agent/skills/compact-to-vault.md
related_hooks: []
---

# Skill: vault-curate

> **현재 단계:** 정의 layer + knowledge_writer 재사용. dispatcher 자동 호출은 후속 PR.
> **단일 owner:** vault write governance 는 `engineering-agent / tech-lead`.
> **핵심 규칙:** curated note 는 inbox 안에서 *자리만 옮기는 게 아니라* **새로 만드는 것**이다.

## Trigger

- `00-inbox` 에 raw 자료가 쌓였고 정제가 필요할 때.
- `compact-to-vault` 가 만든 task-log 가 재사용 가치가 있어 area/project 노트로 승격될 때.
- 사용자가 "이거 정리해서 정식 노트로 만들어줘" 발화.

## Workflow

```
source_note (00-inbox)
        │
        ▼
[1] read raw + 핵심 추출
        │
        ▼
[2] build curated note     ← 필수 frontmatter + 본문 5섹션
        │
        ▼
[3] link related notes     ← orphan/broken link 검사
        │
        ▼
[4] retrieval eval 영향 확인 ← 대량 생성 시 점수 하락이면 regression
        │
        ▼
[5] write + (승인 후) vault commit/push
```

1. inbox raw 자료를 읽고 핵심을 추출한다.
2. 필수 frontmatter(title/kind/status/created_at/tags/related/home_hub) + 본문 5섹션(핵심 요약 / 내 해석 / 적용 맥락 / 관련 노트 / 참고)으로 **새 노트**를 만든다.
3. 관련 노트를 링크하고 orphan/broken link 가 없는지 검사한다(있으면 push 금지).
4. note 를 많이 추가하는 경우 retrieval eval(최소 50 fixture, top-5) 점수가 떨어지지 않는지 확인한다 — 떨어지면 "지식 추가 성공"이 아니라 **regression**.
5. 작성 후 vault git commit/push — **L3 승인 게이트**.

## Decision Matrix

| signal | action |
| --- | --- |
| source 가 inbox 밖 | ✋ 거부 — 본 skill 은 inbox raw 만 입력 |
| "왜" 설명이 추상적/짧음 | reject — 4구조(왜 필요/안 하면 문제/대안/트레이드오프) + 구체 예시 보강 |
| 대량 생성 + eval 점수 하락 | push 금지 — regression 으로 보고 |
| orphan / broken link | push 금지 |
| 가이드라인/커리큘럼 노트 | 코칭 톤 금지 — 공식 문서(목적/범위/competency/산출물/검증) 스타일 |

## How to Use

### Quick Mode

```python
from yule_orchestrator.agents.obsidian.knowledge_writer import build_knowledge_note
note = build_knowledge_note(...)   # 필수 frontmatter + 5섹션
# orphan/eval 검사 통과 후 commit
```

### Full Mode (후속 PR)

```text
[ tech-lead ] skill_id: vault-curate
  inputs: { source_note, home_hub }
  outputs: { curated_note_path, related_links }
```

## Hard rails

- **inbox 내 단순 이동 금지** — 승격은 새 curated 노트 생성.
- **eval 없이 대량 curated push 금지** — fixture 최소 50 / 목표 100 / top-5.
- **orphan / broken link 면 push 금지.**
- **vault commit 은 L3** — 사람/role-approver 승인.

## Obsidian 기록

- 승격 결과: `20-areas/<area>/...` 또는 `10-projects/<project>/...` (home_hub 기준)
- 원본 inbox 자료는 보존(삭제 아님)

## 검증 / 회귀

| 테스트 | 위치 | 상태 |
| --- | --- | --- |
| knowledge_writer frontmatter/5섹션 | `tests/obsidian/` (기존) | ✅ |
| grant table 부여 범위 | `tests/agents/test_slash_command_grants.py` | ✅ (본 PR) |
| dispatcher 자동 호출 | (후속 PR) | ⏳ |
