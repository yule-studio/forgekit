"""Regenerate evidence-vault-evidence.txt — deterministic (tempdir goal store + tempdir vault).

goal store append-only evidence → curated authored Nexus vault notes (final-completion 축4).
Run from repo root with every package src on PYTHONPATH; redirect stdout into the .txt.
Regression: tests/forgekit/test_evidence_vault.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from nexus.vault import evidence as ev
from forgekit_goal.store import GoalStore
from forgekit_goal.models import Goal


def banner(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main():
    print("ForgeKit evidence → Nexus/vault 누적 — deterministic evidence (no fake)")
    print("goal store(append-only EvidenceRecord) → 인증 vault note. 재현: tests/forgekit/test_evidence_vault.py")

    with tempfile.TemporaryDirectory() as gstore, tempfile.TemporaryDirectory() as vault:
        env = {"FORGEKIT_HOME": gstore}
        st = GoalStore(env=env)
        g = Goal.create("Nexus provider runtime completion", intent="close axis 4 evidence accumulation")
        g = g.add_evidence("observation", "per-provider budget + mode/slot 분리 merged", ref="PR#343")
        g = g.add_evidence("execution", "evidence→vault bridge 구현", ref="nexus.vault.evidence")
        g = g.add_evidence("verification", "회귀 9 + 전체 1007 OK", ref="test_evidence_vault")
        st.save(g)
        print(f"\ngoal `{g.id}` — append-only evidence {len(g.evidence)}건 (goal store 영속)")

        banner("STEP 1 — vault 미연결 → not_connected (노트 위조 없음)")
        r0 = ev.accumulate_goal_evidence(g.id, vault_root="", env=env)
        print(f"  accumulate(vault_root='') → status={r0.status}, written={len(r0.written)}")

        banner("STEP 2 — vault 연결 → evidence 가 인증 vault note 로 누적")
        r1 = ev.accumulate_goal_evidence(g.id, vault_root=vault, env=env)
        print(f"  status={r1.status}, written={len(r1.written)}, skipped={len(r1.skipped)}")
        for sub in r1.written:
            print(f"    + {sub}")

        banner("STEP 3 — 재실행(restart) → append-only idempotent (덮어쓰지 않음)")
        fresh = GoalStore(env=env)   # 새 핸들 = 프로세스 재시작
        r2 = ev.accumulate_goal_evidence(g.id, vault_root=vault, env=env, store=fresh)
        print(f"  status={r2.status}, written={len(r2.written)}, skipped={len(r2.skipped)} (기존 노트 보존)")

        banner("STEP 4 — 누적된 vault note 내용 (인증 frontmatter + 5섹션, 실 evidence)")
        first = sorted(Path(vault).rglob("*.md"))[0]
        print(f"$ cat {first.relative_to(vault)}")
        print(first.read_text(encoding="utf-8").rstrip())


if __name__ == "__main__":
    main()
