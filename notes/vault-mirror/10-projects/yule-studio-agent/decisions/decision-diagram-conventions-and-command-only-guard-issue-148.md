---
date: 2026-05-14
issue: 148
kind: decision
status: decided
related_issue: 148
related_pr: pending
---

# Issue #148 — Diagram Conventions + Command-Only Research Thread Guard

## 결정

1. **다이어그램은 Mermaid 가 SSoT** — README / docs / Obsidian mirror 모두 Mermaid 코드 블록. PNG / 손그림 / 외부 도구 출력은 *참고용 mirror* 만.
2. **command-only 운영 문장 (`진행 해줘` / `이대로 진행` / `작업 승인 할게 진행 해줘` 등) 은 4 critical site 에서 차단:**
   - bot.py `_record_engineering_continuation` — canonical prompt 저장 금지.
   - engineering_channel_router `_run_research_loop_hook` 호출 3 사이트 — research loop 호출 금지.
   - research_forum `derive_research_topic` — thread title 후보 거부.
   - engineering_conversation `CONFIRM_INTAKE` 분기 — bare command-only 시 APPROVAL_ACTION ack 로 downgrade.
3. **APPROVAL_ACTION 신규 intent** — P0-J 의 READ_ONLY_INTENTS 에 포함, auto_collect / 새 intake 절대 안 만듦.
4. **resumed_thread_id session resolve** — `forum_message_adapter._resolve_session_for_forum_thread` 가 secondary lookup 추가.

## 왜 / 고민 / 대안

### 왜 Mermaid?

- GitHub markdown / Obsidian / 본 레포 docs 모두 자동 렌더링.
- 텍스트 → git diff 가능 → 도식 변경도 PR review 가 의미 있음.
- 사용자 보고 이미지 (`Argo CD → Kubernetes → Istio / Internal / Data Layer`) 와 같은 *플로우 차트 양식* 을 코드로 표현.

### 왜 4 critical site 모두 가드?

- 한 site 만 가드해서는 회귀가 다른 site 로 우회. continuation prompt 저장만 막아도 derive_research_topic 이 legacy data 로 thread 생성. derive_research_topic 만 막아도 research loop 가 canned 결과 반환.
- 4 site 모두 *defense in depth* — 어느 하나가 회귀해도 나머지가 잡음.

### 왜 APPROVAL_ACTION 신규 intent?

- CONFIRM_INTAKE 와 의미가 달라 — CONFIRM_INTAKE 는 *직전 제안* 을 intake 로 promote. APPROVAL_ACTION 은 *기존 세션 ack* 만.
- P0-J 의 READ_ONLY_INTENTS tuple 에 자동 편입 → P0-J 의 hard rule (auto_collect 차단) 이 그대로 적용.

### 대안과 기각

- **Mermaid 외 다이어그램 도구 (PlantUML / Excalidraw / Figma)** — 외부 도구 의존 + diff 불가 + Obsidian 호환 부족.
- **command-only 가드를 detect_engineering_intent 한 곳에만** — 기존 26개 test 가 CONFIRM_INTAKE 기대했고 단일 site 가드는 legacy 동작과 충돌. CONFIRM_INTAKE 분기 안 가드가 더 안전.
- **resumed_thread_id 미연결** — bonus item E. 1순위는 thread 폭증 방지였지만, secondary lookup 은 5줄 추가로 끝나 함께 처리.

## 회고 / 다음엔 이렇게 더 나아질 수 있음

- continuation_requests history 에 `is_command_only=True` 마킹 추가 — 향후 status surface 에서 "지금까지 사용자가 N 번 승인/진행" 같은 audit 가능.
- diagram-conventions §5 의 Obsidian mirror 자동화 — 현재는 본 노트처럼 수동. P0-G stage 3 의 vault repo workspace 정착 시 자동 sync 후보.

## 관련 문서

- [[CLAUDE]]
- [[governance]]
- [[diagram-conventions]]
- [[design-to-code-assets]]
- [[obsidian-governance]]
- [[github-workflow]]
