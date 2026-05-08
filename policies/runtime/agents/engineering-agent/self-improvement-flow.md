# Engineering Agent — Self-improvement flow (Hermes-inspired, issue #59)

본 정책은 Yule 의 **회고 → 다음 작업 input 자동 흐름** 을 정한다. Hermes Agent 의 `agent/curator.py` 가 보여주는 "스킬 라이브러리 자동 정리" 의 컨셉 중 *명시적 회고 흐름* 만 흡수하고, **자율 LLM-driven consolidation 은 도입하지 않는다.**

이 문서는 issue #59 ([Hermes 흡수 결정 D-5](#)) 의 구현물이다.

## 1. 핵심 원칙

1. **자동 LLM consolidation 금지.** Yule 의 정책 / role profile / lifecycle 은 단일 source-of-truth 가 되어야 한다 — agent 가 자기 결정으로 운영자 변경 없이 정책 / 자산을 갱신하지 못한다.
2. **회고는 *자산* 이지 *코드 변경 트리거* 가 아니다.** 회고가 다음 작업의 input 이 되는 path 는 정책으로 박지만, 회고 본문 자체가 코드 / 정책을 자동 수정하지 않는다.
3. **회고 자동화는 *생성 시점 알림* 까지**. session 종료 시 회고 작성 후보를 운영자에게 안내하는 데까지가 정책 — 본문은 운영자가 작성 (또는 명시적 LLM 요약).
4. **회고 자산이 다음 작업 input 으로 들어가는 통로는 명시적**. `yule engineer intake` 가 회고를 자동 첨부하지 않는다 — 운영자가 영역을 알면 명시 인용.

## 2. retrospective note kind

회고 본문은 `kind=retrospective` Obsidian note. 이미 [obsidian-memory.md](./obsidian-memory.md) §2 path layout 에서 `10-projects/<project>/retrospectives/` 폴더가 정의되어 있고, 본 정책이 그 contract 의 추가 서약을 둔다.

### 2.1 frontmatter 필수 필드

```yaml
---
title: "<영역> 회고 — <YYYY-MM-DD>"
topic: <topic_key>
kind: retrospective
project: yule-studio-agent
session_id: <원본 세션 id>
related_decisions: [path1, path2]   # 다음 작업 input 후보로 다시 인용될 결정 노트들
related_research: [path1, path2]
status: captured                     # 작성 직후 / decided 로 promote 가능
canonical: <true|false>              # 영역의 "1 차 source" 표식 (memory-policy boost +2)
reusable: <true|false>               # 다른 작업에서 자주 인용 (boost +1)
created_at: <ISO>
---
```

### 2.2 필수 본문 섹션 (8 개)

```
# <영역> 회고

## 무엇을 시도했나
## 어떤 결과가 나왔나
## 무엇이 잘 되었나 (keep)
## 무엇이 안 되었나 (problem)
## 다음에 다르게 할 것 (try)
## 영역의 1 차 source 후보       (canonical 표식 후보)
## 다음 작업 input 후보           (다음 intake 에서 인용 후보 noun-phrase)
## 운영자 액션
```

본 8 개 섹션이 비어 있어도 헤더는 유지 — `_(없음)_` placeholder 표시 (knowledge-writer 패턴).

## 3. 회고 생성 트리거 (운영자 명시 안내)

### 3.1 lifecycle 단계 — 회고 후보 알림

`agents/lifecycle_status.py` 가 다음 조건일 때 *회고 작성 후보* 신호를 supervisor 진단에 노출:

| 조건 | 신호 |
|---|---|
| `work_report.status == FINAL` AND 같은 topic 의 retrospective 미작성 | "이 작업 회고 작성 후보" |
| `topic_ledger.status == STATUS_SAVED` AND `revision >= 2` | "revision 누적 — 회고 후보" |
| coding_job 종료 (성공 / 실패) | "coding 결과 회고 후보" |

신호는 **알림** 일 뿐 — 회고 본문 자동 생성 없음. 운영자가 결정.

### 3.2 명시 명령 후보 (후속 milestone)

후속 milestone 의 CLI 후보:
- `yule engineer retrospective --session <id>` — §2.2 8 섹션 template 으로 회고 note 자동 scaffold (본문은 빈 placeholder, 운영자가 채움). 우선순위 P2.
- `yule engineer retrospective --session <id> --auto-summarize` — LLM 으로 keep/problem/try 부분 초안 (사람 검수 필수). 우선순위 P3.

본 phase 는 정책까지.

## 4. 회고 → 다음 작업 input path

### 4.1 retrieval boost (memory-policy §4 와 결합)

retrospective note 는 [memory-policy §4](./memory-policy.md#4-재사용성-hint-정책-read-side-boost-only) 의 boost 를 받는다:
- `kind=retrospective` → +0.5
- `frontmatter.canonical=true` → +2 (위 boost 와 합산 → +2.5)
- `frontmatter.reusable=true` → +1 (합산 → +1.5)

`yule memory search` 결과에서 회고 note 가 다른 `research` / `task-log` 보다 위에 노출됨.

### 4.2 명시 인용 흐름

운영자가 새 작업 intake 시:
1. `yule memory search "<영역 keyword>"` 로 같은 영역 회고 후보 확인.
2. 회고에서 인용할 부분을 prompt 에 명시 — "지난 X 작업의 retrospective 의 *try* 부분을 따라 진행" 형태.
3. session.extra `references_used` 키에 명시 인용 표식 (이미 구현됨).

### 4.3 자동 input 주입 금지

`yule engineer intake` 가 회고 본문을 자동으로 prompt 에 끼워 넣지 않는다 — 운영자가 영역을 알고 명시 인용해야 한다. 자동 주입은 prompt 노이즈 + 잘못된 input 위험.

## 5. Hermes curator 흡수 / 비흡수 비교

| Hermes curator 영역 | Yule 적용 | 이유 |
|---|---|---|
| 활성 / stale / archived state transition | **비도입** | Yule vault 는 git 으로 관리 — 사용자 명시 commit 만 변경 |
| 좁은 스킬 → umbrella 스킬 LLM consolidation | **비도입** | 단일 source-of-truth 자동 갱신 위험 |
| `run.json` + `REPORT.md` 감사 로그 | **부분 도입** | retrospective note 가 본 역할 — kind=retrospective + 8 섹션 |
| cron job reference 자동 갱신 | **비도입** | scheduled-automation 정책에서 별도 다룸 |
| LLM review agent (자기 자신 review) | **비도입** | 운영자 가시성 우선 |

## 6. 안전 가드

- 회고 본문 작성은 **운영자만**. agent 가 본문을 직접 쓰지 않는다.
- LLM 보조 요약 (--auto-summarize) 후속 milestone 도 **사람 검수 필수** — frontmatter `status: captured` 로 작성, 운영자가 검수 후 `decided` 로 promote.
- canonical / reusable 표식은 운영자만 박는다 — agent 자율 표식 금지.

## 7. 후속 milestone

1. **lifecycle status 회고 후보 신호** — `agents/lifecycle_status.py` + `agents/session_status.py` 가 §3.1 조건을 진단에 노출. 우선순위 P1.
2. **`yule engineer retrospective` CLI** — §3.2. 우선순위 P2.
3. **memory retrieval boost wiring** — kind=retrospective + canonical/reusable 표식 boost 적용 (memory-policy §4 와 동시 진행). 우선순위 P1.

## 8. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-08 | 초기 작성 — issue #59 의 Hermes 흡수 결정 D-5 구현물. retrospective note contract / 회고 trigger / 다음 input path / 자율 consolidation 금지 정리 |
