# forgekit-toolchain

Language / framework / runtime **version switching** for ForgeKit — the control-plane P0
companion to [`forgekit-provider-connect`](../forgekit-provider-connect). It answers
"which language runtime versions does this work need, what's actually active, and how do I
switch — safely?" without ever faking the answer.

[`mise`](https://mise.jdx.dev) is the first-class manager (asdf-compatible `.tool-versions`
+ `.mise.toml`). The manager is an injectable seam, so an asdf/other backend can be added
without touching detect / profile / plan.

## What it does

| stage | module | honest contract |
| --- | --- | --- |
| **detect** | `detect.py` | parse the repo's OWN manifests (`.tool-versions`, `.mise.toml`, `.nvmrc`, `.python-version`, `go.mod`, `package.json` engines, `pyproject.toml` …). Unreadable/odd file → skipped, never guessed. |
| **profile** | `profile.py` | a Hephaistos **loadout → toolchain profile** (e.g. `backend-java-local` → java 21 + gradle). Repo-local detection wins; the loadout fills gaps. |
| **manager** | `manager.py` | the ONLY place that touches mise. No mise → `available()` is False and every query returns "don't know"; it never fabricates a version or a switch. |
| **plan / verify / drift** | `plan.py` | `verify`/`drift` compare the profile against mise's *actual* active versions. `plan_switch` turns a profile into scoped manager commands. |
| **surface** | `surface.py` | console line builders + the approval-gated `apply_switch`. |

## Approval gating (hard rail)

Each `SwitchAction` carries a **scope**:

- `local` — `mise use node@20` writes the repo-local `./.mise.toml` pin. Reversible → runs without prompting.
- `install` — `mise install node@20` downloads/installs a runtime (network, disk) → **approval-gated**.
- `global` — `mise use --global …` changes the user-wide pin (affects other projects) → **approval-gated**.
- `destructive` — uninstall / prune → **approval-gated**.

`apply_switch(..., approve=False)` executes the `local` actions only and returns the plan for
the gated ones; gated actions run only with explicit `approve=True` (`/toolchain switch --approve`).
With no manager it refuses entirely — **no fake switch**.

## Console

```
/toolchain detect                      # repo-local version pins (real manifest parse)
/toolchain recommend <loadout>         # loadout → toolchain profile (+ repo merge)
/toolchain verify [<loadout>]          # required vs ACTIVE (mise current)
/toolchain drift   [<loadout>]         # just the mismatches
/toolchain switch  [global] [--approve]  # apply; global/install gated
```

Evidence: `apps/forgekit-console/examples/toolchain/switching-evidence.txt`.
Tests: `tests/forgekit/test_toolchain.py`. Design: `docs/control-plane-architecture.md` §5.2.
