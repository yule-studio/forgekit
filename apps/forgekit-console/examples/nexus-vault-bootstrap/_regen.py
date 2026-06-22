"""Regenerate nexus-vault-bootstrap-evidence.txt — deterministic (tempdir vault, no net).

honest Obsidian vault inspect (not a fake) + opt-in KB scaffold + apply_bootstrap persistence.
Run from repo root with packages on PYTHONPATH; redirect stdout into the .txt.
Regression: tests/forgekit/test_nexus_vault.py.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from hephaistos import nexus_vault as nv
from hephaistos import nexus_ops as nops
from hephaistos import nexus_read as nx
from forgekit_config.paths import config_path


def banner(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main():
    print("ForgeKit Nexus root / Obsidian vault bootstrap — deterministic evidence (no fake)")
    print("재현: tests/forgekit/test_nexus_vault.py")

    banner("STEP 1 — inspect: 미연결 / 없음 / 빈 root (정직 상태, Obsidian 위조 없음)")
    print(f"  none      → {nv.inspect_vault(None).state}")
    print(f"  missing   → {nv.inspect_vault(Path('/no/such/vault')).state}")
    with tempfile.TemporaryDirectory() as empty:
        i = nv.inspect_vault(Path(empty))
        print(f"  empty dir → state={i.state} · is_obsidian={i.is_obsidian} · notes={i.note_count}")

    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as vault:
        env = {"FORGEKIT_HOME": home}
        v = Path(vault)
        (v / ".obsidian").mkdir()                       # a REAL Obsidian vault marker
        (v / "10-projects").mkdir()
        (v / "note.md").write_text("# n", encoding="utf-8")

        banner("STEP 2 — inspect 연결된 Obsidian vault (실 .obsidian + notes + KB layout)")
        i = nv.inspect_vault(v)
        print(f"  state={i.state} · is_obsidian={i.is_obsidian} · notes={i.note_count} · "
              f"KB present={list(i.present_dirs)} missing={list(i.missing_dirs)}")

        banner("STEP 3 — /nexus bootstrap <vault> --create (영속 + scaffold, .obsidian 미생성)")
        ok, msg = nops.apply_bootstrap(vault, create=True, env=env)
        print(f"  ok={ok}\n" + "\n".join("  " + ln for ln in msg.splitlines()))
        print(f"  KB dirs on disk: {[d for d in nv.KB_LAYOUT if (v / d).is_dir()]}")
        print(f"  .obsidian 그대로(부트스트랩이 만들지 않음): {(v / '.obsidian').is_dir()}")

        banner("STEP 4 — 재실행(restart): 저장된 config.json 로드 → 연결 유지 + vault-aware status")
        saved = json.loads(config_path(env).read_text(encoding="utf-8"))
        print(f"  config.json nexus_root = {saved.get('nexus_root')}")
        cs = nx.connection_status(env, saved)
        print(f"  connection_status: status={cs['status']} connected={cs['connected']} "
              f"is_vault={cs.get('is_vault')} note_count={cs.get('note_count')}")


if __name__ == "__main__":
    main()
