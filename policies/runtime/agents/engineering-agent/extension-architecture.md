# engineering-agent — Extension Architecture (F11.1)

`engineering-agent` 의 plugin / agent 확장 구조에 대한 1페이지 운영 정책. 본 문서가 단일 진실. 위반 시 mistake_ledger BLOCK.

## 1. 구성요소

| 계층 | 위치 | 역할 |
| --- | --- | --- |
| Manifest | `plugins/<id>/manifest.json`, `agents/<id>/manifest.json` | static 식별 / 훅 / 위험도 선언 |
| Registry | `apps/engineering-agent/src/yule_engineering/agents/extension/plugin_registry.py`, `agent_registry.py` | in-memory lookup (id / hook / role) |
| Loader | `apps/engineering-agent/src/yule_engineering/agents/extension/loader.py` | fs discovery + lazy `importlib` |
| HookChain | `apps/engineering-agent/src/yule_engineering/agents/extension/hook_chain.py` | deterministic chain dispatch |

## 2. 새 plugin 추가 절차

1. `plugins/<plugin-id>/manifest.json` 작성. 필드 검증은 `manifest.py` 의 `_VALID_*` 규칙에 따른다 (id kebab-case, semver, dotted module_path).
2. `module_path` 는 dotted Python path 만. 빈 문자열이면 `load_plugin_module` 이 ValueError 로 거부.
3. 핸들러 노출 — 모듈에 둘 중 하나 제공:
   - `HOOK_HANDLERS: Mapping[HookEvent, Callable]`
   - 또는 `on_<lower-hook-name>(payload) -> HookResult`
4. `risk_class` 결정:
   - LOW / MEDIUM: 자동 활성
   - HIGH: 자동 활성 **금지**. `invoke_hook(..., allow_high_risk=("plugin-id",))` 로 명시 허용 필요
5. 회귀 추가 — `tests/agents/test_hook_chain.py` 또는 plugin 전용 테스트에 fake module fixture 케이스 1개 이상.
6. discovery 검증 — `discover_manifests(plugins_dir=..., agents_dir=...)` 가 새 manifest 를 인식하는지 단위 테스트.

## 3. 새 agent 추가 절차

1. `agents/<agent-id>/manifest.json` 작성. `role` 은 kebab-case, `plugins_required` 는 등록 plugin id 만.
2. `module_path` 비어있어도 manifest registration 자체는 허용 (entrypoint 미정 단계). 단 runtime 에서 호출 시점에 검증.
3. `agent_registry.register(manifest)` — 동일 id 재등록은 거부. 동일 role 멀티 agent 허용 (v2 successor 등).
4. `prompt_template_ref` 는 prompt 카탈로그의 안정 식별자. 폐기되면 deprecation note 동반 PR 필수.

## 4. HookChain 동작

```
invoke_hook(event, payload, *, plugin_registry, module_loader=None, allow_high_risk=())
```

- Registry 가 `plugins_for_hook(event)` 로 제공자 목록을 id 오름차순 반환 → 결정적.
- 각 plugin handler 는 직전 plugin 의 `modified_payload` 를 입력으로 받음 (chain 합성).
- 반환 `HookResult.level`:
  - `OK` / `WARN`: 다음 plugin 계속
  - `SKIP`: 해당 plugin 건너뛰고 chain 계속
  - `BLOCK`: 즉시 종료, 후속 plugin 미호출
  - `ERROR`: 즉시 종료 + 후속 mistake_ledger 기록 대상
- handler 가 raise → 자동으로 `ERROR` HookResult + signature `hook_chain.handler.exception`.
- handler 반환값이 `Mapping` 이면 OK + `modified_payload` 로 흡수 (편의 어댑터).
- handler 반환값이 `None` 이면 OK + payload 유지.

## 5. Risk class 매트릭스

| risk_class | 자동 활성 | 예시 plugin | 비고 |
| --- | --- | --- | --- |
| LOW | yes | docs / observability read-only | HookChain 통과 자유 |
| MEDIUM | yes | hookify (mistake_ledger), repo-map | 회귀 필수 |
| HIGH | **no** | paste-guard (outbound secret), credential rotation | `allow_high_risk` 명시 + 운영자 승인 |

## 6. Hard rails

- HIGH risk plugin 자동 활성 금지 — `allow_high_risk` 미포함 시 chain 에서 SKIP + `hook_chain.skip.high_risk_not_allowed` 로 기록.
- 알 수 없는 hook 이벤트는 manifest 단에서 `ManifestValidationError`. runtime 단에서는 handler 부재 → `SKIP` + `hook_chain.handler.missing`.
- `discover_manifests` 는 fail-fast — 단일 manifest validation 실패 시 `ManifestDiscoveryError` 로 중단. 등록 거부.
- `load_plugin_module` 의 ImportError 는 `hook_chain.module.load_failed` 로 mistake_ledger 등록 대상.
- destructive / force push / 보호 브랜치 직접 push 금지 (공통 안전 정책).

## 7. 관련 코드 / 테스트

- `apps/engineering-agent/src/yule_engineering/agents/extension/hook_chain.py`
- `apps/engineering-agent/src/yule_engineering/agents/extension/loader.py`
- `tests/agents/test_hook_chain.py`
- `tests/agents/test_extension_loader.py`
- 선행: PR #105 (F11 MVP — manifest + registry), issue #102 / #107.
