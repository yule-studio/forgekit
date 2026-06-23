# Operator surfaces — reality matrix

Honest status of each console surface (working / partial / planned). Code:
`commands/registry.py` + `commands/router.py` + `tui/app.py`.

## Capability reality matrix
| capability | status | surface | evidence |
| --- | --- | --- | --- |
| Hephaistos resolve | working | `/resolve` `/skills` | `examples/hephaistos/`, `test_armory_breadth` |
| forge status | working | `/hephaistos` | `test_hephaistos_surface` |
| loadout verify (real env) | working | `/loadout <id>` | `test_hephaistos` |
| Nexus read | working (live read when connected; not_connected/missing/blocked/restricted honest) | `/resolve` nexus line | `examples/nexus-live-read/`, `test_nexus_live_read` |
| Nexus connection status | working | `/nexus` | `test_nexus_read`, `test_nexus_live_read` |
| Nexus connect/disconnect (operator) | working | `/nexus set <path>` · `/nexus clear` | `test_nexus_live_read` |
| usage ledger (live vs estimate) | working | `/usage` | `examples/usage/` |
| per-provider/model/mode usage breakdown | working | `/usage` | `examples/usage/per-provider-breakdown/` |
| runtime modes (real routing/budget/approval) | working | `/mode`, Shift+Tab | `examples/runtime-teeth/` |
| always-on daemon + safe-class autopilot | working (bounded) | `forgekit runtime serve`, `/autopilot` | `examples/runtime/`, `examples/autopilot/` |
| daemon heartbeat in console (state/tick/pid/kill-switch) | working | `/daemon` · `/daemon stop` | `examples/runtime-daemon/`, `test_runtime_daemon_surface` |
| agent identity (git author / app status) | working | `/whoami` | `test_identity_attribution` |
| discovery collectors (free-first) | working; YouTube/IG/Google planned | `/sources` | `examples/sources/` |
| discovery sweep → digest (왜/다음 질문) | working | `/discovery` | `examples/discovery/sweep-digest.json`, `test_discovery_sweep` |
| discovery 아이디어 누적/dedup/lifecycle (영속 ledger) | working | `/discovery` · `/discovery pending` | `examples/discovery/ledger-accumulation.json`, `test_discovery_ledger` |
| discovery 24h bounded loop (injected clock 누적 driver) | working (core/tick; serve 배선=autonomy lane) | `run_discovery_loop`/`discovery_loop_tick` | `examples/discovery/loop-report.json`, `test_discovery_loop` |
| discovery ask-me-later 후보 (교차 관측·신선도·score 기준) | working | `/discovery candidates` | `test_discovery_loop` |
| discovery evidence track (경쟁gap·self-improve → vault note) | working (연결 시; 미연결 정직 실패) | `/discovery evidence` | `examples/discovery/evidence-*.md`, `test_discovery_loop` |
| GeekNews 무료 radar 수집 (news.hada.io RSS) | working (free; config 토글) | `/sources` · config `discovery.geeknews` | `test_discovery_adoption` |
| discovery 도입 효율 검토 (6-class 분류 + 8축 + 3축 consult, 기본 collect-first) | working (no fake adoption; 기본 collect-first/hold) | `/discovery review <n>` | `examples/discovery/adoption-review.md`, `test_discovery_adoption` |
| discovery adopt-now → armory intake (adopted ≠ equipped) | working (3축 후 operator 결정 → promote_candidate adopted 판정; 장착=별도; raw=정직 reject) | `/discovery adopt <n>` | `examples/discovery/adoption-packet.json`, `test_discovery_adoption` |
| operator-tunable 수집 토픽 (HN/subreddits/GitHub/RSS) | working | config `discovery` 블록 | `test_discovery_ledger` |
| discovery brief → PM handoff (제안) | working | `/discovery promote <n>` | `test_discovery_ledger` |
| discovery brief → authored vault note | working (연결 시; 미연결 정직 실패) | `/discovery save <n>` | `examples/discovery/idea-brief-note.md` |
| discovery 아이디어 보류 | working | `/discovery park <n>` | `test_discovery_ledger` |
| design restricted source | blocked (real TCC) — honest | `/design` | `examples/design/` |
| red/blue planning (owned assets) | working (plan-only) | `/red-blue` | `examples/security/` |
| `/provider` console config (set primary / list / doctor) | working | `/provider [set <id>\|list\|doctor]` | `test_provider_surface` |
| provider link / unlink / slot route | working | `/provider [link\|unlink\|route show\|route set <slot> <id>]` | `test_provider_surface` |
| 4-provider brain preset (real config writer) | working | `/provider preset four-brain` | `test_four_brain_preset_and_routing` |
| `/provider` declared→actual per slot (brain vs live transport) | working | `/provider` (default_chat/execution: declared X → actual Y) | `test_four_brain_preset_and_routing` |
| provider onboarding wizard (connect 점검 → 추천 preset → save+verify) | working | `/setup` · `/setup apply` | `test_provider_connect` |
| provider connect 진단 (CLI attach·API key·daemon, no fake-live) | working | `/provider connect\|disconnect\|test\|recommended <id>` | `test_provider_connect` |
| toolchain 버전 감지/추천 (repo-local manifest·loadout→profile) | working | `/toolchain detect` · `/toolchain recommend <loadout>` | `examples/toolchain/`, `test_toolchain` |
| toolchain verify/drift (required vs ACTIVE, mise; no manager→honest) | working (needs mise to verify; manager-missing surfaced) | `/toolchain verify\|drift [<loadout>]` | `test_toolchain` |
| toolchain switch (mise; global/install **approval-gated**, no fake switch) | working (local 적용; gated 액션은 `--approve`) | `/toolchain switch [global] [--approve]` | `test_toolchain` |
| goal control plane (장기 목표 model/transition/append-only evidence/packet·child linkage, 영속) | working (surface CRUD; tick=runtime/GW4) | `/goal [list\|new <제목>\|show <id>\|activate <id>\|evidence <id>]` | `examples/goal/`, `test_goal_core`·`test_goal_surface`·`test_goal_tick` |
| in-console approve/deny + GW4-B 실행 연결 (awaiting_approval goal → operator 결정 → 실제 게이트 실행) | working (legal transition + decision evidence → GW4-B execute_approved_packet 호출: safe=실행됨(execution+verification 보존, surface 는 reload·비덮어쓰기), risky/blocked=실행 거부, 가짜 실행 0) | `/goal awaiting` · `/goal approve <id> [메모]` · `/goal deny <id> [메모]` | `examples/goal/approval.txt`, `test_goal_approval`·`test_execute_bridge` |
| cockpit status line (mode + 승인대기 goal 개수 + budget% 한눈에, polling 불필요) | working (persistent issue line; awaiting=실제 goal store 카운트(`goal_continuity_status`), budget=실제 ledger spend÷budget; turn-boundary 마다 refresh; store/ledger 실패 시 무배지로 degrade·가짜 숫자 0) | issue line (`/status`·`/mode`·Shift+Tab·매 turn) | `examples/cockpit-status/`, `test_tui_cockpit_status` |
| 선택/복사 visibility + drag-selection contrast (transcript native 선택 색·mode-aware copy 안내) | working (cross-widget `screen--selection`=brand accent-dim+밝은 FG, 런타임 실측 4.75:1 WCAG AA; help 안내가 mode-aware: inline=터미널 native drag, full=앱 drag+Ctrl+C; `/copy` 양쪽 공통; 가짜 0) | drag-select(full=앱/inline=터미널) · `/copy` · `/help` 선택·복사 섹션 | `examples/selection-contrast/`(evidence+SVG), `test_tui_transcript_selection`·`test_tui_selection_contrast` |
| multi-command submit (하나만 인식 차단 — 한 submit 의 여러 `/명령` 순차 실행) | working (모든 줄이 `/`로 시작할 때만 분리, free text/단일 명령 무변경; 각 명령 자체 transcript+process feed) | 멀티라인 submit (Ctrl+J 로 `/a`⏎`/b`) | `examples/cockpit-qa/cockpit-qa.txt`, `test_multi_command` |
| 도입 효율 검토 (외부 plugin/skill/collector/rule/workflow/tool adopt-now/collect-first/hold) | working (8점 검토+proposer/PM/tech-lead/specialist 3축+adopted≠equipped+ponytail verdict; collect-first/hold 장착 금지) | governance artifact (`decision_lane.adoption`) | `examples/cockpit-qa/cockpit-qa.txt`, `test_company_governance_upgrade` |

