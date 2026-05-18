# Engineering governance modes — intake contract SSoT (P1-R)

작업 시작 시점에 결정되는 6 + 1 governance 축.  intake 후 작업 도중에는 절대 바뀌지 않는다 (자율/승인 모드의 토글 금지 — ask-once contract).

코드 SSoT — [`agents/lifecycle/session_mode.py`](../src/yule_orchestrator/agents/lifecycle/session_mode.py),
[`agents/governance/repo_write_policy.py`](../src/yule_orchestrator/agents/governance/repo_write_policy.py).

## 1. 축 (session.extra 키)

| key | 값 | 기본값 | 의미 |
|---|---|---|---|
| `work_mode` | `approval_required` \| `autonomous_merge` | `approval_required` | 작업 중 사람 승인을 거치는지 여부 |
| `topology` | `single_repo` \| `multi_repo` | `single_repo` | 작업 대상 repo 갯수 |
| `scope` | `single_scope` \| `full_stack_single_repo` \| `layer_scoped` \| `cross_repo_program` | `single_scope` | 작업 깊이 |
| `branch_strategy` | `git_flow` | `git_flow` | branch naming 규칙 |
| `release_strategy` | `tagged_release` | `tagged_release` | release/hotfix 완료 시 tag 요구 |
| `issue_policy` | `issue_required` | `issue_required` | branch / commit / PR 전에 issue anchor 필수 |
| `mode_decided_by` | `user_explicit` \| `gateway_inferred` | 결정 |
| `mode_decided_at` | iso8601 UTC | 결정 시점 |

## 2. work_mode 의미

### `approval_required` (기본)
gateway 가 **중요한 write 단계마다** `#승인-대기` 카드를 게시.  사용자 승인 후 다음 단계로 진행.

승인 카드 필수 한국어 4 섹션 (`repo_write_policy.validate_approval_card_quality`):
- **작업 내용** — 무엇을 하는지
- **목적** — 왜 필요한지
- **영향 범위** — repo / branch / 위험도
- **다음 단계** — 승인 후 바로 이어질 다음 동작

vague / machine-like / 영문-only 카드는 enqueue 단계에서 차단.

### `autonomous_merge`
gateway 가 **end-to-end control** — intake → issue → branch (Git Flow) → coding → draft PR → ready_for_review → merge → 다음 slice continuation 까지 사람 개입 없이 진행.

**예외 — 사람 승인 카드가 올라가는 경우**:
1. **draft PR escalation** — gate 가 draft 거부 시 ready_for_review 전환 승인 카드
2. **operator_action 카드** — secret / access / 외부 사실 / blocker 가 필요한 경우
3. **HIGH risk 변경** — 보호 브랜치 정책 변경, secret 처리 변경 등

**일반 진행 메시지는 최소화** — final completion summary 만 운영방에 게시.

## 3. branch_strategy = git_flow

허용 prefix (`repo_write_policy.GIT_FLOW_BRANCH_PREFIXES`):
- `feature/` — 새 기능 (issue anchor 필수)
- `bugfix/` / `fix/` — 버그 수정 (issue anchor 필수)
- `hotfix/` — 긴급 수정 (issue 면제, tag 필수)
- `release/` — 릴리즈 준비 (issue 면제, tag 필수)
- `refactor/` — 리팩토링 (issue anchor 필수)
- `chore/` / `docs/` / `test/` — 보조 작업
- `agent/` — engineering-agent 가 만드는 branch

**protected branch** (main/master/develop/dev/prod/production/release) 직접 작업 절대 금지 — worktree 생성 단계에서 차단.

slug 부분: kebab-case lowercase + `_-.`만 허용.

## 4. release_strategy = tagged_release

`release/`, `hotfix/` branch 완료 시 **반드시** semver tag (`vMAJOR.MINOR.PATCH`, 선택 `-pre.1` 등) 를 남긴다.

tag 없이 release/hotfix 머지 완료 처리는 `repo_write_policy.enforce_release_tag` 가 차단 — `missing_release_tag` reason 으로 surface.

## 5. issue_policy = issue_required

모든 agent (Claude Code / Codex / engineering / 보조) 가 작업 시작 전 GitHub issue 또는 유효한 기존 issue anchor 를 확보해야 한다.

확보 방법 (적어도 하나):
1. branch 이름의 `issue-<n>` (예: `feature/auth-issue-12`)
2. `request.issue_number > 0` (work_order executor 등이 직접 지정)
3. `is_docs_only=True` 명시 (제한 예외)

확보 안 된 상태로 worktree 생성 시도 → `WorktreeProvisionError(reason=issue_required_for_repo_work)`.

**cross-repo** — yule-studio-agent 본 repo 뿐 아니라 봇이 GitHub write 하는 모든 target repo (예: `naver-search-clone`) 에 동일 적용.

## 6. intake — slash option 권장 (P1-R-2)

### 6.1 권장 방식 — `/engineer_intake` 명시 옵션

운영자 UX 우선.  prompt 는 업무 내용 중심, governance 는 슬래시 옵션으로 선택:

| option | choices |
|---|---|
| `work_mode` | `approval_required` / `autonomous_merge` |
| `branch_strategy` | `git_flow` |
| `release_strategy` | `tagged_release` |
| `issue_policy` | `issue_required` |
| `topology` | `single_repo` / `multi_repo` |
| `scope` | `single_scope` / `full_stack_single_repo` / `layer_scoped` / `cross_repo_program` |

예시:
```
/engineer_intake
  prompt: 네이버 검색 풀스택 MVP 구현해줘 https://github.com/yule-studio/naver-search-clone
  work_mode: autonomous_merge
  branch_strategy: git_flow
  release_strategy: tagged_release
  issue_policy: issue_required
  topology: single_repo
  scope: full_stack_single_repo
  write_requested: true
```

intake 직후 접수 메시지 끝에 `🛡 거버넌스 contract (intake 시점에 확정)` 블록이 자동 표시 — operator 가 6 키 + `mode_decided_by` 한눈에 확인.

### 6.2 우선순위 (`slash option > prompt token > default`)

slash option 이 prompt 토큰보다 강하다.  예:
- option `work_mode=approval_required` + prompt `"autonomous_merge"` 포함 → 결과 `approval_required` (slash 우선).
- option 생략 + prompt `"autonomous_merge"` → 결과 `autonomous_merge` (prompt fallback).
- 둘 다 생략 → default `approval_required` (안전측).

`mode_decided_by` 값:
- `slash_option_explicit` — slash option 으로 결정
- `user_explicit` — prompt 토큰으로 결정 (모든 3축 명시)
- `gateway_inferred` — default 또는 일부만 명시

### 6.3 prompt fallback (backward compatibility)

slash option 없이도 옛 prompt 방식 그대로 동작:

```
approval_required, git_flow, tagged_release, issue_required, single_repo, full_stack_single_repo
네이버 검색 풀스택 MVP 구현해줘
```

`parse_mode_hints` 가 한국어/영문 변형 모두 인식 (`자율 머지`, `승인 필요`, `git flow`, `tag release`, `issue required` 등).

## 7. 모드별 operator 운영 절차

| 시나리오 | approval_required | autonomous_merge |
|---|---|---|
| issue 생성 | gateway 가 카드 게시 → 사용자 승인 후 진행 | gateway 자동 생성 |
| draft PR | 자동 생성 후 카드 게시 | 자동 생성 후 자동 ready_for_review → merge |
| draft 가 gate 1단계 거부 | 카드에 `[draft 해제 + 머지 진행]` 한국어 4 섹션 | 동일 (escalation 시점에는 사람 승인 필요) |
| merge 후 다음 slice | 다음 slice 도 승인 카드 | 다음 slice 자동 진행 |
| 완료 보고 | 매 단계 카드 / 답신 | final completion 한 줄 |
| secret / access / blocker | operator_action 카드 (둘 다 동일) | operator_action 카드 |

## 8. 강제 위치 (live path hard guard)

| 검증 | 호출 지점 | 위반 시 |
|---|---|---|
| Git Flow branch | `LocalGitWorktreeProvisioner.provision` | `WorktreeProvisionError(reason=invalid_git_flow_branch / protected_branch_direct_work)` |
| issue anchor | `LocalGitWorktreeProvisioner.provision` | `WorktreeProvisionError(reason=issue_required_for_repo_work)` |
| commit message | `GithubAppCommitter.commit` + `commit-msg` hook | `CodingCommitError(reason=invalid_commit_*)` |
| PR title | `GithubAppDraftPRCreator.open` | `PolicyViolation(reason=invalid_pr_title_not_human_readable_korean)` |
| issue title | `GithubWriter.create_issue` | `PolicyViolation(reason=invalid_issue_title_not_human_readable_korean)` |
| approval card body | `pr_merge_adapter._approval_request_from_proposal` | `PolicyViolation(reason=approval_card_missing_sections)` |
| release tag | (별도 release 마감 path — operator runbook) | `PolicyViolation(reason=missing_release_tag / invalid_release_tag)` |

## 9. 관련 모듈

- 검증 SSoT: [`agents/governance/repo_write_policy.py`](../src/yule_orchestrator/agents/governance/repo_write_policy.py)
- 모드 영속화: [`agents/lifecycle/session_mode.py`](../src/yule_orchestrator/agents/lifecycle/session_mode.py)
- intake 진입: [`agents/coding/coding_session_context.py`](../src/yule_orchestrator/agents/coding/coding_session_context.py)
- slash command: [`discord/commands/__init__.py::_run_engineer_intake`](../src/yule_orchestrator/discord/commands/__init__.py)
- branch 검증 live 호출: [`agents/job_queue/coding_executor_live.py::LocalGitWorktreeProvisioner.provision`](../src/yule_orchestrator/agents/job_queue/coding_executor_live.py)
- approval 카드 quality: [`discord/integrations/pr_merge_adapter.py`](../src/yule_orchestrator/discord/integrations/pr_merge_adapter.py)
