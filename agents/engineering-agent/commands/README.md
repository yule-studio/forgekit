# Commands — Yule engineering-agent operator entry registry

> **단위:** 1 command = 1 markdown 파일 (`<command-id>.md`).
> **소유:** 본 디렉터리의 정책은 `engineering-agent / tech-lead` 가 관리한다.
> **현재 단계:** **markdown spec 만.** Discord slash 자동 등록 / CLI 자동 매핑은 후속 PR.

## 1. 무엇이 command 인가

**Command = 운영자 / 사용자 / agent 자신이 수동으로 호출 가능한 entry point 의 표준 매핑.**

ECC 와 동일하게 *primitive prompt markdown* 패턴을 사용한다 — command 는 dispatcher 가 아니라 행동 명세. 단, Yule 은 기존에 이미 동작하는 slash command (예: `/engineer_intake`, `/engineer_show`) 와 CLI (`yule engineer`, `yule github`, `yule obsidian`) 가 **코드에 존재** 한다. 본 디렉터리는 *그 동작을 markdown 으로 등록* 하는 documentation layer 다.

도입 결정 근거: [`policies/runtime/agents/engineering-agent/ecc-foundation.md`](../../../policies/runtime/agents/engineering-agent/ecc-foundation.md) §2.4.

## 2. 파일 구조 (frontmatter 스키마 v0)

```yaml
---
id: engineer-show
title: 세션 진단 (engineer show)
surface: both                  # discord-slash | cli | both
slash: /engineer_show          # surface 가 discord-slash 또는 both 일 때 필수
cli: yule engineer show        # surface 가 cli 또는 both 일 때 필수
allowed_roles:
  - tech-lead                  # write actor 라벨 (read-only command 면 user 도 허용)
autonomy_level: L0_AUTO_RECORD_OPTIONAL
required_approval: false       # false | role-approver | human
references:
  - docs/engineering.md
  - src/yule_orchestrator/cli/engineer.py
related_skills:                # 호출 가능한 skill 들 (선언적 — runtime 자동 호출 X)
  - skills/research-collect.md
related_hooks:
  - hooks/research-first-gate.md
---
```

## 3. 본문 섹션 (필수)

```
## When to Use
- ...

## Trigger phrases (Korean)
- ...

## Inputs
| field | type | required |

## Outputs
- ...

## Edge Cases
- ...

## Examples
- ...
```

## 4. surface 별 운영 규칙

| surface | 등록 위치 | 기존 코드 | 본 markdown 책임 |
| --- | --- | --- | --- |
| `discord-slash` | `src/yule_orchestrator/discord/bot.py` 의 slash 등록 | 이미 존재 | command 의 *문서화 layer* |
| `cli` | `src/yule_orchestrator/cli/<module>.py` | 이미 존재 | 동일 |
| `both` | 위 두 곳 | 이미 존재 | 동일 |

본 PR 은 코드를 손대지 않는다. markdown 만 추가해 *어떤 entry 가 살아 있는지* 외부에서 읽을 수 있게 한다.

## 5. autonomy_level vs required_approval

| autonomy_level | required_approval | 사용 사례 |
| --- | --- | --- |
| L0 / L1 | false | 상태 조회 / 사용자 가시 응답만 (status 진단, list, show) |
| L2 | false | 자율 실행 가능 변경 (draft 문서 / 자료 수집 / sync) |
| L3 | role-approver 또는 human | knowledge note 확정 / vault commit / GitHub PR 생성 |
| L4 | human (강제) | merge / deploy / secret 변경 — 본 PR 디렉터리에는 등록 안 함 |

## 6. 디렉터리 인벤토리 (2026-05-08 시점)

| command | surface | autonomy | 상태 |
| --- | --- | --- | --- |
| [`engineer-show`](engineer-show.md) | both | L0 | 정의 (reference manifest, 기존 slash + CLI 등록 문서화) |

새 command 추가:

1. `<id>.md` 작성 (위 frontmatter + 본문).
2. 본 인벤토리 표에 행 추가.
3. 기존 코드 (slash 등록 / CLI 모듈) 와 *불일치 없는지* 확인. markdown 만 만들고 동작 X 인 entry 는 금지.
4. `python3 -m unittest discover -s tests -t .` 회귀 0 확인.

## 7. 등록 / 검증 (현재 / 후속)

| 단계 | 본 PR | 후속 PR |
| --- | --- | --- |
| markdown 정의 | ✅ | — |
| 실제 slash / CLI 동작 | ✅ (기존 코드) | — (이미 동작) |
| markdown ↔ 코드 매핑 자동 검증 | ❌ | ✅ (loader 가 검사) |
| 새 command 의 markdown-first 등록 | ❌ | ✅ (loader 가 코드 자동 wiring 또는 lint) |

## 8. 명시적 비범위

- **자동 dispatcher:** 본 markdown 을 읽어 slash 를 *자동 등록* 하는 코드는 후속 PR.
- **인수 schema 검증:** Discord slash 의 option 검증 / CLI argparse 와 본 markdown 의 `Inputs` 표 매칭은 후속 PR.
- **새 command 의 코드 자동 생성:** 본 markdown 만 작성하고 코드 없이 동작 X 인 command 는 금지 (orphan markdown 방지).
