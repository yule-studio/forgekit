# Approval Matrix — A-M10

[autonomy-policy.md](autonomy-policy.md) 가 정의한 5-단계 사다리에 따라
*어떤 행동이 어떤 단계에서 자동/승인/금지되는지* 한 표로 정리한다. 새
action 을 추가할 때는 반드시 본 표 + `autonomy_policy._DEFAULT_LEVELS` +
관련 reason 템플릿 세 곳을 모두 갱신한다.

## 약어

- **L0**: `L0_AUTO_RECORD_OPTIONAL` — 자동, 기록 선택
- **L1**: `L1_AUTO_RECORD_REQUIRED` — 자동, audit 필수
- **L2**: `L2_AUTO_POST_REPORT` — 자동, 사후 보고
- **L3**: `L3_HUMAN_APPROVAL` — 승인 필요
- **L4**: `L4_STRONG_APPROVAL_OR_FORBIDDEN` — 강한 승인 / 금지

## 1. Research / Knowledge

| Action | Level | 비고 |
| --- | --- | --- |
| 운영-리서치 thread 메시지 읽기 | L0 | `local_file_read` 와 동등 |
| 사용자 명시 오더 기반 리서치 | L1 | `user_ordered_research` |
| 운영-리서치 thread 스냅샷 캡처 | L1 | `thread_snapshot_capture` |
| 공개 자료 링크 수집 | L1 | `link_collection` |
| 역할별 take 기록 | L1 | `role_take_record` |
| research-log Obsidian 자동 저장 | L1 | `research_log_save` (M10b 에서 wiring) |
| draft 문서 생성 | L2 | `draft_document_create` |
| **canonical knowledge 확정** | **L3** | `knowledge_note_finalize` — Discord `#승인-대기` 카드 |
| **decision-record 확정** | **L3** | `decision_record_finalize` |
| 기존 문서 supersede / overwrite | L3 | `document_overwrite` |
| 외부 API (유료) 호출 | L3 | `external_paid_call` |
| 대량 크롤링 | L3 | `large_scale_crawl` |
| **외부 채널 공식 발행** | **L4** | `external_publication` / `blog_publication` |

## 2. Code

| Action | Level | 비고 |
| --- | --- | --- |
| 코드 grep / 파일 읽기 | L0 | `local_file_read` |
| feature branch 생성 (로컬) | L2 | `feature_branch_create` |
| 낮은 위험 docs/test 수정 | L2 | `low_risk_docs_edit` / `low_risk_test_edit` |
| 테스트 실행 | L2 | `test_execute` |
| 로컬 commit | L2 | `local_commit` |
| **runtime/prod 코드 변경** | **L3** | `runtime_code_change` |
| 공유 repo 로 push (feature branch) | L3 | `push_to_shared_repo` |
| draft PR 생성 | L3 | `draft_pr_create` (사용자 정책으로 사전 자동 허용 가능) |
| **GitHub issue 자동 생성** | **L2** | `github_issue_create` — `#승인-대기` 카드로 묶인 work_order 안에서만 (P0-S, [`github-agent-workos.md`](github-agent-workos.md) §1.1) |
| **Tag/release create** | **L3** | `tag_create` / `release_create` — `RepoContract.tag_policy` 가 `none` 이면 금지 (P0-S §1.2 / §1.2.1) |
| **main 직접 push** | **L4** | `main_branch_push` — 사실상 금지 |
| **merge** | **L4** | `branch_merge` |
| **deploy** | **L4** | `deploy` |

## 3. Vault (Obsidian) — 통합 commit/push 매트릭스 (P0-G 1차 SSoT)

본 §3 은 vault 작업의 **단일 출처 (SSoT)**. autonomy-policy.md 의 L1~L4 카탈로그가 *행동* 을 정의하고, 본 표가 *vault 작업별 mapping* 을 결정한다.

핵심 원칙 (P0-G 1차 정착):

- **vault commit 은 기본 자동 (L2).** `vault_research_log_commit` / `local_commit` 모두 자동. audit 는 필수.
- **vault push 는 mode 결정.** [`docs/autonomy-policy.md`](autonomy-policy.md) §0.1 의 work mode 가 `approval_required` 면 vault remote push 도 승인 후 (L3). `autonomous_merge` 면 사용자가 인가한 범위에서 자동 가능. mode 결정은 세션 첫 응답 ask-once.
- **코드 repo push 와 vault push 는 분리 기록.** 한 audit 묶음에 섞지 않는다. session.extra 에 `code_push_audit` / `vault_push_audit` 분리.
- **canonical / decision finalize 는 mode 무관 L3.** 학습 미러의 SSoT 가 사용자 인지 없이 굳어지면 회귀 식별이 어려워진다.

