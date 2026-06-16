"""Agent invocation contract — the single source of truth for every role.

Fixes "who is called when, what they take/emit, what they may write/commit, and
where/how they record in the shared vault". Each role maps to a **contract class**
(executor / coordinator / reviewer / advisory / product / observer / curator /
platform); the class fixes the write/commit/PR/worktree capabilities, and the
:mod:`agent_color_registry` adds the per-role color token + vault lane.

Human-facing SSoT: ``docs/agent-invocation-contract.md``. Drift between this
registry and on-disk role manifests is caught by
``tests/governance/test_agent_contracts.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple

from . import agent_color_registry as colors

# --- contract classes -------------------------------------------------------

CLASS_EXECUTOR = "executor"
CLASS_COORDINATOR = "coordinator"
CLASS_REVIEWER = "reviewer"
CLASS_ADVISORY = "advisory"
CLASS_PRODUCT = "product"
CLASS_OBSERVER = "observer"
CLASS_CURATOR = "curator"
CLASS_PLATFORM = "platform"


@dataclass(frozen=True)
class ClassPolicy:
    can_write_code: bool
    can_commit: bool
    can_open_pr: bool
    can_write_vault: bool
    worktree_policy: str
    question_budget: int
    input_packet: str
    output_packet: str
    approval_required_for: Tuple[str, ...]
    escalation_to: str


# Only executor + platform may write code / commit. Everyone else is note/packet.
CLASS_POLICY: Mapping[str, ClassPolicy] = {
    CLASS_EXECUTOR: ClassPolicy(True, True, True, True, "isolated_worktree", 0,
        "task decomposition + ProductIntentPacket", "code diff + tests + draft PR",
        ("merge", "push_protected", "deploy", "secret"), "tech-lead"),
    CLASS_PLATFORM: ClassPolicy(True, True, True, True, "isolated_worktree", 0,
        "setup/connect/runtime request", "runtime wiring + draft PR + doctor report",
        ("deploy", "secret", "runtime_restart"), "operator"),
    CLASS_COORDINATOR: ClassPolicy(False, False, True, True, "orchestrate_only", 0,
        "ProductIntentPacket / raw task", "decomposition + routing + synthesis",
        ("merge",), "operator"),
    CLASS_REVIEWER: ClassPolicy(False, False, False, True, "read_only", 0,
        "other roles' drafts/diffs", "findings (blocking/non-blocking) + recommendation",
        (), "tech-lead"),
    CLASS_PRODUCT: ClassPolicy(False, False, False, True, "none", 3,
        "raw user ask", "ProductIntentPacket (questions + packet)",
        ("pricing", "irreversible"), "operator"),
    CLASS_ADVISORY: ClassPolicy(False, False, False, True, "none", 0,
        "domain request / brief", "advisory note / packet (no code)",
        (), "department-lead"),
    CLASS_OBSERVER: ClassPolicy(False, False, False, True, "none", 0,
        "runtime status / heartbeat / queue", "status summary + triage + next-action",
        (), "operator"),
    CLASS_CURATOR: ClassPolicy(False, False, False, True, "none", 0,
        "raw notes / sources / vault state", "canonical/reusable notes + index + brain pack",
        (), "operator"),
}

# --- role → (department, contract class) ------------------------------------
# Covers every on-disk role manifest plus the 3 new roles. The drift test asserts
# this matches agents/*/*/manifest.json.

ROLE_REGISTRY: Tuple[Tuple[str, str, str], ...] = (
    # engineering
    ("engineering-agent", "tech-lead", CLASS_COORDINATOR),
    ("engineering-agent", "backend-engineer", CLASS_EXECUTOR),
    ("engineering-agent", "frontend-engineer", CLASS_EXECUTOR),
    ("engineering-agent", "devops-engineer", CLASS_EXECUTOR),
    ("engineering-agent", "ai-engineer", CLASS_EXECUTOR),
    ("engineering-agent", "qa-engineer", CLASS_REVIEWER),
    ("engineering-agent", "security-engineer", CLASS_REVIEWER),
    ("engineering-agent", "product-designer", CLASS_ADVISORY),
    ("engineering-agent", "platform-runtime-engineer", CLASS_PLATFORM),   # new
    ("engineering-agent", "knowledge-engineer", CLASS_CURATOR),           # new
    ("engineering-agent", "ops-observer", CLASS_OBSERVER),                # new
    # product
    ("product-agent", "product-manager", CLASS_PRODUCT),
    ("product-agent", "user-researcher", CLASS_ADVISORY),
    ("product-agent", "growth-analyst", CLASS_ADVISORY),
    # planning
    ("planning-agent", "planning-agent", CLASS_ADVISORY),
    # marketing
    ("marketing-agent", "brand-manager", CLASS_ADVISORY),
    ("marketing-agent", "content-strategist", CLASS_ADVISORY),
    ("marketing-agent", "growth-marketer", CLASS_ADVISORY),
    ("marketing-agent", "seo-specialist", CLASS_ADVISORY),
    ("marketing-agent", "example", CLASS_ADVISORY),
    # people / finance / revenue / legal
    ("hr-agent", "culture-coach", CLASS_ADVISORY),
    ("hr-agent", "people-ops", CLASS_ADVISORY),
    ("hr-agent", "recruiter", CLASS_ADVISORY),
    ("finance-agent", "budget-analyst", CLASS_ADVISORY),
    ("sales-cs-agent", "customer-success", CLASS_ADVISORY),
    ("sales-cs-agent", "sales-rep", CLASS_ADVISORY),
    ("legal-agent", "contract-reviewer", CLASS_ADVISORY),
    ("legal-agent", "privacy-officer", CLASS_ADVISORY),
)

# The 3 roles introduced in this slice (documented separately).
NEW_ROLES: Tuple[str, ...] = ("platform-runtime-engineer", "knowledge-engineer", "ops-observer")

RECEIPT_FIELDS: Tuple[str, ...] = (
    "agent", "role", "contract_class", "obsidian_lane", "color_token", "can_commit",
)


@dataclass(frozen=True)
class AgentContract:
    agent_id: str
    department_id: str
    role_id: str
    contract_class: str
    owner_domain: str
    trigger_when: str
    input_packet: str
    output_packet: str
    question_budget: int
    can_write_code: bool
    can_write_vault: bool
    can_commit: bool
    can_open_pr: bool
    worktree_policy: str
    obsidian_write_target: str
    retrieval_scope: str
    approval_required_for: Tuple[str, ...]
    escalation_to: str
    receipt_fields: Tuple[str, ...]
    color_token: str
    color_hex: str

    def to_dict(self) -> dict:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in self.__dict__.items()}


def _build(department: str, role: str, klass: str, role_index: int) -> AgentContract:
    policy = CLASS_POLICY[klass]
    token, hexv = colors.color_for(department, role, role_index=role_index)
    return AgentContract(
        agent_id=f"{department}/{role}",
        department_id=department,
        role_id=role,
        contract_class=klass,
        owner_domain=f"{department.replace('-agent','')} · {role}",
        trigger_when=_TRIGGER.get(klass, "department request"),
        input_packet=policy.input_packet,
        output_packet=policy.output_packet,
        question_budget=policy.question_budget,
        can_write_code=policy.can_write_code,
        can_write_vault=policy.can_write_vault,
        can_commit=policy.can_commit,
        can_open_pr=policy.can_open_pr,
        worktree_policy=policy.worktree_policy,
        obsidian_write_target=colors.lane_for(department, role),
        retrieval_scope="role+project+canonical (metadata, not color)",
        approval_required_for=policy.approval_required_for,
        escalation_to=policy.escalation_to,
        receipt_fields=RECEIPT_FIELDS,
        color_token=token,
        color_hex=hexv,
    )


_TRIGGER = {
    CLASS_EXECUTOR: "tech-lead 가 packet 을 분해해 구현 작업을 배정할 때",
    CLASS_PLATFORM: "설치/연결/runtime/provider/doctor 작업이 필요할 때",
    CLASS_COORDINATOR: "product packet 또는 작업이 들어와 분해/라우팅이 필요할 때",
    CLASS_REVIEWER: "해당 변경 유형(보안/품질)이 감지될 때 cross-cutting 으로",
    CLASS_PRODUCT: "제품/기능 요청이 engineering 앞단에 도착할 때",
    CLASS_ADVISORY: "해당 도메인 brief/요청이 들어올 때",
    CLASS_OBSERVER: "주기적 runtime 감시 / alert / fallback spike",
    CLASS_CURATOR: "vault 구조화 / canonical 승격 / brain pack build",
}


def all_contracts() -> Tuple[AgentContract, ...]:
    # role_index is per-department ordinal so sibling colors differ
    seen: dict[str, int] = {}
    out = []
    for dept, role, klass in ROLE_REGISTRY:
        idx = seen.get(dept, 0)
        seen[dept] = idx + 1
        out.append(_build(dept, role, klass, idx))
    return tuple(out)


def contract_for(role: str) -> AgentContract:
    for c in all_contracts():
        if c.role_id == role:
            return c
    raise KeyError(role)


__all__ = (
    "CLASS_EXECUTOR", "CLASS_COORDINATOR", "CLASS_REVIEWER", "CLASS_ADVISORY",
    "CLASS_PRODUCT", "CLASS_OBSERVER", "CLASS_CURATOR", "CLASS_PLATFORM",
    "ClassPolicy", "CLASS_POLICY", "ROLE_REGISTRY", "NEW_ROLES", "RECEIPT_FIELDS",
    "AgentContract", "all_contracts", "contract_for",
)
