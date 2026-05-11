---
title: "issue #81 통합 polish — 의사결정"
kind: decision
issue: 81
parent_issue: 73
session_id: issue-81-integration-polish
project: yule-studio-agent
created_at: 2026-05-11T00:00:00+09:00
status: decided
branch: feature/issue-81-integration-polish
approval_required: false
tags: [decision, issue-81, integration, regression, handoff]
contract: research-forum-export/v0
---

# 합의안

`feature/issue-81-integration-polish` 는 코드 변경 없이 *회귀 + 산출물 정리 + 후속 이슈 분리* 만 land 한다. 머지된 main (HEAD `4acb160`) 위에서 #81 의 세 갈래 worktree split 이 결합할 때 cross-axis 충돌이 없음을 확인했고, 그 위에 master plan § 3 의 완료도 수치를 한 단계씩 상향한다. 남은 100% 갭은 10 종 후속 이슈로 분리한다.

# 핵심 결정 (D-81-1 ~ D-81-10)

## D-81-1. 회귀 범위 — full discover + cross-axis 슈트 두 단계

`python3 -m unittest discover -s tests -t .` (3493 cases) 전수 + autonomy / knowledge / discussion / runtime 13 모듈 160 cases 추가 슈트.

**왜**: full discover 만으로는 cross-axis 라우팅이 *어느 슈트* 에서 검증되는지 운영자가 한눈에 못 봄. 두 번째 슈트가 통합점 자체를 명시화.

## D-81-2. 코드 변경 없음

본 worktree 에서는 `src/` / `tests/` / `policies/` / `.env.example` 어느 곳도 손대지 않는다.

**왜**: 통합 worktree 의 책임은 cross-axis regression / 문서 / handoff 다. 새 기능 추가는 #81 의 다른 worktree (gateway / execution / geeknews) 또는 후속 이슈가 담당.

## D-81-3. § 3 완료도 수치 갱신 폭

| 영역 | 갱신 |
| --- | --- |
| 운영 골격 | 65~75% → 80~85% |
| Discord 기술 토의 | 40~50% → 50~60% |
| 완전 자율 코딩 루프 | 45~55% → 70~80% |
| 역할별 자료 수집/정형화 | 25~35% → 50~60% |
| 종합 | 45~55% → 60~70% |

**왜**: Round 4 / 4-bis / 4-ter / 4 마무리 / Round 4 후속 시리즈가 main 에 land 된 폭이 § 3 의 이전 추정보다 분명히 위로 옮겨갔다. 다만 #81 worktree split 3축 (discussion-gateway / autonomy-execution / knowledge-geeknews) 은 미머지 → 모든 축의 상한을 70~85% 로 묶어 100% 선언을 유보.

## D-81-4. 자율 코딩 루프 상한이 80% 인 이유

live LLM 코드 편집기 활성화가 잔여이고 — `RecordOnlyCodeEditor` 가 plan markdown 만 작성, 실제 source 변경 없음. 활성화는 운영자 승인 + cost 검토 + secret 정책 + LLM provider 어댑터 별도 PR.

**왜**: hard rail (마스터 플랜 § 16-bis.6 / § 16-ter.6) 그대로 — 두 단계 opt-in 미설정 시 행동 변화 0.

## D-81-5. 자료 수집 루프 상한이 60% 인 이유

provider registry / routing / retrieval / feed_parser 까지 land 했지만 urllib `BytesFetcher` 한 조각 + sitemap / html_list / html_detail / github_api_repo_activity 4 transport 의 라이브 fetcher + `eng-research-collector` runtime service spawn + `SourceRefreshState` 영속화 4 종이 미land.

**왜**: 4 종 모두 *별도 PR* 로 land 가능하지만 본 worktree 책임 밖. 각 후속 이슈 (§ 후속 이슈 드래프트 참조) 에서 처리.

## D-81-6. discussion 축 상한이 60% 인 이유

`feature/issue-81-discussion-gateway` (commit `512ce7c`) 이 PR 미생성, main 미머지. discussion_followup / context_pack / retrieval slot 은 land 했으나 gateway 가독성 + Discord operator surface 강화는 아직 작업 분기에만 존재.

**왜**: 머지 가시성 (`gh pr list` / `git branch --contains`) 이 단일 진실. PR 미생성 분기는 main 수치 계산에서 제외.

## D-81-7. 종합 70% 상한

세 축이 cross-axis 회귀로 충돌 없음 확인. 그러나 #81 worktree split 3 축 미머지 + live LLM editor / live decision provider 미land → 100% 선언은 운영자 명시 승인 + 라이브 활성화가 모두 끝난 뒤로 유보.

**왜**: 마스터 플랜 § 14 "회사형 runtime 완성 시나리오" 10 단계 중 6 ~ 8 단계 (실 코드 수정 / CI 결과 수신 / 후속 처리) 가 라이브 LLM editor 없이는 end-to-end 자동 진행 불가.

## D-81-8. 머지 순서 권장

(1) `discussion-gateway` PR 생성 → 회귀 통과 → 머지, (2) `knowledge-geeknews` PR #83 머지, (3) `autonomy-execution` PR 생성 → 머지, (4) 본 integration-polish PR 머지.

