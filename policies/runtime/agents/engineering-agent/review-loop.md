# Engineering-Agent — Review Feedback Loop

PR 리뷰 코멘트, GitHub Copilot 코멘트, 외부 에이전트 의견을 다시 engineering-agent 작업 흐름으로 되돌리는 루프 정책. 단일 executor 원칙과 write 게이트는 그대로 유지하면서 "리뷰 → 재분배 → 회신" 닫힘 회로만 추가한다.

## 입력 포맷 (`ReviewFeedback`)

`src/yule_orchestrator/agents/review_loop.py`의 `ReviewFeedback` 데이터클래스를 단일 입력 형식으로 사용한다. 어떤 출처든 이 형식으로 정규화해서 들어온다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `feedback_id` | str | 출처별 고유 ID (PR review id, copilot comment id 등) |
| `source` | enum | `github_pr_review` / `github_copilot` / `external_agent` / `user` |
| `submitted_at` | datetime | 피드백 작성 시각 |
| `summary` | str | 한 줄 요약 (라우팅 키워드 매칭 대상) |
| `body` | str | 본문 (선택, 라우팅 보조 신호) |
| `target_session_id` | str? | 연결할 기존 WorkflowSession ID |
| `target_pr_url` / `target_issue_url` | str? | 원본 위치 |
| `target_thread_id` | int? | Discord thread 연결용 |
| `file_paths` | tuple[str] | 영향 파일 (라우팅 보조) |
| `severity` | enum | `blocking` / `high` / `medium` / `low` / `nit` |
| `categories` | tuple[str] | `ui`, `test`, `architecture` 등 자유 라벨 |
| `references_user` | tuple[str] | 사용자가 직접 첨부한 레퍼런스 링크 |
| `author` | str? | 리뷰어 식별자 |

직렬화/역직렬화는 `to_payload`/`from_payload` 헬퍼로 한다.

## 재분배 규칙 (`route_review_feedback`)

`summary + body + categories`를 하나의 텍스트로 합치고, 아래 우선순위로 매칭해 `primary_role`을 정한다.

1. **`architecture` + 심각도 `blocking`/`high`** → `tech-lead` (지원: backend-engineer, frontend-engineer)
2. **`ui`, `ux`, `layout`, `copy`, `branding`, `design`, `visual`** → `product-designer` (지원: frontend-engineer)
3. **`test`, `coverage`, `qa`, `regression`, `edge-case`** → `qa-engineer` (지원: backend-engineer, frontend-engineer)
4. **`backend`, `api`, `data`, `model`, `auth`, `migration`, `server`** 또는 file_paths가 backend 패턴** → `backend-engineer` (지원: qa-engineer)
5. **`frontend`, `component`, `page`, `interaction`, `client`, `react`** 또는 file_paths가 `.tsx`/`.jsx`/`.css` 등** → `frontend-engineer` (지원: product-designer, qa-engineer)
6. **그 외 모호한 경우** → `tech-lead` (혼자 분류 후 재라우팅)

`severity == nit`인 경우 사유에 `nit severity — fix optional` 표시를 붙여, 응답 시 우선순위가 낮음을 알린다.

단일 write executor 원칙은 그대로 유지된다. supporting_roles는 의견을 제출하지만 코드 commit은 primary_role만 한다.

## 레퍼런스 회수 훅

피드백 본문에 다음 키워드가 있으면 `reference_needed=True`로 표시되고 추천 소스가 함께 반환된다.

| 키워드 그룹 | 부족한 측면 | 추천 소스 |
| --- | --- | --- |
| `flow`, `흐름`, `단계`, `navigation` | UX 플로우 | Mobbin, Page Flows |
| `copy`, `카피`, `문구`, `메시지`, `tone`, `헤드라인`, `후크` | 카피 훅 | Really Good Emails, Page Flows |
| `visual`, `비주얼`, `디자인`, `color`, `typography`, `스타일` | 비주얼 완성도 | Awwwards, Behance, Notefolio, Pinterest Trends |
| `conversion`, `설득력`, `cta`, `광고`, `ad`, `캠페인` | 설득력 | Meta Ad Library, TikTok Creative Center, Google Trends |

`categories`에 `ui`/`ux`/`design` 계열이 들어오면 키워드 매칭이 없어도 `reference_needed=True`로 잡힌다.

자동 수집 정책은 `reference-pack.md`를 그대로 따른다 — Mobbin, Behance, Notefolio 등은 약관상 자동 스크래핑 금지이며, **소스 이름만 추천**하고 실제 페치는 사용자가 수동으로 하거나 후속 reference_collector 마일스톤에서 처리한다.

## 워크플로 통합 (`WorkflowOrchestrator.record_review_feedback`)

`WorkflowSession`에 두 필드가 추가된다.

- `review_cycle: int` (기본 0) — 누적 리뷰 회차
- `review_feedbacks: Sequence[Mapping]` — 각 피드백 + 라우팅 결정 누적

`record_review_feedback(session_id, feedback)` 동작:

1. session 로드. `REJECTED` 상태면 `WorkflowError`로 거부.
2. `route_review_feedback(feedback)`로 라우팅 결정.
3. `feedback`을 직렬화해 `routing` 결과와 함께 `review_feedbacks`에 append.
4. `review_cycle += 1`.
5. 세션이 `COMPLETED`였다면 **`IN_PROGRESS`로 재오픈**한다 — 이후 `progress()` / `complete()` / `respond_to_review()` 호출이 그대로 가능.
6. `target_thread_id`가 있으면 session.thread_id로 채운다 (기존 thread 유지).
7. `format_review_intake_message()`로 thread 첫 회신 메시지 생성.

## 회신 (`WorkflowOrchestrator.respond_to_review`)

