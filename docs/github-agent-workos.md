# GitHub Agent WorkOS — operator guide

이 문서는 G1~G6 통합으로 land 된 GitHub App 기반 자동 PR 흐름을
운영자가 한 자리에서 잡을 수 있도록 정리한다. 깊은 설계는 각
모듈의 docstring (특히 `agents/github_workos/*.py` 와
`github_app/*.py`) 을 우선 참조하고, 본 문서는 **CLI / 환경 / 라이브
스모크 / 운영 hard rail** 만 다룬다.

## 1. 한눈에 보는 흐름

```
GitHub issue / Discord 업무-접수 intake
  → senior_triage (G2)
       ├─ primary_role / support_roles / excluded_roles
       ├─ scope / non_scope / hidden_risks / test_plan
       └─ approval_required_actions (L3 카드 후보)
  → derive_branch_name (G3)            ← protected branch 거부
  → discover_repo_contract (P0-H)      ← target repo 의 ISSUE/PR template / tag policy
  → build_issue_auto_create_plan (P0-S)← issue 없으면 template 채워 plan 만 stamp
  → render_pr_body (G3)                ← in_scope/out_of_scope/risks/...
  → build_github_action_plan (G3)      ← issue create / comment / label / branch / draft PR
  → GithubWriter (G3) + LiveGithubAppClient (G6)
       └─ Authorization 헤더는 단 한 곳에서만 작성, 절대 출력 금지
  → audit (G3) + Discord 브릿지 (G4) + e2e harness (G5)
```

**원칙:** 운영자 승인 없이 갈 수 있는 표면은 L0 read / L1 light-write /
L2 plan 까지. **L3+ 는 항상 #승인-대기 카드 또는 별도 승인 토큰을 통한 사람 게이트.**
G6 의 `smoke-pr --live` 는 draft PR 생성을 끝점으로 하며 **merge 는 절대 하지 않는다.**

### 1.1 Issue auto-create (P0-S)

| 조건 | 행동 | audit_reason |
| --- | --- | --- |
| `existing_issue_number` 가 명시됨 | issue 생성 건너뜀, 기존 번호 재사용 | `existing_issue_reused` |
| target repo 에 `ISSUE_TEMPLATE` 가 1 개 | template 의 frontmatter (title prefix / labels) 적용 + body placeholder 보존 + request_summary quote 삽입 | `template_used` |
| `ISSUE_TEMPLATE` 가 여러 개 + 키워드 매칭 ≥1 | best template 선택 (HIGH/MEDIUM confidence) | `template_used` |
| `ISSUE_TEMPLATE` 가 여러 개인데 매칭 0 | LOW confidence + `needs_operator_decision=True` → DECISION_REQUIRED 카드 | `ambiguous_template` |
| `ISSUE_TEMPLATE` 자체가 없음 | safe default body (목표/맥락/작업 항목/audit 4 섹션) | `no_repo_template` |

agent 가 issue 본문을 추측해 꾸며내지 않는다. placeholder/HTML 주석은
그대로 보존. body 끝에는 항상 `## engineering-agent audit` 섹션이
붙어 `audit_reason` + `session_id` + `repo contract summary` 가 명시된다.

승인 등급: `ACTION_GITHUB_ISSUE_CREATE` → **L2** (issue 자체는 reversible
이지만 사람에게 notification 이 가므로 PR draft 와 동일 등급). 즉
`#승인-대기` 카드로 묶인 GitHub work order 안에서만 dispatch 가능.

### 1.2 Tag / version policy (P0-S)

`RepoContract.tag_policy` 가 다음 4 종으로 분류:

| 값 | 신호 | 자동 처리 |
| --- | --- | --- |
| `workflow_driven` | `.github/workflows/{release,publish,tag}*.yml` | agent 가 workflow 트리거 조건에 맞춘 plan 까지만, 실제 tag/release 발행은 L3 승인 필요 |
| `changelog_driven` | `CHANGELOG.md` / `CHANGES.md` / `HISTORY.md` 등 | CHANGELOG entry 추가 plan 만, tag 는 L3 |
| `version_file_only` | `package.json` / `pyproject.toml` / `Cargo.toml` / `setup.cfg` / `setup.py` / `VERSION` / `version.txt` 의 version 필드 | version bump 만, 자동 tag 미적용 |
| `none` | 위 신호 없음 | **자동 tag/version 미적용** — audit 에 `tag_policy=none` 명시. 무단 tag 생성 금지 |

