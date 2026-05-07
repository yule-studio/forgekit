"""Role-failure degrade + all-role fallback policy — A-M7.

Two distinct behaviours, one module so the audit shape stays in
one place:

  * **Degrade** — one or more roles failed but at least one role
    take is still available. Synthesis runs over what we have and
    a degrade banner names the missing roles in the rendered text.
    The session keeps moving; tech-lead's synthesis explicitly
    flags the gap so the operator (and any downstream Obsidian
    write) sees that the consensus is partial.

  * **All-role fallback** — every active role hit
    ``FAILED_TERMINAL``. We can't synthesise from nothing, so the
    runtime builds deterministic role takes from the pure
    fallback templates in :mod:`agents.deliberation` and runs
    synthesis over those. The result is plainly labelled
    "fallback으로 생성됨" so the operator never mistakes it for
    a real consensus, and the audit record sets
    ``human_approval_required=True`` so the M5b approval guard
    blocks an automatic Obsidian knowledge save.

Both branches stamp a :class:`FallbackAuditRecord` onto
``session.extra['fallback_audits']`` (capped list, latest-wins
within session) so an operator can answer "did we use fallback
last week?" by reading the workflow row alone.

This module is **pure-Python** — no Discord, no SQLite. Persistence
is delegated to the workflow_state helpers; tests inject stubs.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vocabulary — frozen constants so tests / status surfaces match by value.
# ---------------------------------------------------------------------------


# Role result classification.
ROLE_RESULT_OK: str = "ok"
ROLE_RESULT_FAILED: str = "failed"
ROLE_RESULT_MISSING: str = "missing"

# Authority — who produced the take.
FALLBACK_AUTHORITY_DEGRADED_SYNTHESIS: str = "degraded_synthesis"
FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE: str = "deterministic_template"


# Approval-block reason. Stamped onto the audit record when the
# fallback should NOT be auto-saved as final knowledge — used by
# M5b's approval guard which already blocks a write_request whose
# ``approval_id`` is empty.
REASON_HUMAN_APPROVAL_REQUIRED: str = (
    "fallback content requires explicit human approval before vault write"
)


# Maximum fallback audit history kept on session.extra. Same
# convention as ``approval_rejections`` — keep recent audit but
# don't grow the cache row unbounded.
MAX_FALLBACK_AUDIT_ENTRIES: int = 32


# ---------------------------------------------------------------------------
# Degrade summary — what came back vs. what was expected.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DegradeNotice:
    """Snapshot of role completion state going into synthesis.

    ``expected_roles`` is the deliberation sequence the gateway
    asked to run. ``completed_roles`` is the subset that actually
    produced a structured take. ``failed_roles`` came back
    ``FAILED_TERMINAL``. ``missing_roles`` is everyone else
    (timed out, never ran, network glitch).

    Property ``all_failed`` is True iff every expected role failed
    — the trigger for the all-role fallback path.
    """

    expected_roles: Tuple[str, ...]
    completed_roles: Tuple[str, ...]
    failed_roles: Tuple[str, ...]
    missing_roles: Tuple[str, ...]

    @property
    def degraded(self) -> bool:
        """True when at least one expected role is not in completed."""

        return bool(self.failed_roles or self.missing_roles)

    @property
    def all_failed(self) -> bool:
        """All expected roles are failed — kicks all-role fallback."""

        return (
            bool(self.expected_roles)
            and not self.completed_roles
            and len(self.failed_roles) == len(self.expected_roles)
        )

    def to_text(self) -> str:
        """Korean-language banner for the synthesis render.

        Empty string when nothing degraded — the caller can drop
        the banner without an `if` check.
        """

        if not self.degraded:
            return ""
        bits: list[str] = []
        if self.failed_roles:
            bits.append(
                "실패한 역할: " + ", ".join(self.failed_roles)
            )
        if self.missing_roles:
            bits.append(
                "누락된 역할: " + ", ".join(self.missing_roles)
            )
        return "[degrade] " + " · ".join(bits)


def summarise_role_results(
    *,
    expected_roles: Sequence[str],
    completed_roles: Sequence[str] = (),
    failed_roles: Sequence[str] = (),
) -> DegradeNotice:
    """Classify each expected role into completed / failed / missing.

    Order of *expected_roles* drives the order of the resulting
    tuples so the rendered banner matches the deliberation sequence
    the user already sees in the forum.
    """

    completed_set = set(completed_roles)
    failed_set = set(failed_roles)
    completed_kept: list[str] = []
    failed_kept: list[str] = []
    missing: list[str] = []
    for role in expected_roles:
        if role in completed_set:
            completed_kept.append(role)
        elif role in failed_set:
            failed_kept.append(role)
        else:
            missing.append(role)
    return DegradeNotice(
        expected_roles=tuple(expected_roles),
        completed_roles=tuple(completed_kept),
        failed_roles=tuple(failed_kept),
        missing_roles=tuple(missing),
    )


# ---------------------------------------------------------------------------
# Audit record — what we persist when degrade or fallback fires.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FallbackAuditRecord:
    """One audit entry per degrade / fallback event.

    ``human_approval_required`` is True for the all-role fallback
    path because the content is template-derived rather than
    role-deliberated — auto-saving such content as final knowledge
    would silently swap a low-confidence template for a real team
    consensus. Keeping this flag explicit lets the M5b approval
    guard block the write without coupling to fallback.py.
    """

    fallback_id: str
    session_id: str
    expected_roles: Tuple[str, ...]
    failed_roles: Tuple[str, ...]
    missing_roles: Tuple[str, ...]
    fallback_authority: str
    reason: str
    human_approval_required: bool
    created_at: str

    def to_payload(self) -> Mapping[str, Any]:
        """JSON-friendly dict for SQLite / status markdown."""

        return {
            "fallback_id": self.fallback_id,
            "session_id": self.session_id,
            "expected_roles": list(self.expected_roles),
            "failed_roles": list(self.failed_roles),
            "missing_roles": list(self.missing_roles),
            "fallback_authority": self.fallback_authority,
            "reason": self.reason,
            "human_approval_required": self.human_approval_required,
            "created_at": self.created_at,
        }


def build_fallback_audit_record(
    *,
    session_id: str,
    notice: DegradeNotice,
    authority: str,
    reason: Optional[str] = None,
    human_approval_required: Optional[bool] = None,
    now: Optional[datetime] = None,
    fallback_id: Optional[str] = None,
) -> FallbackAuditRecord:
    """Construct an audit record from a :class:`DegradeNotice`.

    ``human_approval_required`` defaults to True for the
    all-role fallback authority and False for plain degrade —
    a single missing role is a partial consensus the team can
    publish; an all-template synthesis is not.
    """

    if not session_id:
        raise ValueError("session_id is required for a fallback audit record")
    when = now if now is not None else datetime.now(tz=timezone.utc)
    derived_reason = reason or _default_reason(notice, authority)
    if human_approval_required is None:
        human_approval_required = (
            authority == FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE
        )
    return FallbackAuditRecord(
        fallback_id=fallback_id or _new_fallback_id(),
        session_id=session_id,
        expected_roles=notice.expected_roles,
        failed_roles=notice.failed_roles,
        missing_roles=notice.missing_roles,
        fallback_authority=authority,
        reason=derived_reason,
        human_approval_required=bool(human_approval_required),
        created_at=when.replace(microsecond=0).isoformat(),
    )


def _new_fallback_id() -> str:
    return f"fb-{uuid.uuid4().hex[:12]}"


def _default_reason(
    notice: DegradeNotice, authority: str
) -> str:
    if authority == FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE:
        return (
            "all expected roles failed; deterministic template "
            "synthesis used as fallback"
        )
    if notice.failed_roles and notice.missing_roles:
        return (
            "synthesis proceeded with degrade — "
            f"{len(notice.failed_roles)} failed / "
            f"{len(notice.missing_roles)} missing role(s)"
        )
    if notice.failed_roles:
        return (
            "synthesis proceeded with degrade — "
            f"{len(notice.failed_roles)} failed role(s)"
        )
    if notice.missing_roles:
        return (
            "synthesis proceeded with degrade — "
            f"{len(notice.missing_roles)} missing role(s)"
        )
    return "fallback authority recorded for audit completeness"


# ---------------------------------------------------------------------------
# Persistence — best-effort write to session.extra['fallback_audits'].
# ---------------------------------------------------------------------------


SessionLoaderFn = Callable[[str], Optional[Any]]
SessionUpdaterFn = Callable[..., Any]


def persist_fallback_audit(
    record: FallbackAuditRecord,
    *,
    session_loader: Optional[SessionLoaderFn] = None,
    session_updater: Optional[SessionUpdaterFn] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Stash *record* on ``session.extra['fallback_audits']``.

    Returns True when the audit was actually written; False when
    the session couldn't be loaded / updated. Loader / updater
    default to the workflow_state functions; tests inject stubs.

    Best-effort: any exception in the persistence path is swallowed
    after logging. The fallback content itself is independent of
    the audit succeeding — losing the audit row is recoverable, but
    losing the fallback synthesis would block the user-facing flow.
    """

    loader, updater = _resolve_persistence(session_loader, session_updater)
    if loader is None or updater is None:
        return False

    try:
        session = loader(record.session_id)
    except Exception:  # noqa: BLE001 - persistence is best-effort
        logger.warning(
            "persist_fallback_audit: load_session raised", exc_info=True
        )
        return False
    if session is None:
        return False

    extra = dict(getattr(session, "extra", None) or {})
    bucket = list(extra.get("fallback_audits") or [])
    bucket.append(dict(record.to_payload()))
    if len(bucket) > MAX_FALLBACK_AUDIT_ENTRIES:
        bucket = bucket[-MAX_FALLBACK_AUDIT_ENTRIES:]
    extra["fallback_audits"] = bucket
    try:
        updated = replace(session, extra=extra)
    except TypeError:
        # Session shape doesn't support replace — still try the
        # mutation in place so unit tests with bare SimpleNamespace
        # still observe the bucket update.
        live_extra = getattr(session, "extra", None)
        if isinstance(live_extra, dict):
            live_extra["fallback_audits"] = bucket
            return True
        return False
    try:
        updater(updated, now=now or datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        logger.warning(
            "persist_fallback_audit: update_session raised", exc_info=True
        )
        return False
    return True


def _resolve_persistence(
    loader: Optional[SessionLoaderFn], updater: Optional[SessionUpdaterFn]
) -> Tuple[Optional[SessionLoaderFn], Optional[SessionUpdaterFn]]:
    if loader is not None and updater is not None:
        return loader, updater
    try:
        from ..agents.workflow_state import (
            load_session as _load,
            update_session as _update,
        )
    except Exception:  # noqa: BLE001 - partial install fallback
        return loader, updater
    return loader or _load, updater or _update


# ---------------------------------------------------------------------------
# Synthesis assemblers — the actual degrade / fallback content.
# ---------------------------------------------------------------------------


def render_degraded_synthesis_text(
    *,
    base_text: str,
    notice: DegradeNotice,
) -> str:
    """Prepend a degrade banner to *base_text*.

    *base_text* is whatever ``synthesize_thread`` rendered over the
    available role takes. The banner is non-destructive — the
    deterministic synthesis content stays intact, the operator just
    sees the gap stamped at the top.
    """

    banner = notice.to_text()
    if not banner:
        return base_text
    return f"{banner}\n{base_text}"


def build_deterministic_fallback_synthesis(
    *,
    session: Any,
    expected_roles: Sequence[str],
    research_pack: Optional[Any] = None,
    role_address_fn: Optional[Callable[[str], str]] = None,
) -> Tuple[Any, str]:
    """Build a tech-lead synthesis from deterministic role takes.

    Used when every expected role hit ``FAILED_TERMINAL`` —
    we can't ask synthesis to operate on no role takes, so we
    generate template-derived takes for each role first. The
    rendered text always carries the "fallback으로 생성됨" header
    so it is plainly distinguishable from a real synthesis.

    Returns ``(TechLeadSynthesis, rendered_text)``. The synthesis
    dataclass has ``approval_required=True`` because the content
    is template-only.
    """

    from ..agents.deliberation import (
        DeliberationContext,
        run_role_deliberation,
        synthesize,
        render_synthesis,
    )

    addresser = role_address_fn or _default_role_address
    role_takes: list[Any] = []
    for role in expected_roles:
        ctx = DeliberationContext(
            session=session,
            role=addresser(role),
            research_pack=research_pack,
            previous_turns=tuple(role_takes),
            memory_context=(),
        )
        # No runner_fn → deterministic path always fires.
        take = run_role_deliberation(ctx, runner_fn=None)
        role_takes.append(take)

    synth = synthesize(
        session,
        role_takes,
        research_pack=research_pack,
        memory_context=(),
    )
    # Force approval_required=True so the M5b guard blocks an
    # automatic vault write of fallback content. Reuse the existing
    # frozen dataclass via dataclasses.replace.
    forced = replace(
        synth,
        approval_required=True,
        approval_reason=REASON_HUMAN_APPROVAL_REQUIRED,
    )
    base_text = render_synthesis(forced)
    header = (
        "[fallback으로 생성됨] 모든 역할이 실패해 deterministic "
        "template 기반으로 종합했습니다. 사용자 승인 없이 vault "
        "최종 저장으로 이어지지 않습니다."
    )
    return forced, f"{header}\n{base_text}"


def _default_role_address(role: str) -> str:
    """Mirror ``engineering_team_runtime._role_address`` without
    importing the discord layer (keeps fallback.py free of Discord
    deps so tests don't pull discord.py in).
    """

    cleaned = str(role or "").strip()
    if "/" in cleaned:
        return cleaned
    return f"engineering-agent/{cleaned}"


__all__ = (
    "DegradeNotice",
    "FALLBACK_AUTHORITY_DEGRADED_SYNTHESIS",
    "FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE",
    "FallbackAuditRecord",
    "MAX_FALLBACK_AUDIT_ENTRIES",
    "REASON_HUMAN_APPROVAL_REQUIRED",
    "ROLE_RESULT_FAILED",
    "ROLE_RESULT_MISSING",
    "ROLE_RESULT_OK",
    "build_deterministic_fallback_synthesis",
    "build_fallback_audit_record",
    "persist_fallback_audit",
    "render_degraded_synthesis_text",
    "summarise_role_results",
)
