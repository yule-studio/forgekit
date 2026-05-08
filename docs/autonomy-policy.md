# Autonomy Policy — A-M10

## 왜 이 문서가 필요한가

A-M10 이전까지 engineering-agent 는 "큐 + 승인 카드" 봇이었다. 사용자가
명시적으로 오더한 작업조차 사람이 `#승인-대기` 에 답신을 남겨야만 진전됐다.
이는 사용자의 본래 요구 ("실제 직원처럼 스스로 조사·토의·기록·개선") 와
정면으로 어긋났다.

A-M10 부터는 모든 행동을 다음 5 단계 자율성 사다리(autonomy ladder) 위에
놓는다. 각 단계는 *행동의 부작용 가역성·외부 효과·비용·민감도* 를 기준으로
결정되며, **자동 실행 vs 사람 승인** 의 경계를 명시한다.

본 문서가 정의한 매트릭스는 코드로
[`src/yule_orchestrator/agents/lifecycle/autonomy_policy.py`](../src/yule_orchestrator/agents/lifecycle/autonomy_policy.py)
에 박혀 있고, 모든 자동 실행은
[`agents/lifecycle/agent_ops_log.py`](../src/yule_orchestrator/agents/lifecycle/agent_ops_log.py)
의 `AgentOpsEntry` 로 audit 가 남는다.

## 5-tier ladder

| Level | 이름 | 의미 |
| --- | --- | --- |
| **L0** | `L0_AUTO_RECORD_OPTIONAL` | 자동 실행, 기록 선택. 읽기 전용 plumbing. |
| **L1** | `L1_AUTO_RECORD_REQUIRED` | 자동 실행, **반드시 audit 기록**. 사용자 명시 오더 기반 행동 + 모든 dedup/실패/감사. |
| **L2** | `L2_AUTO_POST_REPORT` | 자동 실행, 사후 보고 필수. 낮은 위험 변경, draft 문서, self-improvement proposal, 로컬 commit. |
| **L3** | `L3_HUMAN_APPROVAL` | 사용자 승인 필요. canonical knowledge / decision 확정, runtime 변경, 공유 repo push. |
| **L4** | `L4_STRONG_APPROVAL_OR_FORBIDDEN` | 강한 승인 또는 금지. main 직접 push, deploy, secret 접근, 외부 발행. |

## 행동 카탈로그 (요약)

### L0 — 읽기 전용 plumbing

| Action | 설명 |
| --- | --- |
| `status_query` | 운영 상태 조회 |
| `queue_inspect` | job queue 조회 |
| `heartbeat_check` | supervisor heartbeat 조회 |
| `local_file_read` | repo 내 로컬 파일 읽기 |
| `session_lookup` | workflow session 조회 |
| `topic_lookup` | research topic ledger 조회 |
| `memory_read` | 메모리 조회 |

### L1 — 자동 실행, audit 필수

| Action | 설명 |
| --- | --- |
| `user_ordered_research` | 사용자 명시 오더 기반 리서치 |
| `thread_snapshot_capture` | 운영-리서치 thread 스냅샷 캡처 |
| `link_collection` | 공개 자료 링크 수집 |
| `role_take_record` | 역할별 의견 기록 |
| `failure_audit_record` | 실패 audit 작성 |
| `retry_audit_record` | 재시도 audit |
| `research_log_save` | research-log Obsidian 자동 저장 (M10b 에서 wiring) |
| `agent_ops_record` | agent-ops 자동 기록 |
| `forum_handoff_decision` | 운영-리서치 thread save dedup/dispatch 결정 |

### L2 — 자동 실행, 사후 보고

