# Yule Studio Agent — 문서 내비게이션

이 파일은 Codex/Claude/Gemini 같은 외부 에이전트와 사용자가 **이 레포에서
어떤 문서를 언제 읽어야 하는지** 한 화면에 정리한 진입점이다. 상세 규칙
자체는 본문 markdown 들이 책임지고, 본 파일은 "어디를 봐야 하는지" 만
알려준다.

> 핵심 원칙
> - **Codex/Claude 는 매번 모든 md 를 읽는다고 가정하지 않는다.**
> - **자주 강제해야 하는 규칙은 상위 문서 (root `CLAUDE.md`) 에 둔다.**
> - **세부적인 도메인 규칙은 작업 맥락에서만 읽히는 하위 문서에 둔다.**
>
> 이 가정 아래 본 진입점은 항상 짧게 유지한다.

## 1. 문서 계층 (high → low specificity)

```
AGENTS.md ─── 진입점 / 어떤 문서를 언제 볼지 안내 (지금 이 파일)
    │
    ├── CLAUDE.md ─────────── 전역 공통 규칙 (모든 에이전트/모든 작업)
    │
    └── agents/<agent>/
            ├── CLAUDE.md ──── 에이전트 전용 규칙 + 작업 맥락별 세부 문서 안내
            ├── CODE_LAYOUT.md ── 모듈 책임 / ownership / 파일 분리 기준
            └── (도메인 정책)

policies/
    ├── reference/   ── BRANCH / COMMIT / NAMING 같은 참조 규칙
    └── runtime/     ── 에이전트별 운영 정책 (governance / mvp-scope / …)

docs/
    ├── operations.md / approval-matrix.md / autonomy-policy.md  ── 운영/승인
    ├── architecture.md / engineering.md / planning.md           ── 아키텍처
    └── 그 외 토픽별 가이드 (ci-* / discord / memory / testing …)
```

## 2. 읽기 우선순위 — "어떤 작업이면 어떤 문서를 보는가"

| 거의 항상 읽는다 | 1. `AGENTS.md` (이 파일) |
|---|---|
|   | 2. root `CLAUDE.md` — 전역 안전 / 코딩 컨벤션 / 파일 크기 규칙 |

작업 맥락이 정해진 다음 추가로 읽는 문서:

