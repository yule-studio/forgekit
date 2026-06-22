# `/provider route show` — declared → actual route resolution evidence

`route-resolution-evidence.txt` proves that `/provider route show`
(`forgekit_provider.policy.provider_surface.route_show_lines`) resolves **each** slot to
its ACTUAL live provider via the explicit fallback — instead of printing a bare
`unsupported_in_console` on every work slot that declares a CLI brain (claude/codex).

- **[A]** four-brain — `execution → codex` is shown reaching **gemini** (fallback live), so
  the operator doesn't read a routing-only declaration as "broken".
- **[B]** a slot whose declared + entire fallback are all routing-only shows an honest
  `○ (live 경로 없음)` plus the exact next action — no fake live, no dead-end.
- **[C]** no primary → `setup-required` with the `/setup` hint.
- **[D]** verified-live vs transport-capable — with a probe-backed `live_map` the surface shows
  `live(검증됨)` only when gemini's key / ollama's daemon is actually verified; unprobed says
  `live-capable(미검증)` and a failed probe says `○ 미검증/연결 필요`. **No fake-live.**

Regenerate (deterministic, pure, no net):

```
PYTHONPATH=$(for d in packages/*/src apps/*/src; do printf '%s:' "$PWD/$d"; done) \
  python3 apps/forgekit-console/examples/provider-route/_regen.py \
  > apps/forgekit-console/examples/provider-route/route-resolution-evidence.txt
```

Regression: `tests/forgekit/test_provider_surface.py::RouteShowResolutionTests`.
SSoT doc: `docs/forgekit-provider-policy.md` §2.2.
