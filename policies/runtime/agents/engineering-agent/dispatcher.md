# Engineering Agent Gateway Dispatcher (v0)

이 문서는 engineering-agent 게이트웨이가 들어온 요청 하나를 받아 **참여 후보, 실행 후보/검토 후보 모델, 참고 reference, write 게이트**를 결정하는 디스패처의 정책 기준선이다. 코드 진실 소스는 `apps/engineering-agent/src/yule_engineering/agents/dispatcher.py`이며, 본 문서는 운영자가 읽고 수정 절차를 따르는 규약이다.

## 1. 입력과 출력

### 입력 — `DispatchRequest`
- `prompt: str` — 자연어 요청.
- `task_type: TaskType | None` — 명시 분류. 비어 있으면 키워드 분류기로 추정.
- `write_requested: bool` — 이 실행에서 코드/문서 쓰기를 요청하는지.
- `user_approved: bool` — 사용자가 명시적으로 승인했는지.
- `repository: str | None`, `extra: Mapping` — 컨텍스트.

### 출력 — `DispatchPlan`
- `task_type` — 최종 분류.
- `role_sequence: tuple[str, ...]` — 참여 후보 목록. legacy 이름은 유지하지만 forum에서는 순번표로 쓰지 않는다.
- `assignments: tuple[RoleAssignment, ...]` — 역할마다 (runner_id, is_executor, score, rationale).
- `reference_sources: tuple[str, ...]` — task_type에 매핑된 추천 소스.
- `write_blocked: bool`, `write_block_reason: str | None`.
- `notes: tuple[str, ...]` — 부분 풀, 게이트 차단 사유 등.

## 2. 분류 (`Dispatcher.classify`)

1. `task_type`이 명시되어 있으면 그대로 사용.
2. 비어 있으면 prompt(소문자)에서 키워드를 순서대로 검사한다. 첫 매치가 결과.
3. 매치 없음 → `TaskType.UNKNOWN`.

키워드 우선순위 (구체 → 일반):
1. VISUAL_POLISH (`polish`, `visual `, `리디자인`, `redesign`, ...)
2. ONBOARDING_FLOW (`onboarding`, `온보딩`, ...)
3. EMAIL_CAMPAIGN (`email`, `캠페인`, `광고`, ...)
4. LANDING_PAGE (`landing`, `랜딩`, `marketing page`)
5. QA_TEST, PLATFORM_INFRA, FRONTEND_FEATURE, BACKEND_FEATURE

이유: `히어로 visual polish 정리`처럼 두 개 이상 시그널이 섞이면 더 구체적인 의도가 우선되어야 한다.

## 3. 참여 후보와 실행 후보

| task_type | sequence | executor |
|---|---|---|
| backend-feature | tech-lead → backend-engineer → qa-engineer | backend-engineer |
| frontend-feature | tech-lead → product-designer → frontend-engineer → qa-engineer | frontend-engineer |
| landing-page | tech-lead → product-designer → frontend-engineer → qa-engineer | frontend-engineer |
| onboarding-flow | tech-lead → product-designer → frontend-engineer → backend-engineer → qa-engineer | frontend-engineer |
| visual-polish | tech-lead → product-designer → frontend-engineer | frontend-engineer |
| email-campaign | tech-lead → product-designer → frontend-engineer → qa-engineer | frontend-engineer |
| qa-test | tech-lead → qa-engineer → backend-engineer | qa-engineer |
| platform-infra | tech-lead → backend-engineer → qa-engineer | backend-engineer |
| unknown | tech-lead | tech-lead |

원칙
- `role_sequence`는 DB/API 호환을 위해 남은 이름이다. 운영-리서치 forum에서는 이 값을 순서표로 렌더링하지 않고, open-call을 본 멤버 봇들이 각자 응답한다.
- tech-lead는 참여 후보에 항상 포함된다. 작업 분해와 의존성 관점을 책임진다.
- executor는 task_type별 실행 후보 1명이다. 나머지는 검토 후보이며, 승인된 실행 루프가 붙기 전까지는 독립 조사와 검토 take를 남긴다.
- visual-polish의 executor는 frontend-engineer (코드 산출물). product-designer는 advisor로 시각 결정만 한다. 향후 design-agent가 분리되면 visual-only 작업의 executor 위임은 그쪽으로 이전한다.

## 4. 모델 선택

각 역할마다 다음 합산을 적용해 가장 높은 점수의 runner를 고른다.

```
score = base_weight + task_bonus + (ranking_signal × ranking_weight)
```

