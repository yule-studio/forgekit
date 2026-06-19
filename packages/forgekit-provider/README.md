# forgekit-provider

> ForgeKit **provider core** — "which provider answers, under what policy, at what usage
> cost". Pure, stdlib-first, so every app shares one provider contract instead of reaching
> into the console.

Part of the **WT2** extraction moving ForgeKit core out of `apps/forgekit-console` into
`packages/*`. Owner matrix + roadmap:
[`docs/forgekit-architecture-ownership.md`](../../docs/forgekit-architecture-ownership.md).

## 보유 모듈

| 모듈 | 책임 |
| --- | --- |
| `forgekit_provider.providers` | provider spec 카탈로그 (builtins / contract / registry) |
| `forgekit_provider.policy` | provider config/ops/policy/surface · routing · recommend · setup_state · main_profile · usage_policy · runtime_mode · auto_mode |
| `forgekit_provider.chat` | submit service(routing→실호출) · models · policy_gate · usage_parse |
| `forgekit_provider.usage` | usage ledger (live vs estimate, provider/model/mode 별) |
| `forgekit_provider.brain` | brain(=primary+linked) 구성 / pack |

## 옛 경로 (compat shim)

구 `forgekit_console.{providers,policy,chat,usage,brain}` 는 본 package 의 동명 모듈을
가리키는 forward-compat shim 이다 (`forgekit_console._compat.alias_package`, `sys.modules`
별칭으로 서브모듈까지 객체 동일성 보존). 신규 코드는 `forgekit_provider.*` 직접 import.

## 의존 규칙

- `forgekit-config`(paths)만 의존. 그 외 `apps/*` import 금지(역방향 hard rail).
- 내부 의존 방향: `chat → policy → providers`, `usage`·`brain` 은 독립(+config).
