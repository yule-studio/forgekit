# Engineering Agent — Live Regression Test Procedures

이 문서는 라이브 MVP 회귀 4 종을 사람이 직접 Discord 에서 검증할 때 따르는 체크리스트다. 자동화 테스트가 모두 green 이라도 사용자에게 보이는 신호 (typing / 메시지 본문 / status diagnostic) 가 정책과 일치하는지를 마지막에 한 번 더 봐야 하기 때문에 따로 둔다.

운영 원칙은 `policies/runtime/agents/engineering-agent/lifecycle-mvp.md` 에 있다. 이 문서는 그 정책을 통과시키는 4 개의 라이브 시나리오만 정의한다.

## 0. 공통 준비

1. 게이트웨이 봇 + 7 개 role 멤버 봇 모두 기동 — 봇 토큰 / Tavily / Brave 키가 `.env.local` 에 셋업 되어 있어야 함.
2. 새 워크플로우 채널을 사용하거나, 기존 채널이라면 직전 작업 세션이 close 되어 있어야 함 (`/status` → "현재 채널에 매칭되는 열린 engineering-agent 세션이 보이지 않아요" 가 깔끔하게 떠야 정상).
3. 본 문서의 시나리오 4 개를 **위에서 아래 순서대로** 실행. 시나리오 간 세션 충돌 방지 위해 각 시나리오 사이에 채널을 비우거나 명시적으로 새 thread 를 사용한다.

### 0.4 사전 차단 — Secret Hygiene 미완료 시 진행 금지

`docs/operations.md` §11 (P0 Secret Hygiene + Token Rotation) 이 완전히 끝나기 전까지 본 라이브 회귀 / M13 readiness 검증은 **시작하지 않는다.** 미완료 상태에서 라이브 봇을 다시 띄우면 이전 토큰이 또 다시 화면 / 로그 / Discord 메시지로 흘러갈 위험이 있다.

진행 전 다음 항목이 모두 ✅ 인지 한 번 본다.

- [ ] **노출 사실 파악** — 토큰이 어떤 표면(스크린샷 / 터미널 / 외부 채팅 / git diff)에 노출됐는지 구체적으로 식별. 추정이 아니라 사실 기반.
- [ ] **9 개 봇 모두 reset** — engineering gateway + 7 멤버 + planning bot 모두 Discord Developer Portal 에서 Reset Token 완료. 마지막 reset 시각이 노출 시각보다 늦어야 함.
- [ ] **`.env.local` 갱신** — 새 토큰이 `.env.local` 의 해당 env key 9 종에 모두 반영. 이전 값은 어디에도 남아 있지 않음(편집기 history / 클립보드 매니저 포함).
- [ ] **runtime restart 완료** — `docs/operations.md` §11.3 의 (A)/(B)/(C) 중 하나로 모든 봇 프로세스 재기동. 메모리에 이전 토큰이 남아 있지 않음.
- [ ] **위생 점검** — 화면 / 영상 / 외부 채팅 / 외부 LLM 컨텍스트 / journalctl / git history 어디에도 토큰이 남아 있지 않은지 §11.4 체크리스트로 한 번 더 본다.
- [ ] **incident note 기록** — 노출 시각 / rotate 시각 / 영향 범위를 별도 incident note 에 기록. (.env.local 과 동일 등급으로 보관, 공개 vault 금지.)

위 6 개 중 한 항목이라도 미충족이면 본 문서의 §1 ~ §6 시나리오 어떤 것도 시작하지 않는다. 자동화 테스트 (`python3 -m unittest discover -s tests -t .`) 는 secret 없이 동작하므로 그쪽은 계속 돌려도 무방하지만 **라이브 봇 기동은 차단**.

## 1. Kubernetes research-only 시나리오

### 1.1 입력

게이트웨이 봇이 보고 있는 운영 채널에 정확히 다음 prompt 를 보낸다.

```
오늘은 k8s 쿠버네티스에 대해서 다루고 싶어. 어떤 지식들이 필요할까? 오늘은 코드 수정 없이 자료 수집이 목표야.
```

