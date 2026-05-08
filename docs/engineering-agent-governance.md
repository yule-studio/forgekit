# engineering-agent governance — 운영자 통합 가이드 (Issue #69)

> **목적:** engineering-agent 부서 전체가 따를 GitHub / Obsidian 운영 규칙을 *운영자 시점* 으로 한 화면에 정리한다.
> **정책 본문:** [`policies/runtime/agents/engineering-agent/governance.md`](../policies/runtime/agents/engineering-agent/governance.md) (umbrella) + 3 layer 본문 (`obsidian-governance.md` / `write-ownership.md` / `github-workflow.md`).
> **출처:** Issue #69 (parent #20). 통합 입력 = #25 / #48 / #59.

본 가이드는 *어떤 정책 어디 봐야 하는지* 의 인덱스다. 정책 자체는 본문 markdown 이 책임지고, 본 가이드는 운영자가 매번 처음부터 읽지 않게 한다.

## 1. 통합 한 화면

```
                       [governance.md]
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
[obsidian-governance]  [write-ownership]  [github-workflow]
  - naming             - 3 mode           - issue / PR template
  - wikilink           - decision tree    - label
  - cross-link         - 7 role × surface - progress comment
  - backlink           - 5 핵심 질문 답   - 커밋 분할 / push
                       
      회귀 보호: tests/engineering/test_engineering_agent_governance_doc.py
```

## 2. 3-mode 결정 트리 — 1 분 요약

```
write 발생:
  Q1 부서 intake / status / 외부 notification?  Yes → gateway
  Q2 다역할 합의·충돌·통합?                        Yes → tech-lead
  Q3 자기 deliverable?                            Yes → 그 역할
  default → tech-lead
```

| mode | author | surface 예 |
| --- | --- | --- |
| `gateway-mediated` | engineering-agent/gateway | `#업무-접수` 응답 / `#봇-상태` / `/engineer_show` / kickoff·closure |
| `tech-lead-mediated` | engineering-agent/tech-lead | 합의 PR body / cross-role 충돌 결정 / 부서 정책 |
| `role-owned` | 해당 역할 (backend / frontend / ...) | 자기 PR body / 자기 take note / 자기 commit author |

## 3. 새 작업 시작 체크리스트

```
[ ] 1. 작업이 #20 의 sub-issue 인지 확인. 그 안에서만 운영.
[ ] 2. label 부착 — 실재 label 만 (✨ Feature / 📃 Docs / ✅ Test / 🔨 Refactor / ...).
[ ] 3. branch / worktree 신설. 컨벤션: feature/<short-purpose>-<scope-id>.
[ ] 4. kickoff comment 게시. 3-mode 중 어느 모드인지 명시.
[ ] 5. Obsidian 노트 3 종 (research / decision / task-log) 신설. naming + ## 관련 문서 강제.
[ ] 6. 선행 노트 (있으면) wikilink 로 연결 + repo mirror 가 있으면 backlink 추가.
[ ] 7. 변경 작업 + ≥3 commit 으로 논리 분할.
[ ] 8. progress comment 게시 (5 섹션).
[ ] 9. push (current branch only) + draft PR 생성 (G6 LiveGithubAppClient 우선).
[ ] 10. PR body 4 섹션 + Audit 블록 + repo PULL_REQUEST_TEMPLATE 준수.
```

## 4. 주의 — 영구 hard rail

본 governance 의 정책으로는 **풀 수 없는** 영구 금지:

- protected branch (`main`/`master`/`dev`/`prod`/`release`) 직접 push
- force push
- auto merge
- production deploy 자동화
- secret / token / pem / Authorization 헤더 출력
- 사용자 기존 변경 덮어쓰기

위 항목 변경은 별도 hard-rail 정책 PR + 사용자 결정이 필요. 본 governance 가 자체 권한으로 변경할 수 없다.

## 5. 정책 위치 인덱스

| 영역 | 파일 |
| --- | --- |
| Umbrella | [`policies/runtime/agents/engineering-agent/governance.md`](../policies/runtime/agents/engineering-agent/governance.md) |
| Obsidian | [`policies/runtime/agents/engineering-agent/obsidian-governance.md`](../policies/runtime/agents/engineering-agent/obsidian-governance.md) |
| Write ownership | [`policies/runtime/agents/engineering-agent/write-ownership.md`](../policies/runtime/agents/engineering-agent/write-ownership.md) |
| GitHub workflow | [`policies/runtime/agents/engineering-agent/github-workflow.md`](../policies/runtime/agents/engineering-agent/github-workflow.md) |
| 통합 입력 — ECC | [`policies/runtime/agents/engineering-agent/ecc-foundation.md`](../policies/runtime/agents/engineering-agent/ecc-foundation.md) (#25) |
| 통합 입력 — Harness | [`policies/runtime/agents/engineering-agent/team-architecture-patterns.md`](../policies/runtime/agents/engineering-agent/team-architecture-patterns.md) (#48) |
| 통합 입력 — Hermes 5 정책 | `memory-policy.md` / `recall-policy.md` / `context-compression.md` / `self-improvement-flow.md` / `scheduled-automation.md` (#59) |
| 회귀 test | [`tests/engineering/test_engineering_agent_governance_doc.py`](../tests/engineering/test_engineering_agent_governance_doc.py) |

## 6. Obsidian mirror 노트 (본 통합 작업)

| 노트 | 위치 |
| --- | --- |
| Research | [`notes/vault-mirror/.../research/2026-05-08_issue-69-research-engineering-agent-governance-synthesis.md`](../notes/vault-mirror/10-projects/yule-studio-agent/research/2026-05-08_issue-69-research-engineering-agent-governance-synthesis.md) |
| Decision | [`notes/vault-mirror/.../decisions/2026-05-08_issue-69-decision-engineering-agent-authoring-policy.md`](../notes/vault-mirror/10-projects/yule-studio-agent/decisions/2026-05-08_issue-69-decision-engineering-agent-authoring-policy.md) |
| Task-log | [`notes/vault-mirror/.../task-logs/2026-05-08_issue-69-task-log-governance-integration.md`](../notes/vault-mirror/10-projects/yule-studio-agent/task-logs/2026-05-08_issue-69-task-log-governance-integration.md) |

## 7. 신규 사용자 액션

본 governance 가 land 된 직후 운영자 직접 액션이 필요한 항목:

1. 사용자 vault 의 #48 / #59 노트에 본 통합 노트 backlink 수동 추가 (repo 외부라 자동 추가 불가).
2. (선택) `🎯 Core` / `🏗 Infrastructure` 등 추천 라벨을 GitHub repo 에 신설하면 자동 적용.
3. (선택) `yule memory reindex` 실행해 vault 인덱스 갱신.

## 8. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-08 | 초안 (Issue #69 — 운영자용 통합 가이드 신설) |
