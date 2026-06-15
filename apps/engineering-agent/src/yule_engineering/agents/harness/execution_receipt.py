"""Execution proof — the per-run receipt (issue #185 follow-up, item D).

An :class:`ExecutionReceipt` is "what this run actually loaded and what it was
allowed to do". It binds together the four enforcement surfaces:

  * the layered context load (:class:`LoadedContext` — docs + policies + the
    selected agent/role + warnings),
  * the grant table (granted skills/commands + blocked/missing capabilities,
    via :mod:`grant_enforcement`),
  * the selected runner,
  * compaction + cleanup statuses (:class:`CompactionReceipt` /
    :class:`CleanupReceipt`).

It renders to both human text and a JSON-able dict so at least one CLI / debug
surface (``yule engineer receipt``) can print it and a test can assert its
contents. It is pure-Python and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from yule_core.context_loader import (
    LABEL_AGENT,
    LABEL_ENTRYPOINT,
    LABEL_POLICY,
    LABEL_ROLE,
    LABEL_ROOT,
    LoadedContext,
)

from .cleanup import CleanupReceipt
from .compaction_protocol import CompactionReceipt
from .grant_enforcement import (
    CapabilityKind,
    GrantDecision,
    GrantVerdict,
    evaluate_capability,
)
from .slash_command_grants import GrantTable

_DOC_LABEL_ORDER = (LABEL_ENTRYPOINT, LABEL_ROOT, LABEL_AGENT, LABEL_ROLE)


@dataclass(frozen=True)
class ExecutionReceipt:
    selected_agent: str
    selected_role: Optional[str]
    selected_runner: Optional[str]
    loaded_docs: Tuple[Tuple[str, str], ...]       # (label, rel_path)
    loaded_policies: Tuple[str, ...]               # rel_path
    granted_commands: Tuple[str, ...]
    granted_skills: Tuple[str, ...]
    capability_decisions: Tuple[GrantDecision, ...]
    warnings: Tuple[str, ...]
    compaction_status: str
    cleanup_status: str
    compaction: Optional[CompactionReceipt] = None
    cleanup: Optional[CleanupReceipt] = None
    security_status: str = "not_evaluated"
    security: Optional[Any] = None  # SecurityReviewDecision (duck-typed)
    token_efficiency: Optional[Mapping[str, Any]] = None  # saved tokens on the input hot path

    @property
    def blocked_or_missing(self) -> Tuple[GrantDecision, ...]:
        """Requested capabilities that were not cleanly allowed."""

        return tuple(d for d in self.capability_decisions if d.verdict is not GrantVerdict.ALLOW)

    def to_dict(self) -> dict:
        return {
            "selected_agent": self.selected_agent,
            "selected_role": self.selected_role,
            "selected_runner": self.selected_runner,
            "loaded_docs": [
                {"label": label, "path": path} for (label, path) in self.loaded_docs
            ],
            "loaded_policies": list(self.loaded_policies),
            "granted_commands": list(self.granted_commands),
            "granted_skills": list(self.granted_skills),
            "capability_decisions": [
                {
                    "kind": d.kind.value,
                    "capability": d.capability,
                    "verdict": d.verdict.value,
                    "reason": d.reason,
                }
                for d in self.capability_decisions
            ],
            "blocked_or_missing": [
                {"capability": d.capability, "verdict": d.verdict.value, "reason": d.reason}
                for d in self.blocked_or_missing
            ],
            "warnings": list(self.warnings),
            "compaction_status": self.compaction_status,
            "cleanup_status": self.cleanup_status,
            "security_status": self.security_status,
            "compaction": self.compaction.to_dict() if self.compaction else None,
            "cleanup": self.cleanup.to_dict() if self.cleanup else None,
            "security": self.security.to_dict() if self.security is not None else None,
            "token_efficiency": dict(self.token_efficiency) if self.token_efficiency else None,
        }

    def render(self) -> str:
        lines: List[str] = ["# Execution Receipt", ""]
        lines.append(f"- selected agent: {self.selected_agent}")
        lines.append(f"- selected role: {self.selected_role or '(none — role not selected)'}")
        lines.append(f"- selected runner: {self.selected_runner or '(unset)'}")
        lines.append("")

        lines.append("## Loaded docs")
        for label, path in self.loaded_docs:
            lines.append(f"- [{label}] {path}")
        lines.append("")

        lines.append("## Loaded policies")
        if self.loaded_policies:
            for path in self.loaded_policies:
                lines.append(f"- {path}")
        else:
            lines.append("- (none)")
        lines.append("")

        lines.append("## Granted skills / commands")
        lines.append(f"- commands: {', '.join(self.granted_commands) or '(none)'}")
        lines.append(f"- skills: {', '.join(self.granted_skills) or '(none)'}")
        lines.append("")

        lines.append("## Blocked or missing skills")
        if self.blocked_or_missing:
            for d in self.blocked_or_missing:
                lines.append(f"- {d.surface()}")
        else:
            lines.append("- (none requested / all granted)")
        lines.append("")

        lines.append(f"## Compaction status: {self.compaction_status}")
        if self.compaction is not None:
            c = self.compaction
            lines.append(
                f"- session={c.session_id} note={c.task_log_note_path or '-'} "
                f"saved_tokens={c.saved_tokens} committed={c.committed}"
            )
        lines.append("")

        lines.append(f"## Cleanup status: {self.cleanup_status}")
        if self.cleanup is not None:
            cl = self.cleanup
            lines.append(
                f"- reclaimable_bytes={cl.reclaimable_bytes} deleted={cl.deleted_count} "
                f"protected={len(cl.protected)} approval_needed={len(cl.approval_needed)}"
            )
        lines.append("")

        if self.token_efficiency:
            lines.append("## Token efficiency")
            for k, v in self.token_efficiency.items():
                lines.append(f"- {k}: {v}")
            lines.append("")

        lines.append(f"## Security review: {self.security_status}")
        if self.security is not None:
            lines.append(f"- {self.security.surface()}")
            for reason in getattr(self.security, "reasons", ()) or ():
                lines.append(f"  - {reason}")
        lines.append("")

        lines.append("## Warnings")
        if self.warnings:
            for w in self.warnings:
                lines.append(f"- {w}")
        else:
            lines.append("- (none)")
        return "\n".join(lines).rstrip() + "\n"


def build_execution_receipt(
    loaded_context: LoadedContext,
    grant_table: GrantTable,
    *,
    actor_id: Optional[str] = None,
    selected_runner: Optional[str] = None,
    requested_capabilities: Sequence[str] = (),
    compaction: Optional[CompactionReceipt] = None,
    cleanup: Optional[CleanupReceipt] = None,
    security: Optional[Any] = None,
    token_efficiency: Optional[Mapping[str, Any]] = None,
) -> ExecutionReceipt:
    """Assemble an :class:`ExecutionReceipt` from the run's enforcement surfaces.

    *actor_id* defaults to ``"<agent>/<role>"`` when a role is selected, else the
    bare agent id. *requested_capabilities* are evaluated against the grant
    table (each ``/cmd`` is a command, anything else a skill) and any non-ALLOW
    verdict surfaces under "blocked or missing".
    """

    agent_id = loaded_context.agent_id
    role_id = loaded_context.role_id
    if actor_id is None:
        actor_id = f"{agent_id}/{role_id}" if role_id else agent_id

    repo_root = loaded_context.manifest_path.parents[2]
    loaded_docs: List[Tuple[str, str]] = []
    loaded_policies: List[str] = []
    for doc in loaded_context.documents:
        rel = _display(doc.path, repo_root)
        if doc.label == LABEL_POLICY:
            loaded_policies.append(rel)
        else:
            loaded_docs.append((doc.label, rel))
    loaded_docs.sort(key=lambda t: (_DOC_LABEL_ORDER.index(t[0]) if t[0] in _DOC_LABEL_ORDER else 99, t[1]))

    eff = grant_table.effective_grants(actor_id)
    granted_commands = tuple(sorted(g.command for g in eff.builtin)) if eff else ()
    granted_skills = tuple(sorted(g.skill for g in eff.skills)) if eff else ()

    decisions = tuple(
        evaluate_capability(grant_table, actor_id, cap) for cap in requested_capabilities
    )

    warnings: List[str] = list(loaded_context.warnings)
    if eff is None:
        warnings.append(f"actor '{actor_id}' has no grant-table entry")
    for d in decisions:
        if d.verdict is GrantVerdict.BLOCK:
            warnings.append(f"blocked capability: {d.capability} — {d.reason}")
        elif d.verdict is GrantVerdict.ADVISORY:
            warnings.append(f"advisory capability: {d.capability} — {d.reason}")
    if compaction is not None:
        warnings.extend(compaction.warnings)
    if cleanup is not None:
        warnings.extend(cleanup.warnings)

    compaction_status = compaction.status if compaction is not None else "not_run"
    cleanup_status = cleanup.status if cleanup is not None else "not_run"
    if security is None:
        security_status = "not_evaluated"
    elif getattr(security, "skip_reason", None):
        security_status = "skipped"
    elif getattr(security, "required", False):
        security_status = "required"
        warnings.append(f"security review required: {', '.join(getattr(security, 'triggers', ()))}")
    else:
        security_status = "not_required"

    return ExecutionReceipt(
        selected_agent=agent_id,
        selected_role=role_id,
        selected_runner=selected_runner,
        loaded_docs=tuple(loaded_docs),
        loaded_policies=tuple(loaded_policies),
        granted_commands=granted_commands,
        granted_skills=granted_skills,
        capability_decisions=decisions,
        warnings=tuple(warnings),
        compaction_status=compaction_status,
        cleanup_status=cleanup_status,
        compaction=compaction,
        cleanup=cleanup,
        security_status=security_status,
        security=security,
        token_efficiency=token_efficiency,
    )


def _display(path, repo_root) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)


__all__ = (
    "ExecutionReceipt",
    "build_execution_receipt",
)
