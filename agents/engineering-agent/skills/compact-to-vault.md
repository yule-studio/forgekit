---
id: compact-to-vault
title: 압축→vault 기록 (compact to vault)
owner_role: tech-lead
applicable_roles:
  - tech-lead
  - backend-engineer
  - ai-engineer
  - product-designer
  - qa-engineer
  - devops-engineer
  - frontend-engineer
cross_department: true   # grant table(slash-command-grants.json)이 전 부서에 부여
autonomy_level: L2_AUTO_RECORD_REQUIRED   # vault git commit 시 L3_HUMAN_OR_ROLE_APPROVER
input_contract:
  - session_id
  - focus            # 압축 초점 토픽 (optional, /compact <instructions> 대응)
  - vault_commit     # bool — true 면 L3 (git commit/push)
output_contract:
  - compaction_summary       # 보호 영역 보존한 압축 요약 본문
  - task_log_note_path       # 10-projects/<project>/task-logs/task-log-compact-<session>.md
  - pre_tokens               # 압축 전 추정 토큰 (live /compact 시 compact_boundary 메타)
  - post_tokens
preconditions:
  - session 이 존재하고 prompt 원문이 남아 있음
  - vault writer(knowledge_writer) 사용 가능
side_effects:
  - vault 에 curated task-log 노트 1개 생성 (append-safe)
  - agent_ops_audit entry 기록 (action=compact_to_vault)
  - vault_commit=true 일 때만 vault git commit/push (L3, 승인 게이트)
references:
  - policies/runtime/agents/engineering-agent/context-compression.md
  - docs/agent-slash-commands.md
  - apps/engineering-agent/src/yule_engineering/agents/harness/context_compaction.py
  - apps/engineering-agent/src/yule_engineering/agents/obsidian/knowledge_writer.py
related_hooks: []
---

# Skill: compact-to-vault

> **현재 단계:** 결정형(deterministic) 코어 + vault 기록 + port seam. live `/compact`(Claude Code/Codex) 호출 wiring 은 후속 PR.
> **단일 owner:** vault/obsidian write governance 는 `engineering-agent / tech-lead`. 다른 부서는 grant table 로 호출하되 actor 라벨은 tech-lead 위임.
> **왜 중요한가:** 대화/세션이 길어질수록 LLM 입력 비용이 커지고 맥락이 흐려진다. 이 skill 은 압축으로 *전송 비용*을 줄이면서, 동시에 압축 요약을 **버리지 않고 vault 에 영속**시켜 나중에 retrieval/회고에 쓰게 한다. 즉 `/compact` 를 단순 휘발이 아니라 *지식 적립*으로 바꾼다.

## Trigger

- 세션의 누적 토큰이 모델 context window 비율 threshold 도달 ([context-compression.md](../../../policies/runtime/agents/engineering-agent/context-compression.md) 3.1).
- 사용자가 `/compact` 또는 "정리해줘 / 압축해줘 / 여기까지 요약해서 남겨" 발화.
- 긴 deliberation/research 세션을 닫기 직전(work_report FINAL) — 요약을 task-log 로 적립.
- 다른 부서(product/hr/legal …)가 자기 작업 세션을 vault 에 남기려 할 때(grant table 부여 범위).

## Workflow

```
session_id (+ focus)
        │
        ▼
[1] resolve_session + token threshold (모델별 비율)
        │
        ▼
[2] build_compaction_summary           ← 보호 영역 보존(원문 prompt/decision/synthesis)
        │   (harness.context_compaction)
        ▼
[3] (옵션, live) harness /compact 호출 → compact_boundary 의 pre/post 토큰 캡처   [후속 PR]
        │
        ▼
[4] write_task_log_note                ← knowledge_writer 컨벤션, append-safe
        │
        ▼
[5] agent_ops_audit (compact_to_vault, L2)
        │
        ▼
[6] (vault_commit=true) vault git commit/push   ← L3 승인 게이트
```