### 1.2 검증 항목

- [ ] **role_selection 정확성** — 게이트웨이가 활성 role 을 `tech-lead + devops-engineer + backend-engineer` 만으로 잡았다고 응답한다. `ai-engineer` / `qa-engineer` / `frontend-engineer` / `product-designer` 는 활성 목록에 없어야 한다.
- [ ] **research-only 게이트** — 게이트웨이가 `**[engineering-agent] 조사 단계 — 코드 수정은 하지 않습니다**` 헤더로 시작하는 메시지를 보낸다. 본문에 "조사 중심 역할:" 이 있고, "executor:" 라벨은 **없어야** 한다. 마지막 안내 라인에 `수정 권한 제안` 이라는 문구로 다음 단계 안내가 있어야 한다.
- [ ] **typing 신호 정확성** — 봇이 무시할 메시지에는 typing 이 뜨지 않고, research_loop 가 도는 동안에는 typing 이 끊기지 않고 유지된다 (~10s 마다 끊겼다 다시 뜨는 현상 X).
- [ ] **forum 댓글 본문** — 운영-리서치 thread 에 active role 봇 (`devops-engineer`, `backend-engineer`) 이 자기 take 를 올린다. 각 댓글에 "조사 결과: N건 (provider: tavily|brave)" 형태의 라인이 있고, 그 아래에 "핵심: …" 으로 시작하는 1~3 개의 finding preview 가 보여야 한다. `qa-engineer` / `ai-engineer` / `frontend-engineer` 봇은 이 thread 에 댓글을 남기지 **않아야** 한다.
- [ ] **session.extra 마킹** — 사용자가 `/status` 또는 "지금 어떻게 진행 중?" 으로 다시 물으면 응답에 `lifecycle_mode: research_only` 흔적이 노출되거나 (a) "조사 중심 역할" / (b) Phase 6 의 "역할 연구 결과" 블록 (devops-engineer + backend-engineer 의 provider / source_count) 이 함께 보여야 한다.

### 1.3 자동화 테스트 cross-reference

- `tests/engineering/test_role_selection_infra.py` — Phase 3, role_selection 결과 정확성 (k8s + 한글 synonyms + 코드 수정 없이 + 자료 수집)
- `tests/engineering/test_research_only_executor_hide.py` — Phase 2, research-only 표시 + executor 숨김
- `tests/discord/test_typing_phase1_accuracy.py` — Phase 1, typing 신호 정확성 + heartbeat
- `tests/engineering/test_role_research_observability.py` — Phase 4, 역할별 record + activity log
- `tests/engineering/test_work_report_role_research_gate.py` — Phase 6, role_research_results 가 work_report 게이트에 반영됨

## 2. AI/RAG memory 시나리오 (explicit roles)

### 2.1 입력

```
RAG/CAG memory 구조를 조사해줘. tech-lead / ai-engineer / backend-engineer / qa-engineer 관점으로 토의해줘.
```

### 2.2 검증 항목

- [ ] **explicit role 우선** — 활성 role 이 정확히 `tech-lead + ai-engineer + backend-engineer + qa-engineer` 4 종. `devops-engineer` / `frontend-engineer` / `product-designer` 는 active 에 없고, `excluded_research_roles` 에 명시적으로 들어 있어야 한다.
- [ ] **research-only 게이트** — "조사해줘" 가 포함되어 research-only 모드로 분기된다. coding executor 가 표시되지 **않아야** 한다.
- [ ] **forum thread 댓글 순서** — `tech-lead` 가 chain opener 로 먼저 글을 쓰고, 그 뒤에 ai-engineer / backend-engineer / qa-engineer 가 자기 댓글을 남긴다. 각 댓글에 "조사 결과: N건" + 핵심 라인이 함께 보여야 한다.
- [ ] **work_report 게이트 (Phase 6)** — 모든 role 의 collection 이 status="ok" 면 work_report 가 `ready` 상태로 빌드되어 사용자 채널에 표시된다. 하나라도 status="failed" / status="empty" 면 work_report 헤더가 `interim` 으로 빌드되고 본문에 "역할 연구 결과 부족 — {role} 가 자료를 모으지 못해 final 보고서로 제출할 수 없습니다." 메시지가 노출된다.

