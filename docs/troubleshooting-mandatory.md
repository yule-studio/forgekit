# Troubleshooting 은 운영 메모리다 — mandatory capture 정책

> **Troubleshooting 은 회고 문서가 아니라 운영 기억이다.**
> 실패 / 우회 / 재시도 / 잘못된 가정 / fallback success / dead path 가 일어났는데
> 그 기록이 대화창에만 남고 시스템에는 안 남는 상태를 **금지** 한다.

이 문서는 사용자가 명시한 §A~§J 항목을 코드 + 정책 양측에서 어떻게 강제하는지
한 페이지에 모은 SSoT 다. runtime agent 뿐 아니라 **Claude Code / Codex
executor 도 같은 규칙을 따른다**.

## 1. Capture surface 3종 (최소 2개)

| Surface | 코드 경로 | 사용 시점 |
| --- | --- | --- |
| 운영-리서치 thread | `ResearchThreadPoster` hook | 즉시 visibility — 다른 역할이 동시에 알아야 할 때 |
| Obsidian troubleshooting note | `ObsidianTroubleshootingWriter` hook + `render_troubleshooting_note` | 운영 기억 — 사람이 다음 작업 전에 본다 |
| Mistake ledger (자동 승격) | `record_mistake` (occurrence_count >= 2 면 자동) | preflight 가 다음 진입에서 차단/경고 |
| session.extra audit (보조) | `stamp_troubleshooting_audit` | session 단위 추적 |
| Record ledger (필수) | `TroubleshootingLedger` JSON sidecar | 영속화 + dedup |

[`CaptureOutcome.meets_minimum_surfaces(minimum=2)`](../src/yule_orchestrator/agents/lifecycle/troubleshooting_ledger.py)
가 호출 측에서 §C 의 "최소 2 표면" 정책을 자동 검증한다.

## 2. 강제 capture 가 발생하는 trigger (§A + §B + §I)

`CaptureReason` enum 으로 코드화:

### §A — runtime/agent 측
- `LIVE_SMOKE_FAILURE`, `QUEUE_STUCK`, `APPROVAL_REPLY_MISMATCH`
- `NO_REPO`, `NO_WRITER`, `NO_PLAN`, `NO_CONTINUATION`
- `WRONG_CLASSIFICATION`, `DUPLICATE_INTAKE`, `DUPLICATE_WORK_ORDER`, `DUPLICATE_REPLAY`
- `FAILED_RETRYABLE_NO_RECOVERY`, `RUNTIME_UNKNOWN_CONFUSION`
- `POLICY_EXISTS_NO_ENFORCEMENT`, `LARGE_FILE_VIOLATION`, `MIXED_RESPONSIBILITY_VIOLATION`
- `DEAD_CODE`, `PARTIAL_WIRING`, `STALE_COMPATIBILITY_SHIM`
- `FALLBACK_TRIGGERED`, `OPERATOR_MANUAL_INTERVENTION`

### §B — Claude Code / Codex 측
- `CLAUDE_WRONG_ASSUMPTION` — 첫 fix 가 잘못된 가정에 기반
- `CLAUDE_INSUFFICIENT_FIX_FOLLOWUP` — 첫 fix 가 불충분 → 후속 commit
- `CI_GREEN_BUT_LIVE_FAIL` — test 는 통과했는데 live 실패
- `SLASH_CHANNEL_PATH_DIVERGENCE` — slash path 와 channel path 가 다르게 진화한 회귀
- `CODE_EXISTS_BUT_WIRING_MISSING` — 모듈은 있는데 supervisor wire-up 누락
- `KNOWN_RULE_VIOLATION` — 정책 문서로 알고 있던 규칙을 실제로 놓친 경우

### §I — silent correction
- `RETRYABLE_FAILURE_RETRY_SUCCESS` — 재시도로 회복
- `FALLBACK_SUCCESS_AFTER_FAIL` — fallback 우회로 회복
- `LIVE_SMOKE_FAIL_SUBSEQUENT_FIX` — live 실패 후 후속 commit 으로 해결

## 3. Structured schema (§D)

`TroubleshootingRecord` (frozen dataclass) — 20 필드:

