# agent.json → manifest.json 통일 (F15 #126)

## 결과 한 줄

레포 안 어떤 파일 / 코드 / 문서도 더 이상 `agent.json` 을 참조하지 않는다.
`manifest.json` 이 부서 / 역할 / 플러그인 모두의 단일 형식.

## 왜 통일했나

### 1. 두 형식이 같은 슬롯을 두고 충돌하고 있었다

F11 (#102 PluginManifest / AgentManifest) 도입 이전에는 모든 부서 /
역할이 `agent.json` 하나로 운영됐다. 거기에는 다음이 섞여 있었다:

- 정체성 (id / name / role)
- Discord wiring (`discord_token_env`)
- 정책 경로 (`instruction_entry`, `policies` 배열)
- 부서 구성 (`members`, `participants`, `integrations`, `tool_catalog`)
- 역할 계약 (`operating_modes`, `prompt_contract_standard`,
  `stop_conditions`, `default_response_template`, 도메인별
  `*_standard` 들 — devops 는 `ci_cd_standard` / `kubernetes_standard` /
  `observability_standard` 등 8 개, frontend 는 8 개, qa 는 7 개)

F11 이 도입된 뒤에는 `manifest.json` 이라는 별도 파일이 같은 디렉터리에
생기면서 **F11 core 필드만** (id / name / role / version / capabilities /
plugins_required / autonomy_level / risk_class / module_path) 담았다.

문제는 두 파일이 모두 살아 있었다는 점이다:

| 파일 | 들고 있던 데이터 | 누가 읽었나 |
|---|---|---|
| `agents/<dept>/<role>/agent.json` | 역할 계약 + Discord wiring | `role_profiles.py`, `coding/authorization.py`, `member_bots.py` |
| `agents/<dept>/<role>/manifest.json` | F11 core 식별자만 | `extension/loader.py` (registry) |
| `agents/<dept>/agent.json` | 부서 구성 + 참여자 pool | `context_loader.py`, `messaging/registry.py`, `member_bots.py` |

**같은 디렉터리에 같은 의미를 가진 두 파일이 존재** → 운영자가 어느
파일을 신뢰해야 하는지 모호. 한 쪽을 수정하면 다른 쪽이 stale.

### 2. 새 부서 (product / marketing) 가 manifest.json 으로 도착했다

F15 commit 2 (product-agent) / commit 3 (marketing-agent) 에서 신설된
역할들은 모두 처음부터 `manifest.json` 으로 들어왔다. 만약
engineering-agent 가 계속 `agent.json` 을 쓴다면, 부서끼리 동일한
스키마 가정 (예: dispatcher 의 capabilities 매칭) 이 깨졌을 것이다.

### 3. F11 PluginManifest 검증을 모든 곳에 적용하고 싶다

`plugins/<id>/manifest.json` 은 이미 엄격 검증 (semver / kebab-case id /
hook enum / module_path regex / risk_class enum) 을 받는다. 역할 / 부서가
같은 파일명 + 같은 디렉터리 컨벤션을 쓰면, 미래에 부서/역할 manifest 도
동일한 validator 흐름 (loader → 검증 → registry) 으로 보낼 수 있다.

## 어떻게 통일했나 (commit 4 → 5 → 6 → 9)

총 4 commit, 단방향 마이그레이션. 중간 단계에서도 시스템은 깨지지 않게
한 commit 씩 잘게 쪼갰다.

### commit 4 — 데이터 흡수

`agents/engineering-agent/<role>/agent.json` 의 모든 키 (rich
role-contract 포함) 를 같은 디렉터리의 `manifest.json` 안으로 합쳤다.
F11 core 필드는 캐노니컬 값으로 정정 (placeholder 였던
`prompt-engineering` 같은 capabilities → 실제 운영 capabilities).

이 시점에는 두 파일이 여전히 공존. loader 는 아직 agent.json 을 읽음.
**시스템 동작 영향 없음.**

### commit 5 — loader 전환 + dept-level rename

다음 6 개 코드 경로를 `manifest.json` 으로 일괄 전환:

- `src/yule_orchestrator/core/context_loader.py`
- `src/yule_orchestrator/discord/member_bots.py`
- `src/yule_orchestrator/agents/role_profiles.py`
- `src/yule_orchestrator/agents/coding/authorization.py`
- `src/yule_orchestrator/agents/runners/github_copilot.py` (docstring)
- `src/yule_orchestrator/agents/messaging/registry.py` (docstring)

동시에 부서 레벨 파일을 git mv:

- `agents/engineering-agent/agent.json` → `manifest.json`
- `agents/planning-agent/agent.json` → `manifest.json`

이 commit 이후 어떤 코드 경로도 agent.json 을 읽지 않는다. 역할 레벨
agent.json 파일은 디스크에 남아 있지만 **사실상 죽은 파일**.

### commit 6 — 일괄 삭제 + 잔존 참조 정리

`engineering-agent/<role>/agent.json` 7 파일 git rm. 동시에 잔존
참조 텍스트 (tests 12, policies 11, docs 3) 일괄 갱신.

회귀: `pytest tests/engineering tests/discord/test_member_bots.py`
→ 1281 passed.

### commit 9 — role-specific standard 필드 복원

commit 4 의 흡수 스크립트가 explicit key list 만 옮긴 탓에 역할별
도메인 standard (backend 7 / frontend 8 / designer 8 / qa 7 / devops 8 /
tech-lead 6) 가 누락 → `test_role_contract_devops_engineer` 회귀 fail.
git history (`0070a47^`) 에서 누락 필드만 골라 manifest 에 머지.

## 한 곳에 두 형식이 살았을 때 발생하던 손해

- **운영자 혼란** — "discord_token_env 를 바꿔야 하는데 어느 파일이지?"
  같은 질문이 PR 리뷰에서 반복.
- **stale drift** — agent.json 의 capabilities 와 manifest.json 의
  capabilities 가 점점 다른 값으로 흘러감 (placeholder vs 실제).
- **registry 가 manifest.json 의 stub 만 보고** "ai-engineer 는
  `prompt-engineering` 만 한다" 라고 잘못 판단 가능.
- **새 부서 추가 비용 ↑** — engineering-agent 가 두 파일 컨벤션이라
  product-agent / marketing-agent 도 그 컨벤션을 따라야 하나 매번
  고민해야 함.

## 통일 후 단일 형식 — 위치별 스키마 차이

같은 `manifest.json` 파일명이지만 위치 / kind 에 따라 스키마가 다르다:

| 위치 | kind | 스키마 |
|---|---|---|
| `plugins/<id>/manifest.json` | (kind 필드: guard/learning/...) | F11 PluginManifest — **엄격 검증** |
| `agents/<dept>/<role>/manifest.json` | `role` | F11 AgentManifest + role-contract 확장 필드 |
| `agents/<dept>/manifest.json` | (department) | dept config (members / participants / integrations / policies / tool_catalog / write_policy) — untyped dict |

context_loader 는 untyped dict 로 다루므로 위치 간 스키마 차이가
충돌을 만들지 않는다. F11 strict validator 는 PluginManifest /
AgentManifest 에만 적용.

## 검증

- 자동 회귀: 1281 tests passed (`pytest tests/engineering
  tests/discord/test_member_bots.py`)
- doctor: `OK agent context  agents/engineering-agent/manifest.json`
- end-to-end smoke (Discord 없이):
  - `yule context engineering-agent` → manifest 로 dept context 로드
  - `yule runtime up --dry-run` → 13 services 인식
  - `yule discord up --dry-run` → 9 봇 inventory active
  - `yule engineer intake → approve → progress → complete` →
    dispatcher 6 후보 / executor 1 / reviewer 5 합의 게이트 통과
  - `yule supervisor run --once` → 세션 상태 진단 정상

## 본 통일을 뒤집고 싶을 때

다음 조건이 모두 만족되어야 한다:

1. 부서 / 역할 / 플러그인 manifest 스키마가 분리되어야 할 **새로운
   기술적 이유** 가 등장
2. `policies/runtime/plugins/README.md` 에 정의된 추가 절차 + 본
   문서의 status 를 `superseded` 로 갱신한 후속 문서
3. 운영자 명시 승인

이유 없이 다시 두 형식으로 분리하지 않는다.

## 관련

- F11 (#102 / #150) PluginManifest / AgentManifest 도입
- F15 #126 corporate-structure (본 통일이 이 사이클의 commit 4-6, 9)
- `policies/runtime/plugins/README.md`
- `policies/runtime/vault/naming-convention.md`
- vault: `10-projects/yule-studio-agent/task-logs/2026-05-11_task-log_issue-126-corporate-structure-f15.md`