`RepoContract.has_tag_policy` 가 False 이면 agent 는 tag 작업을 시도하지
않는다 — 정책이 없는데 추측해 만들면 fake success 가 된다.

### 1.3 Operator action inbox

agent 가 진행 중 외부 사실/권한/secret 값이 필요하면 `#승인-대기` 에
operator action 카드 (INFO/ACCESS/SECRET/DECISION) 가 올라간다. 흐름은
[`docs/approval-matrix.md`](approval-matrix.md) §6 참조. issue auto-create
의 LOW confidence 도 같은 inbox 로 흐른다 (`DECISION_REQUIRED`).

### 1.4 Issue-less bootstrap end-to-end (P0-S)

"issue 가 없는 repo 에 업무 요청만 들어와도 agent 가 스스로 issue 를
만들고 그것을 anchor 로 끝까지 이어가는" 종단:

```
Discord intake
  └─ build_github_work_order_proposal(repo_contract, issue_template_loader)
       └─ build_issue_auto_create_plan       ← #166 PR
       └─ session.extra.github_work_order_issue 이미 있으면 existing_anchor 로 전환
  └─ ApprovalRequest 카드 (#승인-대기)
  └─ 승인 1회 → handle_github_work_approval_reply
  └─ dispatch_github_work_order (queue)
  └─ GitHubWorkOrderWorker.process_job        ← #168 신설 consumer
       └─ existing_issue_number 있으면 issue 생성 skip + anchor stamp
       └─ plan 있으면 GithubWriter.create_issue 호출 + anchor stamp
       └─ session.extra["github_work_order_issue"] = {...}
       └─ promote_session_to_coding_ready          ← 본 PR continuation
              └─ session.extra["coding_job"] status="ready" + anchor metadata
              └─ session.extra["github_work_order_progress"] = {
                     "issue_created": ..., "coding_dispatch_queued": ...
                 }
  └─ iter_ready_coding_jobs (autonomy_producer / 운영 tick) 가 같은 세션 발견
  └─ build_coding_execute_request 가 anchor 의 repo/issue_number 를 자동 fallback
  └─ CodingExecutorWorker 가 branch / edit / test / commit / push / draft PR
```

승인 **1회** 후 operator 추가 입력 없이 다음 단계가 자동 이어진다:

1. `GithubWriter.create_issue` 실제 호출 (`auto_create` 또는 `existing_anchor`)
2. `session.extra["github_work_order_issue"]` anchor stamp
3. `session.extra["coding_job"]` status=ready 로 promote + anchor metadata
4. `iter_ready_coding_jobs` 가 세션을 yield
5. `build_coding_execute_request` 가 anchor 의 repo/issue_number 를 자동 fallback
6. dispatcher 가 `coding_execute` 큐에 enqueue
7. `CodingExecutorWorker` 가 branch / edit / test / commit / push / draft PR

`session.extra["github_work_order_progress"]` 가 operator-visible progress
SSoT. 가능한 marker:

| marker | 누가 stamp 하나 |
| --- | --- |
| `issue_created` | `GitHubWorkOrderWorker` (anchor stamp 직후) |
| `coding_dispatch_queued` | `promote_session_to_coding_ready` (continuation) |
| `coding_in_progress` | `CodingExecutorWorker` (작업 시작) |
| `draft_pr_opened` | `CodingExecutorWorker` (draft PR 생성 완료) |
| `coding_blocked` | `operator_action_reply` (외부 요인 대기) |

idempotency:
- 같은 anchor 로 두 번 promote 호출 → noop (`coding_job_already_ready_same_anchor`)
- worker 재시작으로 같은 work_order 가 다시 drain → 이미 SAVED 라 queue 측에서 skip
- coding_job 이 이미 다른 path (Discord chat phrase 승인) 로 ready 면 다시 build 안 함

비표준 진입:
- `coding_proposal` 이 session.extra 에 없으면 `promote_session_to_coding_ready` 가 noop 반환 + audit reason `no_coding_proposal` — operator 가 status diagnostic 으로 즉시 확인 가능 (Discord intake 가 proposal 을 stamp 안 한 경우).

## 2. 환경 contract

