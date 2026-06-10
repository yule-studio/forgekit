# Policy Stack Stage 3 — Enforcement Layer (P0-I)

> **Status:** stage 3 audit — single source of truth for 7-commit sequence.
> **Issue:** #141 (parent #138, follows #139 #140). Stage 1 정책 land + stage 2 gateway wiring 완료 후, *런타임 가드* 로 정책 위반을 실시간 차단.

## 0. 목표

coding flow 가 stage 1 정책대로 자동 강제. "아무 repo 나 제멋대로 수정하는 흐름" 차단 → `RepoContract + tracking + mirror + growth loop` 가진 작업 엔진.

7 영역:

1. Tracking enforcement (issue→branch→commit→PR→merge chain).
2. PR splitting 가이드 (semantic C/R/U/D slice).
3. Obsidian routing (kind → folder 자동) — **이미 land** (`export.py:67-95`), 본 stage 는 cross-link 만.
4. Growth loop capture (references / retrospectives / promotion 후보).
5. Vault commit/push behavior (mode 따른 분기 + "not configured" 상태 surface).
6. Merge hard rails — **이미 land** (`pr_approval.py:evaluate_merge_gate`), 본 stage 는 `autonomous_merge` mode 와 연결만.
7. Design-to-code asset 지원 (naming validator + SVG vs raster boundary).

## 1. 충돌 가능 지점 (10줄)

1. Tracking enforcement 부재 — stage 2 가 session.extra 저장만, validate 없음. 신규 module 필요.
2. PR slice classifier 부재 — `CodingHandoffPacket` 도 slice 분류 안 함. 신규 module.
3. Obsidian routing — `agents/obsidian/export.py:67-95` 에 8 folder 매핑 + `recommend_path(kind, project)` 이미 land. **수정 불필요**, cross-link 만.
4. Growth loop — `self_improvement.py` 가 signal 감지만 함. *capture* 부재 (references / retrospectives). 신규 module.
5. Vault commit/push — `autonomy_policy.py` 에 `ACTION_VAULT_REMOTE_PUSH` (L3) 상수만, dispatcher / executor 부재. 신규 module + 상태 surface.
6. Merge hard rails — `agents/job_queue/pr_approval.py:349-437` + `pr_merge_executor.py` 완전 구현. `autonomous_merge` mode 와의 연결만 추가.
7. Design-to-code asset — 코드 0. frontend production 없을 때 fake success 금지 → naming validator + SVG/raster boundary helper 만 land. 실 자산 폴더는 deferred.
8. session.extra 신규 schema — 모두 optional. 기존 caller 무회귀.
9. `format_status_diagnostic_response` 라인 추가는 dataclass default 채택 → 기존 lint test 무회귀.
10. 7 commit 분할 — audit / 5 신규 module (tracking / pr-split / growth / vault-push / asset) / 통합 wiring.

## 2. session.extra 신규 schema

| key | type | 산출 위치 |
| --- | --- | --- |
| `tracking_validation` | dict (TrackingValidation.to_dict()) | tracking_enforcement |
| `tracking_blocked_reason` | optional str | 동일 |
| `pr_slice_classification` | dict (PRSliceClassification.to_dict()) | pr_slice_classifier |
| `pr_split_recommendation` | optional str | 동일 |
| `growth_ledger` | list[dict] | growth_ledger |
| `growth_promotion_candidates` | list[str] | 동일 |
| `vault_push_audit` | list[dict] | vault_push_dispatcher (stage-1 #4 approval-matrix §3.1 SSoT) |
| `vault_push_not_configured_reason` | optional str | 동일 |
| `design_asset_validations` | list[dict] | design_asset_routing |

모두 optional default. 기존 caller / 회귀 test 무영향.

## 3. 신규 / 갱신 파일 매트릭스

| 위치 | C/R/U/D | 책임 |
| --- | --- | --- |
| `docs/policy-stack-stage3-enforcement-layer.md` | C (본 doc) | 7-commit SSoT. |
| `apps/engineering-agent/src/yule_engineering/agents/coding/tracking_enforcement.py` | C | validate_tracking_chain. |
| `apps/engineering-agent/src/yule_engineering/agents/coding/pr_slice_classifier.py` | C | classify_pr_slice + recommend_split. |
| `apps/engineering-agent/src/yule_engineering/agents/lifecycle/growth_ledger.py` | C | GrowthEvent + capture + promotion 후보. |
| `apps/engineering-agent/src/yule_engineering/agents/job_queue/vault_push_dispatcher.py` | C | VaultPushRequest + dispatch (mode-aware). |
| `apps/engineering-agent/src/yule_engineering/agents/design/asset_routing.py` | C | validate_asset_name + SVG/raster boundary. |
| `apps/engineering-agent/src/yule_engineering/discord/engineering_channel_router.py` | U | `_persist_coding_session_context` 가 tracking_validation / growth_ledger 호출. |
| `apps/engineering-agent/src/yule_engineering/agents/lifecycle/session_status.py` | U | SessionStatusReport 에 신규 7 필드 surface. |
| `apps/engineering-agent/src/yule_engineering/discord/engineering_conversation.py` | U | format_status_diagnostic_response 의 신규 라인. |
| `tests/agents/coding/test_tracking_enforcement.py` | C | + 4 추가 test 파일. |

## 4. 호출 그래프 (after stage 3)

```
on_message
└── route_engineering_message
    ├── 1) extract_urls / parse_github_target  ← stage 2
    ├── 2) discover_repo_contract              ← stage 2
    ├── 3) ensure_session_mode                  ← stage 2
    ├── 4) conversation_fn                      ← stage 2
    ├── 5) intake_fn                            ← 기존
    ├── 6) _persist_role_selection / _lifecycle_mode  ← 기존
    └── 7) _persist_coding_session_context      ← stage 2 + (NEW)
           ├── validate_tracking_chain          ← stage 3
           ├── classify_pr_slice (if PR target) ← stage 3
           ├── seed_growth_ledger               ← stage 3
           └── validate_design_assets           ← stage 3 (only if assets mentioned)
```

merge path (PR 머지 자동화):

```
ApprovalWorker → pr_approval.evaluate_merge_gate (이미 land)
                  └── mode check (stage 3 wire — autonomous_merge 만 통과)
```

vault push path:

```
ObsidianWriterWorker (이미 land — L2 local commit)
└── vault_push_dispatcher.dispatch_vault_push (stage 3 신규)
    └── autonomy_policy.decide_autonomy(action="vault_remote_push", mode=...)
```

## 5. acceptance criteria 매핑

| Stage 3 AC | 처리 commit |
| --- | --- |
| tracking enforcement tests | 2 |
| split recommendation tests | 3 |
| obsidian routing tests | 3 (cross-link) — Obsidian export.py 이미 회귀 test 보호 |
| vault commit/push policy tests | 5 |
| merge hard rail tests | 7 (autonomous_merge mode 연결 부분만) — gate 자체는 이미 보호 |
| reference/retrospective capture tests | 4 |
| 기존 4456 PASS 무회귀 | 매 commit |

## 6. 남은 미결정 (deferred)

- 실 frontend production code 가 생기면 SVG asset 폴더 / `<Icon>` 컴포넌트 / raster export 스크립트 (asset_routing.py 의 validator 가 그때 production wire).
- vault repo workspace 가 실제 클론되면 vault_push_dispatcher 의 backend (`git push origin <branch>`) 활성화. 현재는 SSoT contract 만 land.
- design-to-code asset 의 실제 storage / CDN / publishing pipeline.

## 7. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-14 | 초안 — Issue #141 P0-I stage 3 audit. parent #138, follows #139 #140. |