**왜**: (1)이 가독성 / operator surface 변경을 먼저 흡수해야 (2)(3)(4)의 status surface 텍스트 회귀가 안 깨짐. 단, cross-axis 회귀가 충돌을 이미 통과시켰으므로 순서가 바뀌어도 머지 자체는 가능 — 본 PR 의 § 3 수치만 후행 갱신 PR 으로 한 번 더 올라가야 한다.

## D-81-9. 후속 이슈 분리 기준

본 PR 의 task-log § "본 worktree 비범위 → 후속 이슈 매핑" 10 종이 후속 이슈 분리 후보. 이 중 (1)(2)(3) 은 이미 worktree branch 가 존재 → PR 생성만 필요. (4)~(10) 은 신규 worktree + 별도 PR.

**왜**: 이슈 단위가 작아야 운영자가 cost 검토 + 승인 게이트를 빠르게 통과시킨다. 마스터 플랜 § 12.1 PR 분리 원칙 (한 PR 안에 하나의 중심 목표) 그대로.

## D-81-10. progress comment 본문은 운영자가 직접 post

본 worktree 가 `gh issue comment` 를 직접 호출하지 않고 — 본 PR body / 별도 파일에 본문만 남긴다.

**왜**: issue post 는 외부 surface 변경이고 (사용자 / 팀에 visible), 운영자 명시 승인 게이트 유지가 핵심 안전 규칙 (CLAUDE.md "human approval is required before destructive commands, production deployments, or secret access" 와 같은 결.

# 후속 이슈 드래프트

이 결정에서 분리할 10 종 후속 이슈는 [[task-log-integration-polish-issue-81]] § "본 worktree 비범위 → 후속 이슈 매핑" 에 1:1 매핑된다.

| # | 제목 (가안) | 핵심 산출물 | 비고 |
| --- | --- | --- | --- |
| F-81-1 | discussion-gateway PR 생성 + 머지 | `src/yule_orchestrator/discord/*`, `agents/discussion/*`, `tests/discord/*` | branch 이미 존재 (commit `512ce7c`). PR description 작성 + 회귀 / 머지. |
| F-81-2 | autonomy-execution PR 생성 + 머지 | 역할별 반복 실수 ledger + preflight seam round 1 | branch 이미 존재 (commit `38e9332`). PR description 작성 + 회귀 / 머지. |
| F-81-3 | knowledge-geeknews PR #83 머지 | engineering-knowledge canonical title | PR 이미 OPEN. 회귀 통과 / 머지. |
| F-81-4 | live LLM 코드 편집기 활성화 | `RecordOnlyCodeEditor` → 실제 LLM 어댑터 | 운영자 승인 + cost 검토 + secret 정책 별도. |
| F-81-5 | urllib `BytesFetcher` 한 조각 | RSS / Atom / GitHub releases atom 라이브 전환 | `register_safe_feed_providers(registry, bytes_fetcher_factory=...)` 한 호출로 3 transport 동시 활성. |
| F-81-6 | sitemap / html_list / html_detail / github_api 라이브 fetcher | 4 transport NO_LIVE_IMPL 해소 | 각 parser 추가 필요. |
| F-81-7 | runtime service spawn `eng-research-collector` | scheduler tick → routing → fetcher → adapter → vault writer | 서비스 spec 등록 + auto-spawn opt-in env flag. |
| F-81-8 | `SourceRefreshState` 영속화 | sqlite or vault sidecar | 재시작 후 backoff 상태 유지. |
| F-81-9 | `next_task_selector` 의 `DECISION_KIND_NEXT_TASK` 호출 | decision seam 의 두 번째 콜사이트 | `consult_decision_port` 헬퍼 재사용. |
| F-81-10 | Discord escalation alert | `blocked` / `needs_approval` N 분 지속 시 운영자 직접 멘션 | 현재는 dedup 게이트 / banner 만. |

# 보류

- live Anthropic / OpenAI SDK 활성화 (vs. `claude -p` 서브프로세스) — F-81-4 의 하위 결정. 별도 PR.
- vault git auto-commit 정책 (`yule obsidian sync --git-commit`) 확장 (push 자동화 / Obsidian 동기화 플러그인 연동) — 마스터 플랜 § 11 정책 그대로 보류.

# 비도입

- producer 가 큐 직접 enqueue — 큐 dedup 단일 지점 원칙 (§ 16-ter.6) 위배, 도입 안 함.
- protected branch 직접 push / force push — 가드 그대로.
- live LLM editor 의 record/replay 모드 — F-81-4 별도 PR 까지 보류.

# 승인 필요 여부

no — 본 PR 은 코드 변경 없이 문서 / 회귀 결과 / 후속 이슈 드래프트만 포함. 운영자가 후속 이슈 (F-81-1 ~ F-81-10) 각각의 PR 생성 / 머지 시점에서 자체 승인 게이트를 진행.

# 관련 문서

- [[task-log-integration-polish-issue-81]]
- [[research-integration-polish-issue-81]]
- [[decision-tech-lead-runtime-loop-issue-73]]
- [[research-tech-lead-runtime-loop-issue-73]]
- [[task-log-tech-lead-runtime-loop-issue-73]]
