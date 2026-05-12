# Plugin Catalog (F11+)

F11 (#102) 에서 도입된 PluginManifest 기반 11 개 플러그인의 운영 카탈로그.
"왜 도입했고, 어디서 동작하고, 운영자가 무엇을 끄고 켜야 하는지" 한 페이지
요약. 매니페스트 (json 스키마) 는 `plugins/<id>/manifest.json` 참조.

## 한눈에 보기

| 플러그인 | kind | risk | 도입 이유 한 줄 |
|---|---|---|---|
| [paste-guard](paste-guard.md) | guard | HIGH | outbound payload 의 secret/PII 차단 |
| [hookify](hookify.md) | learning | MEDIUM | preflight/completion/postmortem 훅 + mistake ledger |
| [repo-map](repo-map.md) | exploration | LOW | 작업 시작 전 레포 구조 파악 |
| [lsp-preflight](lsp-preflight.md) | guard | MEDIUM | 정적 분석 (mypy/eslint/...) 기반 사전 차단 |
| [claude-mem](claude-mem.md) | learning | MEDIUM | F10 5-소스 long-term memory 통합 |
| [tool-call-gate](tool-call-gate.md) | guard | HIGH | F12 5×5 autonomy × risk 매트릭스 게이트 |
| [live-llm-editor](live-llm-editor.md) | delivery | HIGH | F4 live coding executor (env-gated default OFF) |
| [live-research-provider](live-research-provider.md) | delivery | MEDIUM | F5 live research fetcher (host allow-list) |
| [discussion-response](discussion-response.md) | delivery | MEDIUM | Discord 멤버 봇 응답 페이로드 빌더 |
| [auto-merge-decider](auto-merge-decider.md) | delivery | HIGH | PR 자동 머지 결정 (yes/no/wait) |
| [obsidian-vault-push](obsidian-vault-push.md) | delivery | MEDIUM | vault 산출물의 자동 git push |

## 도입 흐름 (시계열)

| Tier | 시점 | 플러그인 | 정책 commit |
|---|---|---|---|
| 1 | F1–F3 (pre-F11) | paste-guard, hookify, repo-map | #102 PluginManifest 도입 시 흡수 |
| 2 | F4 (#148) | live-llm-editor | 환경변수 default OFF, claude-cli 전용 |
| 2 | F5 (#148) | live-research-provider | host allow-list / rate limit 강제 |
| 2 | F7–F8 | discussion-response, obsidian-vault-push | Discord/vault 출력 표준화 |
| 3 | F9–F10 (#150) | lsp-preflight, claude-mem | 정적 분석 + 장기 기억 |
| 3 | F12 (#151) | tool-call-gate | autonomy × risk 매트릭스 |
| 3 | F13+ | auto-merge-decider | PR 머지 자율성 게이트 |

## Hard rails (모든 플러그인 공통)

- `paste_guard_required: true` 가 박힌 플러그인은 outbound 페이로드가
  PasteGuard 를 먼저 통과한 뒤에만 호출된다 (manifest validation 강제).
- `module_path` 는 dotted Python identifier 만 허용. 파일 경로 금지.
- `hooks_provided` / `hooks_consumed` 는 HookEvent enum 의 멤버만 가능.
- `risk_class HIGH` 플러그인은 outbound / secret / 자율 머지 등 비가역
  영역만 표시. 운영자 명시 승인 + autonomy gate 통과 필요.

## 플러그인 추가 절차

1. `plugins/<id>/manifest.json` 작성 (F11 PluginManifest 스키마 준수)
2. 본 카탈로그 한 줄 + `policies/runtime/plugins/<id>.md` 작성
3. 회귀 테스트 (`tests/extension/test_plugin_manifest_*` 갱신)
4. 도입 결정 노트 (`vault/.../decisions/<date>_decision_plugin-<id>.md`)
   — 사용자 vault, 운영자 수기 또는 자동화 task-log 와 함께
