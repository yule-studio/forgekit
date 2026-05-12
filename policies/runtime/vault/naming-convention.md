# Vault Naming Convention (F15)

Obsidian vault (`yule-agent-vault/obsidian-vault/10-projects/yule-studio-agent/`)
안의 노트 명명 규칙. 운영자 / 에이전트가 한 눈에 무엇인지 보이게 한다.

## 표준 형식

```
YYYY-MM-DD_<kind>_<topic-kebab-case>.md
```

- `YYYY-MM-DD` — 생성 날짜 (KST 기준)
- `<kind>` — `decision` / `research` / `task-log` / `knowledge` /
  `meeting-notes` / `retrospective`
- `<topic-kebab-case>` — 주제 식별자. issue 번호 포함 시
  `issue-NN-<주제>` 형태

## 예시

| 형식 | 예 |
|---|---|
| ✅ 표준 | `2026-05-11_decision_product-vs-marketing-cpo-cmo-separation.md` |
| ✅ 표준 + issue | `2026-05-09_task-log_issue-73-tech-lead-runtime-loop.md` |
| ❌ dash 구분자 | `2026-05-08_decision-tech-lead-single-write-subject.md` |
| ❌ kind 누락 | `2026-05-08_59-hermes-tech-lead.md` |
| ❌ kind plural / 뒤쪽 | `2026-05-08_hermes-yule-integration-decisions.md` |

## 폴더 매핑

| 폴더 | kind | 용도 |
|---|---|---|
| `decisions/` | decision | 결정 박스 + 의미 + 비결정 + 변경 영향 |
| `research/` | research | 외부 / 내부 자료 조사 결과 + 출처 |
| `task-logs/` | task-log | issue 단위 작업 진행 / 결정 흐름 |
| `knowledge/` | knowledge | 운영 지식 surface (auto-collected 포함) |
| `meeting-notes/` | meeting-notes | 운영자 회의 / 합의 기록 |
| `retrospectives/` | retrospective | 사이클 회고 |

## frontmatter 표준

```yaml
---
title: "한 줄 제목"
kind: decision | research | task-log | knowledge | ...
session_id: <짧은 식별자>
project: yule-studio-agent
created_at: 2026-MM-DDTHH:MM:SS+09:00
agent: <부서>/<역할>
status: decided | draft | superseded | in-progress | current
related:
  - ../<폴더>/<관련-노트>.md
tags:
  - <topic-tag>
  - <kind>
---
```

## 자동화 에이전트 작성 규칙

runtime / agent 가 vault 에 노트를 작성할 때:

1. 본 컨벤션 준수 (파일명 + frontmatter)
2. PasteGuard 통과한 페이로드만 작성 (secret/PII 자동 마스킹)
3. `OUTBOUND_VAULT` hook 통과 → `obsidian-vault-push` 가 켜져 있으면
   자동 git push, 아니면 vault 만 write
4. 관련 시기 vault 의 다른 노트와 cross-link (`related`)
5. 같은 결정/연구가 이미 존재하면 새로 만들지 말고 `status: superseded`
   로 갱신한 새 노트 + 본 노트 첫 줄에 후속 링크

## 본 컨벤션의 vault 사본

vault 안에도 같은 내용의 README 가 존재 (운영자 vault 만 들고 다닐 때
참조용):

```
10-projects/yule-studio-agent/README.md
```

본 repo 의 policies 와 vault 의 README 가 어긋나면 본 repo 가 권위.

## 관련

- F15 #126 corporate-structure 사이클의 commit 8 에서 도입
- `policies/runtime/plugins/obsidian-vault-push.md` — vault push 자동화 게이트