| Action | Level | mode 영향 | 비고 |
| --- | --- | --- | --- |
| vault 파일 읽기 | L0 | 없음 | `local_file_read` |
| research-log 자동 저장 | L1 | 없음 | `research_log_save` |
| agent-ops audit 자동 기록 | L1 | 없음 | `agent_ops_record` |
| failure-postmortem 자동 작성 | L2 | 없음 | `failure_postmortem_create` |
| self-improvement proposal | L2 | 없음 | `self_improvement_proposal` |
| blog draft 작성 | L2 | 없음 | `blog_draft_create` |
| vault research-log branch 자동 commit | L2 | 없음 | `vault_research_log_commit` — 로컬 commit. push 별도. |
| **canonical knowledge 확정 저장** | **L3** | mode 무관 | `knowledge_note_finalize` — 항상 사용자 명시 승인. |
| **decision-record 확정** | **L3** | mode 무관 | `decision_record_finalize` — 항상 사용자 명시 승인. |
| **vault remote push** | **L3** ↔ **L2 (autonomous_merge)** | **mode 적용** | `vault_remote_push`. `approval_required` 시 L3. `autonomous_merge` + 사용자 인가 범위 시 L2 자동. audit 는 `vault_push_audit` 로 별도 기록. |
| **vault main merge** | **L4** | mode 무관 | `branch_merge` 와 동일. mode 무관 영구 hard rail. |

### 3.1 코드 repo vs vault repo 분리

| 비교 | 코드 repo (예: yule-studio-agent) | vault repo (예: yule-agent-vault) |
| --- | --- | --- |
| commit | L2 (`local_commit`) | L2 (`vault_research_log_commit`) |
| feature branch push | L3 `push_to_shared_repo` | L3 `vault_remote_push` (또는 mode 적용 시 L2) |
| main merge | L4 `branch_merge` | L4 `branch_merge` (vault 도 동일 hard rail) |
| audit key | `session.extra["code_push_audit"]` | `session.extra["vault_push_audit"]` |
| 회귀 test | 본 레포 `tests/` | mirror 노트는 본 레포에 land 되므로 `tests/engineering/` |

### 3.2 vault repo workspace 부재 시

operator 의 실제 vault repo (`yule-agent-vault`) 가 현재 workspace 에 클론되지 않은 상태에서는:

- 본 레포 `notes/vault-mirror/` 에 mirror 만 land. operator 가 sync.
- `vault_remote_push` 자체는 미실행 — 코드 land 는 P0-G 3차 (#141) scope.
- 본 정책 (§3 표) 은 vault repo 가 존재할 때를 가정한 *contract*. 없으면 contract 만 land, write 자체는 미구현 — fake success 금지.

## 4. Runtime / Infra

| Action | Level | 비고 |
| --- | --- | --- |
| heartbeat 조회 | L0 | `heartbeat_check` |
| supervisor 자가진단 | L0 | `status_query` 와 동등 |
| **runtime 재시작** | **L3** | `runtime_restart` |
| **infra 변경** | **L3** | legacy `infra_change` 매핑 |
| **deploy** | **L4** | `deploy` |

## 5. Secrets / Data

| Action | Level | 비고 |
| --- | --- | --- |
| 메모리 조회 | L0 | `memory_read` |
| 세션 조회 | L0 | `session_lookup` |
| **secret 조회** | **L4** | `secret_access` |
| **secret 변경** | **L4** | `secret_modify` |
| **prod DB write** | **L4** | `prod_db_write` |
| **비가역 delete** | **L4** | `destructive_delete` |

## 메타데이터에 의한 상승

행동 자체가 L1 이어도 다음 메타가 붙으면 **자동으로 상위 단계** 로
상승한다. `AutonomyDecision.escalation_reasons` 에 사유가 남는다.

| 메타 | 효과 |
| --- | --- |
| `risk_level=critical` | 최소 L4 |
| `reversible=False` | 최소 L3 |
| `external_side_effect=True` | 최소 L3 |
| `cost_impact=major` | 최소 L3 |
| `data_sensitivity=confidential` 또는 `secret` | 최소 L4 |

## 알 수 없는 action

`_DEFAULT_LEVELS` 에 없는 action 은 **L4** 로 떨어진다 — 안전한 회귀 경로.
새 action 을 추가하려면 카탈로그 + 매트릭스 + reason 템플릿을 같이
업데이트해야 한다.

## audit 기록

`audit_required == True` 인 모든 결정 (L1 이상) 은
`session.extra['agent_ops_audit']` 에 `AgentOpsEntry` 로 append.
A-M10b 에서 vault `40-agent-ops/<date>.md` 자동 export.
A-M10c 에서 `#봇-상태` daily summary 게시.

## 6. Operator Action Inbox — 기술 자율 vs 외부 사실 (P0-S)

`#승인-대기` 채널은 더 이상 *approval-only* 가 아니라 **operator action
inbox** 로 동작한다. agent 가 진행 중에 사람의 응답을 필요로 하는 모든
순간이 같은 채널 카드로 표면화돼야 한다 — "조용히 멈추는" 회귀를
영구히 막는 것이 §6 의 본질이다.

### 6.1 5 가지 request_type

| request_type | 의미 | 세션 sub-state | approval_kind |
| --- | --- | --- | --- |
| `APPROVAL_REQUIRED` | write/push/PR/merge/deploy 같은 **승인** | `waiting_approval` | 기존 vocabulary (`engineering_write` / `pr_merge` / …) |
| `INFO_REQUIRED` | 서버 IP, 도메인, 운영 사실, 배포 대상 식별자 등 | `waiting_user_input` | `info_request` |
| `ACCESS_REQUIRED` | SSH/서버 접근/권한 부여/repo access/cloud access | `waiting_access` | `access_request` |
| `SECRET_REQUIRED` | 실제 secret 값/저장 위치/secret 등록 승인 | `waiting_secret` | `secret_request` |
| `DECISION_REQUIRED` | 드물게 사람 정책/제품 판단 (단순 기술 선택은 금지) | `waiting_user_input` | `decision_request` |

세션 macro state (`WorkflowState`) 와 직교하며,
`session.extra["operator_state"]` 에 저장된다. 응답이 도착해 미해결이
모두 해소되면 `running` 으로 복귀.

### 6.2 기술 자율 vs 외부 사실 경계

| 영역 | agent 자율 | 사람에게 요청 |
| --- | --- | --- |
| 인증 방식 | JWT vs session 선택 | — |
| 데이터 모델 | 기본 DB 이름 규칙 | 기존 운영 DB endpoint |
| 인프라 구조 | Docker Compose 구조, 디렉터리 구조 | 클라우드 프로젝트 / 계정 식별자 |
| 프레임워크 | Next/Nest 연결 방식, auth/API 구조 | — |
| 일반 기술 선택 | 라이브러리 선택, 테스트 도구 | — |
| 서버 / 운영 | — | 실제 서버 IP / hostname, 실제 도메인 |
| 접근 권한 | — | SSH user / key, 환경 수정 인가 |
| Secret | secret 키 이름 정의, `.env.example` / compose / CI wiring 작성, GitHub Actions secret 이름 제안, 어떤 값이 필요한지 설명 | 실제 secret 값, secret 저장 위치, secret 등록 승인 |

위 경계는 코드에서도 명시:
`src/yule_orchestrator/agents/coding/authorization.py` →
`TECH_DECISION_AUTONOMOUS` / `EXTERNAL_FACT_HUMAN_REQUIRED` /
`classify_user_request_facts(...)`.

### 6.3 Secret hard rails

agent 가 자동으로 해도 되는 것
- secret key **이름** 정의 (예: `JWT_SECRET`, `STRIPE_API_KEY`)
- `.env.example` / `docker-compose.yml` / GitHub Actions workflow 의
  **wiring** 작성 (값은 `${{ secrets.JWT_SECRET }}` 같은 placeholder)
- "이 시크릿이 왜 필요한지" 사용자에게 설명

agent 가 절대 자동으로 하면 안 되는 것
- 실제 secret 값을 임의 생성해 운영 환경에 저장
- GitHub secret / cloud secret manager 를 사용자 동의 없이 수정
- prod `.env` 파일 직접 변경
- 기존 secret 값을 다른 위치로 복사 / mirror

위 두 목록은 `src/yule_orchestrator/agents/operator_action.py` 의
`SECRET_AUTO_ALLOWED` / `SECRET_AUTO_FORBIDDEN` 와 동기화 유지.

`SECRET_REQUIRED` 카드의 thread reply 는 반드시 **저장 위치만** 받는다 —
`secret_value=...` / `raw_value=...` / `value=...` 형태로 raw 값이 채널
에 붙으면 reply parser 가 거부하고 `RESPONSE_OPERATOR_SECRET_VALUE_REJECTED`
응답을 게시한다.

### 6.4 Operator action 카드 — 필수 필드

각 카드는 최소 다음을 포함해야 한다 (
`render_operator_action_card`):

- `request_type` 라벨 + 이모지
- `session_id` + `requested_by`
- 현재 `stage` (어디서 멈췄는지)
- `why_blocked` (왜 사람이 필요한지)
- `expected_answer` (지금 필요한 답변 1개 또는 짧은 목록)
- `answer_examples` (`host=10.0.0.5` 같은 thread reply 예시)
- "이 카드의 thread 에서 답하라" 안내
- `next_action` (응답 후 어떤 작업이 이어지는지)
- `timeout_hint` (가능하면 timeout / fallback 메모)

### 6.5 Hard rules

- 사람 응답이 필요한데 카드 없이 멈추면 **금지** — 멈출 때는 반드시
  `#승인-대기` 카드 게시.
- 외부 사실을 추측해서 진행 **금지**.
- secret 값을 agent 가 임의로 만들고 운영에 주입 **금지**.
- 단순 기술 판단을 불필요하게 사람에게 떠넘기지 말 것 — `DECISION_REQUIRED`
  남용 금지.
- 승인/정보/접근/secret 요청은 모두 auditable 해야 함
  (`session.extra["operator_pending_requests"]` /
  `session.extra["operator_answered_requests"]`).
- 본문 채널 / 업무-접수 / 운영-리서치에서 흩어진 응답을 기대하지 말 것 —
  반드시 카드 thread.
