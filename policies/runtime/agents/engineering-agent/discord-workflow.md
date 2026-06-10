# Engineering Agent Discord Workflow (v0)

이 문서는 Discord 안에서 engineering-agent에게 작업을 위임하는 **접수(intake) → 승인(approve) → 진행(progress) → 완료(complete)** 흐름의 정책 기준선이다. 코드 진실 소스는 `apps/engineering-agent/src/yule_engineering/agents/workflow.py` + `workflow_state.py`, CLI는 `yule engineer`, Discord 슬래시 커맨드는 `commands.py`의 `engineer_intake` / `engineer_approve` / `engineer_reject` / `engineer_progress` / `engineer_complete` / `engineer_show` / `engineer_review`(+ `engineer_review_reply`).

## 1. 접수 채널 규칙

- **engineering 전용 `#업무-접수` 채널**에서 자유 발화 또는 `/engineer_intake` 슬래시 커맨드로 접수한다. planning conversation은 별도 채널(`#일정-관리` / 기존 CONVERSATION)에서 동작한다 — 두 흐름은 채널 단위로 분리한다.
- DAILY 채널은 broadcast 전용으로 잠겨 있다 (DAILY 채널 정책과 동일).
- 슬래시 커맨드 `/engineer_intake` 인자:
  - `prompt`: 자연어 작업 요청 (필수).
  - `task_type`: 명시 분류 (선택, 생략 시 키워드 분류).
  - `write_requested`: 코드/문서 쓰기 요청 여부.
- 부서 게이트웨이 봇만 접수한다. 멤버 봇은 dispatch marker가 있는 작업/research thread 안에서만 역할별 발화를 이어간다.
- 접수가 확정되면 게이트웨이는 작업 thread를 만들고 `WorkflowSession.thread_id`에 저장한다. 사용자가 "새로 등록하지 말고", "기존/열려 있는 thread에서 이어가"처럼 명시하면 새 intake를 만들기 전에 같은 사용자/채널의 열린 세션 thread를 먼저 찾고, 찾으면 그 thread에 이어 붙인다. 열린 thread를 찾지 못하면 새 세션을 만들지 않고 사용자에게 재지시를 요청한다.

### 1.1 채널 환경변수 매트릭스

운영자가 어느 키가 실제 런타임에 영향을 주고 어느 키가 후속 자동화용 예약인지 헷갈리지 않게 한곳에 정리한다.

| 채널 | env 키 | 런타임 사용 | 용도 |
|---|---|---|---|
| `#업무-접수` | `DISCORD_ENGINEERING_INTAKE_CHANNEL_ID`, `DISCORD_ENGINEERING_INTAKE_CHANNEL_NAME` | **활성** — `engineering_channel_router.EngineeringRouteContext.from_env()`가 직접 읽는다 | 자유 대화 + 작업 접수. ID/NAME 중 하나만 매치돼도 라우팅된다. 둘 다 비어 있으면 라우터의 `configured`가 False로 떨어져 engineering 경로 자체가 비활성된다. |
| `#승인-대기` | `DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID`, `DISCORD_ENGINEERING_APPROVAL_CHANNEL_NAME` | **예약** — 런타임 미연결 | write 승인 UX 자동화(예: 접수 메시지 미러링, ✅ 반응 승인)에서 사용 예정. |
| `#봇-상태` | `DISCORD_ENGINEERING_STATUS_CHANNEL_ID`, `DISCORD_ENGINEERING_STATUS_CHANNEL_NAME` | **예약** — 런타임 미연결 | 헬스체크/오류 알림/봇 가동 상태 broadcast 예정. |
| `#실험실` | `DISCORD_ENGINEERING_LAB_CHANNEL_ID`, `DISCORD_ENGINEERING_LAB_CHANNEL_NAME` | **예약** — 런타임 미연결 | 신규 워크플로/프롬프트 실험용 sandbox. |
| `#운영-리서치` (Forum) | `DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_ID`, `DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_NAME` | **활성** — `ResearchForumContext.from_env()`가 직접 읽는다 | 부서 공통 research/deliberation inbox. gateway 자료 seed 게시 → `[research-open:<session_id>]` 공개 호출 → 멤버 봇별 독립 조사/검토. 운영 규약은 `research-forum.md` 참조. |