```
record_id / title / problem_signature / capture_reason / detected_at / recorded_at
detected_by / owner_role / scope / severity / status
symptom / exact_evidence / reproduction_steps
root_cause_hypothesis / confirmed_root_cause
attempted_fix / final_fix / prevention_rule
related_session_ids / related_job_ids / related_prs / related_files
followup_required / tags / occurrence_count
```

## 4. Note quality (§E) — `render_troubleshooting_note` 가 강제

Obsidian markdown 노트는 **항상 8 섹션 헤더가 있다**. 빈 섹션은
`_기록되지 않음 — operator follow-up 필요._` 로 명시 렌더되어, "왜 비었지?" 가
한눈에 보인다.

1. 증상
2. 재현 절차
3. 관찰 증거
4. 원인 분석 (가설 + 확인된 원인)
5. 수정 내용 (시도 + 최종)
6. 재발 방지
7. 관련 세션 / PR / 파일 / 큐 row
8. 남은 리스크

## 5. Mistake ledger 자동 승격 (§F)

`TroubleshootingLedger.capture(...)` 가 같은 `problem_signature` 의 2 번째
호출을 받으면 `_promote_to_mistake_ledger` 가 자동 fire → `record_mistake` 가
mistake ledger row 를 만든다. 이후 [`preflight_judgement`](../src/yule_orchestrator/agents/lifecycle/preflight_judgement.py)
가 다음 작업 진입을 advisory/warning/block 으로 자동 분류.

특별 케이스 (즉시 승격):
- 정책 문서가 있었는데 enforcement 가 빠져 발생 (`POLICY_EXISTS_NO_ENFORCEMENT`)
- live smoke 에서 이미 본 실패가 재현 (`LIVE_SMOKE_FAIL_SUBSEQUENT_FIX`)
- 사람이 같은 유형의 수동 개입을 2회 이상 (`OPERATOR_MANUAL_INTERVENTION`)
- Claude Code / Codex 가 같은 구조 실수를 반복 (`CLAUDE_INSUFFICIENT_FIX_FOLLOWUP`)

## 6. Preflight enforcement (§G + §H)

작업 시작 전 [`troubleshooting_preflight.evaluate_combined_preflight`](../src/yule_orchestrator/agents/lifecycle/troubleshooting_preflight.py) 호출:

```python
briefing = evaluate_combined_preflight(
    source=session,                                  # session.extra (mistake ledger 자동 조회)
    role_id="backend-engineer",
    action="runtime_code_change",
    ledger=troubleshooting_ledger,
    file_paths=["src/.../reply_router.py"],          # 작업 대상 파일
    problem_signature="approval_reply_mismatch",     # 알면 정확 매칭
)
if briefing.is_block():
    raise PreflightBlocked(briefing.markdown_block)
print(briefing.markdown_block)                       # operator surface
```

* `briefing.verdict` — pass / advisory / warning / block
* `briefing.troubleshooting_records` — 이 작업과 관련된 prior record 들
* `briefing.markdown_block` — operator 가 그대로 #봇-상태 / Discord 에 붙여
  볼 수 있는 한 덩어리 마크다운

`high`/`critical` severity 가 2회 이상이면 자동으로 verdict 한 단계 escalate.
`critical` 3회 이상이면 verdict=block.

## 7. No silent correction (§I)

`troubleshooting_enforcer` 의 두 entry point:

```python
# 자동: with 블록 안에서 ledger.capture 가 일어나지 않으면 violation
with mandatory_capture(
    ledger, enforcement_journal,
    capture_reason=CaptureReason.LIVE_SMOKE_FAILURE,
    detected_by=DETECTED_BY_RUNTIME_GATEWAY,
    scope="approval_reply_router",
) as guard:
    if failure_observed:
        guard.record(title="...", symptom="...", ...)
    elif fallback_used:
        guard.mark_silent_correction(title="...", symptom="...", attempted_fix=..., final_fix=...)
    else:
        guard.skip(reason="normal path — happy")
```

```python
# 직접: fallback / retry 성공 후 즉시
record_silent_correction(
    ledger,
    capture_reason=CaptureReason.RETRYABLE_FAILURE_RETRY_SUCCESS,
    title="approval_post enqueue retry 1회 후 성공",
    ...
)
```

