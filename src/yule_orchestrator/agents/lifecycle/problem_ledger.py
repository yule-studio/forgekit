"""Problem object + in-process ledger — self-improvement runtime.

Self-improvement loop 가 다루는 "발견된 문제" 의 1급 표현.

기존 :class:`SelfImprovementSignal` 은 한 번의 *감지* 결과를 표현한다.
같은 신호가 매 sweep tick 마다 반복 감지될 때, 그것을 그대로
파이프라인에 흘리면 운영-리서치 thread 가 spam 으로 가득 차고
worktree / draft PR 도 무한 복제된다.

:class:`ProblemObject` 는 다음을 묶어준다:

* ``signature`` — 같은 문제로 보이게 하는 안정적 키
* ``first_seen_at`` / ``last_seen_at`` / ``occurrence_count``
* ``status`` — detected → triaged → fixing → verifying → completed
  / blocked / waiting_operator / escalated
* ``owner_role`` — tech-lead triage 가 정한 담당 역할
* ``approval_scope`` — delegated_ok / needs_human / blocked
* ``related_session_ids`` / ``related_job_ids`` / ``related_pr_urls``
* ``worktree_branch`` — 분기 작업이 생성됐다면 그 이름
* ``retry_count``

:class:`ProblemLedger` 는 supervisor 한 프로세스 내에서 problem 들을
관리한다. 파일 기반 영속화 옵션이 있지만, 기본은 메모리 — supervisor
재시작 시 깨끗한 상태로 출발하는 것이 안전한 기본값.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Tuple


logger = logging.getLogger(__name__)


class ProblemStatus(str, Enum):
    """Lifecycle states of a :class:`ProblemObject`.

    String-valued so payload dumps survive JSON round-trip without a
    mapper. The order roughly mirrors the runtime flow but transitions
    aren't enforced here — the ledger trusts the caller.
    """

    DETECTED = "detected"
    TRIAGED = "triaged"
    FIXING = "fixing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    WAITING_OPERATOR = "waiting_operator"
    ESCALATED = "escalated"
    SUPPRESSED = "suppressed"


_TERMINAL_STATUSES: frozenset[ProblemStatus] = frozenset(
    {
        ProblemStatus.COMPLETED,
        ProblemStatus.SUPPRESSED,
    }
)


_SIGNATURE_CLEANUP_RE = re.compile(r"[^a-zA-Z0-9._:-]")


def build_problem_signature(
    *, signal_id: str, evidence: Mapping[str, Any] = (), salt: str = ""
) -> str:
    """Build a stable signature for a signal-evidence pair.

    Same ``signal_id`` + same canonical evidence keys → identical
    signature. Used as the dedup key in the ledger.

    The evidence is sorted by key and only the *anchoring* fields (the
    ones that identify "which instance of this problem we're looking
    at") are included. Volatile counters and timestamps would defeat
    dedup, so we restrict to a known set of canonical keys.
    """

    anchors = (
        "topic_key",
        "job_type",
        "service_id",
        "session_id",
        "approval_kind",
        "channel_id",
        "thread_id",
        "executor_role",
        "branch",
        "repo_full_name",
        "issue_number",
        "intent",
    )
    parts = [str(signal_id or "?")]
    if isinstance(evidence, Mapping):
        for key in anchors:
            value = evidence.get(key)
            if value is None or value == "":
                continue
            parts.append(f"{key}={value}")
    if salt:
        parts.append(f"salt={salt}")
    raw = "|".join(parts)
    # Short hex digest for human-readable signatures + collision safety.
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    cleaned = _SIGNATURE_CLEANUP_RE.sub("-", str(signal_id))
    return f"{cleaned}.{digest}"


@dataclass(frozen=True)
class ProblemObject:
    """First-class representation of an autonomously detected problem.

    Frozen so the ledger can rely on "the only way to mutate is to
    re-insert" — keeps audit / dedup logic simple.
    """

    signature: str
    signal_id: str
    severity: str
    summary: str
    first_seen_at: str
    last_seen_at: str
    occurrence_count: int = 1
    status: ProblemStatus = ProblemStatus.DETECTED
    evidence: Mapping[str, Any] = field(default_factory=dict)
    owner_role: Optional[str] = None
    suggested_next_action: Optional[str] = None
    approval_scope: Optional[str] = None  # "delegated_ok" | "needs_human" | "blocked"
    delegated_ok: bool = False
    retry_count: int = 0
    worktree_branch: Optional[str] = None
    related_session_ids: Tuple[str, ...] = ()
    related_job_ids: Tuple[str, ...] = ()
    related_pr_urls: Tuple[str, ...] = ()
    last_error: Optional[str] = None
    last_status_change_at: str = ""

    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def to_payload(self) -> Mapping[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["related_session_ids"] = list(self.related_session_ids)
        payload["related_job_ids"] = list(self.related_job_ids)
        payload["related_pr_urls"] = list(self.related_pr_urls)
        payload["evidence"] = dict(self.evidence)
        return payload

    @classmethod
    def from_payload(cls, data: Mapping[str, Any]) -> "ProblemObject":
        status_raw = str(data.get("status") or ProblemStatus.DETECTED.value)
        try:
            status = ProblemStatus(status_raw)
        except ValueError:
            status = ProblemStatus.DETECTED
        return cls(
            signature=str(data.get("signature") or ""),
            signal_id=str(data.get("signal_id") or ""),
            severity=str(data.get("severity") or "medium"),
            summary=str(data.get("summary") or ""),
            first_seen_at=str(data.get("first_seen_at") or ""),
            last_seen_at=str(data.get("last_seen_at") or ""),
            occurrence_count=int(data.get("occurrence_count") or 1),
            status=status,
            evidence=dict(data.get("evidence") or {}),
            owner_role=_optional_str(data.get("owner_role")),
            suggested_next_action=_optional_str(
                data.get("suggested_next_action")
            ),
            approval_scope=_optional_str(data.get("approval_scope")),
            delegated_ok=bool(data.get("delegated_ok") or False),
            retry_count=int(data.get("retry_count") or 0),
            worktree_branch=_optional_str(data.get("worktree_branch")),
            related_session_ids=tuple(
                str(s)
                for s in (data.get("related_session_ids") or ())
                if isinstance(s, str) and s
            ),
            related_job_ids=tuple(
                str(s)
                for s in (data.get("related_job_ids") or ())
                if isinstance(s, str) and s
            ),
            related_pr_urls=tuple(
                str(s)
                for s in (data.get("related_pr_urls") or ())
                if isinstance(s, str) and s
            ),
            last_error=_optional_str(data.get("last_error")),
            last_status_change_at=str(data.get("last_status_change_at") or ""),
        )


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


class ProblemLedger:
    """In-process registry of :class:`ProblemObject` rows.

    Lookup is by ``signature``. The ledger never enforces lifecycle
    state machine transitions — the caller (runtime loop / tech-lead /
    executor) owns those — but it does provide convenience helpers for
    incrementing occurrence counters and recording status changes.

    Optionally persists to a JSON file (``ledger_path``). On load the
    file is read once; on each mutation the new state is dumped. Disk
    failures are logged and swallowed so a broken disk never crashes
    the supervisor.
    """

    def __init__(self, *, ledger_path: Optional[Path] = None):
        self._items: dict[str, ProblemObject] = {}
        self._path = ledger_path
        if ledger_path is not None and ledger_path.exists():
            self._load_from_disk()

    # -- public API -------------------------------------------------------

    def register_or_update(
        self,
        *,
        signal_id: str,
        severity: str,
        summary: str,
        evidence: Mapping[str, Any] = (),
        salt: str = "",
        now: Optional[datetime] = None,
    ) -> Tuple[ProblemObject, bool]:
        """Register a new problem or bump an existing one.

        Returns ``(problem, is_new)``. ``is_new`` True iff this signature
        hadn't been seen before this call.
        """

        when = _format_iso(now or _utc_now())
        signature = build_problem_signature(
            signal_id=signal_id, evidence=evidence, salt=salt
        )
        existing = self._items.get(signature)
        if existing is None:
            new_obj = ProblemObject(
                signature=signature,
                signal_id=signal_id,
                severity=severity,
                summary=summary,
                first_seen_at=when,
                last_seen_at=when,
                occurrence_count=1,
                status=ProblemStatus.DETECTED,
                evidence=dict(evidence or {}),
                last_status_change_at=when,
            )
            self._items[signature] = new_obj
            self._persist()
            return new_obj, True
        # Don't re-bump terminal problems — let suppression hold.
        if existing.is_terminal():
            return existing, False
        bumped = replace(
            existing,
            last_seen_at=when,
            occurrence_count=existing.occurrence_count + 1,
            severity=severity or existing.severity,
            summary=summary or existing.summary,
            evidence={**existing.evidence, **(evidence or {})},
        )
        self._items[signature] = bumped
        self._persist()
        return bumped, False

    def get(self, signature: str) -> Optional[ProblemObject]:
        return self._items.get(signature)

    def all(self) -> Tuple[ProblemObject, ...]:
        return tuple(self._items.values())

    def open_problems(self) -> Tuple[ProblemObject, ...]:
        return tuple(p for p in self._items.values() if not p.is_terminal())

    def by_status(self, status: ProblemStatus) -> Tuple[ProblemObject, ...]:
        return tuple(p for p in self._items.values() if p.status == status)

    def transition(
        self,
        signature: str,
        *,
        status: ProblemStatus,
        owner_role: Optional[str] = None,
        suggested_next_action: Optional[str] = None,
        approval_scope: Optional[str] = None,
        delegated_ok: Optional[bool] = None,
        worktree_branch: Optional[str] = None,
        related_session_ids: Optional[Iterable[str]] = None,
        related_job_ids: Optional[Iterable[str]] = None,
        related_pr_urls: Optional[Iterable[str]] = None,
        last_error: Optional[str] = None,
        increment_retry: bool = False,
        now: Optional[datetime] = None,
    ) -> Optional[ProblemObject]:
        """Mutate a problem's status / metadata. Returns the new value.

        Unknown signatures return None — callers should not silently
        create a problem from a transition request.
        """

        existing = self._items.get(signature)
        if existing is None:
            return None
        when = _format_iso(now or _utc_now())
        updated = replace(
            existing,
            status=status,
            owner_role=owner_role if owner_role is not None else existing.owner_role,
            suggested_next_action=(
                suggested_next_action
                if suggested_next_action is not None
                else existing.suggested_next_action
            ),
            approval_scope=(
                approval_scope
                if approval_scope is not None
                else existing.approval_scope
            ),
            delegated_ok=(
                bool(delegated_ok)
                if delegated_ok is not None
                else existing.delegated_ok
            ),
            worktree_branch=(
                worktree_branch
                if worktree_branch is not None
                else existing.worktree_branch
            ),
            related_session_ids=_merge_tuple(
                existing.related_session_ids, related_session_ids
            ),
            related_job_ids=_merge_tuple(
                existing.related_job_ids, related_job_ids
            ),
            related_pr_urls=_merge_tuple(
                existing.related_pr_urls, related_pr_urls
            ),
            last_error=(
                last_error if last_error is not None else existing.last_error
            ),
            retry_count=(
                existing.retry_count + 1
                if increment_retry
                else existing.retry_count
            ),
            last_status_change_at=when,
        )
        self._items[signature] = updated
        self._persist()
        return updated

    def suppress(self, signature: str, *, now: Optional[datetime] = None) -> Optional[ProblemObject]:
        """Mark a problem as suppressed — the loop will not act on it
        again. Used when the operator manually closes a problem or the
        detector logic decides it's a false positive.
        """

        return self.transition(
            signature, status=ProblemStatus.SUPPRESSED, now=now
        )

    def clear(self) -> None:
        """Reset the ledger — used by tests + by operator action."""

        self._items.clear()
        self._persist()

    # -- counters surface for status post --------------------------------

    def summary_counters(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for problem in self._items.values():
            counts[problem.status.value] = counts.get(problem.status.value, 0) + 1
        counts["total"] = len(self._items)
        return counts

    # -- persistence -----------------------------------------------------

    def _load_from_disk(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw or "{}")
        except Exception:  # noqa: BLE001 - corrupt file → start fresh
            logger.warning(
                "ProblemLedger: failed to read ledger from %s; starting fresh",
                self._path,
                exc_info=True,
            )
            return
        items = data.get("problems") if isinstance(data, Mapping) else None
        if not isinstance(items, list):
            return
        for entry in items:
            if not isinstance(entry, Mapping):
                continue
            try:
                problem = ProblemObject.from_payload(entry)
            except Exception:  # noqa: BLE001 - skip malformed row
                continue
            if not problem.signature:
                continue
            self._items[problem.signature] = problem

    def _persist(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "problems": [p.to_payload() for p in self._items.values()],
                "saved_at": _format_iso(_utc_now()),
            }
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001 - disk full / read-only → log
            logger.warning(
                "ProblemLedger: failed to persist to %s",
                self._path,
                exc_info=True,
            )


def _merge_tuple(
    existing: Tuple[str, ...], new: Optional[Iterable[str]]
) -> Tuple[str, ...]:
    if new is None:
        return existing
    seen: list[str] = list(existing)
    for value in new:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.append(text)
    return tuple(seen)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def default_ledger_path(*, env: Optional[Mapping[str, str]] = None) -> Path:
    """Default location: alongside the runtime SQLite cache.

    Operator can override via ``YULE_SELF_IMPROVEMENT_LEDGER_PATH``.
    Tests pass an explicit Path so they never touch the real .cache dir.
    """

    env = env if env is not None else os.environ
    override = (env.get("YULE_SELF_IMPROVEMENT_LEDGER_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    cache = (env.get("YULE_CACHE_DB_PATH") or "").strip()
    if cache:
        return Path(cache).expanduser().parent / "self_improvement_problems.json"
    return Path(".cache/yule/self_improvement_problems.json")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


def _format_iso(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat()


__all__ = (
    "ProblemLedger",
    "ProblemObject",
    "ProblemStatus",
    "build_problem_signature",
    "default_ledger_path",
)
