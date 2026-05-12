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

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple


# #73 Round 2 — env override that flips ``eng-coding-executor`` from opt-in
# (auto_spawn=False) to auto_spawn=True so ``yule runtime up`` will spawn
# it without an explicit ``--include`` flag. Operators set this in
# .env.local *only after* the live executor wiring + push credentials
# have been validated against a non-protected branch.
ENV_CODING_EXECUTOR_AUTOSPAWN: str = "YULE_CODING_EXECUTOR_AUTOSPAWN"


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
    # #73 Phase 1 — coding executor worker. Registered as a service kind
    # so ``runtime up`` *can* spawn it, but the engineering profile keeps
    # it OFF by default (opt-in flag in the service spec). Production
    # spawn after live-executor wiring lands in a follow-up PR.
    CODING_EXECUTOR = "coding_executor"
    SUPERVISOR = "supervisor"
    DISCORD_GATEWAY = "discord_gateway"
    # F13 #122 — 부서별 자동 이슈 수집 (RSS/release crawler + dept dispatch).
    DIGEST_SCHEDULER = "digest_scheduler"
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
    # #73 — when False, ``runtime up`` lists the service in the inventory
    # but does not spawn it automatically. Used for the coding executor
    # which needs explicit operator opt-in (live executor calls + git push).
    auto_spawn: bool = True

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


def _is_truthy_env(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coding_executor_autospawn_enabled(
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """Operator opt-in flag for the coding executor.

    Reads ``YULE_CODING_EXECUTOR_AUTOSPAWN`` from *env* (defaults to
    the live process environment). Truthy values (``1`` / ``true`` /
    ``yes`` / ``on``) flip ``eng-coding-executor`` from opt-in to
    always-spawn under ``yule runtime up``.

    Hard rail: missing / empty / falsey value → opt-in remains off.
    Detecting a related env (e.g. GitHub App creds) does NOT enable
    auto-spawn — the operator must set this exact flag.
    """

    source = env if env is not None else os.environ
    return _is_truthy_env(source.get(ENV_CODING_EXECUTOR_AUTOSPAWN))


def _build_engineering_profile(
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[ServiceSpec, ...]:
    coding_executor_auto = _coding_executor_autospawn_enabled(env)
    rows: list[ServiceSpec] = [
        ServiceSpec(
            service_id="eng-supervisor-watch",
            kind=ServiceKind.SUPERVISOR,
            description=(
                "supervisor watchdog — heartbeat sweep + lease reaper "
                "(reads service_heartbeats / job_queue; no queue consumer)"
            ),
        ),
        ServiceSpec(
            service_id="eng-research-worker",
            kind=ServiceKind.RESEARCH_WORKER,
            description=(
                "research_collect queue consumer — runs auto_collect, "
                "stamps research_pack onto session.extra"
            ),
        ),
    ]
    for role in _ENGINEERING_ROLES:
        rows.append(
            ServiceSpec(
                service_id=f"eng-role-{role}",
                kind=ServiceKind.ROLE_WORKER,
                description=(
                    f"role_take queue consumer (role={role}) — produces "
                    "deliberation take + posts to #운영-리서치 thread"
                ),
                role=role,
            )
        )
    rows.append(
        ServiceSpec(
            service_id="eng-approval-worker",
            kind=ServiceKind.APPROVAL_WORKER,
            description=(
                "approval_post queue consumer — broadcasts approval cards "
                "to #승인-대기, ingests user replies via handle_approval_reply"
            ),
        )
    )
    rows.append(
        ServiceSpec(
            service_id="eng-obsidian-writer",
            kind=ServiceKind.OBSIDIAN_WRITER,
            description=(
                "obsidian_write queue consumer — writes approved knowledge "
                "/ research notes into OBSIDIAN_VAULT_PATH (approval guard)"
            ),
        )
    )
    rows.append(
        ServiceSpec(
            service_id="eng-coding-executor",
            kind=ServiceKind.CODING_EXECUTOR,
            description=(
                "coding_execute queue consumer (#73) — drives worktree → edit → "
                "test → commit → push → draft PR for approved coding_jobs. "
                "Default auto_spawn=False; operator flips it on by setting "
                f"{ENV_CODING_EXECUTOR_AUTOSPAWN}=true in .env.local once live "
                "executor wiring + push credentials are validated."
            ),
            auto_spawn=coding_executor_auto,
        )
    )
    rows.append(
        ServiceSpec(
            service_id="eng-discord-gateway",
            kind=ServiceKind.DISCORD_GATEWAY,
            description=(
                "engineering Discord gateway — listens on #업무-접수, "
                "enqueues research_collect/role_take/approval_post jobs "
                "for the workers above (does not consume from queue itself)"
            ),
        )
    )
    rows.append(
        ServiceSpec(
            service_id="eng-digest-scheduler",
            kind=ServiceKind.DIGEST_SCHEDULER,
            description=(
                "F13 (#122) 부서별 자동 이슈 수집 — RSS/release feed 16 host "
                "interval crawl → dept_router (design/planning/engineering/multi-dept) → "
                "dedup ledger (24h url+title hash) → 부서 채널 GeekNews 카드 게시. "
                "다중 부서 영향 시 #운영-리서치 thread 자동 생성. "
                "YULE_DIGEST_SCHEDULER_ENABLED=true 일 때만 실행 (exit 78 if disabled)."
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
    "ENV_CODING_EXECUTOR_AUTOSPAWN",
    "PROFILES",
    "ServiceKind",
    "ServiceSpec",
    "build_engineering_profile",
    "is_coding_executor_autospawn_enabled",
    "list_services",
    "resolve_service",
)


# Public aliases — tests + callers that want to rebuild the profile
# under a different env import these. Underscore-prefix originals stay
# for internal symmetry with prior code.
build_engineering_profile = _build_engineering_profile
is_coding_executor_autospawn_enabled = _coding_executor_autospawn_enabled
