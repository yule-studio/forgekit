# PR merge continuation — production runbook (P1-L-3)

Draft PR open 직후 자동으로 머지 / 승인 카드 / 다음 slice 까지 이어지는
runtime path 의 운영 가이드.  사람용 SSoT.

코드 SSoT —
[`agents/job_queue/pr_merge_continuation.py`](../src/yule_orchestrator/agents/job_queue/pr_merge_continuation.py),
[`agents/job_queue/pr_merge_continuation_worker.py`](../src/yule_orchestrator/agents/job_queue/pr_merge_continuation_worker.py),
[`agents/job_queue/next_slice_dispatcher.py`](../src/yule_orchestrator/agents/job_queue/next_slice_dispatcher.py),
[`runtime/coding_executor_runner.py::_pr_merge_continuation_loop`](../src/yule_orchestrator/runtime/coding_executor_runner.py).

## 1. 전체 흐름

```text
intake (gateway)
  ↓ explicit prompt token "approval_required / autonomous_merge"
  ↓ session.extra[work_mode/topology/scope/mode_decided_*] 영속
coding_execute (worker)
  ↓ draft PR 생성 성공
  ↓ _stamp_pr_merge_continuation 가
     session.extra[pr_merge_stage]=pr_merge_pending stamp
background loop (coding_executor_runner)
  ↓ 매 30s tick (env YULE_PR_MERGE_CONTINUATION_INTERVAL_SECONDS)
  ↓ work_mode 분기:
     ├ approval_required → ApprovalEnqueuer 가 카드 한 번만 게시
     │                     (audit "approval_card_enqueued" event 로 dedup)
     └ autonomous_merge  → PRMergeExecutor 가 gate + merge 시도
                           - gate fail → pr_merge_blocked
                           - merge success → pr_merged
approval reply (approval_required 경로)
  ↓ #승인-대기 채널 사용자 "승인" 회신
  ↓ reply_router _try_handle_pr_merge_reply
  ↓ handle_pr_merge_approval_reply → live PRMergeExecutor → merge
  ↓ on_pr_merge_result 콜백 → pr_merged stamp + next slice dispatch
next slice dispatch
  ↓ session.extra[coding_backlog] 첫 항목 pop
  ↓ promote_session_to_coding_ready → 새 coding job ready
  ↓ backlog 비어있으면 session state COMPLETED
```

## 2. 환경 변수 contract

| Var | 효과 | 기본값 |
|---|---|---|
| `YULE_GITHUB_APP_MERGE_OPT_IN` | live merge API 호출 활성화 (off 이면 reply 가 `merge_disabled` 로 ack) | unset |
| `YULE_PR_MERGE_CONTINUATION_INTERVAL_SECONDS` | background loop tick 간격 | 30 |
| `DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID` (또는 NAME) | approval reply 라우팅 대상 | unset = 라우팅 안 함 |
| GitHub App config (`GITHUB_APP_ID` 외) | `build_live_client_from_env` 가 사용 | 필수 (autonomous_merge live 동작) |

merge env 미설정 / GitHub App config 누락 시 runtime 은 **죽지 않고**
loop startup log 에 `merge_executor=no` 를 출력. autonomous_merge 세션
은 매 tick `no_executor_wired` skip 으로 남아 운영자가 즉시 발견.

## 3. session.extra schema

| Key | 채워지는 시점 | 의미 |
|---|---|---|
| `work_mode` | intake (ensure_session_mode) | `autonomous_merge` \| `approval_required` |
| `topology` | intake | `single_repo` \| `multi_repo` |
| `scope` | intake | `single_scope` \| `full_stack_single_repo` \| `layer_scoped` \| `cross_repo_program` |
| `mode_decided_at` / `mode_decided_by` | intake | 영속 시점 + 결정 주체 |
| `pr_merge_stage` | coding_execute 성공 직후 | 4-state: `pr_merge_pending` → `pr_merge_approved` → `pr_merged` / `pr_merge_blocked` |
| `pr_merge_pr_number` / `pr_merge_pr_url` / `pr_merge_repo` / `pr_merge_head_sha` / `pr_merge_base_branch` | coding_execute 성공 직후 | live continuation 이 필요로 하는 PR 메타 |
| `pr_merge_continuation_audit` | 매 stage 전환 | append-only list — `stage` / `prior_stage` / `reason` / 추가 필드 |
| `coding_backlog` | tech-lead planning 이 미리 채워 둠 (또는 빈 list) | merge 성공 시 dispatch_next_coding_slice 가 pop |
| `session_completed_reason` | 마지막 slice merge 후 backlog 비면 | `backlog_empty_after_merge` |

## 4. canonical session `11917bf1e75d` recovery

기존 상태:
- session 은 `coding_execute` 성공 후 PR #2 (`yule-studio/naver-search-clone`) 까지 도달
- 옛 wiring 에는 post-PR continuation 이 없어서 session.extra 에
  `pr_merge_*` 키가 전혀 없음 (즉 background loop 가 pick 하지 않음)

회복 절차 (operator 수동):