규약
- intake 채널 키는 ID/NAME 둘 다 같은 채널을 가리키는 게 권장이다. 한쪽만 채워도 라우팅은 동작하지만, 채널 ID가 바뀌었을 때 NAME fallback이 있으면 무중단 복구가 쉽다.
- 예약 슬롯 키는 비워둬도 정상이다. 후속 마일스톤에서 키를 활성화하기 전까지 어떤 코드 경로도 이 키를 읽지 않는다.
- 운영-리서치 forum은 engineering 전용이 아니라 부서 공통이므로 키 prefix가 `DISCORD_ENGINEERING_*`이 아니라 `DISCORD_AGENT_RESEARCH_*`다.
- planning 흐름의 `DISCORD_DAILY_CHANNEL_*` / `DISCORD_CHECKPOINT_CHANNEL_*` / `DISCORD_CONVERSATION_CHANNEL_*` 와는 키가 분리되어 있어 한쪽 변경이 다른 쪽 동작을 흔들지 않는다.

## 2. 승인 게이트

- `write_requested=True`로 접수된 세션은 `intake → approved` 전이 전까지 **어떤 write도 시작하지 않는다**.
- 승인 방법:
  - **CLI**: `yule engineer approve --session <id>`.
  - **Discord 슬래시 커맨드**: `/engineer_approve session_id:<id>`. 승인이 끝나면 운영자에게 `**[engineering-agent] 세션 승인 완료**` 메시지로 다음 단계 안내(`/engineer_progress`, `/engineer_complete`)를 노출한다.
  - 접수 메시지의 ✅ 반응 기반 승인은 후속 이슈에서 추가한다 (multi-bot 활성화 시).
- 거절은 `yule engineer reject --session <id> --reason "..."` 또는 `/engineer_reject session_id:<id> reason:"..."`로 처리한다. Discord 응답에는 사유가 그대로 게시되고 "재개할 수 없습니다" 안내가 포함된다. 거절된 세션은 progress/complete/approve가 모두 차단된다.
- write 게이트는 dispatcher의 `write_block_reason`을 그대로 받아 표시한다 (dispatcher.md §6).

## 3. 메시지 포맷

### 3.1 접수 메시지 (`format_intake_message`)
표준 섹션:
1. 헤더: `**[engineering-agent] 새 작업 접수**`, 세션 ID, 분류, 참여 후보, 실행 후보, 검토 후보.
2. **참고 레퍼런스 (제안)**:
   - 사용자 제공 (1순위): prompt 안의 URL을 추출해 노출 (`extract_urls`).
   - task_type 추천 카테고리: dispatcher가 매핑한 소스 이름.
   - 둘 다 없으면 "이 task_type에는 시각 reference를 강제하지 않습니다" 명시.
3. **승인 필요** 섹션: write 게이트 차단 시 사유 + 승인 명령 안내.

### 3.2 진행 메시지 (`format_progress_message`)
- 상태, 실행 후보, 최근 메모 5건 (FIFO 누적).
- 메모는 짧게(한 줄). 자세한 내용은 thread 또는 PR 링크로 풀어쓴다.

### 3.3 완료 메시지 (`format_completion_message`)
- 분류, 실행 후보, **요약**, **반영한 레퍼런스**.
- 반영한 레퍼런스는 `{title, source, url, rationale}` 키 4종을 가진 객체 배열. rationale은 "차용한 패턴 + 어떻게 재구성했는지" 한 줄.
- 제안된 reference가 있었지만 실제 인용이 없으면 `- (없음 — 본 작업은 reference를 직접 인용하지 않았습니다)`로 명시 (감추지 않는다).

## 4. Reference 정책

### 4.1 우선순위
1. **사용자 제공 링크/스크린샷** — prompt 안의 URL을 자동 추출. 슬래시 커맨드 인자에 없어도 prompt 본문에 붙어 있으면 1순위로 인식.
2. **task_type 추천 카테고리** — dispatcher의 `reference_sources` (landing-page / onboarding-flow / visual-polish / email-campaign 4종).
3. (이번 단계는 자동 fetch 없음. 후속 마일스톤에서 `REFERENCE_*` env 슬롯이 채워졌을 때만 동작.)

### 4.2 산출 양식
완료 보고에 반영한 레퍼런스는 `reference-pack.md`의 4가지 명문화 항목 중 적어도 `차용 패턴(rationale)`을 포함해야 한다. 단순 복제는 금지(공통 정책).

