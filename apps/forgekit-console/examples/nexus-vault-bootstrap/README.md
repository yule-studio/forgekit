# Nexus root / Obsidian vault bootstrap — evidence

`nexus-vault-bootstrap-evidence.txt` 는 honest vault 검증 + opt-in scaffold 를 **deterministic**
(tempdir vault)으로 캡처. SSoT: [`docs/nexus-read-path.md`](../../../../docs/nexus-read-path.md)
"Vault bootstrap" 절. 코드 `packages/hephaistos/src/hephaistos/nexus_vault.py`, 회귀
`tests/forgekit/test_nexus_vault.py`.

증명:
1. **honest inspect** — not_connected/missing/empty/connected, 실제 `.obsidian/` 만 Obsidian 으로
   인정(위조 없음), bounded note count, KB layout present/missing.
2. **opt-in scaffold** — `create=False` 보고만, `create=True` 만 KB dir 생성. `.obsidian` 은 절대
   생성하지 않음(가짜 vault 금지). missing root 면 정직 실패.
3. **persistence** — `apply_bootstrap` 가 nexus_root 를 canonical config 에 영속 → 재실행(config 재로드)
   후에도 연결 + vault-aware status 유지.

재생성:

```
PYTHONPATH=<packages/*/src> python3 \
  apps/forgekit-console/examples/nexus-vault-bootstrap/_regen.py \
  > apps/forgekit-console/examples/nexus-vault-bootstrap/nexus-vault-bootstrap-evidence.txt
```
