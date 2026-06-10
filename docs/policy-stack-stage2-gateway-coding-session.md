# Policy Stack Stage 2 — Gateway Coding-Session Wiring (P0-H)

> **Status:** stage 2 audit doc — single source of truth for 7-commit sequence.
> **Issue:** #140 (parent #138, follows #139). Stage 1 정책 8 종 land 완료 후 그 정책을 *기능 코드로* 따르도록 wiring.

## 0. 목표

gateway 가 GitHub 링크를 받았을 때 코딩 작업 세션으로 정확히 해석 + 한 번만 mode/topology 를 묻고 세션 내내 지속. 5 영역:

1. GitHub URL ingress (repo / issue / PR / commit / compare / branch context).
2. RepoContract discovery wiring (#139 정책 1 의 코드 land).
3. Session mode/topology negotiation (#139 정책 4 의 ask-once 코드 land).
4. Coding-capable handoff packet (tech-lead 에게 넘기는 single envelope).
5. Status surface 확장 (repo / mode / topology / branch / PR / contract / Obsidian path).

## 1. 충돌 가능 지점 (10줄)

1. `parse_github_url` (`collector.py:316`) 가 issue/PR 만 지원. 신규 commit/compare/branch shape 가 필요. 기존 3 caller 무회귀 위해 새 파서를 만들고 collector helper 는 위임.
2. `WorkflowSession.extra` 가 freeform Mapping — 신규 key (`work_mode` / `topology` / `scope` / `repo_contract` / `github_target` / `branch_name` / `pull_request_number` / `obsidian_mirror_path` / `mode_decided_at` / `mode_decided_by`) 추가 무회귀.
3. `RepoContract` 코드 신설 필요 — stage-1 doc 이 shape 만 정의. gh CLI / GitHub Apps 권한 없을 때 graceful fallback (사용자 명시 — fake success 금지).
4. `build_engineering_conversation_response` (`engineering_conversation.py:113`) 가 hook point. 시그니처 확장 시 `bot.py:1832` 의 lambda + `engineering_channel_router.route_engineering_message` 의 conversation_fn 호출부 (`engineering_channel_router.py:576`) 도 같이.
5. `session_status.SessionStatusReport` (`session_status.py:81`) field 추가는 default 값 강제 — 기존 governance 26 + P0-G 37 = 63 test 무회귀 필수.
6. `CodingAuthorizationProposal` (`coding/authorization.py:117`) 이 이미 dataclass — 그러나 사용자 요구의 `CodingHandoffPacket` 은 *상위 포장*. 별도 dataclass 신설 후 proposal 을 안에 둠 (또는 `Optional[CodingAuthorizationProposal]` 필드).
7. ask-once 는 hook 부재 — `agents/lifecycle/session_mode.py` 신설 + intake 전 negotiation. session.extra 에 mode 있으면 즉시 skip.
8. RepoContract discovery 가 권한 없으면 `fallback=True`. PR body 의 §📚 에 "이 repo 에 자체 컨벤션이 없어 Yule 기본 규칙 사용" 한 줄 자동 surface.
9. 새 repo 생성은 범위 밖. 본 PR 은 *이미 존재하는 repo* 의 contract 만 수집.
10. 8 commit → 7 commit 으로 줄임 (audit / URL parser / RepoContract / mode negotiation / handoff packet / status surface / wiring + tests).

## 2. 신규 세션 메모리 contract (session.extra)

| key | type | 산출 위치 | 비고 |
| --- | --- | --- | --- |
| `work_mode` | `"autonomous_merge" \| "approval_required"` | `session_mode.ensure_session_mode` | stage-1 autonomy-policy §0.1. |
| `topology` | `"single_repo" \| "multi_repo"` | 동일 | stage-1 autonomy-policy §0.2. |
| `scope` | `"single_scope" \| "full_stack_single_repo" \| "layer_scoped" \| "cross_repo_program"` | 동일 | stage-1 autonomy-policy §0.3. |
| `mode_decided_at` | iso8601 string | 동일 | ask-once 추적. |
| `mode_decided_by` | `"user_explicit" \| "gateway_inferred"` | 동일 | 추측 vs 명시. |
| `github_target` | dict (GithubTarget.to_dict()) | URL parser | repo / issue / PR / commit / compare / branch context. |
| `repo_contract` | dict (RepoContract.to_dict()) | discovery 모듈 | 발견된 contract + fallback flag. |
| `branch_name` | optional string | conversation 또는 후속 packet | status surface 용. |
| `pull_request_number` | optional int | URL parser | 동일. |
| `obsidian_mirror_path` | optional string | growth-loop wiring (P0-H 범위) | status surface 용. |

신규 key 는 모두 *optional*. 기존 caller / 회귀 test 무영향.

## 3. 신규 / 갱신 파일 매트릭스

| 위치 | C/R/U/D | 책임 |
| --- | --- | --- |
| `docs/policy-stack-stage2-gateway-coding-session.md` | C (본 doc) | 7-commit single source of truth. |
| `apps/engineering-agent/src/yule_engineering/agents/git/github_url.py` | C | GithubTarget dataclass + parse_github_url (5 shape). |
| `apps/engineering-agent/src/yule_engineering/agents/research/collector.py` | U | 기존 `parse_github_url` 을 github_url 모듈 위임 wrapper 로. |
| `apps/engineering-agent/src/yule_engineering/agents/git/repo_contract.py` | C | RepoContract dataclass + discover (gh CLI / 로컬 클론) + fallback. |
| `apps/engineering-agent/src/yule_engineering/agents/lifecycle/session_mode.py` | C | ensure_session_mode helper + question prompt builder. |
| `apps/engineering-agent/src/yule_engineering/agents/coding/handoff_packet.py` | C | CodingHandoffPacket dataclass + build_packet. |
| `apps/engineering-agent/src/yule_engineering/agents/lifecycle/session_status.py` | U | SessionStatusReport 신규 7 필드 + diagnose_session 추출. |
| `apps/engineering-agent/src/yule_engineering/discord/engineering_conversation.py` | U | format_status_diagnostic_response 신규 라인 + build_engineering_conversation_response 가 GithubTarget / RepoContract 인식. |
| `apps/engineering-agent/src/yule_engineering/discord/engineering_channel_router.py` | U | URL 발견 → RepoContract discovery → mode negotiation → handoff packet 순서로 wiring. |
| `tests/agents/git/test_github_url.py` | C | URL parser test (5 shape + edge). |
| `tests/agents/git/test_repo_contract.py` | C | RepoContract discovery + fallback test. |
| `tests/agents/lifecycle/test_session_mode.py` | C | mode negotiation + persistence + no-repeat test. |
| `tests/engineering/test_handoff_packet.py` | C | CodingHandoffPacket builder test. |
| `tests/engineering/test_session_status_p0h.py` | C | status surface 신규 필드 렌더 test. |

## 4. 호출 그래프 (after stage 2)

```
on_message
└── route_forum_message / _route_engineering_approval_reply / route_engineering_message
    └── route_engineering_message  (engineering_channel_router.py)
        ├── 1) extract_urls(text)            ← 기존
        ├── 2) parse_github_target(urls)     ← 신규 (commit 2)
        ├── 3) discover_repo_contract(...)   ← 신규 (commit 3), best-effort, fallback OK
        ├── 4) ensure_session_mode(...)      ← 신규 (commit 4), ask-once
        ├── 5) conversation_fn(...)          ← 기존, RepoContract / GithubTarget 를 context 로 전달
        ├── 6) build_coding_handoff_packet() ← 신규 (commit 5), coding-capable 일 때만
        └── 7) intake_fn / handoff_fn       ← 기존, session.extra 에 신규 key 영구 저장
```

## 5. acceptance criteria 매핑

| Stage 2 AC | 처리 commit |
| --- | --- |
| GitHub URL parsing tests | 2 |
| RepoContract detection tests | 3 |
| mode persistence tests | 4 |
| topology persistence tests | 4 |
| status surface tests | 6 |
| no repeated questioning regression tests | 4, 7 |
| 기존 4363 PASS 무회귀 | 매 commit |

## 6. 남은 미결정 (3차 #141 로 deferred)

- vault repo workspace 가 실제로 발견됐을 때 `obsidian_mirror_path` 자동 산정 (현재는 None 으로만 채움).
- semantic CRUD-like slice 의 PR lint CI check (GitHub Actions workflow).
- growth-loop 의 5 신호 자동 감지 wiring (failed_retryable + PR review 반복 등).
- design asset 의 실제 SVG source 폴더 / `<Icon>` 컴포넌트 (frontend production code 생성 시).

## 7. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-14 | 초안 — Issue #140 P0-H stage 2 audit. parent #138, follows #139. |
