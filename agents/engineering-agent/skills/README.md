# Skills — Yule engineering-agent capability registry

> **단위:** 1 skill = 1 markdown 파일 (`<skill-id>.md`).
> **소유:** 본 디렉터리의 정책은 `engineering-agent / tech-lead` 가 관리한다.
> **현재 단계:** **foundation 정의 layer 만.** loader / dispatcher / runtime 자동 호출은 별도 PR (이슈 #25 의 후속 작업).

## 1. 무엇이 skill 인가

**Skill = 한 역할(또는 여러 역할) 이 수행 가능한 단일 capability 의 명세.**

skill 은 다음을 한 곳에 모은다:

- 어떤 입력에 대해 무엇을 출력하는지 (input / output contract)
- 누가 owner 이고 누가 참여 가능한지
- 어떤 autonomy level 인지 (L0~L4)
- 어떤 lifecycle 단계 / Obsidian 기록 / 하드 레일과 연동되는지

## 2. 파일 구조 (frontmatter 스키마 v0)

```yaml
---
id: research-collect            # kebab-case 고정
title: 자료 수집 (research collect)
owner_role: tech-lead           # 작성·승인 책임
applicable_roles:               # 호출 가능 역할
  - tech-lead
  - ai-engineer
  - backend-engineer
autonomy_level: L1_AUTO_RECORD_REQUIRED   # autonomy_policy 의 string value
input_contract:
  - prompt
  - active_research_roles
output_contract:
  - research_pack
  - research_status
  - research_source_count
preconditions:
  - session.extra has research_forum_thread_id (when forum mode)
side_effects:
  - session.extra.research_pack written
  - agent_ops_audit entry recorded
references:
  - policies/runtime/agents/engineering-agent/lifecycle-mvp.md
  - policies/runtime/agents/engineering-agent/research-budget.md
---
```

## 3. 본문 4 섹션 (필수, 순서 고정)

```
## Trigger
- "..." (Use this skill when ...)
- ...

## Workflow
1. ...
2. ...
3. ...

## Decision Matrix
| signal | action |
| --- | --- |

## How to Use
### Quick Mode
- inline 호출 형태
### Full Mode
- subagent / runtime 위임 형태 (loader 가 land 한 뒤 활성)
```

본 4 섹션 컨벤션은 ECC (`affaan-m/everything-claude-code`) 의 `skills/<id>/SKILL.md` 패턴에서 차용. 자세한 도입 결정 근거: [`policies/runtime/agents/engineering-agent/ecc-foundation.md`](../../../policies/runtime/agents/engineering-agent/ecc-foundation.md) §2.2.

## 4. 작성 가이드라인

- **단일 책임.** 1 skill = 1 capability. 변형은 별도 파일.
- **autonomy_level 명시 필수.** L3 이상은 본문에 *왜 사람 승인이 필요한지* 명시.
- **input/output contract 는 frontmatter 에서 deterministic.** 본문은 prose.
- **Obsidian 기록 정책 참조.** 어떤 vault 경로 / kind 로 떨어지는지 본문 명시.
- **side_effects 는 모두 프런트매터에 나열.** 누락된 부작용은 정책 위반.
- **변경 시 `references` 갱신.** 정책 / docstring / 테스트 위치를 reverse-link 으로.

## 5. 등록 / 검증 (현재 / 후속)

| 단계 | 본 PR | 후속 PR |
| --- | --- | --- |
| markdown 정의 | ✅ | — |
| frontmatter schema 검증 | ⏳ (수작업) | ✅ (loader 자동) |
| Discord intake / role runtime 자동 호출 | ❌ | ✅ |
| skill 호출 audit (agent_ops_audit) | ❌ | ✅ |
| 회귀 테스트 (skill 별) | ❌ | ✅ |

## 6. 디렉터리 인벤토리 (2026-05-08 시점)

| skill | owner | autonomy | 상태 |
| --- | --- | --- | --- |
| [`research-collect`](research-collect.md) | tech-lead | L1 | 정의 (reference manifest) |

새 skill 을 추가할 때:

1. `<id>.md` 작성 (위 frontmatter + 4 섹션).
2. 본 인벤토리 표에 행 추가.
3. `policies/runtime/agents/engineering-agent/ecc-foundation.md` 의 §2.2 와 충돌 없는지 확인.
4. `python3 -m unittest discover -s tests -t .` 회귀 0 확인.

## 7. 명시적 비범위

- **runtime 자동 호출:** 본 디렉터리는 정의 layer. dispatcher 가 아직 없음.
- **여러 skill 의 chain:** 1 skill 안에 다른 skill 호출 명시 가능하나, chain 자동화는 후속.
- **사용자 정의 skill 의 권한 계산:** 모든 skill 은 owner_role 의 권한을 따름. 사용자 정의 권한은 후속.