- `base_weight` — `role-weights-v0.md` 표를 그대로 옮긴 `ROLE_DEFAULT_WEIGHTS`.
- `task_bonus` — task_type × role × runner의 작은 보정. 음수 허용. 합산 결과가 0 이하면 제외.
- `ranking_signal` — 외부 신호(LMSys 등). MVP 기본 `ranking_weight=0`으로 무력화. 슬롯만 예약.
- `pool에 없는 runner는 후보에서 제외` — 새 백엔드를 등록하기 전이라도 기존 세트로 동작.

현재 task_bonus 매트릭스 (코드 `TASK_BONUSES`):

| task_type | role | runner | 보정 |
|---|---|---|---|
| landing-page | product-designer | gemini | +2 |
| landing-page | frontend-engineer | codex | +1 |
| visual-polish | product-designer | gemini | +3 |
| visual-polish | frontend-engineer | gemini | +1 |
| onboarding-flow | tech-lead | gemini | +1 |
| onboarding-flow | product-designer | gemini | +1 |
| email-campaign | product-designer | gemini | +2 |
| email-campaign | frontend-engineer | codex | +1 |
| qa-test | qa-engineer | codex | +2 |
| platform-infra | backend-engineer | claude | +1 |
| platform-infra | qa-engineer | codex | +1 |
| backend-feature | backend-engineer | codex | +1 |

타이브레이커: 점수 동률이면 runner_id 알파벳순.

## 5. Reference Pack 추천

| task_type | sources |
|---|---|
| landing-page | Wix Templates, Awwwards, Behance, Pinterest Trends |
| onboarding-flow | Mobbin, Page Flows |
| visual-polish | Pinterest Trends, Notefolio, Behance, Canva Design School |
| email-campaign | Really Good Emails, Meta Ad Library, TikTok Creative Center, Google Trends |
| 기타 | (없음 — 백엔드/QA/인프라는 시각 reference 강제 안 함) |

규약
- 자동 수집이 약관상 민감한 소스(Notefolio, Behance, Mobbin 등)는 사용자 제공 링크와 수동 참고로만 사용한다 (env-strategy.md §7, reference-pack.md §자동 수집 정책).
- 디스패처는 소스 이름만 추천하고, 실제 페치는 후속 reference fetcher 마일스톤에서 처리한다.

## 6. Write 게이트

```
if write_requested and not user_approved:
    write_blocked = True
    write_block_reason = "write is requested for <role> but user_approved=False. Block until the operator confirms."
```

- 게이트는 **실행 단계**에서 검사한다. 디스패처는 plan을 생성하더라도 executor가 실제로 쓰기를 시도하기 전에 이 플래그를 확인해야 한다.
- single-executor 원칙은 `write_policy.max_write_executors_per_run=1`(manifest.json)과 일치한다.

## 7. RankingSignal 슬롯

`Dispatcher(pool, ranking_signal=…)` 또는 `dispatcher.ranking_signal = …`로 주입한다. 호출 측에서 `dispatch(request, ranking_weight=...)`를 사용해 가중치를 명시한다. MVP에서는 `ranking_weight=0`(기본)로 무력화한다.

이 슬롯이 채워지는 시점은:
- 로컬 성과(승인률, 회귀률) 수집기가 도입될 때.
- 외부 리더보드(LMSys, HumanEval) 변동이 안정화될 때.

자동 점수 반영은 별도 정책 변경으로 결정한다 (`role-weights-v0.md` §외부 신호 처리 정책).

## 8. 변경 절차

가중치 / task bonus / sequence 변경 시:

1. PR 본문에 변경 이유와 어느 task_type에서 발견된 문제를 적는다.
2. `dispatcher.py`의 상수와 `dispatcher.md`의 표를 함께 수정한다 (둘이 어긋나면 정합성 테스트가 실패).
3. role-weights-v0.md의 base 가중치를 바꾸는 경우, dispatcher의 `ROLE_DEFAULT_WEIGHTS`도 같이 갱신한다.
4. 새 task_type 추가 시: TaskType enum, TASK_ROLE_SEQUENCE, TASK_EXECUTOR_ROLE, (선택) TASK_BONUSES, (선택) TASK_REFERENCE_SOURCES, _KEYWORD_RULES 모두 갱신.
5. 테스트 추가 (분류, 시퀀스, 가중치 표 일치, references 표 일치).

## 9. 후속 작업

- 멤버 봇 IPC: dispatcher 결과를 멤버 봇 큐에 넣어 advisor → executor 순서 실행.
- 게이트 자동 yes/no: Discord 체크포인트 yes/no 응답을 `user_approved`로 매핑.
- Reference fetcher: 소스 이름만 노출하던 슬롯에 실제 자료를 채운다 (`format_references_block` 사용).
- PerformanceTracker → ranking_signal 입력으로 연결.