### 2.3 자동화 테스트 cross-reference

- `tests/engineering/test_role_selection_infra.py::RagMemoryTests`
- `tests/engineering/test_research_only_executor_hide.py::ResearchOnlyTriggerPhraseTests::test_jorae_keyword_triggers_research_only`
- `tests/engineering/test_work_report_role_research_gate.py`

## 3. 진행 상태 진단 시나리오

### 3.1 입력

위 시나리오 1 또는 2 가 끝난 직후 (또는 일정 turn 이상 흐른 다음) 게이트웨이 채널에 다음을 보낸다.

```
지금 누가 어디까지 했어?
```

(다른 자연어도 가능: "현재 상태 알려줘", "진행 상황 어떻게 되고 있어?", "왜 멈췄어?")

### 3.2 검증 항목

- [ ] **status intent 분류** — 게이트웨이가 새 작업 intake 로 받지 않는다. "현재 채널에 매칭되는 열린 engineering-agent 세션…" 또는 "session abc123… 진행 상황" 처럼 status 응답이 떠야 한다.
- [ ] **활성 role 목록** — 응답에 `- 활성 role: …` 라인이 있고, 시나리오 1 이라면 `devops-engineer, backend-engineer` 가, 시나리오 2 라면 `ai-engineer, backend-engineer, qa-engineer` 가 보인다.
- [ ] **역할 활동 기록 (Phase B)** — `- 역할 활동 기록:` 블록이 있고, 발화한 role 마다 `posted (open, 2026-…)` 또는 `error (turn, …)` 라인이 노출.
- [ ] **역할 연구 결과 (Phase 5)** — `- 역할 연구 결과:` 블록이 있고, 각 role 라인에 `provider: tavily|brave` + `N건` + (있으면) `핵심: …` 미리보기. 실패한 role 은 `failed` + error 메시지 같이 노출.
- [ ] **활동 로그 요약 (Phase 5)** — `- 활동 로그: research_completed=N, research_started=N, …` 한 줄과 `· 마지막 이벤트: …` 한 줄이 보인다. 이전에 실패가 있었다면 `· 마지막 실패: …` 라인도 추가로 노출.
- [ ] **research_loop 보고 / synthesis** — 마지막 보고 메시지 1 줄, tech-lead synthesis 가 기록되었는지 여부 (`기록됨` / `아직 기록되지 않음`).

### 3.3 자동화 테스트 cross-reference

- `tests/discord/test_status_diagnostic.py`
- `tests/discord/test_status_diagnostic_role_research.py` — Phase 5 의 두 신규 블록 (역할 연구 결과 / 활동 로그)

## 4. Obsidian write 시나리오

### 4.1 입력

위 시나리오 1 또는 2 의 work_report 가 `ready` / `final` 상태로 빌드된 직후, 게이트웨이 채널에 정확히 다음 phrase 중 하나를 보낸다.

```
이 결과 옵시디언에 저장해줘
```

또는 `옵시디언 적재` / `옵시디언 기록` / 정책에 정의된 다른 explicit phrase 를 보낸다.

### 4.2 검증 항목

- [ ] **Obsidian gate (lifecycle_status.can_write_obsidian_record)** — 시나리오 1/2 가 정상 종료된 경우 (active role 모두 status=ok 이고 synthesis + work_report.status=ready 보유) 게이트웨이가 Obsidian write 를 시작한다.
- [ ] **work_report.status 강제** — work_report 가 `interim` / `insufficient` 인 상태에서 위 phrase 가 들어오면 게이트웨이가 "research_pack 미수집 …" / "역할 토의 미완료 …" / "역할 연구 결과 부족 — {role}" 같은 정확한 reject 사유를 응답한다.
- [ ] **vault 경로 확인** — write 가 성공했다면 `obsidian-vault/...` 경로 (정책에 정의된 폴더) 에 새 markdown 노트가 생기고, 게이트웨이가 그 경로를 응답에 함께 노출.
- [ ] **재실행 멱등성** — 같은 phrase 를 한 번 더 보내도 동일 노트를 두 번 만들지 않는다. (이미 저장됨 / 같은 path 에 갱신, 정책에 따라 한 가지 거동.)

