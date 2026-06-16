# Agent invocation contract — 모든 역할의 호출 계약 (SSoT)

> 누가 언제 호출되고 / 무엇을 받고 / 무엇을 내고 / 어디까지 쓸 수 있고 / 공통 vault 에
> 어떤 lane·색·메타데이터로 기록하는지를 **역할 단위로 고정**한다. 코드 SSoT 는
> [`agents/governance/agent_contract_registry.py`](../apps/engineering-agent/src/yule_engineering/agents/governance/agent_contract_registry.py)
> + [`agent_color_registry.py`](../apps/engineering-agent/src/yule_engineering/agents/governance/agent_color_registry.py)
> + [`note_frontmatter.py`](../apps/engineering-agent/src/yule_engineering/agents/governance/note_frontmatter.py).
> 드리프트 가드: [`tests/governance/test_agent_contracts.py`](../tests/governance/test_agent_contracts.py).

## 1. 계약 필드

| 필드 | 의미 |
| --- | --- |
| `agent_id` / `department_id` / `role_id` | 식별자 (`<dept>/<role>`) |
| `contract_class` | executor / coordinator / reviewer / advisory / product / observer / curator / platform |
| `owner_domain` | 담당 도메인 |
| `trigger_when` | 언제 호출되는가 |
| `input_packet` / `output_packet` | 입력 / 출력 형태 |
| `question_budget` | 사용자 질문 상한(product=3, 그 외 0) |
| `can_write_code` / `can_commit` / `can_open_pr` | 코드/커밋/PR 권한 |
| `can_write_vault` | vault note 쓰기 권한 |
| `worktree_policy` | isolated_worktree / orchestrate_only / read_only / none |
| `obsidian_write_target` | 공통 vault 안의 write lane (`<lane>/<role>`) |
| `retrieval_scope` | retrieval 범위 (metadata 기반, **색 아님**) |
| `approval_required_for` / `escalation_to` | 승인 필요 액션 / 에스컬레이션 대상 |
| `receipt_fields` | execution receipt 에 남길 필드 |
| `color_token` / `color_hex` | **사람용** 시각 구분 토큰 |

## 2. 계약 클래스 → 권한 (핵심 불변)

| class | code | commit | PR | vault | worktree | 대표 역할 |
| --- | :--: | :--: | :--: | :--: | --- | --- |
| **executor** | ✅ | ✅ | ✅ | ✅ | isolated_worktree | backend/frontend/devops/ai-engineer |
| **platform** | ✅ | ✅ | ✅ | ✅ | isolated_worktree | platform-runtime-engineer |
| **coordinator** | ❌ | ❌ | ✅ | ✅ | orchestrate_only | tech-lead |
| **reviewer** | ❌ | ❌ | ❌ | ✅ | read_only | security-engineer, qa-engineer |
| **product** | ❌ | ❌ | ❌ | ✅ | none | product-manager |
| **advisory** | ❌ | ❌ | ❌ | ✅ | none | user-researcher, marketing/*, hr/*, finance/*, legal/*, sales-cs/*, product-designer, planning |
| **observer** | ❌ | ❌ | ❌ | ✅ | none | ops-observer |
| **curator** | ❌ | ❌ | ❌ | ✅ | none | knowledge-engineer |

> **코드 커밋은 executor / platform 만.** advisory·reviewer·observer·curator·product·
> coordinator 는 **note/packet 중심** — 절대 직접 커밋하지 않는다(governance test 강제).

## 3. 적용 원칙 (역할별 호출)

- **product-manager** = engineering 앞단 *product intake gate* — raw ask → ProductIntentPacket
  (질문 ≤3 + 보강 + acceptance/non-goals). [`product-intake-gate.md`](product-intake-gate.md).
- **tech-lead** = packet 분해 / 라우팅 / synthesis (technical approval).
- **backend/frontend/devops/ai-engineer** = packet 기준 구현 (worktree + draft PR).
- **qa-engineer / security-engineer** = cross-cutting 리뷰 게이트 (findings only).
- **platform-runtime-engineer** = 설치/연결/runtime/provider/doctor (executor 권한).
- **ops-observer** = 24h 감시 / budget / alert / fallback triage (note + next-action).
- **knowledge-engineer** = vault schema / canonical 승격 / brain pack / retrieval policy.
- 그 외 부서(marketing/hr/finance/legal/sales-cs/planning) = advisory note/packet.

## 4. vault 기록 (공통 vault, lane 분리)

별도 위키를 만들지 않는다. 하나의 공통 vault 안에서 역할별 **write lane** 과 **메타데이터**
로 구분한다. lane = `<department-lane>/<role>` (예 `30-engineering/backend-engineer`,
`20-product/product-manager`, `40-ops/ops-observer`). 색/메타데이터 정책은
[`obsidian-agent-color-policy.md`](obsidian-agent-color-policy.md) 가 SSoT.

## 5. 관련
- [`obsidian-agent-color-policy.md`](obsidian-agent-color-policy.md) ·
  [`product-intake-gate.md`](product-intake-gate.md) ·
  [`agents/product-agent/CLAUDE.md`](../agents/product-agent/CLAUDE.md) ·
  [`policies/runtime/agents/corporate-org-chart.md`](../policies/runtime/agents/corporate-org-chart.md) ·
  [`approval-matrix.md`](approval-matrix.md)