## Provider reality matrix
| provider | connection | live submit | usage basis | mode influence |
| --- | --- | --- | --- | --- |
| ollama (local, openai-compat) | zero-config if running | yes | **live** (native usage) | yes |
| openai / gemini (openai-compat) | api key | yes (when keyed) | live (when reported) else estimate | yes |
| claude / codex (CLI) | routable | **no** (`unsupported_in_console`) | n/a | routing only |

The operator **sets `primary_provider`**; no implicit local fallback (a reachable ollama is NOT used
unless `fallback_policy.implicit_local_fallback: true`). No provider → `setup-required`.

**Primary brain ≠ actual live transport.** A free-text submit does NOT hit `primary_provider`
directly — it follows `slot_routing.default_chat` resolved to the **actual live provider**
(declared → actual, explicit fallback surfaced). claude/codex stay **routing/brain participants**
(`unsupported_in_console`); **gemini/ollama are the live lane**. So `primary=claude` +
`default_chat=gemini` resolves the submit to gemini (live) — shown as `declared claude → actual
gemini`, never "Submitting to claude" then dying. Only when *every* linked provider is
`unsupported_in_console` does the submit honestly fail. With a live linked provider (gemini/ollama)
the brain is `configured-live`, NOT `configured-no-live`.

**Submit teeth (WT1 — `chat/service.py`):** the submit path builds an ordered attempt chain
`routing.submit_chain(cfg, default_chat, prefer=<gate target>)` = declared/routed head + the operator's
**explicit `slot_fallback_orders`** + (opt-in only) ollama. If the head is unusable
(`unsupported_in_console` / auth missing / transport error) it falls to the next provider in that order
and the receipt shows the honest hop (`· fallback claude→ollama`). Per-provider `model_overrides[<id>]`
is applied to the actual call (model precedence: override → global `model` → ollama-installed → id).
Evidence: `examples/provider-runtime-core/`, `test_provider_runtime_core`.

