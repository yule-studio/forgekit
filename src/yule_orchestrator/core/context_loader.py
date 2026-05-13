from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence


class ContextError(Exception):
    """Raised when agent context cannot be loaded."""


@dataclass(frozen=True)
class ContextDocument:
    label: str
    path: Path
    content: str


@dataclass(frozen=True)
class LoadedContext:
    agent_id: str
    manifest_path: Path
    manifest: Dict[str, Any]
    documents: Sequence[ContextDocument]
    warnings: Sequence[str]


def load_agent_context(repo_root: Path, agent_id: str) -> LoadedContext:
    repo_root = repo_root.resolve()
    manifest_path = repo_root / "agents" / agent_id / "manifest.json"
    manifest = _load_manifest(manifest_path)
    documents: List[ContextDocument] = []
    warnings: List[str] = []

    documents.append(_read_required_document(repo_root, "root_instructions", "CLAUDE.md"))

    instruction_entry = manifest.get("instruction_entry")
    if not isinstance(instruction_entry, str) or not instruction_entry:
        raise ContextError(f"{_display_path(manifest_path, repo_root)} must define instruction_entry")
    documents.append(_read_required_document(repo_root, "agent_instructions", instruction_entry))

    policies = manifest.get("policies", [])
    if not isinstance(policies, list):
        raise ContextError(f"{_display_path(manifest_path, repo_root)} policies must be a list")

    for policy_path in policies:
        if not isinstance(policy_path, str):
            warnings.append("Skipping non-string policy path in agent manifest.")
            continue

        document = _read_optional_document(repo_root, "policy", policy_path)
        if document is None:
            warnings.append(f"Missing policy file: {policy_path}")
            continue
        documents.append(document)

    return LoadedContext(
        agent_id=agent_id,
        manifest_path=manifest_path,
        manifest=manifest,
        documents=documents,
        warnings=warnings,
    )


def render_context(loaded_context: LoadedContext) -> str:
    lines: List[str] = [
        f"# Loaded Context: {loaded_context.agent_id}",
        "",
        f"Manifest: {_display_path(loaded_context.manifest_path, loaded_context.manifest_path.parents[2])}",
        f"Execution Mode: {loaded_context.manifest.get('execution_mode', 'unknown')}",
        f"Default Executor: {loaded_context.manifest.get('default_executor', 'unknown')}",
        "",
    ]

    if loaded_context.warnings:
        lines.append("## Warnings")
        for warning in loaded_context.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    for document in loaded_context.documents:
        lines.extend(
            [
                f"## {document.label}: {_display_path(document.path, loaded_context.manifest_path.parents[2])}",
                "",
                document.content.rstrip(),
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _load_manifest(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ContextError(f"Agent manifest not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContextError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ContextError(f"Agent manifest must be a JSON object: {path}")

    return data


def _read_required_document(repo_root: Path, label: str, relative_path: str) -> ContextDocument:
    path = _safe_join(repo_root, relative_path)
    if not path.exists():
        raise ContextError(f"Required context file not found: {relative_path}")
    return ContextDocument(label=label, path=path, content=path.read_text(encoding="utf-8"))


def _read_optional_document(repo_root: Path, label: str, relative_path: str) -> ContextDocument | None:
    path = _safe_join(repo_root, relative_path)
    if not path.exists():
        return None
    return ContextDocument(label=label, path=path, content=path.read_text(encoding="utf-8"))


def _safe_join(repo_root: Path, relative_path: str) -> Path:
    path = (repo_root / relative_path).resolve()
    try:
        path.relative_to(repo_root)
    except ValueError as exc:
        raise ContextError(f"Context path escapes repository root: {relative_path}") from exc
    return path


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)