| Action | 설명 |
| --- | --- |
| `draft_document_create` | draft 문서 자동 생성 |
| `blog_draft_create` | 블로그 초안 자동 생성 (외부 발행은 L4) |
| `self_improvement_proposal` | 자기 개선 제안 자동 작성 |
| `failure_postmortem_create` | failure postmortem 자동 작성 |
| `test_execute` | 테스트 자동 실행 |
| `low_risk_docs_edit` / `low_risk_test_edit` | 낮은 위험 docs/test 수정 |
| `feature_branch_create` | feature branch 생성 (로컬) |
| `local_commit` | 로컬 commit (push 는 L3) |
| `vault_research_log_commit` | vault research-log branch 자동 commit |

### L3 — 사용자 승인 필요

| Action | 설명 |
| --- | --- |
| `knowledge_note_finalize` | canonical knowledge 확정 |
| `decision_record_finalize` | decision-record 확정 |
| `document_overwrite` | 기존 문서 덮어쓰기 / supersede |
| `runtime_code_change` | runtime/prod 코드 변경 |
| `push_to_shared_repo` | 공유 repo 로 branch push |
| `draft_pr_create` | draft PR 생성 (사용자 정책에 따라 자동 가능) |
| `runtime_restart` | runtime/prod 재시작 |
| `external_paid_call` | 외부 유료 API 호출 |
| `large_scale_crawl` | 대량 크롤링 |
| `vault_remote_push` | Obsidian vault remote push |

### L4 — 강한 승인 또는 금지

| Action | 설명 |
| --- | --- |
| `main_branch_push` | main 직접 push (사실상 금지) |
| `branch_merge` | merge |
| `deploy` | prod deploy |
| `secret_access` | secret 조회 |
| `secret_modify` | secret 변경 |
| `prod_db_write` | prod DB write |
| `destructive_delete` | 비가역 delete |
| `external_publication` | 외부 채널 공식 발행 |
| `blog_publication` | Tistory/Velog 등 실제 게시 |

## 상승 규칙 (escalation)

기본 등급은 행동 종류에서 결정되지만, 위험 메타데이터가 붙으면 **반드시
상승**한다. 절대 *완화* 되지 않는다.

| 신호 | 효과 |
| --- | --- |
| `risk_level == "critical"` | 최소 L4 |
| `reversible == False` | 최소 L3 |
| `external_side_effect == True` | 최소 L3 |
| `cost_impact == "major"` | 최소 L3 |
| `data_sensitivity in {"confidential", "secret"}` | 최소 L4 |
| `proposed_level` (호출자가 직접 지정) | 더 높을 때만 채택 |

상승 사유는 `AutonomyDecision.escalation_reasons` 튜플로 audit 에 기록되어
"왜 L1 행동이 L3 로 올라갔는지" grep 한 줄로 확인 가능하다.

## 알려진 제약 / 명시적 금지

- **모든 행동을 L0/L1 으로 자동 처리하지 않는다.** 위험·비가역·외부 효과
  메타데이터를 신중히 선언해야 한다. 실수로 `external_side_effect=False` 라
  쓰지 말 것.
- **알 수 없는 action 은 기본 L4** 로 떨어진다. 새 action 을 추가할 때는
  반드시 `_DEFAULT_LEVELS` 매핑을 갱신하고 본 문서에 행을 추가한다.
- **`proposed_level` 로 단계를 낮출 수 없다.** L4 → L1 같은 우회는 불가능.
  완화하려면 위험 메타데이터를 정직하게 다시 선언해야 한다.
- **L3/L4 는 직접 실행 금지.** 반드시 `decision.to_action_context()` 로
  legacy `agents/approval_policy.py` 의 `ActionContext` 를 만들어 M5a
  ApprovalWorker → `#승인-대기` 카드 경로로 보낸다.

## audit log 와 #봇-상태 의 관계

`AutonomyDecision.audit_required == True` 인 모든 결정은
`session.extra['agent_ops_audit']` 에 `AgentOpsEntry` 로 append 된다.
M10b 에서는 이 list 가 vault `40-agent-ops/<date>.md` 로 자동 export 되고,
M10c 에서는 `#봇-상태` 채널에 daily summary 가 게시된다.

현재 (A-M10a 시점) 의 통합 지점:

- `forum_obsidian_handoff.route_forum_obsidian_save_request` — 5 가지 결정
  분기 (queued / topic_pending / topic_already_saved / duplicate-message /
  failure) 모두 audit 기록.

A-M10b 추가:

- 5 가지 자율 실행 note 종류 — `research-log`, `agent-ops`,
  `failure-postmortem`, `self-improvement-proposal`, `blog-draft` —
  를 `obsidian_writer_worker.default_render_fn` 이 직접 처리.
- 폴더 라우팅: 모두 `10-projects/<project>/` 아래에 자리 잡음
  (`research-log/`, `agent-ops/`, `agent-ops/postmortems/`,
  `agent-ops/proposals/`, `blog-drafts/`).
- 승인 우회: 5 가지 모두 `requires_approval()` 가 False 라 `#승인-대기`
  카드 없이 vault 에 자동 저장.
- 빈 본문 가드: snapshot/pack/synthesis/prompt 가 모두 비면 vault 저장
  거부 (failed_retryable).
- 빌더: `lifecycle/autonomous_producers.py` 가
  `build_research_log_request` / `build_agent_ops_request` /
  `build_simple_body_request` 제공. M10c 트리거 (research synthesis
  완료, 정해진 audit-flush 주기, postmortem/proposal 발생 등) 가
  이 빌더를 호출해 obsidian_write 큐에 enqueue 하면 끝.

A-M10c 추가:

- 운영-리서치 thread 저장 요청 → 동시에 L1 research-log obsidian_write
  자동 enqueue. forum-handoff 가 `obsidian_writer_worker` 를 받아
  `build_research_log_request` 로 페이로드를 만들고 worker.enqueue 의
  find_active dedup 으로 중복 방지. 실패는 audit 로 기록되며 approval
  카드 흐름은 영향을 받지 않음.
- forum-message-adapter 가 production 기본 ObsidianWriterWorker 를
  lazy 빌드하므로 #운영-리서치 thread 메시지 한 번이면 vault 에 자동
  저장 잡이 동시 enqueue.
- self-improvement 감지 skeleton (`lifecycle/self_improvement.py`):
  failed_retryable 누적 / 동일 topic 의 중복 approval / 빈 knowledge
  hydration 실패 / stale heartbeat 신호를 감지해 severity-ranked
  `SelfImprovementSignal` 튜플 반환. 마크다운 렌더러는
  `build_simple_body_request` 가 받을 proposal body 를 생성.

향후 (A-M10d 후속) 통합 작업:

- ClaudeCodeRunner / OllamaRunner 와 deliberation runner_fn 실제 wiring.
- self-improvement 신호 → 제안서 자동 작성 → L2 vault commit / L3 승인
  요청 자동 분기.
- 낮은 위험 docs/test 자동 fix branch + commit 루프.

## 코드 진입점

| 모듈 | 책임 |
| --- | --- |
| `agents/lifecycle/autonomy_policy.py` | 5-단계 enum + action 카탈로그 + `decide_autonomy()` |
| `agents/lifecycle/agent_ops_log.py` | `AgentOpsEntry` + session.extra round-trip + markdown 렌더 |
| `agents/lifecycle/autonomous_producers.py` | M10b 자율 실행 kind 들의 `ObsidianWriteRequest` 빌더 |
| `agents/obsidian/research_log_writer.py` | research-log / agent-ops / postmortem / proposal / blog-draft 렌더러 |
| `agents/approval_policy.py` (M5a) | 기존 4-단계 정책. M10 의 L3/L4 가 `to_action_context()` 로 다리 놓음 |
| `agents/job_queue/forum_obsidian_handoff.py` | 운영-리서치 thread 저장 dispatch (audit 기록 포함) |

## 관련 문서

- [approval-matrix.md](approval-matrix.md) — action × level 매트릭스 표
- [engineering.md](engineering.md) — engineering-agent 전체 아키텍처
- [operations.md](operations.md) — runtime / supervisor / heartbeat
