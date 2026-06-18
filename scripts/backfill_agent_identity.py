#!/usr/bin/env python3
"""Backfill agent identity fields into manifests from the canonical registry.

Deterministic + idempotent + non-destructive: for each ``agents/**/manifest.json``
whose ``id``/``role`` resolves to a canonical identity, it sets the identity fields
from the registry (preserving every other key). Manifests that don't resolve (container
/ example agents) are left untouched and reported. Re-running changes nothing.

Usage:  python3 scripts/backfill_agent_identity.py [--check] [--agents-root agents]
  --check : report drift / missing fields, write nothing (CI-friendly).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "forgekit-console" / "src"))

from forgekit_console.identity import registry as reg  # noqa: E402

_FIELDS = ("github_app_env_prefix", "git_author_name", "git_author_email",
           "vault_agent_id", "vault_css_class", "vault_callout", "vault_color_token",
           "identity_aliases")


def identity_fields(canonical_id: str) -> dict:
    ident = reg.resolve_identity(canonical_id)
    return {
        "github_app_env_prefix": ident.github_app_env_prefix,
        "git_author_name": ident.git_author_name,
        "git_author_email": ident.git_author_email,
        "vault_agent_id": ident.canonical_id,
        "vault_css_class": ident.vault_cssclass,
        "vault_callout": ident.vault_callout,
        "vault_color_token": ident.vault_color,
        "identity_aliases": list(ident.identity_aliases),
    }


def run(agents_root: Path, *, check: bool) -> int:
    changed, skipped, drift = [], [], []
    for mf in sorted(Path(agents_root).rglob("manifest.json")):
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        mid = str(data.get("id", "") or data.get("role", "")).strip()
        cid = reg.canonical_id(mid) or reg.canonical_id(str(data.get("role", "")).strip())
        if not cid:
            skipped.append(mid)
            continue
        want = identity_fields(cid)
        if all(data.get(k) == v for k, v in want.items()):
            continue  # already consistent (idempotent)
        if check:
            drift.append(mid)
            continue
        data.update(want)
        mf.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changed.append(mid)
    print(f"backfill: {'check' if check else 'applied'} — "
          f"changed={len(changed)} drift={len(drift)} skipped(unknown)={len(skipped)}")
    if skipped:
        print("  skipped (not in registry):", ", ".join(sorted(set(skipped))))
    if check and drift:
        print("  drift (need backfill):", ", ".join(sorted(set(drift))))
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--agents-root", default=str(ROOT / "agents"))
    args = ap.parse_args()
    raise SystemExit(run(Path(args.agents_root), check=args.check))