| 작업 맥락 | 우선 읽을 문서 |
| --- | --- |
| engineering-agent 전반 | `agents/engineering-agent/CLAUDE.md` |
| 코드 구조 / 리팩터링 / 모듈 분할 | `agents/engineering-agent/CODE_LAYOUT.md` |
| 브랜치 / 커밋 / PR / 네이밍 | `policies/reference/{BRANCH_STRATEGY,COMMIT_CONVENTION,NAMING_CONVENTION}.md` + `agents/governance/runtime_policy.py` |
| 승인 / 자율 / hard rail | `docs/approval-matrix.md`, `docs/autonomy-policy.md` |
| 운영 / 배포 / 상태 / 업타임 | `docs/operations.md`, `docs/configuration.md` |
| Discord / forum / member-bot | `docs/discord.md`, `docs/runtime-member-bot-dispatch-parity.md` |
| 거버넌스 / Obsidian write ownership | `docs/engineering-agent-governance.md` (+ `policies/runtime/agents/engineering-agent/*`) |
| Vault / 지식 / inbox / retrieval | `docs/memory.md` (§"Curated 정책" / §"Retrieval eval") |
| 테스트 작성 / 회귀 가이드 | `docs/testing.md` |
| 성능 개선 / 고도화 opening criteria | `docs/engineering-company-runtime-master-plan.md` §"Post-test hardening" |
| Troubleshooting / 실수 기록 / preflight | `docs/troubleshooting-mandatory.md` (mandatory capture · 8 섹션 스키마 · mistake ledger 자동 승격) |
| 슬래시 명령어 / 스킬 / harness 플러그인 / compact→vault / grant 강제 / execution receipt / cleanup | `docs/agent-slash-commands.md` (+ `agents/grants/slash-command-grants.json` SSoT) |
| 보안 검토 / cross-cutting security 게이트 / `/security-review` | `docs/security-review.md` (+ `agents/engineering-agent/security-engineer/` 역할 계약 SSoT) |
| plugin/hook/skill/MCP/backend 분리 / provider(Claude·Codex·Gemini·Ollama) 배치 | `docs/plugin-taxonomy.md` + `docs/provider-capability-matrix.md` (vendor-neutral SSoT→projection) |
| git write 안전 (HOME/모호 경로·broad add 금지) | `docs/git-write-safety.md` (+ `agents/governance/git_path_safety.py`) |
| engineering-agent role council / tech-lead signoff / execution review | `docs/engineering-role-council-runtime.md` (+ council contract SSoT `apps/engineering-agent/src/yule_engineering/agents/council.py`) |
| 설계 결정 레인 / PM→gateway→tech-lead→engineer handoff / stack 비교·권고 / fake meeting·signoff 금지 | `docs/pm-techlead-lane.md` (+ 코드 SSoT `packages/forgekit-runtime/src/forgekit_runtime/decision_lane/`) |
| Hephaistos forge plan → 실제 승인 게이트 → execution receipt / forge governance | `docs/hephaistos-governance.md` (+ 코드 SSoT `packages/forgekit-runtime/src/forgekit_runtime/forge/`) |
| 모노레포 구조 / packages·apps / compat shim / 코드 이전 | `docs/monorepo-structure.md` (달성 구조 · 의존 hard rail · shim 카탈로그 · 남은 로드맵 SSoT) |
| ForgeKit 플랫폼 경계 / console=operator app / 코어 packages 분리 | `docs/forgekit-architecture-ownership.md` (owner 매트릭스 · import 경계 · 이전 우선순위 SSoT) |
| packages/* 분류 / 네이밍 충돌 / 새 기능 위치 / transitional debt | `docs/package-topology.md` (18 package 분류표 · migration matrix · 결정 트리 SSoT) |
| control-plane 방향 / 외부 프로젝트 흡수 / onboarding bootstrap / P0~P2 / Mac mini host | `docs/control-plane-architecture.md` (역할 · 소유 경계 · 우선순위 로드맵 SSoT) |

규칙:
- 위 표에 없는 토픽이면 그 작업과 가장 가까운 디렉터리의 `CLAUDE.md`,
  그 다음 `docs/<topic>.md` 순으로 찾는다.
- 같은 의미의 규칙이 두 문서에 있으면 **상위 문서가 우선**한다 (`CLAUDE.md`
  ≻ agent-specific `CLAUDE.md` ≻ `CODE_LAYOUT.md` ≻ `policies/reference`).
- 새 규칙을 추가할 때는 "자주 강제해야 하면 상위, 도메인 한정이면 하위" 를
  지켜 같은 규칙이 여러 문서에 흩어지지 않게 한다.

## 3. provider 별 진입 — 누가 메인이든 같은 경로

**규칙 본문은 어느 provider 문서에도 복제하지 않는다.** 메인 provider 가
무엇이든 읽기 순서는 동일하다: **이 파일(`AGENTS.md`) → root `CLAUDE.md`(공통 규칙
SSoT) → 작업 맥락 문서(§2)**. provider 문서는 "진입 + 기본 역할"만 얇게 둔다.

| provider | native 진입 파일 | 역할 안내 |
| --- | --- | --- |
| **Codex** | `AGENTS.md` (이 파일) | 기본 advisor / reviewer / patch proposer. executor 는 작업이 명시할 때만. |
| **Claude** | `CLAUDE.md` (= 공통 규칙 SSoT) | root `CLAUDE.md` + 작업 맥락 agent `CLAUDE.md` 가 일차 컨텍스트. |
| **Gemini** | [`GEMINI.md`](GEMINI.md) (얇은 projection → `CLAUDE.md`) | 기본 advisor — 분석 / 긴 맥락 / 계획 보조. |

- 안전/승인/파괴적 명령 등 **공통 규칙은 모두 root `CLAUDE.md`** 에 있다(provider 무관).
- skill projection: Claude `.claude/skills/` · Codex `.agents/skills/` · Gemini
  `.gemini/commands/` — SSoT 는 `skills/<id>.md` + grants, 손편집 금지([`docs/agent-slash-commands.md`](docs/agent-slash-commands.md)).
- `.codex/` / `.gemini/` 등은 로컬 실행 설정이며 공유 정책이 아니다.

> **`CODEX.md` 를 따로 두지 않는다.** `AGENTS.md` 가 곧 Codex 의 native 진입
> 파일이라 별도 wrapper 는 같은 역할을 중복할 뿐이다 — Codex 사용자는 이 파일에서
> 바로 root `CLAUDE.md` 로 라우팅된다. (provider 가 늘어도 같은 패턴: native 진입
> 파일은 얇게, 규칙은 `CLAUDE.md` 한 곳.)

## 4. 변경 시 반드시 동기화할 것

새 md 를 추가하거나 핵심 규칙을 옮기면 다음을 같이 갱신한다:

- 이 파일의 §2 표 (작업 맥락 → 문서 매핑)
- root `CLAUDE.md` 의 "읽기 우선순위 / 코딩 컨벤션" 섹션
- 영향받는 agent 의 `CLAUDE.md`
- 영향받는 모듈의 `CODE_LAYOUT.md`

> 같은 규칙이 여러 문서에 중복되면 일부만 갱신돼 silently 어긋난다.
> 한 곳에만 두고 나머지는 cross-link 한다.
