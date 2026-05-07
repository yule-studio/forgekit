"""Service inventory — A-M6.0.

Defines what ``yule runtime up --profile engineering`` should
spawn, plus the lookup that ``yule run-service <service-name>``
uses to build the right worker. Adding a new service is a one-row
edit here — the CLI and the supervisor pick it up automatically.

Why an inventory module instead of a flat list inside the CLI:
the same data shape backs three concerns at once: (1) the parent
process's spawn list, (2) ``run-service`` 's lookup, (3) docs that
list every long-running service. Keeping all three in lockstep
matters more than CLI brevity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple


class ServiceKind(str, Enum):
    """Worker / supervisor classification.

    ``RESEARCH_WORKER`` / ``ROLE_WORKER`` / ``APPROVAL_WORKER`` /
    ``OBSIDIAN_WRITER`` map 1:1 onto the queue's ``job_type`` so
    the consumer loop knows which ``pick`` filters to apply.
    ``SUPERVISOR`` runs ``run_supervisor_watch_loop`` instead of
    a queue consumer. ``RESERVED_DISCORD_GATEWAY`` is the M6.1
    placeholder for the gateway entrypoint — listed here so docs
    stay accurate, but ``run_service`` refuses to start it for now.
    """

    RESEARCH_WORKER = "research_worker"
    ROLE_WORKER = "role_worker"
    APPROVAL_WORKER = "approval_worker"
    OBSIDIAN_WRITER = "obsidian_writer"
    SUPERVISOR = "supervisor"
    DISCORD_GATEWAY = "discord_gateway"
    # Kept for backward compatibility — older inventory dumps may
    # reference the reserved name. New code should use
    # ``DISCORD_GATEWAY``.
    RESERVED_DISCORD_GATEWAY = "reserved_discord_gateway"


@dataclass(frozen=True)
class ServiceSpec:
    """One row of the service inventory.

    ``service_id`` is the canonical identifier — it doubles as the
    heartbeat ``service_id``, the systemd unit name suffix, and the
    ``yule run-service`` argument. ``role`` is set only for
    ``ROLE_WORKER`` services so the queue's ``pick`` can filter on it.
    """

    service_id: str
    kind: ServiceKind
    description: str
    role: Optional[str] = None

    def is_implemented(self) -> bool:
        """Whether ``run_service`` has a real worker for this row.

        ``RESERVED_*`` rows are listed for inventory completeness
        but error loudly when started — they're TODO markers, not
        live services.
        """

        return self.kind != ServiceKind.RESERVED_DISCORD_GATEWAY


# ---------------------------------------------------------------------------
# Engineering profile
# ---------------------------------------------------------------------------
#
# Order matters: when ``runtime up`` boots, we spawn in this order so
# the supervisor + research worker come up before role workers, and
# Obsidian writer comes up last (depends on approval worker landing
# first in the steady-state runtime). The order is advisory — once
# all services are running, the queue dispatch order takes over.


_ENGINEERING_ROLES: Tuple[str, ...] = (
    "tech-lead",
    "backend-engineer",
    "qa-engineer",
    "devops-engineer",
    "ai-engineer",
    "frontend-engineer",
    "product-designer",
)


def _build_engineering_profile() -> Tuple[ServiceSpec, ...]:
    rows: list[ServiceSpec] = [
        ServiceSpec(
            service_id="eng-supervisor-watch",
            kind=ServiceKind.SUPERVISOR,
            description="watchdog: heartbeat sweep + lease reaper",
        ),
        ServiceSpec(
            service_id="eng-research-worker",
            kind=ServiceKind.RESEARCH_WORKER,
            description="research_collect job consumer",
        ),
    ]
    for role in _ENGINEERING_ROLES:
        rows.append(
            ServiceSpec(
                service_id=f"eng-role-{role}",
                kind=ServiceKind.ROLE_WORKER,
                description=f"role_take consumer for {role}",
                role=role,
            )
        )
    rows.append(
        ServiceSpec(
            service_id="eng-approval-worker",
            kind=ServiceKind.APPROVAL_WORKER,
            description="approval_post broadcast (#승인-대기)",
        )
    )
    rows.append(
        ServiceSpec(
            service_id="eng-obsidian-writer",
            kind=ServiceKind.OBSIDIAN_WRITER,
            description="obsidian_write vault writer",
        )
    )
    rows.append(
        ServiceSpec(
            service_id="eng-discord-gateway",
            kind=ServiceKind.DISCORD_GATEWAY,
            description=(
                "engineering Discord gateway — listens on #업무-접수, "
                "routes #승인-대기 replies through the queue"
            ),
        )
    )
    return tuple(rows)


ENGINEERING_PROFILE: Tuple[ServiceSpec, ...] = _build_engineering_profile()


# ---------------------------------------------------------------------------
# Profile registry — extended in later milestones (planning profile etc).
# ---------------------------------------------------------------------------


PROFILES: Mapping[str, Tuple[ServiceSpec, ...]] = {
    "engineering": ENGINEERING_PROFILE,
}


def list_services(profile: str = "engineering") -> Tuple[ServiceSpec, ...]:
    if profile not in PROFILES:
        raise ValueError(
            f"unknown profile {profile!r}; known profiles: "
            + ", ".join(sorted(PROFILES.keys()))
        )
    return PROFILES[profile]


def resolve_service(service_id: str) -> Optional[ServiceSpec]:
    """Look up a single :class:`ServiceSpec` across all profiles.

    Returns ``None`` when the id isn't in any profile — the CLI
    raises with a friendly "unknown service" message.
    """

    for profile_specs in PROFILES.values():
        for spec in profile_specs:
            if spec.service_id == service_id:
                return spec
    return None


__all__ = (
    "ENGINEERING_PROFILE",
    "PROFILES",
    "ServiceKind",
    "ServiceSpec",
    "list_services",
    "resolve_service",
)
