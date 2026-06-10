# F16 — PR Approval / Merge Loop

> **Status**: F16 PR-2 (issue #128). 변경 B — gateway 가 `#승인-대기` 채널에 PR summary card 를 올리고, 승인 + green CI + branch protection 통과시에만 merge 시도. 본 doc 은 운영자 진입점.

## 1. 흐름 개요

```
[GitHub PR opened/synchronize/ready_for_review]
        ↓ (existing polling: list_open_pull_requests, 60s)
[pr_event_producer 가 신규 PR 감지]
        ↓
[pr_merge_adapter.enqueue_pr_merge_approval]
        ↓
[ApprovalWorker → #승인-대기 채널에 summary card 게시]
        ↓ (사용자: "승인" / "거절" / "수정 후 다시" / "머지 보류")
[approval_reply_router → handle_pr_merge_approval_reply]
        ↓ (intent=APPROVE)
[5-step merge gate]
  1. draft != True
  2. mergeable_state == "clean"
  3. all check_runs green
  4. branch protection rules 통과
  5. head_sha unchanged since approval
        ↓ (all pass)
[live_client.merge_pull_request]
        ↓
[Discord 에 결과 게시 + agent_ops_log audit]
```

## 2. opt-in 환경 변수

**모든 production 시점에 strict 강제. 안전이 기본값.**

| 변수 | 기본 | 의미 |
| --- | --- | --- |
| `YULE_GITHUB_MERGE_ENABLED` | `false` | merge API 호출 자체의 master gate. 미설정 또는 false 면 approval card + 5-step gate 까지만 동작하고 GitHub merge 는 거부. |
| `YULE_GITHUB_DEFAULT_DRY_RUN` | `true` | 기존 G3 의 dry_run 가드. merge_enabled 와 별개로 함께 검증. |
| `YULE_PR_MERGE_REVIEW_COMMENT_ENABLED` | `false` | "수정 후 다시" 응답 시 PR 에 자동 review comment 게시. live GitHub write → 신중. |
| `YULE_PR_MERGE_POLL_INTERVAL_SECONDS` | `60` | PR 변경 감지 polling 주기. 너무 짧으면 API rate limit. |

## 3. Summary card 의 모양

`#승인-대기` 채널에 게시되는 카드 — 사용자가 짧게 판단할 수 있도록 6 섹션:

```
🔀 PR 머지 승인 — #127 ✨ F15 회사 팀 구조 lockdown

📋 무엇이 바뀌나
- agents/{hr,finance,sales-cs,legal}/ 6 부서 + 19 역할 manifest land
- skills/pm/ 14 PM skill 카탈로그
- governance test 18/18 PASS

🎯 영향 범위: docs / agents / tests / prompts

⚠️ 위험도: LOW (manifest JSON + docs + test; runtime 코드 변경 없음)

✅ Tests: 18/18 PASS (corporate structure governance)
✅ CI: green
🔒 Branch protection: required reviews 1 ✓ / required checks 3 ✓

🔗 https://github.com/yule-studio/yule-studio-agent/pull/127

응답 어휘: "승인" / "거절" / "수정 후 다시" / "머지 보류"
```

## 4. 승인 어휘 (`ApprovalIntent`)

기존 `agents/job_queue/approval_reply.py` 의 `ApprovalIntent` enum 확장:

| 어휘 | enum | 의미 |
| --- | --- | --- |
| 승인 / 이대로 진행 / merge | `APPROVE` | merge gate 통과 시 merge 시도 |
| 거절 / 반려 | `REJECT` | merge 안 함, audit log 만 |
| 머지 보류 / 보류 | `HOLD` | 일시 보류, 같은 card 유효 |
| 수정 후 다시 / revise | `REVISE_AND_REPEAT` *(신규)* | PR 에 review comment 게시 (env opt-in 시), card invalidate |
| 인식 불가 | `UNCLEAR` | 다시 어휘 요청 |

## 5. 5-step Merge Gate

`approval_reply_router` 가 `APPROVE` intent + `approval_kind=pr_merge` 일 때 다음을 순차 검증. **하나라도 fail 시 merge 거부 + 사용자에게 이유 명시**:

1. **draft != True** — draft PR 은 승인 받아도 merge X
2. **mergeable_state == "clean"** — 충돌 / behind base 면 거부
3. **모든 check_runs green** — `list_check_runs` 의 `conclusion == "success"` 전부
4. **branch protection rules** — `get_branch_protection` 호출 → required_status_checks 통과, required_pull_request_reviews 통과
5. **head_sha 일치** — approval card 작성 시점의 sha 와 현재 sha 가 같음 (race 방지)

`get_branch_protection` 이 401/403 → **거부 (안전)**. 권한 부족도 risk 로 간주.

## 6. Audit Trail

모든 단계는 `session.extra["pr_merge_audit"]` 의 append-only list 에 기록:

```json
[
  {"stage": "card_posted", "pr_number": 127, "head_sha": "abc123", "ts": "2026-05-13T07:00:00Z"},
  {"stage": "approved", "by_user_id": 12345, "intent": "approve", "ts": "..."},
  {"stage": "merge_gate_check", "draft": false, "mergeable": true, "checks_green": true, "protection_ok": true, "sha_match": true},
  {"stage": "merge_disabled", "reason": "YULE_GITHUB_MERGE_ENABLED=false"},
  {"stage": "merge_executed", "merge_sha": "def456", "method": "squash"}
]
```

`agent_ops_log.append_agent_ops_audit` 재사용 — 별도 SQLite table 안 만든다.

## 7. Hard Rails (다시 명시)

- **절대 기본값 자동 merge X** — `YULE_GITHUB_MERGE_ENABLED=true` 명시 필수
- **draft / red checks / branch protection violation / sha mismatch → merge 거부**
- **force push 금지 / required review 우회 금지 / secret 변경 / production deploy 범위 밖**
- **권한 부족 (401/403)** → 안전한 측에서 거부 (admin override 허용 X)
- live merge 불가 시 — approval summary + routing + disabled seam 까지는 동작, merge 만 "disabled" 응답

## 8. Acceptance Criteria

- [ ] PR opened/synchronize/ready_for_review → approval card 발행 (변경 후 test)
- [ ] Summary 에 title / scope / risk / tests / link 포함
- [ ] 승인 응답 → merge gate 호출
- [ ] `YULE_GITHUB_MERGE_ENABLED != true` → merge 거부 (분기 test)
- [ ] draft / checks not green / missing approval / branch protection conflict → merge 차단 (각 분기 test)
- [ ] 기존 approval flow (OBSIDIAN_WRITE, ENGINEERING_WRITE) 회귀 X

## 9. 후속 (별도 issue)

- 다중 repo 지원 (현재는 yule-studio-agent 만)
- Webhook 수신 인프라 (현재는 polling)
- BIMI / VMC / 외부 통지 (Slack alarm) 의 연동
- merge 후 release note 자동 생성

## 10. 참고

- `policies/runtime/agents/engineering-agent/github-workflow.md` — GitHub workflow 기존 정책
- `docs/runtime-recall-first.md` — F16 PR-1 의 recall-first 변경 (본 PR 과 독립)
- 사용자 정책 (2026-05-13): "merge 는 high-risk 이므로 반드시 opt-in + human approval + green checks + rule compliance 강제"
