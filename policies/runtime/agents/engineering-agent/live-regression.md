# Engineering Agent — Live Regression Test Procedures

이 문서는 라이브 MVP 회귀 4 종을 사람이 직접 Discord 에서 검증할 때 따르는 체크리스트다. 자동화 테스트가 모두 green 이라도 사용자에게 보이는 신호 (typing / 메시지 본문 / status diagnostic) 가 정책과 일치하는지를 마지막에 한 번 더 봐야 하기 때문에 따로 둔다.

운영 원칙은 `policies/runtime/agents/engineering-agent/lifecycle-mvp.md` 에 있다. 이 문서는 그 정책을 통과시키는 4 개의 라이브 시나리오만 정의한다.

## 0. 공통 준비

1. 게이트웨이 봇 + 7 개 role 멤버 봇 모두 기동 — 봇 토큰 / Tavily / Brave 키가 `.env.local` 에 셋업 되어 있어야 함.
2. 새 워크플로우 채널을 사용하거나, 기존 채널이라면 직전 작업 세션이 close 되어 있어야 함 (`/status` → "현재 채널에 매칭되는 열린 engineering-agent 세션이 보이지 않아요" 가 깔끔하게 떠야 정상).
3. 본 문서의 시나리오 4 개를 **위에서 아래 순서대로** 실행. 시나리오 간 세션 충돌 방지 위해 각 시나리오 사이에 채널을 비우거나 명시적으로 새 thread 를 사용한다.

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
- 환경: branch=…, commit=…, 봇 기동시각=…
- 발견된 회귀: …
```

발견된 회귀는 자동화 테스트로 옮겨서 `tests/engineering/` 또는 `tests/discord/` 에 pin 하고, 본 문서에 새 시나리오 또는 검증 항목을 추가한다.
