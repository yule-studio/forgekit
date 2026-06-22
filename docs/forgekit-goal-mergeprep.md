# ForgeKit goal control-plane — merge-prep / QA (GW8)

> 본 라운드(`/goal` 중심 ForgeKit 완성)의 **final QA + merge-prep 체크리스트**. 전부 **로컬 커밋
> 만, 미push** — push/PR/merge 는 operator 요청 시에만([[feedback_no_auto_merge]]). 방향/acceptance
> SSoT 는 [`forgekit-goal-roadmap.md`](forgekit-goal-roadmap.md).

## 1. Worktree 스택 (7 stacked 브랜치, base=main)

순서대로 stacked (각자 코드+테스트+evidence+honest boundary):

1. `feat/forgekit-goal-core` — GW1 `/goal` 코어(`packages/forgekit-goal`)
2. `feat/forgekit-commit-governance` — GW2-A commit CI guard + Co-Authored-By 금지
3. `feat/forgekit-approval-chain-verify` — GW3 approval chain 검증/정정(문서)
4. `feat/forgekit-goal-selfimprove` — GW4-A bounded goal-tick
5. `feat/forgekit-import-boundary` — GW6 app→app drift guard
6. `feat/forgekit-goal-surface` — GW5 `/goal` console surface
7. `feat/forgekit-launchd-units` — GW7 launchd 데몬 템플릿
8. `feat/forgekit-goal-qa` — GW8 본 문서

> 스택은 작업 순서이고 의존 순서와 약간 다름(GW6 이 GW5 앞에 들어감). 머지 시 권장 순서는 §5.

## 2. 테스트 (실측, 로컬)

신규 GW 회귀 **42 tests green (1 skip)** — `python3 -m unittest` 개별 모듈:

| suite | n | 대상 |
| --- | ---: | --- |
| `test_goal_core` | 14 | 모델/전이/evidence/linkage/영속 round-trip/스키마 |
| `test_goal_tick` | 5 | bounded tick(linkage/evidence/approval-wait/실행없음) |
| `test_goal_surface` | 7 | `/goal` 라우팅·CRUD·영속(실 router) |
| `test_commit_governance_ci` | 5 (1 skip) | commit 정책 재사용 + Co-Authored-By 금지 |
| `test_app_boundary_drift` | 2 | app→app 51-edge baseline freeze |
| `test_launchd_template` | 5 | plist 구조 + README lid-close 정직 |
| `test_package_topology_guard`(2 클래스) | 4 | packages→apps + topology doc(forgekit-goal 포함) |

> skip 1 = `test_commit_governance_ci` 의 실 정책 통합(로컬 `yule_core` 미설치 — PEP668
> externally-managed). CI 의 clean `pip install -e .` 에서 실행됨. 로컬 `tests/forgekit` 전체
> discover 의 다수 ModuleNotFound 는 동일 원인(main baseline 동일, 본 라운드와 무관).

## 3. Evidence index (실행 캡처)

- `apps/forgekit-console/examples/goal/roundtrip.txt` — goal 생성→active→done거부(evidence없음)→evidence→done→재실행 복원
- `apps/forgekit-console/examples/goal/tick.txt` — safe/risky/blocked 신호 tick → linkage+evidence → awaiting_approval(실행 0)
- `apps/forgekit-console/examples/goal/surface.txt` — `/goal` 실 router 세션(new→activate→show→list→usage)
- `apps/forgekit-console/examples/commit-governance/guard-smoke.txt` — 실 git 4 commit 통과 + 합성 Co-Authored-By 거부

## 4. 9 기둥 DoD 최종 상태 (정직)

| # | 기둥 | 상태 | 본 라운드 기여 |
| --- | --- | --- | --- |
| 1 | provider 연결/설정 | ✅ 기존 | — (기존 working) |
| 2 | routing/fallback 영속 | 🟡 기존 | — (per-provider budget 후속) |
| 3 | approval chain 런타임 강제 | ✅ 검증 | GW3 — 이미 구현·배선·테스트 확인/정정 |
| 4 | Claude 근접 TUI | 🟡 | GW5 — `/goal` surface 추가; approve/deny UI seam 남음 |
| 5 | `/goal` 자기관리 | 🟡 core+tick+surface | **GW1·GW4-A·GW5** — 모델/영속/tick/surface ✅; 실행 bridge(GW4-B) 남음 |
| 6 | 경계 명확 | 🟡 guarded | GW6 — app→app drift guard(51-edge freeze); paydown 별도 트랙 |
| 7 | commit/identity 강제 | 🟡 GW2-A | GW2-A — CI guard+Co-Authored-By; trailer→identity(GW2-B) 남음 |
| 8 | 24h bounded always-on | 🟡 +units | GW7 — launchd 템플릿; lid-close 정직; auto-install 남음 |
| 9 | 자기조사→backlog 승격 | 🟡 promotion | GW4-A — 신호→packet→goal linkage+evidence; 외부 connector(P2) 남음 |

## 5. 권장 머지 순서 (operator 요청 시)

의존 기준 — base=main 위에:
**GW1 → GW2-A → GW3 → GW4-A → GW5 → GW6 → GW7 → GW8.**
(GW2-A/GW6/GW7 은 GW1 과 독립이라 병렬 가능하나, stacked 단순화를 위해 직렬 권장.)
각 worktree 는 CI(`pip install -e .` + `unittest discover -s tests`)에서 자체 회귀 + 신규
`commit-governance` job 통과 필요.

## 6. 남은 seam (정직, 후속 라운드)

- **GW4-B** — 승인된 `RepoImprovementPacket` → `RepoFinding` → 기존 `autopilot.chain` 실행 bridge.
  닫히면 "승인 후 safe-class 실제 실행→verify→evidence" 완성(현재 route/record 까지).
- **GW2-B** — commit trailer 기반 approval-metadata + agent identity(`fk-<role>`) → git author 바인딩.
- **approve/deny UI** — `/daemon`/inbox 가 콘솔에서 상태만 표시, in-console 승인/거부 액션 없음.
- **app→app 51-edge paydown** — 모놀리스 분해(`monorepo-structure.md §4`) 별도 트랙.
- **unit auto-install** — launchd/systemd 수동 설치(현재). 
- **per-provider budget** — global budget 만 강제(기존 gap).

## 7. honesty 원칙 준수 확인

- fake-green 없음: 모든 ✅ 는 실 테스트+evidence. `done` 은 evidence 강제(`transitions`).
- 미구현을 구현으로 보고 안 함: 남은 것 전부 §6 seam 으로 명시.
- 이미 된 걸 미구현이라 하지 않음: GW3(approval chain) 정정이 그 예.
- 승인 없는 파괴적 실행 없음: goal-tick/self-improve 는 관찰·제안·기록만, 실행은 approval-gated chain.
- push/PR/merge 자율 없음: 전부 로컬 커밋까지만.