### 4.3 자동화 테스트 cross-reference

- `tests/engineering/test_work_report_lifecycle.py`
- `tests/engineering/test_work_report_role_research_gate.py::CanGenerateFinalReasonTests` — Phase 6 의 새 reject 사유

## 5. 리포트 양식

라이브 회귀 4 시나리오를 한 번에 돌고 나면 아래 형식으로 결과를 기록한다.

```
- 시나리오 1 (k8s research-only) : ✅ / ❌  — 메모
- 시나리오 2 (RAG/CAG memory) : ✅ / ❌  — 메모
- 시나리오 3 (status diagnostic) : ✅ / ❌  — 메모
- 시나리오 4 (Obsidian write) : ✅ / ❌  — 메모
- 시나리오 5 (M7.5 forum 토의 + role change + Obsidian handoff) : ✅ / ❌  — 메모
- 환경: branch=…, commit=…, 봇 기동시각=…
- 발견된 회귀: …
```

발견된 회귀는 자동화 테스트로 옮겨서 `tests/engineering/` 또는 `tests/discord/` 에 pin 하고, 본 문서에 새 시나리오 또는 검증 항목을 추가한다.

## 6. M7.5 forum 토의 + role change + Obsidian handoff (A-M7.5d 추가)

A-M7.5 / A-M7.5b 가 닫은 세 가지 운영 흐름의 라이브 회귀를 한 시나리오로 묶는다 — (a) `#운영-리서치` thread 안의 토의, (b) 사용자 주도 역할 변경, (c) thread 안 Obsidian 저장 요청 → `#승인-대기` 카드 → 사용자 승인 → vault write.

자율 검증 결과 (production code path against real SQLite + WorkflowSession + ApprovalWorker + ObsidianWriterWorker stack, Discord 트랜스포트만 stub) 는 A-M7.5c 에서 7/7 통과. 이 시나리오는 그 결과를 실제 Discord 표면에서 한 번 더 확인하는 절차다.

### 6.0 사전 체크리스트

라이브 검증 시작 전 다음 항목이 모두 ✅ 인지 한 번 본다 (한 항목이라도 미충족이면 시나리오를 시작하지 말 것).

- [ ] **승인 채널 env 활성** — `.env.local` 의 `DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID` (또는 `_NAME` + `DISCORD_GUILD_ID`) uncomment + 채널 ID 가 실제 길드의 `#승인-대기` 채널을 가리킴.
- [ ] **gateway token 활성** — `ENGINEERING_AGENT_BOT_GATEWAY_TOKEN` 또는 단일 봇 모드면 `DISCORD_BOT_TOKEN`. resolve_discord_bot_token 의 fallback 우선순위 적용.
- [ ] **봇 권한** — engineering gateway 가 `#승인-대기` 채널에 `Send Messages` 권한 보유. forum thread 안에서 `Send Messages in Threads` 권한 보유.
- [ ] **운영-리서치 forum 활성** — `DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_ID` (또는 `_NAME`) 설정. thread 가 만들어졌고 `session.extra['research_forum_thread_id']` 에 thread id 가 persist 되어 있어야 한다 (시나리오 1 또는 2 를 먼저 돌려서 자동 생성 권장).
- [ ] **vault dry-run 권장** — 진짜 운영 vault 가 부담되면 `OBSIDIAN_VAULT_PATH` 를 잠시 임시 디렉토리로 바꿔 둔다. 시나리오 종료 후 원복.
- [ ] **승인 전 vault write 금지 확인** — `ObsidianWriterWorker` 의 approval guard (M5b) 가 켜져 있어 `note_kind=knowledge` 또는 `overwrite=True` 면 `approval_id` / `approved_by` / `approved_at` 없이는 `failed_retryable` 로 떨어진다. 사용자가 명시 승인하지 않으면 절대 vault 에 들어가지 않는다.
- [ ] **자동화 테스트 baseline** — `python3 -m unittest discover -s tests -t .` 모두 green. 직전 commit 해시 기록.

