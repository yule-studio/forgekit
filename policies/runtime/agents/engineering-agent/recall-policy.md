# Engineering Agent — Recall policy (Hermes-inspired, issue #59)

본 정책은 Yule 의 **cross-session recall** 을 정한다. Hermes Agent 의 `agent/insights.py` 가 보여주는 "지난 N 일 동안 어떤 작업을 했고 어떤 자료를 봤는지" 의 일자별 회상 흐름을 Yule lifecycle 안에 결합한다.

이 문서는 issue #59 ([Hermes 흡수 결정 D-3](#)) 의 구현물이다.

## 1. 핵심 원칙

1. **recall 은 이미 영속화된 자산을 다시 꺼내 쓰는 행위다.** 새로 인덱스를 만들지 않고, [memory-policy](./memory-policy.md) §3 의 6 가지 trigger 가 만든 자산만 본다.
2. **session 단위 recall 은 이미 잘 됨** — `agents/session_status.py` + topic ledger 가 단일 세션 내부 진행/막힘 을 답한다. 본 정책은 *cross-session* 영역.
3. **운영자 정량 보고와 정성 진단을 분리한다.** Hermes insights 는 token / cost / 빈도 (정량). Yule supervisor 는 어디까지 했고 무엇이 막혔나 (정성). 둘 다 의미 있고 상호 보완.
4. **recall 결과는 새 작업의 input 후보**. 자동 input 주입은 *명시적 인용* 만 — `yule engineer intake` 가 자동으로 어제의 결정을 prompt 에 끼워 넣지 않는다.

## 2. recall 영역 (3 종)

| 영역 | 답하는 질문 | source |
|---|---|---|
| **session-internal** (이미 구현됨) | 이 세션은 어디까지 진행했나 / 무엇이 막혔나 | `session_status.diagnose_session()` + topic ledger |
| **cross-session 일자별 회고** (본 정책 신규) | 지난 N 일 동안 어떤 결정 / 자료 / 작업이 있었나 | task-logs / decisions / research / agent_ops_audit |
| **topic 횡단 recall** (본 정책 신규) | 같은 주제로 한 작업이 여러 세션 / 여러 thread 에 흩어져 있을 때 한 곳에서 보기 | topic ledger `topic_key` + Obsidian frontmatter `topic` 키 |

## 3. cross-session 일자별 회고

### 3.1 입력 source

운영자가 `yule insights --days 7` (후속 milestone) 또는 본 정책에 따라 수동 grep 하면 다음 source 를 본다:

| source | 위치 | 무엇을 보나 |
|---|---|---|
| Obsidian `task-logs/` | vault `10-projects/<project>/task-logs/` | 일자별 작업 진행 로그 |
| Obsidian `decisions/` | vault `10-projects/<project>/decisions/` | TechLeadSynthesis 기반 결정 노트 |
| Obsidian `research/` | vault `10-projects/<project>/research/` | ResearchPack 자료 노트 |
| Obsidian `retrospectives/` | vault `10-projects/<project>/retrospectives/` | 회고 노트 ([self-improvement-flow](./self-improvement-flow.md)) |
| `session.extra.agent_ops_audit` | SQLite | 의사결정 / dispatch / handoff 로그 |
| `session.extra.research_topic` | SQLite | topic ledger transitions |

### 3.2 출력 형태 (보고서 contract)

후속 milestone 의 `yule insights` 가 만들 보고서 형태:

```markdown
# 지난 7 일 회고 — 2026-05-01 ~ 2026-05-08

## 활동 요약
- 신규 세션: N 건
- 종료 세션: N 건
- decisions 신규: N 건
- research 신규: N 건
- retrospectives 신규: N 건

## 영역별 활동 (top 5)
| topic_key | 세션 수 | 결정 수 | 마지막 활동 |

## 막힌 작업 (in-flight)
| session_id | thread | 마지막 lifecycle | stop_reason |

## 회고 후보
| topic | 회고 미작성 결정 | hint |

## audit 빈도 분석
| action | 빈도 | 마지막 발생 |
```

본 정책은 *contract* 를 정한다. 코드는 [§5 후속 milestone](#5-후속-milestone) 에서.

## 4. topic 횡단 recall

같은 topic 으로 한 작업이 여러 세션에 흩어져 있는 경우의 recall.

### 4.1 매칭 키

- 1 차: `session.extra.research_topic.topic_key` (topic ledger).
- 2 차: Obsidian frontmatter `topic` 키.
- 3 차 fallback: `kind=decision/research` 노트의 `slug` prefix 일치 (운영자 grep).

### 4.2 사용 시나리오

- 사용자: "Hermes 통합 관련 자료 다 보여줘"
  - 검색 키: `topic=hermes-yule-integration`
  - 결과: research / decision / task-log 모두 묶어서 반환.

- 사용자: "지난 달 결제 모듈 작업한 거"
  - 검색 키: `topic prefix payment-` AND created_at within 30 days.

### 4.3 retrieval boost (memory-policy §4 와 결합)

topic 횡단 recall 결과는 [memory-policy §4](./memory-policy.md#4-재사용성-hint-정책-read-side-boost-only) 의 boost 를 따른다 — `kind=decision` + `frontmatter.canonical=true` 가 가장 위.

## 5. 후속 milestone

본 정책의 코드 구현 — 본 phase 범위 밖:

1. **`yule insights` CLI** (`cli/insights.py`) — `--days N` / `--topic` 옵션, 보고서 §3.2 contract 준수. 우선순위 P1.
2. **topic 횡단 검색** — `cli/memory.py` 에 `--topic` filter 추가. 우선순위 P2.
3. **supervisor 의 회고 후보 안내** — `agents/session_status.py` 가 *회고 미작성 + decision 존재* 세션을 별도 신호로 노출. 우선순위 P2.

각 milestone 은 별도 issue + decision note 로.

## 6. 안전 가드

- recall 보고서는 **읽기 전용**. recall 결과를 본 작업이 새 산출물을 자동 만들지 않는다.
- recall 결과의 인용은 운영자가 명시적으로 prompt 에 붙임. 자동 prompt 주입 금지.
- secret / private key / Authorization 헤더가 task-logs / agent_ops_audit 안에 들어 있을 가능성 → recall 결과 출력 시 [audit redact_secrets](../../../../docs/github-agent-workos.md#5-hard-rails) 패턴 동일 적용.

## 7. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-08 | 초기 작성 — issue #59 의 Hermes 흡수 결정 D-3 구현물. cross-session 회고 contract / topic 횡단 recall / 후속 milestone 정리 |