| env key | 설명 | placeholder 거부 값 |
|---|---|---|
| `YULE_GITHUB_APP_ID` | App ID (Discord-equivalent App settings 의 숫자) | `0`, `111111`, `123456`, `1234567`, `999999` |
| `YULE_GITHUB_APP_INSTALLATION_ID` | App installation id | (없음 — 빈 값만 거부) |
| `YULE_GITHUB_APP_PRIVATE_KEY_PATH` | pem 파일 경로 (절대 경로 권장) | 존재하지 않거나 권한이 다른 사용자 read 인 경우 doctor 가 WARN/FAIL |
| `YULE_GITHUB_OWNER` / `YULE_GITHUB_REPO` | 작업 대상 owner/repo | (없음 — 빈 값만 거부) |
| `YULE_GITHUB_DEFAULT_DRY_RUN` | `true`/`false`. 미설정 시 **true** (자동 live 금지) | (다른 값 거부) |

`.env.local` 은 **절대 git 에 들어가지 않는다.** `.env.example` 의 placeholder
주석을 그대로 두고, 실제 값은 운영자 머신에서만 채운다. pem 파일도 마찬가지로
저장소 안에 두지 말고 `~/.config/yule/github-app/...` 같이 외부에 둔다.

## 3. CLI

모든 command 는 `yule github <subcommand>` 로 호출한다. 출력은 secret
패턴 (`gh*_`, `Bearer`, `-----BEGIN ... PRIVATE KEY-----`) 을 자동 redact
한다.

### 3.1 `yule github doctor [--json] [--live]`

* 옵션 없이 — 로컬 env / pem 파일 / placeholder app id 점검 (네트워크 0건).
* `--json` — 같은 점검을 JSON 으로 출력 (CI / 모니터링용).
* `--live` — 실제 installation token 발급 + repo 접근 점검. 4xx 응답은
  분류해 친절 메시지로 보여준다 (401 → auth, 403 → permission, 404 → 미발견,
  5xx → server). placeholder / pem 미준비 상태에서는 live 단계가
  "skipped" 로 떨어지고 토큰 발급을 시도하지 않는다.

`overall` 값이 `fail` 이면 exit code 1, `ok` / `warn` 은 exit code 0.

### 3.2 `yule github triage <issue> --dry-run [--json] [--repo OWNER/REPO]`

* gh CLI (`gh issue view --json …`) 를 통해 issue 본문을 가져와
  :func:`agents.github_workos.triage.senior_triage` 에 넣는다.
* 결과는 G2 의 :class:`TriagePlan` 형태 — `request_type` / `primary_role`
  / `support_roles` / `excluded_roles` / `rationale_by_role` / `scope` /
  `non_scope` / `hidden_risks` / `assumptions` / `implementation_steps` /
  `test_plan` / `approval_required_actions` / `suggested_branch` /
  `risk_level` / `autonomy_level` / `coding_required` /
  `approval_required_before_write` / `decisions` / `role_work_orders`.
* 현 단계는 `--dry-run` 만 지원. 실제 issue 수정은 plan-pr → smoke-pr
  또는 별도 승인 플로우를 거친다.

### 3.3 `yule github plan-pr <issue> --dry-run [--json] [--base-branch main]`

* triage 결과를 G3 의 :func:`derive_branch_name` / :func:`render_pr_body`
  에 흘려 미리보기 branch 이름 + 제목 + 본문을 출력한다.
* protected branch (`main`/`master`/`develop`/...) 가 후보로 잡히면
  하드 거부.
* JSON 출력 시 PR body 전체 + `merge_blocked: true` 마커 포함.

### 3.4 `yule github smoke-pr --live [--issue N] [--repo OWNER/REPO] [--base-branch main] [--branch-name NAME] [--json]`

* `--live` 없이는 즉시 거부. **이 명령어가 GitHub App API 로 실제 branch /
  파일 / draft PR 을 만든다.**
* 흐름:
  1. `doctor --live` 가 FAIL 이면 abort.
  2. `LiveGithubAppClient` 가 installation token 발급.
  3. `--issue` 가 주어지면 senior_triage + render_pr_body 로 PR body 의
     "목적/범위/리스크/테스트 계획/승인 필요" 섹션을 채운다.
  4. base branch HEAD sha 조회 → smoke branch ref 생성.
  5. `runs/github-workos-smoke/<UTC ts>.md` blob + tree + commit 생성
     (commit 메시지에 `audit_id` 포함).
  6. branch ref 를 새 commit 으로 PATCH.
  7. **draft PR** 생성 — PR body 끝에 항상 ⚠️ Merge 금지 안내 포함.
* 운영자는 검증 후 `gh pr close <num>` 으로 닫고, branch 도 필요시 직접 삭제.

## 4. 라이브 스모크 절차

> **사전 게이트:** `docs/operations.md` §11 (P0 Secret Hygiene + Token
> Rotation) 가 모두 ✅ 인 상태에서만 시작한다. placeholder app id /
> 미rotate 토큰이 있으면 즉시 중단.

