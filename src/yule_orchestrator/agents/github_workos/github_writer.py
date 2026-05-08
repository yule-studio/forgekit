"""GitHub write adapter — G3.

Wraps a Protocol-based :class:`GithubClient` so the rest of the
adapter (actions / branching / PR template) never touches the live
GitHub REST surface directly. Production wires the client to a
PyGithub or HTTPX-backed implementation; tests inject a stub.

Responsibilities:

  * Default to ``dry_run=True``. A real write needs both
    ``live=True`` (caller intent) AND a policy gate that returns
    ``allowed=True`` for the action's autonomy level.
  * Refuse protected branches as ref targets (delegates to
    :func:`branching.is_protected_branch`).
  * Map HTTP failure codes to short, operator-readable messages with
    cause / impact / recovery hints — without leaking response bodies
    that may carry tokens.
  * Strip Authorization headers, bearer tokens, and PEM bodies from
    every audit / log surface (delegates to
    :func:`audit.redact_secrets`).

The writer *does not* itself persist audit rows — it returns a
:class:`GithubWriteResult` and a half-built
:class:`audit.GithubWriteAudit` so the caller (typically
:func:`actions.execute_github_action_plan`) can route the audit row
through whatever sink the surrounding runtime uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import (
    Any,
    Iterable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from .audit import (
    ACTION_GITHUB_BRANCH_CREATE,
    ACTION_GITHUB_COMMIT_CREATE,
    ACTION_GITHUB_ISSUE_COMMENT,
    ACTION_GITHUB_LABEL_ADD,
    ACTION_GITHUB_PR_DRAFT_CREATE,
    GithubWriteAudit,
    OUTCOME_DENIED_BY_POLICY,
    OUTCOME_DENIED_PROTECTED_BRANCH,
    OUTCOME_DRY_RUN,
    OUTCOME_FAILED,
    OUTCOME_OK,
    build_github_audit_record,
    redact_secrets,
)
from .branching import is_protected_branch


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocols — loose coupling with G1 / live REST client
# ---------------------------------------------------------------------------


class GithubClient(Protocol):
    """Minimum surface the writer needs from a live GitHub client.

    Concrete implementations may carry more methods (the production
    PyGithub-backed client does); the writer only calls these.

    Each method returns an arbitrary dict-like response — the writer
    pulls ``status``, ``url``, ``id``, ``number`` etc. defensively so a
    response shape mismatch raises a single :class:`GithubWriteError`
    instead of cascading attribute errors.
    """

    def create_issue_comment(
        self, *, repo: str, issue_number: int, body: str
    ) -> Mapping[str, Any]:
        ...

    def add_labels(
        self, *, repo: str, issue_number: int, labels: Sequence[str]
    ) -> Mapping[str, Any]:
        ...

    def create_branch_ref(
        self, *, repo: str, branch: str, base_sha: str
    ) -> Mapping[str, Any]:
        ...

    def create_commit_via_data_api(
        self,
        *,
        repo: str,
        branch: str,
        message: str,
        tree: Mapping[str, Any],
        author: Mapping[str, Any],
        committer: Mapping[str, Any],
        parents: Sequence[str],
    ) -> Mapping[str, Any]:
        ...

    def create_draft_pull_request(
        self,
        *,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> Mapping[str, Any]:
        ...


# ---------------------------------------------------------------------------
# Policy gate Protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyGateDecision:
    allowed: bool
    reason: str = ""
    autonomy_level: str = ""

    @property
    def denied(self) -> bool:
        return not self.allowed


class PolicyGate(Protocol):
    """Decide whether (action, autonomy_level) may execute.

    The policy gate is the single chokepoint between "the writer
    knows how to call GitHub" and "this specific action should run
    right now". It should encapsulate:

      * Autonomy mapping (L0 → dry-run only, L1 → comments, L2 →
        branches/draft PR, L3 → ready PR / push / merge requires
        human approval).
      * Repo allow-list / ownership checks.
      * Approval token verification for L3.

    A simple :func:`make_default_policy_gate` is provided so callers
    that haven't wired G2/G6 yet still get a sensible default.
    """

    def __call__(
        self, *, action: str, autonomy_level: str, repo: Optional[str] = None
    ) -> PolicyGateDecision:
        ...


# ---------------------------------------------------------------------------
# Default policy gate
# ---------------------------------------------------------------------------


_AUTONOMY_RANK: Mapping[str, int] = {
    "L0": 0,
    "L1": 1,
    "L2": 2,
    "L3": 3,
    "L4": 4,
}


def _autonomy_rank(level: Optional[str]) -> int:
    return _AUTONOMY_RANK.get((level or "").strip().upper(), 0)


# Minimum autonomy level required to even *consider* an action. The
# policy gate may refuse for additional reasons (repo allow-list,
# approval token absent, etc.); these are the floor.
_DEFAULT_MIN_AUTONOMY: Mapping[str, str] = {
    ACTION_GITHUB_ISSUE_COMMENT: "L1",
    ACTION_GITHUB_LABEL_ADD: "L1",
    ACTION_GITHUB_BRANCH_CREATE: "L2",
    ACTION_GITHUB_COMMIT_CREATE: "L2",
    ACTION_GITHUB_PR_DRAFT_CREATE: "L2",
    # L3 actions live in audit constants but the writer doesn't
    # implement them — they require an approval-routed runner that's
    # G6's territory.
    "github_pr_ready": "L3",
    "github_push": "L3",
    "github_merge": "L3",
}


def make_default_policy_gate(
    *,
    allowed_repos: Optional[Sequence[str]] = None,
    require_approval_for_l3: bool = True,
    approval_token: Optional[str] = None,
) -> PolicyGate:
    """Return a simple :class:`PolicyGate` callable.

    * Refuses actions whose minimum autonomy isn't met.
    * Refuses unknown actions (deny by default).
    * Optionally restricts to *allowed_repos*.
    * Refuses L3 actions unless *approval_token* is non-empty (and
      *require_approval_for_l3* is True).

    Production wiring eventually replaces this with a G6 approval
    router; this stub keeps G3 testable without that dependency.
    """

    repos = (
        tuple(str(r).strip().lower() for r in allowed_repos if str(r).strip())
        if allowed_repos
        else None
    )

    def _gate(
        *,
        action: str,
        autonomy_level: str,
        repo: Optional[str] = None,
    ) -> PolicyGateDecision:
        action = (action or "").strip()
        level = (autonomy_level or "").strip().upper()
        if not action or action not in _DEFAULT_MIN_AUTONOMY:
            return PolicyGateDecision(
                allowed=False,
                reason=f"unknown action {action!r}; default policy denies",
                autonomy_level=level,
            )
        if repos is not None:
            if not repo or str(repo).strip().lower() not in repos:
                return PolicyGateDecision(
                    allowed=False,
                    reason=f"repo {repo!r} not in allow-list",
                    autonomy_level=level,
                )
        min_required = _DEFAULT_MIN_AUTONOMY[action]
        if _autonomy_rank(level) < _autonomy_rank(min_required):
            return PolicyGateDecision(
                allowed=False,
                reason=(
                    f"action {action} requires {min_required}, caller has "
                    f"{level or 'L0'}"
                ),
                autonomy_level=level,
            )
        if (
            require_approval_for_l3
            and _autonomy_rank(level) >= _autonomy_rank("L3")
            and not (approval_token or "").strip()
        ):
            return PolicyGateDecision(
                allowed=False,
                reason="L3+ action requires an approval token",
                autonomy_level=level,
            )
        return PolicyGateDecision(
            allowed=True,
            reason=f"allowed (min={min_required})",
            autonomy_level=level,
        )

    return _gate


# ---------------------------------------------------------------------------
# Result + error
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GithubWriteResult:
    """What :class:`GithubWriter` returns for one attempted action."""

    ok: bool
    outcome: str
    detail: str = ""
    body: Mapping[str, Any] = field(default_factory=dict)
    audit: Optional[GithubWriteAudit] = None

    @property
    def succeeded(self) -> bool:
        return self.ok and self.outcome == OUTCOME_OK


class GithubWriteError(Exception):
    """Raised by client wrappers on shape mismatch.

    The writer catches this internally and converts it into a
    :class:`GithubWriteResult` with ``outcome="failed"`` so the caller
    handles a single result type.
    """


# ---------------------------------------------------------------------------
# HTTP status mapping
# ---------------------------------------------------------------------------


_HTTP_FRIENDLY: Mapping[int, Tuple[str, str, str]] = {
    401: (
        "GitHub 인증 실패",
        "App 토큰이 만료되었거나 잘못 발급되었을 수 있습니다.",
        "App private key / installation token 갱신 후 재시도.",
    ),
    403: (
        "GitHub 권한 거부",
        "App에 해당 repo / 작업에 대한 권한이 없습니다.",
        "Installation 권한 (issues:write / contents:write / pull_requests:write) 확인.",
    ),
    404: (
        "GitHub 자원 없음",
        "지정한 repo / issue / branch / commit이 존재하지 않거나 App이 접근할 수 없습니다.",
        "repo 이름, issue 번호, branch SHA를 다시 확인.",
    ),
    409: (
        "GitHub 충돌",
        "branch가 이미 존재하거나 비교 base가 변경됐습니다.",
        "collision-suffix branch 이름으로 재시도하거나 base SHA 갱신.",
    ),
    422: (
        "GitHub validation 실패",
        "request body 형식이 GitHub API 스펙과 어긋납니다.",
        "PR title 또는 commit author/committer 필드 점검.",
    ),
    429: (
        "GitHub rate limit",
        "App 단위 또는 IP 단위 호출 한도 도달.",
        "Retry-After 헤더 만큼 대기 후 backoff.",
    ),
    500: ("GitHub 서버 오류", "GitHub 측 일시 장애.", "잠시 후 재시도."),
    502: (
        "GitHub bad gateway",
        "GitHub 측 일시 장애.",
        "잠시 후 재시도; 반복 시 https://www.githubstatus.com 확인.",
    ),
    503: (
        "GitHub 서비스 불가",
        "GitHub 측 일시 장애.",
        "잠시 후 재시도; 운영자에게 githubstatus.com 확인 요청.",
    ),
    504: (
        "GitHub timeout",
        "API 응답 시간 초과 — 멱등성 가능한 작업이면 재시도.",
        "create_branch / create_comment 같은 멱등 작업은 retry, write 작업은 응답 sha 확인 후 결정.",
    ),
}


def map_http_status_to_friendly(status: Optional[int]) -> str:
    """Return a one-line operator-readable summary for *status*.

    Returns the literal status code with ``"unmapped"`` suffix when
    the code isn't in the table — operator still gets a hint without
    the writer pretending to know more than it does.
    """

    if status is None:
        return "unknown HTTP status"
    entry = _HTTP_FRIENDLY.get(int(status))
    if entry is None:
        if 500 <= int(status) < 600:
            return f"HTTP {status} (5xx — unmapped server error; retry then escalate)"
        if 400 <= int(status) < 500:
            return f"HTTP {status} (4xx — unmapped client error; check request shape)"
        return f"HTTP {status} (unmapped)"
    name, cause, recovery = entry
    return f"{name} (cause: {cause}; recover: {recovery})"


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class GithubWriter:
    """Adapter around a :class:`GithubClient`.

    Construction:

        writer = GithubWriter(client=live_client, dry_run=False, live=True,
                              policy_gate=make_default_policy_gate(...))

    Each public method returns a :class:`GithubWriteResult` that
    carries:

      * ``ok`` — overall success / failure for the caller's flow.
      * ``outcome`` — granular bucket for audit (dry_run / ok /
        denied_by_policy / denied_protected_branch / failed).
      * ``audit`` — pre-built :class:`audit.GithubWriteAudit` ready to
        flush to ``session.extra['agent_ops_audit']`` or the audit
        worker queue.

    Default ``dry_run=True`` AND default ``live=False`` → the writer
    refuses any GitHub call without explicit caller intent. Tests rely
    on this default to ensure no test accidentally hits the live API.
    """

    def __init__(
        self,
        *,
        client: Optional[GithubClient] = None,
        policy_gate: Optional[PolicyGate] = None,
        dry_run: bool = True,
        live: bool = False,
        actor_role: str = "engineering-agent",
    ) -> None:
        self._client = client
        self._policy_gate = policy_gate or make_default_policy_gate()
        self._dry_run = bool(dry_run)
        self._live = bool(live)
        self._actor_role = str(actor_role or "engineering-agent")

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def live(self) -> bool:
        return self._live

    @property
    def actor_role(self) -> str:
        return self._actor_role

    @property
    def client(self) -> Optional[GithubClient]:
        return self._client

    # ------------------------------------------------------------------
    # Public actions
    # ------------------------------------------------------------------

    def post_issue_comment(
        self,
        *,
        repo: str,
        issue_number: int,
        body: str,
        autonomy_level: str = "L1",
        session_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> GithubWriteResult:
        return self._run(
            action=ACTION_GITHUB_ISSUE_COMMENT,
            autonomy_level=autonomy_level,
            repo=repo,
            issue_number=issue_number,
            session_id=session_id,
            decision_id=decision_id,
            branch=None,
            summary=f"comment on {repo}#{issue_number}",
            client_call=lambda: self._require_client().create_issue_comment(
                repo=repo, issue_number=int(issue_number), body=body
            ),
        )

    def add_labels(
        self,
        *,
        repo: str,
        issue_number: int,
        labels: Sequence[str],
        autonomy_level: str = "L1",
        session_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> GithubWriteResult:
        cleaned = tuple(
            str(l).strip() for l in labels if str(l).strip()
        )
        return self._run(
            action=ACTION_GITHUB_LABEL_ADD,
            autonomy_level=autonomy_level,
            repo=repo,
            issue_number=issue_number,
            session_id=session_id,
            decision_id=decision_id,
            branch=None,
            summary=f"add labels {list(cleaned)} to {repo}#{issue_number}",
            client_call=lambda: self._require_client().add_labels(
                repo=repo, issue_number=int(issue_number), labels=cleaned
            ),
        )

    def create_branch(
        self,
        *,
        repo: str,
        branch: str,
        base_sha: str,
        autonomy_level: str = "L2",
        session_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> GithubWriteResult:
        if is_protected_branch(branch):
            return self._denied_protected(
                action=ACTION_GITHUB_BRANCH_CREATE,
                autonomy_level=autonomy_level,
                repo=repo,
                branch=branch,
                session_id=session_id,
                decision_id=decision_id,
                summary=f"refuse branch creation on protected ref {branch!r}",
            )
        return self._run(
            action=ACTION_GITHUB_BRANCH_CREATE,
            autonomy_level=autonomy_level,
            repo=repo,
            issue_number=None,
            session_id=session_id,
            decision_id=decision_id,
            branch=branch,
            summary=f"create branch {branch} on {repo} (base={base_sha[:7]})",
            client_call=lambda: self._require_client().create_branch_ref(
                repo=repo, branch=branch, base_sha=base_sha
            ),
        )

    def create_commit(
        self,
        *,
        repo: str,
        branch: str,
        message: str,
        tree: Mapping[str, Any],
        author: Mapping[str, Any],
        committer: Mapping[str, Any],
        parents: Sequence[str],
        autonomy_level: str = "L2",
        session_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> GithubWriteResult:
        if is_protected_branch(branch):
            return self._denied_protected(
                action=ACTION_GITHUB_COMMIT_CREATE,
                autonomy_level=autonomy_level,
                repo=repo,
                branch=branch,
                session_id=session_id,
                decision_id=decision_id,
                summary=f"refuse commit on protected ref {branch!r}",
            )
        return self._run(
            action=ACTION_GITHUB_COMMIT_CREATE,
            autonomy_level=autonomy_level,
            repo=repo,
            issue_number=None,
            session_id=session_id,
            decision_id=decision_id,
            branch=branch,
            summary=f"commit on {repo}@{branch}",
            client_call=lambda: self._require_client().create_commit_via_data_api(
                repo=repo,
                branch=branch,
                message=message,
                tree=tree,
                author=dict(author),
                committer=dict(committer),
                parents=tuple(parents),
            ),
        )

    def create_draft_pull_request(
        self,
        *,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        autonomy_level: str = "L2",
        session_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> GithubWriteResult:
        if is_protected_branch(head):
            return self._denied_protected(
                action=ACTION_GITHUB_PR_DRAFT_CREATE,
                autonomy_level=autonomy_level,
                repo=repo,
                branch=head,
                session_id=session_id,
                decision_id=decision_id,
                summary=f"refuse draft PR from protected head ref {head!r}",
            )
        return self._run(
            action=ACTION_GITHUB_PR_DRAFT_CREATE,
            autonomy_level=autonomy_level,
            repo=repo,
            issue_number=None,
            session_id=session_id,
            decision_id=decision_id,
            branch=head,
            summary=f"draft PR {repo}: {head} → {base}",
            client_call=lambda: self._require_client().create_draft_pull_request(
                repo=repo,
                head=head,
                base=base,
                title=title,
                body=body,
                draft=True,
            ),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_client(self) -> GithubClient:
        if self._client is None:
            raise GithubWriteError("GithubWriter has no client configured")
        return self._client

    def _run(
        self,
        *,
        action: str,
        autonomy_level: str,
        repo: str,
        issue_number: Optional[int],
        session_id: Optional[str],
        decision_id: Optional[str],
        branch: Optional[str],
        summary: str,
        client_call,
    ) -> GithubWriteResult:
        decision = self._policy_gate(
            action=action, autonomy_level=autonomy_level, repo=repo
        )
        if decision.denied:
            return self._build_denied_result(
                action=action,
                autonomy_level=autonomy_level,
                repo=repo,
                issue_number=issue_number,
                branch=branch,
                session_id=session_id,
                decision_id=decision_id,
                summary=summary,
                reason=decision.reason,
            )

        # Default behaviour: dry_run unless caller flips both
        # ``dry_run=False`` and ``live=True``. This belt-and-suspenders
        # default is intentional — a misconfigured production deploy
        # that forgets one of the flags must NOT silently start
        # writing to GitHub.
        if self._dry_run or not self._live:
            audit = build_github_audit_record(
                action=action,
                actor_role=self._actor_role,
                autonomy_level=autonomy_level,
                policy_reason=decision.reason or "policy allowed",
                target_repo=repo,
                issue_number=issue_number,
                session_id=session_id,
                pr_number=None,
                branch=branch,
                dry_run=True,
                outcome=OUTCOME_DRY_RUN,
                summary=summary,
                decision_id=decision_id,
            )
            return GithubWriteResult(
                ok=True,
                outcome=OUTCOME_DRY_RUN,
                detail="dry_run — no client call made",
                body={},
                audit=audit,
            )

        try:
            response = client_call() or {}
        except GithubWriteError as exc:
            return self._build_failed_result(
                action=action,
                autonomy_level=autonomy_level,
                repo=repo,
                issue_number=issue_number,
                branch=branch,
                session_id=session_id,
                decision_id=decision_id,
                summary=summary,
                reason=decision.reason or "policy allowed",
                exc=exc,
                response=None,
            )
        except Exception as exc:  # noqa: BLE001 - convert to result, never propagate
            logger.warning(
                "GithubWriter: client call raised for action=%s repo=%s",
                action,
                repo,
                exc_info=True,
            )
            return self._build_failed_result(
                action=action,
                autonomy_level=autonomy_level,
                repo=repo,
                issue_number=issue_number,
                branch=branch,
                session_id=session_id,
                decision_id=decision_id,
                summary=summary,
                reason=decision.reason or "policy allowed",
                exc=exc,
                response=None,
            )

        status = _safe_int(response.get("status"))
        body = response.get("body") if isinstance(response, Mapping) else None
        if status is not None and status >= 400:
            return self._build_failed_result(
                action=action,
                autonomy_level=autonomy_level,
                repo=repo,
                issue_number=issue_number,
                branch=branch,
                session_id=session_id,
                decision_id=decision_id,
                summary=summary,
                reason=decision.reason or "policy allowed",
                exc=None,
                response=response,
                http_status=status,
            )

        pr_number = _safe_int(response.get("number")) if isinstance(response, Mapping) else None
        url_ref = response.get("url") or response.get("html_url") if isinstance(response, Mapping) else None
        references: Tuple[str, ...] = (str(url_ref),) if isinstance(url_ref, str) and url_ref else ()
        audit = build_github_audit_record(
            action=action,
            actor_role=self._actor_role,
            autonomy_level=autonomy_level,
            policy_reason=decision.reason or "policy allowed",
            target_repo=repo,
            issue_number=issue_number,
            session_id=session_id,
            pr_number=pr_number,
            branch=branch,
            dry_run=False,
            outcome=OUTCOME_OK,
            summary=summary,
            references=references,
            decision_id=decision_id,
        )
        # The body we hand back is redacted — the response can
        # carry caller-supplied content (PR body, etc.) but never a
        # token; redact_secrets is idempotent so a clean body
        # passes through unchanged.
        safe_body = redact_secrets(dict(response) if isinstance(response, Mapping) else {})
        return GithubWriteResult(
            ok=True,
            outcome=OUTCOME_OK,
            detail="",
            body=safe_body,
            audit=audit,
        )

    def _build_denied_result(
        self,
        *,
        action: str,
        autonomy_level: str,
        repo: str,
        issue_number: Optional[int],
        branch: Optional[str],
        session_id: Optional[str],
        decision_id: Optional[str],
        summary: str,
        reason: str,
    ) -> GithubWriteResult:
        audit = build_github_audit_record(
            action=action,
            actor_role=self._actor_role,
            autonomy_level=autonomy_level,
            policy_reason=reason,
            target_repo=repo,
            issue_number=issue_number,
            session_id=session_id,
            pr_number=None,
            branch=branch,
            dry_run=self._dry_run or not self._live,
            outcome=OUTCOME_DENIED_BY_POLICY,
            summary=summary,
            decision_id=decision_id,
        )
        return GithubWriteResult(
            ok=False,
            outcome=OUTCOME_DENIED_BY_POLICY,
            detail=redact_secrets(reason),
            body={},
            audit=audit,
        )

    def _denied_protected(
        self,
        *,
        action: str,
        autonomy_level: str,
        repo: str,
        branch: str,
        session_id: Optional[str],
        decision_id: Optional[str],
        summary: str,
    ) -> GithubWriteResult:
        audit = build_github_audit_record(
            action=action,
            actor_role=self._actor_role,
            autonomy_level=autonomy_level,
            policy_reason=f"protected branch {branch!r} — refuse to write",
            target_repo=repo,
            issue_number=None,
            session_id=session_id,
            pr_number=None,
            branch=branch,
            dry_run=self._dry_run or not self._live,
            outcome=OUTCOME_DENIED_PROTECTED_BRANCH,
            summary=summary,
            decision_id=decision_id,
        )
        return GithubWriteResult(
            ok=False,
            outcome=OUTCOME_DENIED_PROTECTED_BRANCH,
            detail=f"protected branch {branch!r}",
            body={},
            audit=audit,
        )

    def _build_failed_result(
        self,
        *,
        action: str,
        autonomy_level: str,
        repo: str,
        issue_number: Optional[int],
        branch: Optional[str],
        session_id: Optional[str],
        decision_id: Optional[str],
        summary: str,
        reason: str,
        exc: Optional[BaseException],
        response: Optional[Mapping[str, Any]],
        http_status: Optional[int] = None,
    ) -> GithubWriteResult:
        if http_status is not None:
            detail = map_http_status_to_friendly(http_status)
        elif exc is not None:
            detail = f"{type(exc).__name__}: {redact_secrets(str(exc))}"
        else:
            detail = "unknown failure (no HTTP status, no exception)"
        audit = build_github_audit_record(
            action=action,
            actor_role=self._actor_role,
            autonomy_level=autonomy_level,
            policy_reason=reason,
            target_repo=repo,
            issue_number=issue_number,
            session_id=session_id,
            pr_number=None,
            branch=branch,
            dry_run=False,
            outcome=OUTCOME_FAILED,
            summary=f"{summary} — failed: {detail}",
            decision_id=decision_id,
        )
        # Never store the raw response — it can carry an Authorization
        # header echo or a token in the error message.
        safe_body = redact_secrets(dict(response)) if isinstance(response, Mapping) else {}
        return GithubWriteResult(
            ok=False,
            outcome=OUTCOME_FAILED,
            detail=detail,
            body=safe_body,
            audit=audit,
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = (
    "GithubClient",
    "GithubWriteError",
    "GithubWriteResult",
    "GithubWriter",
    "PolicyGate",
    "PolicyGateDecision",
    "make_default_policy_gate",
    "map_http_status_to_friendly",
)