## 8. Claude Code / Codex 도 같은 ledger 에 (§B)

LLM 코딩 executor 가 작업 중 잘못 판단했다가 다시 고친 경우:

```python
record_claude_correction(
    ledger,
    title="첫 fix 가 회귀 일부만 잡음 — 후속 commit 으로 보강",
    symptom="...",
    attempted_fix="reply_router.py 만 수정",
    final_fix="channel router 의 phrase_detect 도 동시 수정",
    prevention_rule="slash path 와 channel path 변경은 항상 paired diff 확인",
    related_files=("src/yule_orchestrator/discord/...",),
)
```

`detected_by=DETECTED_BY_CLAUDE_CODE` / `DETECTED_BY_CODEX` 로 자동 stamp 되어
runtime agent record 와 같은 ledger 에 누적된다. mistake ledger 승격 / preflight
조회도 동일하게 작동.

## 9. Operator visibility (§H)

다음 surface 가 이미 troubleshooting record 와 연동:

* **`#봇-상태` 상태 포스팅** — `PreflightBriefing.markdown_block` 을 그대로 추가
* **runtime status note** — `TroubleshootingLedger.summary_counters()` 의
  `total / open / fixed / repeated` 가 status surface 에 stamp
* **Obsidian vault** — 60-troubleshooting/<area>/ 폴더에 8 섹션 markdown
* **session.extra audit** — agent-ops 와 같은 row 형식으로 stamp

## 10. Self-improvement loop 통합

`SelfImprovementDispatcher` 가 매 dispatched problem 마다 자동으로
`troubleshooting_ledger.capture(...)` 를 호출한다. 즉:

- detect → ProblemObject 생성
- triage → TriageVerdict
- delegated 평가 → 위임 or 사람 escalate
- worktree provision / executor handoff
- **자동: TroubleshootingRecord 생성** (signal_id → CaptureReason 매핑)

[`runtime_self_improvement_loop._SIGNAL_TO_CAPTURE_REASON`](../src/yule_orchestrator/agents/lifecycle/runtime_self_improvement_loop.py)
이 mapping 의 SSoT.

## 11. 적용 범위 + cross-link

| 문서 | 무엇을 담는가 |
| --- | --- |
| `CLAUDE.md` | "Troubleshooting 은 운영 메모리 (mandatory)" 1줄 + 본 문서 링크 |
| `AGENTS.md` | §2 표에 "Troubleshooting / 실수 기록" 행 추가 |
| `docs/memory.md` | mistake ledger 와의 관계 (이 문서가 *상위 정책*, memory.md 는 retrieval 정책) |
| `docs/engineering-agent-governance.md` | runtime governance hard rail 표에 mandatory capture 한 줄 추가 |

## 12. 남은 공백 (이 PR 이후 후속 작업)

* **운영-리서치 thread poster** wiring — 현재 hook 은 정의됐지만 production
  poster (Discord forum POST) 는 후속 PR. 임시: poster=None → journalctl
  echo + Obsidian/record ledger 만 작동.
* **Obsidian writer** wiring — 같음. 후속 PR 에서 `ObsidianWriterWorker`
  를 wrap 하면 끝.
* **slash command divergence detector** — `SLASH_CHANNEL_PATH_DIVERGENCE`
  enum 은 있지만 detector 는 없음. 후속 detector 추가 권장.

## 13. 회귀 테스트 (필수 7종)

[`tests/engineering/test_troubleshooting_*`](../tests/engineering/) 가 사용자
명시 7 종을 커버:

1. `test_troubleshooting_capture_live_smoke_failure` — live smoke failure → record 생성
2. `test_troubleshooting_repeated_promotes_mistake_ledger` — repeat → mistake ledger promotion
3. `test_troubleshooting_fallback_success_creates_record` — fallback success 도 capture
4. `test_troubleshooting_claude_code_correction_captured` — Claude Code 후속 수정 path
5. `test_troubleshooting_preflight_surfaces_prior_records` — preflight 가 prior record 노출
6. `test_troubleshooting_structured_output_round_trip` — structured schema JSON round-trip
7. `test_troubleshooting_normal_path_no_noise` — 정상 성공 경로는 spam 안 만듦