### 4.3 자동 수집 민감 소스
- Notefolio, Behance, Mobbin, Page Flows, Awwwards 등은 약관상 자동 수집 금지. 사용자 제공 링크 또는 운영자 수동 참고로만 사용한다.
- env-strategy.md §7 슬롯이 채워지더라도 위 소스는 fetcher 대상에서 제외된다.

## 5. 상태 모델 (`WorkflowState`)

```
intake ──approve──▶ approved ──progress──▶ in_progress ──complete──▶ completed
   │                    │                       │
   │                    └─reject─▶ rejected     └─reject─▶ rejected (open notes 보존)
   └─reject─▶ rejected
```

규약
- `progress` / `complete`는 `intake` 상태에서 직접 호출 불가 — 승인 단계를 강제한다.
- `completed` / `rejected`는 종료 상태. 재접수가 필요하면 새 세션을 만든다.
- 세션은 SQLite JSON 캐시(`engineering-agent-workflow` 네임스페이스)에 저장되며 30일 보존.

## 6. CLI 사용 예 (운영자 e2e 검증)

```bash
# 접수
yule engineer intake \
  --prompt "우리 새 랜딩페이지 hero 섹션 다시 짜야 해 — https://stripe.com/pricing" \
  --write
# stderr에 session_id=<sid>가 출력됨

yule engineer approve --session <sid>
yule engineer progress --session <sid> --note "designer 시안 1차 정리"
yule engineer complete --session <sid> \
  --summary "hero 카피 + CTA 색 정리, 모바일 반응형 보정" \
  --references-used /tmp/refs.json
yule engineer show --session <sid>     # JSON 상태
```

`refs.json` 예:
```json
[
  {"title": "Stripe Pricing", "source": "Mobbin", "url": "https://example.com/stripe", "rationale": "step copy + CTA 강조 패턴 차용"},
  {"title": "Wix Templates 6선", "source": "Wix Templates", "rationale": "히어로 grid 분해 참고"}
]
```

## 7. Discord 사용 (Phase 2 토큰 활성 후)

```
/engineer_intake prompt:"우리 랜딩 hero..." write_requested:true
/engineer_approve session_id:<sid>
/engineer_progress session_id:<sid> note:"디자이너 1차 시안 정리"
/engineer_complete session_id:<sid> summary:"hero 카피 + CTA 색 정리"
/engineer_reject  session_id:<sid> reason:"요구사항 불명확"
/engineer_show    session_id:<sid>
```

각 슬래시 커맨드는 `workflow.py` 의 동일 메서드를 그대로 호출하며, 잘못된 상태 전이(예: intake 상태에서 progress, completed 세션 재승인, 빈 사유로 거절)를 만나면 한국어 한 줄 에러 메시지(`engineer X 실패: <원문>`)로 graceful 하게 답한다.

> ⚠️ Discord 슬래시 커맨드에서는 `complete` 의 `references_used` 인라인 입력을 받지 않는다. 레퍼런스 인용을 같이 남기고 싶다면 같은 세션을 CLI(`yule engineer complete --references-used <json>`)로 마무리한다. Discord 와 CLI 는 같은 SQLite 세션을 공유하므로 어느 쪽에서 닫아도 결과가 동일하다.

reaction-기반 승인은 후속 이슈에서 추가한다. thread 자동 생성과 기존 thread continuation은 자유 대화 라우터에서 활성화되어 있다.

## 8. 후속 작업

1. **승인 reaction 핸들러** — 접수 메시지의 ✅ 반응으로 승인, ❌로 거절.
2. **Thread continuation UX 강화** — 열린 thread 후보가 여러 개일 때 선택지를 보여주는 복구 UI.
3. **멤버 봇 실행 루프 연결** — approve된 세션이 각 멤버 봇의 작업 큐에 흘러들어가 GitHub issue/branch/PR 단위 실행으로 이어진다. write 게이트는 실행 시점에서 한 번 더 검사.
4. **Reference fetcher** — `REFERENCE_*` env 슬롯이 채워졌을 때만 동작. fetch 결과를 references_suggested에 채운다.
5. **PR 본문 자동 생성** — 완료 메시지 포맷을 그대로 PR description으로 옮기는 헬퍼 (reference-pack.md §산출 양식과 일치).
