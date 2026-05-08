# Hooks — Yule engineering-agent lifecycle policy points

> **단위:** 1 hook = 1 markdown 파일 (`<hook-id>.md`).
> **소유:** 본 디렉터리의 정책은 `engineering-agent / tech-lead` 가 관리한다.
> **현재 단계:** **markdown spec 만.** `hooks.json` runtime registry 와 dispatcher 는 후속 PR.

## 1. 무엇이 hook 인가

**Hook = 13 단계 lifecycle 사이의 사전(pre) / 사후(post) 정책 점.**

ECC 가 채택한 6 native 이벤트 (PreToolUse / PostToolUse / Stop / SessionStart / SessionEnd / PreCompact) 대신, Yule 은 자체 lifecycle 의 명시 단계를 사용한다 — 더 deterministic 하고 회귀 진단이 쉽다. 자세한 도입 결정 근거: [`policies/runtime/agents/engineering-agent/ecc-foundation.md`](../../../policies/runtime/agents/engineering-agent/ecc-foundation.md) §2.3.

## 2. lifecycle stage 키 (13 단계)

```
intake → triage → role_selection → research_planning → role_scoped_research
   → sufficiency_check → deliberation → synthesis
   → interim_report | insufficient_report | final_report
   → obsidian_preview → obsidian_recorded
   (optional) coding_authorization_pending → coding_job_ready
```

각 단계는 `pre`(진입 직전) / `post`(완료 직후) 두 점을 갖는다. 즉 hook 의 `fires_on` × `phase` 는 26 + 2 = 28 점.

## 3. 파일 구조 (frontmatter 스키마 v0)

```yaml
---
id: research-first-gate
title: research-first 게이트 (research_status 강제)
fires_on: deliberation     # lifecycle stage
phase: pre                 # pre | post
sync: blocking             # blocking → 실패 시 stage 중단 | advisory → 로그만
owner_role: tech-lead
output_contract:
  - blocked: bool
  - reason: str
  - audit_entry: AgentOpsEntry
side_effects:
  - 사용자에게 차단 사유 응답 (gateway 가 발화)
  - agent_ops_audit 에 (action=research_first_gate, autonomy=L1) 기록
references:
  - policies/runtime/agents/engineering-agent/ecc-foundation.md
  - policies/runtime/agents/engineering-agent/lifecycle-mvp.md
related_skills:
  - skills/research-collect.md
---
```

## 4. 본문 섹션 (필수)

```
## 트리거 조건
- ...

## 동작
1. ...
2. ...

## 차단 / 통과 매트릭스
| 조건 | 결과 |
| --- | --- |

## 실패 시 routing
- ...

## 관련 audit 기록
- ...
```

## 5. blocking vs advisory

| 종류 | 의미 | 권장 사용 |
| --- | --- | --- |
| `advisory` (default) | 실패 시 로그만 기록, lifecycle 진행 | 통계 / observation / continuous-learning |
| `blocking` | 실패 시 lifecycle stage 중단 + 사유 surface | research-first 게이트, secret access 차단, protected branch 거부 |

`blocking` hook 은 본문에 *왜 stage 를 멈추는 게 정당한지* 명시 (autonomy_policy L2+ 사유 첨부).

## 6. Hard rails (코드 측면)

본 markdown 정의가 *아무리* `blocking=true` 라고 선언해도, 실제 **secret 출력 / pem 노출 / protected branch / merge / push** 같은 가드는 코드 수준 (autonomy_policy + github_writer + redact_secret_like) 에서 1 차 차단된다. hook 은 정책 추적·감사용이지 보안 1 차 line 이 아니다.

## 7. 디렉터리 인벤토리 (2026-05-08 시점)

| hook | fires_on | phase | sync | 상태 |
| --- | --- | --- | --- | --- |
| [`research-first-gate`](research-first-gate.md) | deliberation | pre | blocking | 정의 (reference manifest) |

새 hook 추가:

1. `<id>.md` 작성 (위 frontmatter + 본문).
2. 본 인벤토리 표에 행 추가.
3. fires_on / phase 충돌 (같은 점에 multiple blocking hook) 여부를 [`policies/runtime/agents/engineering-agent/ecc-foundation.md`](../../../policies/runtime/agents/engineering-agent/ecc-foundation.md) §2.3 정책에 비춰 검토.
4. `python3 -m unittest discover -s tests -t .` 회귀 0 확인.

## 8. 등록 / 검증 (현재 / 후속)

| 단계 | 본 PR | 후속 PR |
| --- | --- | --- |
| markdown 정의 | ✅ | — |
| `hooks.json` runtime registry | ❌ | ✅ |
| lifecycle stage 진입 시 hook 자동 호출 | ❌ | ✅ |
| hook audit (agent_ops_audit) 통합 | ❌ | ✅ |
| autonomy_policy 의 새 action_id 와 hook 매핑 | ❌ | ✅ |

## 9. 명시적 비범위

- **외부 스크립트 (Node / Bash) hook 형태:** ECC 의 `hooks.json` + Node script 패턴은 본 PR 비도입. Yule 의 hook 은 **단일 Python 모듈 호출** 형태로 후속 PR 에서 dispatcher 에 wiring 한다.
- **PreCompact / SessionStart 같은 플랫폼 이벤트:** Yule 의 lifecycle 13 단계와 무관. 필요해지면 `runtime` lifecycle 으로 별도 namespace 추가 (예: `runtime:session-start`).
