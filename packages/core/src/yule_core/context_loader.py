from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


class ContextError(Exception):
    """Raised when agent context cannot be loaded."""


# Canonical load order (high → low specificity), per
# ``policies/runtime/common/context-loading.md`` and ``AGENTS.md`` §1:
#
#   1. AGENTS.md          — repo entrypoint / doc-navigation (required)
#   2. CLAUDE.md          — global rules for every agent/task (required)
#   3. agent instruction  — active agent's instruction_entry (required)
#   4. role instruction   — active role's instruction_entry (optional)
#   5. policies           — manifest-listed policy files (optional)
#
# A missing *optional* file is reported as a warning and the load continues
# (context-loading policy: "report it and continue with the available
# context"). A missing *required* file raises ``ContextError``.
ENTRYPOINT_DOC = "AGENTS.md"
ROOT_INSTRUCTIONS_DOC = "CLAUDE.md"

LABEL_ENTRYPOINT = "entrypoint"
LABEL_ROOT = "root_instructions"
LABEL_AGENT = "agent_instructions"
LABEL_ROLE = "role_instructions"
LABEL_POLICY = "policy"


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
    role_id: Optional[str] = None
    role_manifest: Optional[Dict[str, Any]] = None

    def documents_by_label(self, label: str) -> Sequence[ContextDocument]:
        return tuple(doc for doc in self.documents if doc.label == label)

    def has_role_instructions(self) -> bool:
        return any(doc.label == LABEL_ROLE for doc in self.documents)


def load_agent_context(
    repo_root: Path,
    agent_id: str,
    *,
    role_id: Optional[str] = None,
) -> LoadedContext:
    """Load the layered instruction context for *agent_id* (and *role_id*).

    The load order follows ``AGENTS.md`` §1 and the context-loading policy:
    ``AGENTS.md`` → root ``CLAUDE.md`` → agent ``instruction_entry`` →
    (when *role_id* is given) role ``instruction_entry`` → policies.

    Two cases are handled explicitly:

      * *role_id is None* — no role selected yet. Only the agent layer is
        loaded; no role document and no role warning are emitted.
      * *role_id is given* — the role layer is loaded from
        ``agents/<agent_id>/<role_id>/manifest.json``. A missing role
        manifest / ``instruction_entry`` / file is recorded as a warning
        (philosophy preserved) instead of raising, because role contracts
        are an additive layer over the always-present agent layer.
    """

    repo_root = repo_root.resolve()
    manifest_path = repo_root / "agents" / agent_id / "manifest.json"
    manifest = _load_manifest(manifest_path)
    documents: List[ContextDocument] = []
    warnings: List[str] = []

    # 1. entrypoint + 2. root rules (both required — they always exist at the
    # repo root and anchor every agent/role load).
    documents.append(_read_required_document(repo_root, LABEL_ENTRYPOINT, ENTRYPOINT_DOC))
    documents.append(_read_required_document(repo_root, LABEL_ROOT, ROOT_INSTRUCTIONS_DOC))

    # 3. active agent instruction_entry (required).
    instruction_entry = manifest.get("instruction_entry")
    if not isinstance(instruction_entry, str) or not instruction_entry:
        raise ContextError(f"{_display_path(manifest_path, repo_root)} must define instruction_entry")
    documents.append(_read_required_document(repo_root, LABEL_AGENT, instruction_entry))

    # 4. active role instruction_entry (optional — only when a role is selected).
    role_manifest: Optional[Dict[str, Any]] = None
    if role_id:
        role_manifest, role_warnings = _load_role_layer(
            repo_root, agent_id, role_id, documents
        )
        warnings.extend(role_warnings)

    # 5. policies listed in the agent manifest (optional).
    policies = manifest.get("policies", [])
    if not isinstance(policies, list):
        raise ContextError(f"{_display_path(manifest_path, repo_root)} policies must be a list")

    for policy_path in policies:
        if not isinstance(policy_path, str):
            warnings.append("Skipping non-string policy path in agent manifest.")
            continue

        document = _read_optional_document(repo_root, LABEL_POLICY, policy_path)
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
        role_id=role_id,
        role_manifest=role_manifest,
    )


def _load_role_layer(
    repo_root: Path,
    agent_id: str,
    role_id: str,
    documents: List[ContextDocument],
) -> tuple[Optional[Dict[str, Any]], List[str]]:
    """Append the role instruction document; return (role_manifest, warnings).

    Never raises — a malformed/missing role layer degrades to warnings so the
    agent layer (already loaded) still wins. This keeps the "missing file →
    report and continue" philosophy for the additive role tier.
    """

    warnings: List[str] = []
    role_manifest_path = repo_root / "agents" / agent_id / role_id / "manifest.json"
    if not role_manifest_path.exists():
        warnings.append(
            f"Missing role manifest for {agent_id}/{role_id}: "
            f"{_display_path(role_manifest_path, repo_root)}"
        )
        return None, warnings

    try:
        role_manifest = json.loads(role_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        warnings.append(f"Invalid JSON in role manifest {agent_id}/{role_id}: {exc}")
        return None, warnings
    if not isinstance(role_manifest, dict):
        warnings.append(f"Role manifest must be a JSON object: {agent_id}/{role_id}")
        return None, warnings

    role_entry = role_manifest.get("instruction_entry")
    if not isinstance(role_entry, str) or not role_entry:
        warnings.append(
            f"Role {agent_id}/{role_id} manifest missing instruction_entry"
        )
        return role_manifest, warnings

    document = _read_optional_document(repo_root, LABEL_ROLE, role_entry)
    if document is None:
        warnings.append(f"Missing role instruction file: {role_entry}")
        return role_manifest, warnings

    documents.append(document)
    return role_manifest, warnings


def render_context(loaded_context: LoadedContext) -> str:
    repo_root = loaded_context.manifest_path.parents[2]
    role_line = (
        f"Active Role: {loaded_context.role_id}"
        if loaded_context.role_id
        else "Active Role: (none — role not yet selected)"
    )
    lines: List[str] = [
        f"# Loaded Context: {loaded_context.agent_id}",
        "",
        f"Manifest: {_display_path(loaded_context.manifest_path, repo_root)}",
        role_line,
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
                f"## {document.label}: {_display_path(document.path, repo_root)}",
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
