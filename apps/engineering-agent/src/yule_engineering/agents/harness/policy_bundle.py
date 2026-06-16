"""Minimal policy bundle selector — feed the runner only the relevant policies.

``context_loader`` loads *every* policy listed in the agent manifest (~20 files).
Shipping all of them to a runner — even as digests — is more than most tasks
need. This selector narrows the policy set by ``role`` / ``task_type`` /
``intent`` to a relevant bundle, then the digest renderer
(:func:`token_budget.build_policy_bundle`) carries pointers, not full text.

Safety first — narrowing never drops context we're unsure about:
  * the instruction layers (entrypoint / root / agent / role) are ALWAYS kept;
  * core policies (``safety`` / ``context-loading``) are always included;
  * when no mapping matches the (role, task, intent), ALL policies are kept
    (digest mode) — we only narrow when we have a confident bundle.

Never ships full text — the bundle is always digest/pointer based.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Set, Tuple

from yule_core.context_loader import LABEL_POLICY

from .token_budget import PolicyBundle, build_policy_bundle

# Always present regardless of task — the safety/loading floor.
ALWAYS_INCLUDE: Set[str] = {"safety", "context-loading"}

# task_type → relevant policy stems (filename without .md).
TASK_POLICY_BUNDLES: Mapping[str, Set[str]] = {
    "testing": {"testing"},
    "coding": {"version-control", "workflow", "testing"},
    "backend-feature": {"version-control", "workflow", "testing", "message-protocol"},
    "frontend-feature": {"workflow", "testing"},
    "landing-page": {"workflow", "testing"},
    "research": {"role-profiles", "role-weights-v0", "recall-policy", "memory-policy"},
    "deliberation": {"role-profiles", "role-weights-v0", "dispatcher"},
    "deploy": {"version-control", "scheduled-automation", "env-strategy"},
    "compaction": {"context-compression", "memory-policy", "recall-policy"},
}

# intent → extra relevant stems (coarse).
INTENT_POLICY_BUNDLES: Mapping[str, Set[str]] = {
    "compress": {"context-compression"},
    "memory": {"memory-policy", "recall-policy"},
    "research": {"role-profiles", "recall-policy"},
    "deploy": {"scheduled-automation", "env-strategy"},
    "test": {"testing"},
}

# role → extra relevant stems.
ROLE_POLICY_BUNDLES: Mapping[str, Set[str]] = {
    "qa-engineer": {"testing"},
    "devops-engineer": {"scheduled-automation", "env-strategy", "multi-bot-launcher"},
    "tech-lead": {"role-profiles", "role-weights-v0", "workflow"},
    "backend-engineer": {"version-control", "message-protocol"},
    "ai-engineer": {"memory-policy", "recall-policy", "context-compression"},
    "security-engineer": set(),  # core only — security gate doesn't need the runtime bundle
}


@dataclass(frozen=True)
class PolicySelection:
    selected: Tuple[Any, ...]            # selected context documents (instruction + policy)
    reason: str                          # why this set (matched bundle / keep-all)
    total_policies: int
    selected_policies: int
    dropped_policies: int


def _stem(doc: Any) -> str:
    return Path(str(getattr(doc, "path", ""))).stem


def select_policy_documents(
    documents: Sequence[Any],
    *,
    role: Optional[str] = None,
    task_type: Optional[str] = None,
    intent: Optional[str] = None,
) -> PolicySelection:
    """Narrow the policy documents by (role, task, intent).

    Non-policy documents (entrypoint/root/agent/role instructions) are always
    kept. Policy documents are kept only when their stem is in the wanted set;
    if nothing beyond the always-include floor matched, ALL policies are kept.
    """

    role_short = (role or "").split("/", 1)[-1].strip().lower()
    wanted: Set[str] = set(ALWAYS_INCLUDE)
    matched_specific = False
    if task_type and task_type.strip().lower() in TASK_POLICY_BUNDLES:
        wanted |= TASK_POLICY_BUNDLES[task_type.strip().lower()]
        matched_specific = True
    if intent and intent.strip().lower() in INTENT_POLICY_BUNDLES:
        wanted |= INTENT_POLICY_BUNDLES[intent.strip().lower()]
        matched_specific = True
    if role_short in ROLE_POLICY_BUNDLES:
        wanted |= ROLE_POLICY_BUNDLES[role_short]
        matched_specific = True

    policy_docs = [d for d in documents if getattr(d, "label", "") == LABEL_POLICY]
    instruction_docs = [d for d in documents if getattr(d, "label", "") != LABEL_POLICY]
    total = len(policy_docs)

    if not matched_specific:
        # Unknown task/role/intent → keep everything (digest still applies).
        return PolicySelection(
            selected=tuple(documents),
            reason="no_bundle_match_keep_all",
            total_policies=total,
            selected_policies=total,
            dropped_policies=0,
        )

    kept_policies = [d for d in policy_docs if _stem(d) in wanted]
    selected = tuple(instruction_docs + kept_policies)
    return PolicySelection(
        selected=selected,
        reason=f"bundle(role={role_short or '-'}, task={task_type or '-'}, intent={intent or '-'})",
        total_policies=total,
        selected_policies=len(kept_policies),
        dropped_policies=total - len(kept_policies),
    )


@dataclass(frozen=True)
class SelectedPolicyBundle:
    selection: PolicySelection
    bundle: PolicyBundle  # digest bundle of the selected policies only

    @property
    def fed_tokens(self) -> int:
        return self.bundle.fed_tokens

    def to_dict(self) -> dict:
        return {
            "reason": self.selection.reason,
            "total_policies": self.selection.total_policies,
            "selected_policies": self.selection.selected_policies,
            "dropped_policies": self.selection.dropped_policies,
            "policy_full_tokens": self.bundle.full_tokens,
            "policy_fed_tokens": self.bundle.fed_tokens,
            "policy_saved_tokens": self.bundle.saved_tokens,
        }


def build_selected_policy_bundle(
    documents: Sequence[Any],
    *,
    role: Optional[str] = None,
    task_type: Optional[str] = None,
    intent: Optional[str] = None,
    max_chars: int = 280,
) -> SelectedPolicyBundle:
    """Select relevant policies, then digest-bundle only those (never full text)."""

    selection = select_policy_documents(
        documents, role=role, task_type=task_type, intent=intent
    )
    selected_policy_docs = [
        d for d in selection.selected if getattr(d, "label", "") == LABEL_POLICY
    ]
    bundle = build_policy_bundle(selected_policy_docs, mode="digest", max_chars=max_chars)
    return SelectedPolicyBundle(selection=selection, bundle=bundle)


__all__ = (
    "ALWAYS_INCLUDE",
    "TASK_POLICY_BUNDLES",
    "INTENT_POLICY_BUNDLES",
    "ROLE_POLICY_BUNDLES",
    "PolicySelection",
    "SelectedPolicyBundle",
    "select_policy_documents",
    "build_selected_policy_bundle",
)
