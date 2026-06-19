# forgekit-config

> ForgeKit **config core** — the owner of ForgeKit's on-disk shape. Pure, stdlib-first,
> so every app (`forgekit-console` + the sibling execution apps) shares one config
> contract instead of reaching into the console.

This package is part of the **WT2** extraction that moves ForgeKit core out of
`apps/forgekit-console` and into `packages/*`. Owner matrix + roadmap:
[`docs/forgekit-architecture-ownership.md`](../../docs/forgekit-architecture-ownership.md).

## 현재 보유

- `forgekit_config.paths` — runtime 데이터 home(`~/.forgekit`, `FORGEKIT_HOME` override)
  과 그 하위 경로(personal brain / starter pack / config / state / escalation ledger /
  operator inbox). 순수·무부작용(경로 계산만, 디렉터리 생성은 호출부 책임).
  - 구 경로 `forgekit_console.runtime_paths` 는 본 모듈을 가리키는 **forward-compat
    shim**(`sys.modules` alias, 객체 동일성 보존). 신규 코드는 `forgekit_config.paths`
    직접 import.

## 로드맵 (WT2 후속)

- agent identity(`forgekit_console.identity`) → `forgekit_config.identity`.
- config schema / persistence(`provider_config` 등 저장 로직의 공통 기반) 정리.

## 의존 규칙

- stdlib only. `apps/*` 를 import 하지 않는다(역방향 금지 hard rail).
- `forgekit-provider` / `forgekit-runtime` 가 본 package 를 의존한다(역은 금지).
