"""``yule memory`` subcommand wiring.

Indexes the Obsidian vault, repo policy docs, and recent workflow
session artifacts into a local FTS5 SQLite store, then exposes a search
front-end. Network-free — this is the deterministic layer the retrieval
wiring (Phase 3) sits on top of.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from ..agents.obsidian.writer import ENV_VAULT_PATH, resolve_vault_root
from ..agents.workflow_state import list_sessions
from yule_memory import (
    open_memory_index,
    reindex_paths,
    reindex_workflow_sessions,
    search as memory_search,
)
from yule_memory.models import (
    SOURCE_OBSIDIAN,
    SOURCE_POLICY,
    SOURCE_WORKFLOW,
)


def run_memory_reindex_command(
    repo_root: Path,
    *,
    vault_path: Optional[str],
    skip_obsidian: bool,
    skip_policies: bool,
    skip_workflow: bool,
    json_output: bool,
) -> int:
    repo_root = repo_root.resolve()
    counts: dict[str, int] = {
        SOURCE_OBSIDIAN: 0,
        SOURCE_POLICY: 0,
        SOURCE_WORKFLOW: 0,
    }
    notes: List[str] = []

    with open_memory_index(repo_root=repo_root) as index:
        if not skip_obsidian:
            try:
                vault_root = resolve_vault_root(override=vault_path)
            except Exception as exc:  # noqa: BLE001 - graceful skip without vault
                notes.append(f"obsidian skipped: {exc}")
            else:
                counts[SOURCE_OBSIDIAN] = reindex_paths(
                    paths=[vault_root],
                    source_kind=SOURCE_OBSIDIAN,
                    index=index,
                    base_dir=vault_root,
                )

        if not skip_policies:
            policy_root = repo_root / "policies"
            agent_root = repo_root / "agents"
            counts[SOURCE_POLICY] = reindex_paths(
                paths=[policy_root, agent_root, repo_root / "README.md"],
                source_kind=SOURCE_POLICY,
                index=index,
                base_dir=repo_root,
            )

        if not skip_workflow:
            try:
                sessions = list_sessions(limit=200)
            except Exception as exc:  # noqa: BLE001 - workflow cache may be empty
                notes.append(f"workflow skipped: {exc}")
                sessions = ()
            counts[SOURCE_WORKFLOW] = reindex_workflow_sessions(
                sessions=sessions,
                index=index,
            )

        total = index.count_documents()

    if json_output:
        print(
            json.dumps(
                {"counts": counts, "total": total, "notes": notes},
                ensure_ascii=False,
            )
        )
        return 0

    for source, count in counts.items():
        print(f"{source:>10}: {count} indexed")
    print(f"     total: {total}")
    for note in notes:
        print(f"note: {note}")
    return 0


def run_memory_search_command(
    repo_root: Path,
    *,
    query: str,
    limit: int,
    source_kind: Optional[str],
    role: Optional[str],
    note_kind: Optional[str],
    task_type: Optional[str],
    json_output: bool,
    boost: bool = False,
) -> int:
    if not query.strip():
        print("error: search query must not be empty", file=sys.stderr)
        return 1

    results = memory_search(
        query,
        limit=limit,
        source_kind=source_kind,
        role=role,
        note_kind=note_kind,
        task_type=task_type,
        repo_root=repo_root,
    )

    # Optional reuse-boost visibility (memory-policy section 4): re-rank by boost
    # and surface boost_score + why_retrieved per hit.
    boost_info: dict = {}
    if boost:
        from yule_engineering.agents.harness.retrieval_boost import boost_for

        scored = []
        for r in results:
            b, reasons = boost_for(r.document)
            boost_info[r.document.doc_id] = {"boost_score": b, "why_retrieved": list(reasons)}
            scored.append((r.score - b, r))  # bm25 lower is better → subtract boost
        scored.sort(key=lambda t: t[0])
        results = [r for _eff, r in scored]

    if json_output:
        payload = [
            {
                "doc_id": r.document.doc_id,
                "source_kind": r.document.source_kind,
                "title": r.document.title,
                "path": r.document.path,
                "role": r.document.role,
                "task_type": r.document.task_type,
                "note_kind": r.document.note_kind,
                "tags": list(r.document.tags),
                "score": r.score,
                "snippet": r.snippet,
                **({"boost": boost_info.get(r.document.doc_id)} if boost else {}),
            }
            for r in results
        ]
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if not results:
        print("(no results)")
        return 0
    for hit in results:
        doc = hit.document
        path_or_id = doc.path or doc.doc_id
        print(f"[{doc.source_kind}] {doc.title}")
        print(f"  path: {path_or_id}")
        meta_bits = []
        if doc.role:
            meta_bits.append(f"role={doc.role}")
        if doc.task_type:
            meta_bits.append(f"task_type={doc.task_type}")
        if doc.note_kind:
            meta_bits.append(f"kind={doc.note_kind}")
        if doc.tags:
            meta_bits.append(f"tags={','.join(doc.tags)}")
        meta_bits.append(f"score={hit.score:.3f}")
        if boost:
            bi = boost_info.get(hit.document.doc_id) or {}
            meta_bits.append(f"boost=+{bi.get('boost_score', 0)}")
        print("  " + " · ".join(meta_bits))
        if boost:
            why = (boost_info.get(hit.document.doc_id) or {}).get("why_retrieved") or []
            if why:
                print(f"  why: {', '.join(why)}")
        if hit.snippet:
            print(f"  > {hit.snippet}")
        print()
    return 0