### 6.1 시나리오 — 단계별 입력 + 기대 결과

`#업무-접수` intake 부터 vault write 까지의 전체 흐름을 한 번에 검증한다. 단계 사이에 5–10초 정도 간격을 두면 Discord rate-limit / 큐 처리 타이밍을 자연스럽게 흡수한다.

| 순 | 사용자 액션 | 기대 결과 | 검증 명령 / 관찰 지점 |
|---|---|---|---|
| 0 | `#업무-접수` 채널에 intake prompt 입력 (예: "DevOps 엔지니어가 되려면 어떻게 공부해야 할까") | 게이트웨이가 작업 thread 생성 + 운영-리서치 forum thread 생성 + kickoff 메시지에 `참여 역할 / 대기 역할 / 추가 안내 / 다음 단계` routing summary 3–5 줄 포함 | `yule engineer show <session_id>` 로 `extra.research_forum_thread_id` / `extra.active_research_roles` 둘 다 채워졌는지 확인 |
| 1 | 운영-리서치 thread 자체가 만들어졌는지 채널에서 직접 확인 | `#운영-리서치` 에 새 forum thread 표시. starter 본문에 자료 + 핵심 라인 | thread 클릭 → starter 본문 확인. 시나리오 1/2 의 §1.2 / §2.2 항목과 일치 |
| 2 | 활성 역할만 thread 댓글을 남기는지 관찰 (개입 없음) | excluded 역할 봇은 thread 안에서 발화 X | thread 댓글 작성자 확인. `qa-engineer` / `frontend-engineer` 등 비활성 역할이 댓글을 남기면 실패 |
| 3 | thread 안에 "QA도 참여시켜" 입력 | "✅ 다음 turn 부터 qa-engineer 도 함께 참여하도록 했어요…" 친절 응답이 thread 에 게시 | `yule engineer show <session_id>` 로 `extra.active_research_roles` 에 `qa-engineer` 추가 / `extra.role_changes` audit 한 건 추가 확인 |
| 4 | thread 안에 "Obsidian에 정리하고 싶어" 입력 | "📨 Obsidian 저장 요청을 받았어요. `#승인-대기` 채널에 승인 카드를 게시했어요 (job=`…`)." 응답 + `#승인-대기` 채널에 카드 등장 | `yule runtime status` → `approval_post` job_type 의 saved 카운트 +1. `#승인-대기` 채널에서 카드 본문에 thread 제목 / source thread / decision_id 가 보여야 한다 |
| 5 | 같은 thread 에서 같은 phrase 한 번 더 입력 | "⏳ 이 thread 의 동일 저장 요청이 이미 `#승인-대기` 큐에 들어가 있어요." 응답. 새 카드 만들어지지 **않음** | `#승인-대기` 채널에 새 메시지 추가 X. queue 의 `approval_post` 카운트 변화 X |
| 6 | `#승인-대기` 카드에 "이대로 저장" 또는 "승인" 답신 | "✅ 승인 받았어요. Obsidian 저장 큐에 넣었습니다 (job=`…`)." 응답. queue 에 `obsidian_write` 행 추가 | `yule runtime status` → `obsidian_write` job_type 의 queued/saved 카운트 +1 |
| 7 | obsidian-writer 가 vault write 완료 (자동) — `yule run-service eng-obsidian-writer` 또는 `yule runtime up` 으로 띄워진 워커가 자연스럽게 처리 | vault 디렉토리에 새 markdown 파일 생성. `obsidian_write` 행 state=saved | `ls $OBSIDIAN_VAULT_PATH` 또는 vault 동기화 도구. 파일 내부에 `approval_id` / `approved_by` / `approved_at` 메타데이터 또는 frontmatter 확인 |
| 8 | `/engineer_show <session_id>` 또는 supervisor 응답으로 `session.extra` 검사 | `extra.obsidian_writes` 에 항목 추가 + `extra.fallback_audits` 에 변경 없음 (정상 흐름이라 fallback 없음) | `yule engineer show <session_id> --json` |

