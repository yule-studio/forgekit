# `/setup` 컨트롤플레인 부트스트랩 — evidence

`setup-bootstrap-evidence.txt` 는 통합 `/setup` 부트스트랩(provider · knowledge(nexus/vault)
· toolchain)을 **deterministic fake probe**(real IO 없음 — CI 재현 가능)로 처음부터 끝까지
캡처한 것이다. SSoT 문서: [`docs/forgekit-setup-bootstrap.md`](../../../../docs/forgekit-setup-bootstrap.md).

증명하는 완료 조건:

1. **한 화면 정직 집계** — provider/knowledge/toolchain 세 lane 이 각자 패키지의 정직한
   assessor 에 위임되어 한 화면에 표면화된다. 어떤 lane 도 green 으로 위장하지 않는다
   (STEP 1: provider `setup-required`, knowledge `not_connected`, toolchain `detected`).
2. **no fake provider** — claude/codex 는 CLI attach 라 `connected · routing only`(live 아님),
   live lane 은 실제 검증된 gemini/ollama 뿐. live 전송 provider 가 없으면 verdict 는
   정직하게 `setup-required`.
3. **live / unsupported / setup-required 정직 표면** — verdict 와 lane 상태가 실제 검증
   결과만 반영.
4. **재실행 후 설정 유지** — STEP 2 에서 `apply`(provider preset)와 `/nexus set`(nexus_root)이
   단일 canonical `~/.forgekit/config.json` 에 persist 되고, STEP 3 의 *restart 시뮬레이션*
   (in-memory state 없이 disk 재독)에서 그대로 살아남아 STEP 4 verdict 가 `ready` 로 뒤집힌다.
   같은 config 에 `slot_routing` + `fallback_policy`(routing/fallback)도 함께 보존된다.

재생성:

```
PYTHONPATH=<packages/*/src:apps/*/src> python3 \
  apps/forgekit-console/examples/bootstrap/_regen.py \
  > apps/forgekit-console/examples/bootstrap/setup-bootstrap-evidence.txt
```

(회귀 테스트: `python3 -m unittest tests.forgekit.test_bootstrap`.)
