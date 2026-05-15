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

## 4.1 Runtime governance hard rails (P0-T)

**코드 SSoT**: [`src/yule_orchestrator/agents/governance/runtime_policy.py`](../src/yule_orchestrator/agents/governance/runtime_policy.py).
**회귀 test**: [`tests/governance/test_runtime_policy.py`](../tests/governance/test_runtime_policy.py).
**docs cross-link**: [`/docs/github-agent-workos.md`](github-agent-workos.md) §1.2.1 / [`/docs/memory.md`](memory.md).

### 4.1.1 Git / branch / PR / tag

| 영역 | 규칙 | 코드 | Caller 통합 (P0-T) |
| --- | --- | --- | --- |
| Branch | protected branch 직접 작업 금지. 표준 prefix `feat/fix/chore/refactor/docs/test/perf`. issue 번호 anchor 권장. | `validate_branch_name`, `derive_standard_branch_name` | `CodingExecutorWorker.process_job` 가 branch 결정 후 호출, deny 시 `REASON_BRANCH_POLICY_VIOLATION` terminal |
| Commit | 의미 있는 작업 단위. raw 수집과 curated promotion 분리. 30+ vault 변경은 분할. 봇 identity 만 사용. force push 금지. | (commit author = GitHub App identity 만) | `LocalGitCommitter` 가 봇 identity 강제 |
| PR | draft 기본. repo PR template 우선. 5 섹션 (`purpose / scope / risks / tests / issue_linkage`) + audit block 필수. | `validate_pr_body` | `GithubAppDraftPRCreator.open` 가 `_draft_pr_body` 직후 호출, warning logger 로 audit. `_draft_pr_body` 자체가 5 섹션 + audit 충족 |
| Tag/release | `RepoContract.tag_policy` 기반. `none` 이면 자동 발행 금지 + audit. 실제 create 는 L3 별도. | `RepoContract.tag_policy` + `has_tag_policy` | (별 worker — 후속 PR) |
| Progress marker | 5 단계 (issue_created / coding_dispatch_queued / coding_in_progress / draft_pr_opened / coding_blocked) | `stamp_progress_marker` | `CodingExecutorWorker._stamp_progress` 가 각 분기마다 호출 |

### 4.1.2 Vault / inbox / curated note / hub linkage

- `00-inbox` 는 **raw 자료 보관소** — curated 승격 대상이 아님. 승격은
  `20-areas` / `40-patterns` / `60-troubleshooting` / `10-projects/*`
  아래에 **새 curated note** 를 만드는 행위.
- Curated note 필수 frontmatter (7 키): `title / kind / status /
  created_at / tags / related / home_hub`.
- Curated note 필수 본문 섹션 (5 종): `핵심 요약 / 내 해석 / 적용 맥락 /
  관련 노트 / 참고`.
- 하루 자동 생성/갱신 curated note 20~30 개 / raw reference 100 개 제한.
- 모든 curated note 는 ① `home_hub` 1 개 + ② `related` 최소 1 개.
- orphan note / broken link 면 push 금지.
- 검증: `validate_curated_note`, `is_inbox_path`, `detect_orphan_note`,
  `detect_broken_links`.

### 4.1.3 Retrieval eval

- 평가셋 스키마: `question / expected_notes / allowed_alternatives /
  failure_reason`.
- 최소 50 / 목표 100 문항.
- top-5 평가 — 기대 note 가 top-5 에 없으면 **regression** (지식 추가
  성공 아님).
- vault 구조 변경 / 대량 노트 추가 전후 eval 실행 의무. eval 없이
  대량 curated generation push 금지.
- 검증: `validate_retrieval_eval_entry`, `validate_retrieval_eval_fixture`.

### 4.1.4 Post-test hardening — 성능 개선 opening criteria

`correctness > visibility > maintainability > performance` 순서. 테스트가
green 이어도 성능 개선은 자동 의무가 아니다. 다음 8 종 중 **최소 1 개
가 충족돼야** 성능/고도화 작업을 연다:

1. queue_backlog (큐 적체)
2. runtime_status_latency (status 응답 30s+)
3. retrieval_eval_regression
4. prompt_size_ceiling (90%+ 근접)
5. large_file_rule (700 warning / 1000 split)
6. duplicate_work (중복 dispatch / 같은 repo contract / template 반복 작업)
7. critical_path_bottleneck (executor / approval / operator action 명시적 정체)
8. flaky_or_slow_test

성능 개선 작업 시 의무:
- baseline_measurement (이전 값)
- target_metric (개선 대상)
- behavior_change_separated (semantic 변경 ≠ perf refactor)
- regression_test

코드: `decide_hardening_opening(observations)` → `allowed` + `matched_criteria` + `required_artifacts`.

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