### 6.2 검증 항목 (체크리스트)

- [ ] **routing summary 게시** — kickoff 본문에 `참여 역할 / 대기 역할 / 추가 안내 / 다음 단계` 4 라인이 포함된다. "전원이 참여합니다" 는 사용자가 명시적으로 "전체 팀" 류 표현을 쓴 경우에만 등장 (`SOURCE_USER_ALL_TEAM`).
- [ ] **excluded 역할 침묵** — thread 안에서 비활성 역할 봇은 발화 X. team-turn marker 가 임의 포함되어도 `_route_engineering_approval_reply` 이전 단계에서 `build_turn_plan` 이 필터링.
- [ ] **role-change 친절 응답** — "QA도 참여시켜" / "백엔드도 불러줘" / "프론트도 같이 봐줘" / "전체 팀 관점으로 봐줘" 전부 친절 응답 + `active_research_roles` 갱신 + audit 한 건. 잘못된 역할명은 `RESPONSE_ROLE_NO_CHANGE` 로 떨어진다.
- [ ] **forum → approval 핸드오프** — "Obsidian 에 정리하고 싶어" / "이거 저장해줘" / "옵시디언에 정리해줘" 류 phrase 를 `is_obsidian_save_request` 가 인식. context 부족 (세션 없음 / 채널 unset) 이면 silent fail 금지, 친절 안내 게시.
- [ ] **idempotency** — 같은 forum 메시지 (`message.id` 동일) 로는 절대 두 번째 카드 안 나옴. SAVED 행도 dedup 차단 (`source_message_id` 기반).
- [ ] **승인 전 vault write 금지** — 6 단계 (사용자 승인) 가 완료되기 전 vault 디렉토리에 새 파일이 만들어지면 회귀. ObsidianWriterWorker.process_job 의 approval guard 가 동작하는지 확인.
- [ ] **승인 후 vault write 발생** — 7 단계에서 obsidian-writer 워커가 동작했을 때만 vault 에 파일 등장.
- [ ] **토큰 비노출** — 라이브 흐름 중 supervisor / `journalctl` 출력 / `#승인-대기` 카드 본문 / forum thread 응답 어디에도 `Bot ` 토큰 prefix 또는 토큰 hex prefix 가 노출되지 않는다.
- [ ] **fallback/degrade 정합성** — 시나리오 진행 중 의도치 않게 fallback / degrade 가 발생하지 않아야 한다 (`session.extra['fallback_audits']` 에 신규 entry X). 발생한다면 자동화 테스트로 옮겨 회귀 처리.

### 6.3 자동화 테스트 cross-reference

- `tests/engineering/test_role_selection_m75.py` — 8-prompt matrix + effective-roles helper + role-change parser + routing summary
- `tests/discord/test_team_turn_active_gate.py` — team-turn legacy path active-roles 게이트 5 종
- `tests/job_queue/test_forum_obsidian_handoff.py` — forum 저장 요청 → ApprovalRequest producer 9 종 (idempotency + 토큰 비노출 + 종단간 approval reply → obsidian_write)
- `tests/discord/test_forum_message_adapter.py` — bot.py on_message → 어댑터 wiring 9 종 (Obsidian save / role change / 비-forum 통과 / kickoff routing summary)
- `tests/runtime/test_synthesis_fallback_wiring.py` — degrade/fallback 트리거가 active_research_roles 만 기준으로 판단하는지 회귀

### 6.4 발견된 회귀 처리

라이브에서 회귀가 잡히면:

1. 가능하면 unit test 로 재현 → `tests/engineering/test_role_selection_m75.py` / `tests/discord/test_forum_message_adapter.py` / `tests/job_queue/test_forum_obsidian_handoff.py` 중 한 곳에 pin.
2. 본 문서 §6.2 체크리스트에 새 항목 추가.
3. 별도 fix 커밋 (M7.5 이후 milestone) 으로 정리. 라이브 회귀 보고에 commit 해시 기록.