1. `yule github doctor` 가 모두 OK / WARN 인지 확인.
2. `yule github doctor --live` 로 토큰 발급 + repo 접근 검증.
3. 테스트 issue 준비 — 기존 사용 가능한 issue 가 있다면 그걸 쓰고, 없으면
   GitHub UI 에서 `[smoke]` 라벨이 붙은 issue 하나를 만든다.
4. `yule github triage <issue> --dry-run --json` 으로 senior triage 결과
   확인 (excluded_roles 의 사유까지 채워졌는지, hidden_risks 가 있는지).
5. `yule github plan-pr <issue> --dry-run` 으로 branch 이름 + PR body
   미리보기 검수.
6. `yule github smoke-pr --live --issue <issue>` 실행. 출력에서 PR URL
   복사.
7. PR URL 에서 본문 확인:
   - "## ⚠️ Merge 금지" 안내가 있는가?
   - `runs/github-workos-smoke/<ts>.md` 파일이 commit 1 건으로 추가됐는가?
   - PR body 에 audit_id / branch / commit / smoke marker 가 있는가?
8. Discord / Obsidian 브릿지는 dry-run 또는 fake 로 검증 (G4 어댑터의
   기본 dry_run=True 그대로 둔다).
9. PR 닫기 (`gh pr close`) + 필요 시 smoke branch 삭제.

## 5. Hard rails

* `main` / `master` / `develop` 등 protected branch 는 GithubWriter / G6
  smoke 양쪽에서 거부한다.
* `force=True` push 는 fake / live 양쪽에서 거부.
* merge / deploy / secret modify 는 본 코드 경로에 **존재하지 않는다.**
  사람이 수동으로만 한다.
* draft PR body 는 항상 "Merge 금지" 안내를 포함하며, 운영자가 실수로
  머지하지 못하게 한다 (GitHub branch protection + 본 본문 두 단의 안전).
* 모든 audit / 로그 출력은 `redact_secrets` / `redact_secret_like`
  필터를 거친다 — `Authorization`, `Bearer`, `gh*_`, PEM 블록 패턴이 자동
  제거된다.

## 6. 자동화 테스트 cross-reference

| 영역 | 테스트 | 비고 |
|---|---|---|
| G1 — auth/config/doctor | `tests/github_app/test_*.py` (48종) | offline 친화적, live는 mock HTTP |
| G2 — triage | `tests/github_workos/test_issue_context.py`, `test_issue_triage.py`, `test_identity.py`, `test_policy.py` | senior_triage 결과 검증 |
| G3 — branch/PR/audit | `tests/github_workos/test_branching.py`, `test_pr_template.py`, `test_github_writer.py`, `test_github_audit.py`, `test_commit_policy.py`, `test_senior_quality_contract.py` | dry-run 가드 + 정책 게이트 |
| G4 — Discord 브릿지 | `tests/discord/test_github_workos_*.py` | proposal/approval/dispatch 어댑터 |
| G5 — e2e harness | `tests/github_workos/test_end_to_end_workos.py` | 통합 시나리오 |
| G6 — 통합 + CLI | `tests/github_app/test_*.py` 도 G6 의 wiring 을 커버 | 본 모듈은 추가 unit 없음 — 라이브 검증을 자동화 (live smoke 자동 실행) 하지 않는 정책 |

전체 회귀: `python -m unittest discover -t . -s tests` (2707 tests OK 기준).

## 7. 알려진 한계 / 다음 단계

* **Real coding runner** — 본 G6 까지의 지점은 issue triage + 계획서 +
  draft PR 까지. 실제 코드 수정 (LLM driven) 은 다음 milestone.
* **Multi-repo** — 현재는 `YULE_GITHUB_OWNER` / `YULE_GITHUB_REPO`
  하나만 본다. 여러 repo 관리는 env 분리 또는 launcher 별도 필요.
* **Reviewer account** — draft PR 의 reviewer auto-assign / GitHub
  branch protection 의 "review required" 자동 충족은 별도 G7 후보.
* **Schema drift between G2 and G3** — G2 의 `TriagePlan` 과 G3 의
  `TriagePlanLike` Protocol 이 약간 다른 vocabulary 를 쓴다.
  `cli/github_workos.py` 의 `_G3PlanAdapter` 가 둘 사이를 잇는다.
  이후 G2 dataclass 가 변경되면 adapter 도 같이 업데이트.