1. 세션을 로드하고 모델 family 기준 threshold 를 계산한다(절대 토큰 아님).
2. `build_compaction_summary()` 로 middle turn 을 한 줄 placeholder 로 접되 **보호 영역은 절대 손대지 않는다**: 첫 3 메시지, 최근 5 메시지, 모든 `kind=decision`/tech-lead synthesis, RoleProfile output 헤더 (context-compression 3.2).
3. (후속) harness 가 live 라면 `/compact <focus>` 를 호출하고 `compact_boundary` 메시지에서 `pre_tokens`/`post_tokens` 를 읽어 metric 으로 기록한다. 현재 단계는 추정치.
4. 압축 요약을 vault 의 task-log 노트로 기록한다 — 원문은 SQLite/Obsidian export 에 그대로(압축은 전송 영역에만).
5. `action=compact_to_vault` audit 항목(L2) 추가.
6. `vault_commit=true` 면 vault git commit/push — **L3, 사람/role-approver 승인 필요**.

## Decision Matrix

| signal | action |
| --- | --- |
| threshold 미도달 + 사용자 미요청 | noop (압축 불필요) — 강제 압축 금지 |
| 보호 영역만으로 이미 budget 이하 | placeholder 접기 생략, 요약만 기록 |
| focus 지정됨 | 해당 토픽 turn 우선 보존 + 요약 초점 |
| vault_commit 미지정/false | 노트 파일만 생성(working tree), commit 은 사람 결정 |
| vault_commit=true 인데 orphan/broken link | push 금지 — `vault-curate` governance 위반, 차단 후 안내 |
| session 없음/prompt 원문 유실 | ✋ 압축 거부 — audit root 가 없으면 압축은 audit 파괴 |

## How to Use

### Quick Mode (현재 단계 — 결정형)

```python
from yule_engineering.agents.harness.context_compaction import (
    from_workflow_session,
    build_compaction_summary,
    write_compaction_note,
)

turns = from_workflow_session(session)          # 방어적 어댑터
summary = build_compaction_summary(turns, session_id=session.session_id, focus="...")
note = write_compaction_note(
    summary, vault_root=vault_root, project="yule-studio-agent", commit=False
)
# note.relative_path == 10-projects/<project>/task-logs/task-log-compact-<session>.md
# note.committed is False — commit/push 는 별도 L3 단계
```

### Full Mode (후속 PR — live /compact)

```text
[ tech-lead ] 호출:
  skill_id: compact-to-vault
  inputs: { session_id, focus, vault_commit: false }
  outputs: { compaction_summary, task_log_note_path, pre_tokens, post_tokens }
```

후속 PR 의 dispatcher 가 harness(`claude -p` / `codex`)에 `/compact <focus>` 를 보내고 `compact_boundary` 토큰을 캡처해 `pre_tokens`/`post_tokens` 를 채운다.

## Hard rails

- **보호 영역 압축 금지.** 원문 prompt / decision body / synthesis consensus / approval card / commit message / PR body 는 어떤 경우에도 압축·절단하지 않는다(context-compression 6장).
- **vault commit 은 L3.** 노트 *작성*(working tree)은 L2 자동, *commit/push* 는 사람/role-approver 승인.
- **secret 미기록.** 압축 요약/노트/audit 에 key·token 값 금지(redact_secret_like 1차 책임).
- **orphan/broken link 면 push 금지.** vault governance 의 hard rail 그대로.

## Obsidian 기록

- 기록 위치: `10-projects/<project>/task-logs/task-log-compact-<session>.md` (kind=task-log, 날짜 prefix 금지 — F8/#99 컨벤션, 날짜는 frontmatter created_at)
- frontmatter: knowledge_writer 컨벤션(title/kind/status/created_at/tags/related/home_hub) + `original_prompt` mirror
- 본문: 핵심 요약 / 압축된 turn placeholder 목록(audit_id 링크) / 다음 액션

## 검증 / 회귀

| 테스트 | 위치 | 상태 |
| --- | --- | --- |
| 보호 영역 보존 + placeholder 접기 | `tests/agents/test_context_compaction.py` | ✅ (본 PR) |
| vault 노트 frontmatter/경로 | `tests/agents/test_context_compaction.py` | ✅ (본 PR) |
| grant table 이 전 부서에 compact-to-vault 부여 | `tests/agents/test_slash_command_grants.py` | ✅ (본 PR) |
| live `/compact` compact_boundary 캡처 | (후속 PR) | ⏳ 대기 |
