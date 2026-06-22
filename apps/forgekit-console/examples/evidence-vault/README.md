# evidence → Nexus/vault 누적 — evidence

`evidence-vault-evidence.txt` 는 final-completion 축4 "evidence 가 Nexus/vault 에 누적"의 lane-D
브리지를 **deterministic**(tempdir goal store + tempdir vault)으로 캡처한 것이다. SSoT:
[`docs/nexus-read-path.md`](../../../../docs/nexus-read-path.md) "Write path" 절. 코드
`packages/nexus/src/nexus/vault/evidence.py`, 회귀 `tests/forgekit/test_evidence_vault.py`.

증명:
1. **goal store → vault** — `forgekit_goal` 의 append-only `EvidenceRecord` 가 연결된 Nexus root
   아래 인증 vault note(frontmatter + 5섹션)로 누적된다.
2. **no fake nexus connection** — vault 미연결이면 `not_connected`(노트 0개), goal 미해결이면 `no_goal`.
3. **append-only / idempotent** — 재실행(restart)에도 기존 note 를 덮어쓰지 않고 skip, 파일은 disk 영속.

재생성:

```
PYTHONPATH=<packages/*/src:apps/*/src> python3 \
  apps/forgekit-console/examples/evidence-vault/_regen.py \
  > apps/forgekit-console/examples/evidence-vault/evidence-vault-evidence.txt
```
