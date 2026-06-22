# ForgeKit `/setup` 컨트롤플레인 부트스트랩 (SSoT)

> `/setup` 은 단일 provider wizard 가 아니라 **여러 onboarding lane 을 한 화면으로 묶는
> control-plane bootstrap** 이다. [`control-plane-architecture.md`](control-plane-architecture.md) §4
> 가 방향(provider + toolchain + nexus/vault + … → save → verify)의 SSoT 이고, 본 문서는 그
> 통합 표면의 **구현 SSoT** 다.
>
> 코드 SSoT:
> - 합성/표면: `apps/forgekit-console/src/forgekit_console/bootstrap.py`
> - lane core: `forgekit-provider-connect`(provider) · `hephaistos.nexus_read`(knowledge) ·
>   `forgekit-toolchain`(toolchain)
> - 회귀: `tests/forgekit/test_bootstrap.py` · evidence: `apps/forgekit-console/examples/bootstrap/`

## 1. 왜 통합 표면인가

각 onboarding lane(provider / knowledge / toolchain)은 이미 독립적으로 존재하고 **각자
정직하게** 상태를 보고하며 같은 canonical `~/.forgekit/config.json` 에 persist 한다. 그러나
`/setup` 은 provider lane 만 보여줬다 — 운영자는 "내 컨트롤플레인이 지금 어떤 상태인가"를
한 화면에서 볼 수 없었다.

`bootstrap.py` 는 그 **합성(composition)** 이다. core 로직을 새로 갖지 않고 각 lane 의 정직한
assessor 에 위임만 한다. 따라서 거짓 표면이 생길 여지가 없다 — 각 lane 의 진실을 그대로 모은다.

## 2. lane 과 정직 상태

| lane | core | 상태(정직) | blocking? |
| --- | --- | --- | --- |
| **provider** | `forgekit-provider-connect` (`wizard.assess`) | `live`(검증된 live 전송 lane 존재) / `setup-required`(없음) | **예** — readiness 를 결정 |
| **knowledge** (nexus/vault) | `hephaistos.nexus_read.connection_status` | `connected` / `not_connected` / `missing` / `blocked` (+`.obsidian` 감지 시 vault 표기) | 아니오 |
| **toolchain** | `forgekit-toolchain.detect_requirements` | `detected`(repo-local manifest) / `not_configured`(manifest 없음) / `unavailable`(미설치) | 아니오 |

**readiness 규칙:** `verdict = ready` ⟺ 모든 *blocking* lane 이 connected. 현재 blocking 은
provider 하나뿐 — 콘솔 live-submit 은 실제 live 전송(gemini API / ollama daemon)이 있어야
가능하고, claude/codex 는 CLI attach = `connected · routing only`(brain participant)이지 live
전송이 아니다. **no fake-live**: 검증 못 한 lane 은 `connected` 로 표기하지 않는다.

knowledge/toolchain 은 **non-blocking 정직 표면** — 미연결이어도 콘솔은 동작하지만 상태는
숨기지 않는다(green-wash 금지).

## 3. 명령 표면

```
/setup                 # 통합 부트스트랩 — 세 lane 한 화면 + verdict + 다음 액션
/setup apply [preset]  # 추천 provider preset(기본 four-brain)을 canonical config 에 저장 후 재검증
```

lane 별 연결/전환은 기존 명령이 그대로 담당한다(통합 표면은 그 상태를 모아 보여줄 뿐):

- provider: `/provider connect|disconnect|test|recommended|preset|route ...`
- knowledge: `/nexus set <vault/repo 경로>` · `/nexus clear`
- toolchain: `/toolchain detect|recommend|verify|drift|switch`

`/setup apply` 가 provider lane 만 one-shot 으로 쓰는 이유: provider 는 "추천 4-provider"
라는 단일 합리적 기본값(`four-brain` preset)이 있지만, nexus_root(운영자별 vault 경로)와
toolchain(repo 마다 다름)은 운영자 입력이 필요하므로 자동 추측하지 않는다(거짓 기본값 금지).

## 4. 영속성 (재실행 후 유지)

모든 lane 은 단일 canonical config(`forgekit_config.paths.config_path` → `~/.forgekit/config.json`,
`$FORGEKIT_HOME` 로 override)에 쓴다. `provider_ops`(provider preset/routing/fallback)와
`nexus_ops`(nexus_root)가 그 writer 다. `assess_bootstrap` 은 **별도 state 없이** 같은 config 를
읽으므로, 운영자가 콘솔을 재실행해도 저장된 설정(primary provider · slot_routing ·
fallback_policy · nexus_root)이 그대로 반영된다.

evidence(`examples/bootstrap/setup-bootstrap-evidence.txt`)의 STEP 3 가 *restart 시뮬레이션*으로
이를 증명한다 — in-memory state 없이 disk config 를 재독했을 때 verdict 가 `ready` 로 유지.

## 5. routing / fallback / actual-live 표시

`/setup apply` 가 저장하는 config 는 `slot_routing`(slot→provider)과 `fallback_policy`
(`slot_fallback_orders`)를 포함한다 — routing 과 fallback 의 SSoT 는
[`forgekit-provider-policy.md`](forgekit-provider-policy.md). 통합 표면은 provider lane 의
**declared(primary/brain) vs actual-live(검증된 전송)** 분리를 그대로 보여준다(`provider 상세`
블록): claude/codex 는 routing-only, gemini/ollama 만 live lane 으로 표기.

## 6. 테스트 / evidence

- 회귀: `python3 -m unittest tests.forgekit.test_bootstrap` — lane 별 정직 상태, no-fake,
  non-blocking, **재실행-후-유지**, 콘솔 라우팅을 fake probe + tempdir 로 검증.
- evidence: `apps/forgekit-console/examples/bootstrap/`(`_regen.py` 로 재생성, deterministic).
