# Corporate Structure — F15 Lockdown

> **Status**: F15 (issue #126) 에서 6 부서 + 19 역할 + PM skills 14 개 + governance test land. 본 doc 은 운영자 진입점.

## 1. 단일 진실

- **`policies/runtime/agents/corporate-org-chart.md`** — C-level / 부서 / 역할 매트릭스. 새 부서 / 역할 추가 시 항상 이 doc 부터 갱신.
- **`agents/<dept>/<role>/manifest.json`** — F11 AgentManifest schema 의 role 단위 정의.
- **`agents/<dept>/<role>/prompt.md`** — 역할의 책임 경계 + Hard rails + skills referencing.
- **`skills/<domain>/*.md`** — portable skill 카탈로그 (Claude / Gemini / Cursor 호환).

## 2. 부서 매트릭스 (F15 시점)

| C-level | 부서 디렉터리 | 역할 수 | 1차 책임 |
| --- | --- | --- | --- |
| CTO | `agents/engineering-agent/` | 7 | 코드 / 인프라 / 보안 / 품질 |
| CPO | `agents/product-agent/` | 3 | PRD / OKR / discovery |
| CMO | `agents/marketing-agent/` | 4 | 그로스 / 콘텐츠 / SEO / 브랜드 |
| CHRO | `agents/hr-agent/` | 3 | 채용 / 온보딩 / 코칭 |
| CFO | `agents/finance-agent/` | 1 | 예산 / cost / 재무 보고 |
| CRO | `agents/sales-cs-agent/` | 2 | 영업 / 고객 성공 |
| GC | `agents/legal-agent/` | 2 | 계약 / 프라이버시 |

자세한 row 는 `policies/runtime/agents/corporate-org-chart.md` 참조.

## 3. PM skills 카탈로그

`skills/pm/` — 14 skill. github.com/phuryn/pm-skills 패턴.

| Stage | Skills |
| --- | --- |
| Discovery | user-interview-prep / discovery-synth / persona-mapping |
| Strategy | okr-quarterly / roadmap-quarterly / metric-tree |
| Execution | prd-draft / feature-spec / experiment-design / prioritisation-rice |
| GTM | launch-checklist / positioning-message / beta-rollout |
| Cross | retrospective |

후속 도메인 (`skills/hr/`, `legal/`, `finance/`, `sales/`) 은 빈 디렉터리 — 후속 PR 에서 차례로 채운다.

## 4. Governance

- `tests/engineering/test_corporate_structure_governance.py` — 18 assertion / 5 TestCase. org-chart ↔ manifest ↔ plugin ↔ skill 의 chain 검증.
- 새 부서 / 역할 추가 시: org-chart row 추가 → manifest.json / prompt.md → governance test 자동 검증.
- Legacy 부서 (`engineering-agent`) 의 id 컨벤션 / plugin id 잔재 — 본 test 에서 의도적 skip. 별도 migration issue 에서 정렬.

## 5. 운영 정책

- **단일 책임**: 한 역할 — 하나 axis. cross-functional 필요 시 운영-리서치 forum thread.
- **HIGH risk plugin (live LLM / 외부 API write)** — 모든 부서 동일 — 운영자 명시 opt-in.
- **prompt_template_ref** — 모든 role manifest 필수 채움. governance test 가 깨진 link 차단.
- **plugins_required** — `plugins/<id>/` 에 실제 등록된 manifest id 만. governance test 가 미등록 reference 차단.

## 6. .env

새 6 부서 의 GitHub App 자격 envelope 은 `.env.example` 의 "#126 F15 — Multi-department GitHub Apps" 섹션. 실제 봇 등록 전엔 빈 채로 둔다 (advisory only).

## 7. 후속 작업

- Legacy `engineering-agent` 의 id 컨벤션 / plugin id 정렬 (별도 issue).
- hr / finance / legal / sales 도메인 skill 카탈로그 (각 5-10 skill 목표).
- 각 역할의 runner 모듈 (`agents.<dept>.<role>.runner`) 실제 land.
- 부서 별 Discord 채널 (issue 큐) + 운영-리서치 forum 의 routing.