1. (한 번만) session.extra 에 다음 키를 stamp:
   - `work_mode = autonomous_merge` (또는 `approval_required`)
   - `pr_merge_stage = pr_merge_pending`
   - `pr_merge_pr_number = 2`
   - `pr_merge_pr_url = https://github.com/yule-studio/naver-search-clone/pull/2`
   - `pr_merge_repo = yule-studio/naver-search-clone`
   - `pr_merge_head_sha = <PR #2 head sha>`
   - `pr_merge_base_branch = main`

   CLI helper (one-liner, repo root 에서 실행):
   ```bash
   python3 -c "
   from dataclasses import replace
   from datetime import datetime, timezone
   from yule_orchestrator.agents.workflow_state import load_session, update_session
   from yule_orchestrator.agents.job_queue.pr_merge_continuation import (
       EXTRA_PR_MERGE_BASE_BRANCH, EXTRA_PR_MERGE_HEAD_SHA,
       EXTRA_PR_MERGE_PR_NUMBER, EXTRA_PR_MERGE_PR_URL,
       EXTRA_PR_MERGE_REPO, EXTRA_PR_MERGE_STAGE,
       STAGE_PR_MERGE_PENDING,
   )
   from yule_orchestrator.agents.lifecycle.session_mode import (
       EXTRA_WORK_MODE, WORK_MODE_AUTONOMOUS,
   )
   s = load_session('11917bf1e75d')
   extra = dict(s.extra or {})
   extra[EXTRA_WORK_MODE] = WORK_MODE_AUTONOMOUS
   extra[EXTRA_PR_MERGE_STAGE] = STAGE_PR_MERGE_PENDING
   extra[EXTRA_PR_MERGE_PR_NUMBER] = 2
   extra[EXTRA_PR_MERGE_PR_URL] = 'https://github.com/yule-studio/naver-search-clone/pull/2'
   extra[EXTRA_PR_MERGE_REPO] = 'yule-studio/naver-search-clone'
   extra[EXTRA_PR_MERGE_HEAD_SHA] = '<PR #2 head sha>'
   extra[EXTRA_PR_MERGE_BASE_BRANCH] = 'main'
   update_session(replace(s, extra=extra), now=datetime.now(tz=timezone.utc))
   print('canonical session recovery stamped — background loop will pick it up next tick')
   "
   ```

2. 다음 background loop tick (기본 30s) 안에:
   - autonomous_merge → live PRMergeExecutor 가 PR #2 gate 평가 → 통과
     시 merge → `pr_merged` stamp + next slice dispatch.
   - approval_required → `#승인-대기` 에 카드 한 번만 게시, 사용자 회신
     기다림.

3. 운영자는 startup log 에서 다음 두 줄을 확인:
   ```
   coding_executor wired: editor=GreenfieldBootstrapEditor bootstrap_enabled=True ...
   pr_merge_continuation loop wired: approval_enqueuer=yes merge_executor=yes (env=YULE_GITHUB_APP_MERGE_OPT_IN)
   ```

## 5. Idempotency 가드

| 시나리오 | 가드 |
|---|---|
| background loop tick 이 같은 세션을 2번 advance | `is_pending_approval_card` 가 audit 의 `approval_card_enqueued` event 확인 후 ACTION_SKIPPED_ALREADY_ENQUEUED 반환 |
| approval reply + background loop 가 동시에 merge 시도 | `_advance_to_merged_and_dispatch_next_slice` 가 `pr_merge_stage == pr_merged` 면 no-op |
| merge 성공 직후 next slice 가 두 번 dispatch | `dispatch_next_coding_slice` 가 backlog pop 후 persist — 두 번째 호출은 이미 비어있는 backlog 를 보고 SESSION_DONE 처리 |
| 같은 anchor 로 coding_execute 가 두 번 enqueue | `CodingExecutorWorker.enqueue` 가 (session_id, executor_role, branch) dedup |

## 6. 실패 경로 (pr_merge_blocked)

| Reason | 의미 | 운영자 액션 |
|---|---|---|
| `gate_failed:draft` | PR 이 draft 상태 — gate 거부 | "Ready for review" 로 전환 |
| `gate_failed:mergeable` | conflict / behind base | rebase / 충돌 해결 후 새 commit |
| `gate_failed:checks_green` | CI red | 실패 step 수정 후 push |
| `gate_failed:branch_protection` | 필요한 review / status 미충족 | 추가 review 요청 |
| `gate_failed:sha_race` | head_sha 가 카드 시점과 달라짐 | 새 카드 자동 게시 (다음 tick) |
| `merge_disabled` | `YULE_GITHUB_MERGE_ENABLED=true` 가 없거나 GitHub App 미설정 | env 설정 |
| `merge_api_failed` | GitHub merge API 가 5xx / 4xx | 메시지 status 확인 |

세션은 항상 `pr_merge_blocked` 로 advance + audit 에 상세 사유 기록.

## 7. FE/BE planning metadata 보존

- single executor 모델은 유지되지만 tech-lead planning 단계에서 role
  take / review / plan 이 session.extra (`role_takes` / `role_reviews`
  / `tech_lead_slice_plan` 등) 에 stamp 되면 pr_merge_continuation 이
  그 키를 **건드리지 않음**.
- operator 의 `/status` panel 은 동일 session.extra 를 읽으므로 FE/BE
  의 parallel planning 흔적이 그대로 보임.

## 8. 회귀 테스트 매핑

| 테스트 파일 | 커버리지 |
|---|---|
| `tests/job_queue/test_pr_merge_continuation.py` | pure decision / stage transition / dedup helper |
| `tests/job_queue/test_pr_merge_continuation_worker_hook.py` | coding_executor_worker 통과 시 stamp |
| `tests/job_queue/test_pr_merge_continuation_end_to_end.py` | 10 사용자 acceptance |
| `tests/runtime/test_pr_merge_continuation_loop.py` | background loop tick / idempotency / canonical recovery integration |