## Source reality matrix
| source | read status | restrictions | evidence |
| --- | --- | --- | --- |
| repo-local docs | working | — | `examples/sources/` |
| Nexus vault | working (live read when connected via `/nexus set` / `FORGEKIT_NEXUS_ROOT` / config; not_connected default) | restricted projection for non-allowed roles | `examples/nexus-live-read/` |
| restricted design source | blocked (TCC) | design role only (else projection) | `examples/design/` |
| Figma | planned seam | — | — |
| YouTube / Google / Instagram | planned | — | — |
| GitHub / HN / Reddit / RSS | working (injected fetcher; free-first) | rate/ToS | `examples/sources/` |

## Provider / Nexus / Always-on execution core (WT1–WT4) — honest status

This round wired the three execution axes from "seam present" to "real teeth", with
integration evidence. Truly working / partial / planned / blocked:

| axis | truly working | partial | planned / blocked |
| --- | --- | --- | --- |
| **Provider runtime** | primary/slot routing → submit; **explicit `slot_fallback_orders` fallback** on unusable head; **`model_overrides` per provider**; no-implicit-ollama; unsupported_in_console honest; usage_basis live/estimate | fallback uses the `default_chat` slot order (mode→slot for non-chat slots not yet split in the gate) | per-provider `budget_policy` not yet enforced (global budget only); claude/codex live submit (CLI) |
| **Nexus read** | live root via `/nexus set` / env / config; 5-way status (not_connected/exists/missing/blocked/restricted); real bounded `.md` reads; restricted → projection_only unless role-allowed; surfaced in `/nexus` `/resolve` `/skills` `/hephaistos` | role is a single context value (no per-command `--role`) | remote/non-filesystem Nexus transports |
| **Always-on daemon** | bounded serve loop (heartbeat/kill-switch/max-ticks/cooldown/signals); CLI `serve\|once\|status\|stop`; **console `/daemon` `/daemon stop`** read the same heartbeat; safe-class autopilot tick; approval/alert → inbox + opt-in desktop; systemd/launchd units | TUI shows status only (no in-console approve/deny UI) | auto-install of units; macOS lid-close suspends (Linux/systemd is the 1급 path — honest) |

**Integration evidence:** `examples/integration/scenarios.txt` runs three scenarios
(Spring Boot JWT · Next.js UI · Terraform ECS/K3s) through provider resolution
(+ fallback) → `/resolve` (Hephaistos + live Nexus line) → usage rollup → `/daemon`,
proving the axes compose. Test: `test_integration_provider_nexus_daemon`.

**Honesty rails kept:** no implicit ollama (no-config → setup-required, zero provider
calls); Nexus never fakes a read (not_connected/missing/blocked surfaced as-is); the
daemon `/daemon` surface shows honest `stopped` when no heartbeat exists.

### Cross-lane wave integration + consult merge gate

The integration/QA lane threads the **whole wave** in one flow (not each lane alone):
intake(discovery ledger) → Armory/Hephaistos(resolve→curated loadout) → Nexus(attachment,
honest not_connected) → provider projection(persist+reload) → runtime governance(execution
receipt + ledger). Representative scenarios: Spring Boot JWT (safe eng → **authorized**
receipt), Next.js design-system (non-engineering → **blocked**, no exec slot), Terraform+ECS
deploy (**destructive/L4 blocked**), discovery signal → curated packet, ponytail-like OSS CLI
candidate (intake only — Armory catalog stays curated). Test: `test_integration_wave_e2e`;
evidence: `examples/integration-wave/e2e.txt`.

| capability | status | surface | evidence |
| --- | --- | --- | --- |
| cross-lane wave E2E (intake→Armory→Hephaistos→Nexus→provider→receipt) | working | — (QA lane) | `examples/integration-wave/e2e.txt`, `test_integration_wave_e2e` |
| consult-required merge gate (design/review 변경은 consult artifact 없이 머지 금지) | working | merge-prep checklist | `test_consult_gate`, `decision_lane.consult_gate` |

Merge rule (SSoT — `docs/forgekit-integration-wave-qa.md`): consult required + artifact
missing = **merge 금지**; required + artifact(verdict/design-log/waive) = pass; not
required = pass. A *fake* consult (no consultee / no question) does not satisfy — content
validity is delegated to `validate_consult`.