`respond_to_review(session_id, feedback_id=..., applied=..., proposed=..., remaining=..., references_used=...)` 동작:

1. session에서 해당 `feedback_id` 레코드 조회. 없으면 에러.
2. 적용/제안/남은 이슈/사용 레퍼런스를 모아 `format_review_reply_message()`로 회신 메시지 생성.
3. session.progress_notes에 `"review cycle N 회신: 적용 X건, 제안 Y건, 남음 Z건"` 한 줄 추가.
4. session.references_used에 새 레퍼런스 누적.

상태 전이는 일으키지 않는다 — 별도 `progress()`/`complete()` 흐름과 자유롭게 조합할 수 있다.

## 운영 메시지 양식

### 인테이크 (재분배 안내)

```
리뷰 피드백 수신 (cycle N)
- 세션: `<session_id>`
- 출처: <source> / 심각도: <severity>
- 작성자: <author>

요약
<summary>

본문
<body>

재분배
- 담당 역할: `<role>`
- 지원 역할: `<role>`, ...
- 라우팅 사유: ...
- 영향 파일: `<path>`, ...

레퍼런스 회수 필요
- 부족한 측면: <gaps>
- 추천 소스: <sources>
```

### 회신

```
리뷰 회신 (cycle N)
- 세션: `<session_id>`
- 담당: `<role>`
- 원 피드백: <summary>

적용한 수정
- ...

추가 제안
- ...

남은 이슈
- ...

참고한 레퍼런스
- <title> — <url>
```

## 건드리지 말아야 할 것

- 단일 executor 계약: supporting_roles는 의견용, write는 primary_role만.
- `WorkflowState` 다이어그램: `INTAKE → APPROVED → IN_PROGRESS → COMPLETED|REJECTED` 그대로 유지. 리뷰는 새 상태를 넣지 않고 `review_cycle`만 증가시킨다.
- Write 게이트: review-loop은 write_blocked_reason을 만지지 않는다. 재작업이 새로 write를 요구하면 `approve()` 흐름을 다시 타야 한다.
- Reference 자동 수집 금지 정책: 소스 이름만 추천, 실제 페치는 사용자/후속 마일스톤.

## Discord 진입점

`/engineer_review` 와 `/engineer_review_reply` 두 슬래시 명령으로 thread에서 직접 루프를 돌릴 수 있다.

### `/engineer_review`

| 파라미터 | 필수 | 설명 |
| --- | --- | --- |
| `session_id` | ✅ | 피드백을 연결할 워크플로 세션 ID |
| `summary` | ✅ | 한 줄 요약 (라우팅에 사용) |
| `body` | ⏵ | 피드백 본문 |
| `severity` | ⏵ | `blocking`/`high`/`medium`/`low`/`nit` (기본 medium) |
| `categories` | ⏵ | 쉼표로 구분 (예: `ui, copy`) |
| `source` | ⏵ | `github_pr_review`/`github_copilot`/`external_agent`/`user` (기본 user) |
| `file_paths` | ⏵ | 쉼표로 구분한 영향 파일 경로 |

실행 결과로 인테이크 메시지와 함께 자동 생성된 `feedback_id`(예: `fb-1a2b3c4d`)를 표시한다. 이 ID를 다음 회신에 사용한다.

### `/engineer_review_reply`

| 파라미터 | 필수 | 설명 |
| --- | --- | --- |
| `session_id` | ✅ | 회신 대상 세션 |
| `feedback_id` | ✅ | 인테이크 시 받은 ID |
| `applied` | ✅ | 적용한 수정 (개행 또는 `;` 분리, `-`/`•` 글머리 자동 제거) |
| `proposed` | ⏵ | 추가 제안 |
| `remaining` | ⏵ | 남은 이슈 |

회신 메시지가 thread에 게시되고 session.progress_notes에 회차 메모가 누적된다.

## Execution Review 와의 관계 (council runtime cross-link)

[`docs/engineering-role-council-runtime.md`](../../../../docs/engineering-role-council-runtime.md)
§6 이 새 lifecycle stage `execution_review` 를 정의한다. 본 review-loop
은 그 stage 의 **한 분기**로 위치한다.

```
execution_review
├── decision: accept_and_close          ← retrospective candidate 만 stamp
├── decision: accept_with_followups     ← 새 후속 task brief
├── decision: reopen_for_rework         ← 같은 council 새 round (round_index +1)
└── decision: reroute_to_review_loop    ← 본 문서의 record_review_feedback
```

규칙:

- review-loop 은 **PR 외부 origin** 도 받는다. CI failure / production
  incident / external auditor 의견 모두 `ReviewSource.EXTERNAL_AGENT` 로
  들어올 수 있다.
- `decision = reopen_for_rework` 의 경우 review-loop 을 거치지 않고
  `role_councils[role]` 에 새 `round_index` 의 결과를 누적한다. 단일
  executor 원칙은 그대로 — primary_role 한 명만 실행.
- 본 문서의 `record_review_feedback` 입력 stage 는 이제 `execution_review`
  substage `review_feedback_routed` 안에서 호출된다. `WorkflowState`
  자체는 그대로 — `review_cycle` 증가는 같음.

## 후속 마일스톤

- 피드백 자동 수집: GitHub PR review API / Copilot 코멘트 fetch → ReviewFeedback 변환기 (webhook 또는 polling).
- 자동 reference_collector 연동: reference_needed=True인 피드백은 실제 사이트에서 추천 카드 가져오기 (약관 검토 후).
- Multi-feedback batching: 같은 PR에 여러 코멘트가 동시에 들어올 때 묶어서 한 번에 라우팅.
- 라벨 사전 표준화: `categories`를 자유 라벨에서 정해진 사전으로 좁혀 라우팅 정확도 향상.
