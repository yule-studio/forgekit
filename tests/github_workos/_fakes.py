"""Fakes + Protocols for the G1–G4 GitHub work-order pipeline.

The G1 (auth) → G2 (issue triage) → G3 (work-order executor) → G4
(Discord intake) production code is being built in parallel worktrees.
G5 (this module's home) has to validate the *seams between* those
layers without depending on whichever layer happens to land first.

Two design decisions follow from that:

  * Every interaction the harness needs (GitHub, Discord, Obsidian,
    senior-quality verdicts, branch/PR planning) is expressed here as
    a small :class:`typing.Protocol` so individual G1–G4 worktrees
    can each ship a concrete implementation without prearranging a
    shared base class.
  * The fakes record every call so the test asserts behaviour by
    inspecting the recorded log — no real network, no real subprocess,
    no real vault file outside the test's tempdir.

Important hard-rails the fakes enforce, mirroring the production
contract documented in the G5 brief:

  * **No live GitHub.** :class:`FakeGitHubAPI` raises if any helper
    tries to hit the network — caller paths must go through it.
  * **No main-branch push / no force push.** Both refused at the fake
    GitHub layer with a deterministic exception so tests asserting
    the safety contract see a real failure surface, not a silent
    no-op.
  * **No secret echo.** :func:`redact_secret_blob` strips obvious
    PEM blocks / GitHub PAT / Discord-token shapes. Any call that
    surfaces an error string must run through it.
  * **Dry-run by default.** :class:`FakeWorkOrderExecutor.run`
    starts dry-run unless the caller explicitly opts in, and asserts
    the executor under test does the same.

All public names are exported via ``__all__`` so the test modules
import a single symbol set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)


# ---------------------------------------------------------------------------
# Secret redaction — every error / log path must run through this
# ---------------------------------------------------------------------------


_PEM_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----",
    re.DOTALL,
)
_GH_PAT_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")
_GH_INSTALL_TOKEN_RE = re.compile(r"\bghs_[A-Za-z0-9]{20,}\b")
_DISCORD_BOT_TOKEN_RE = re.compile(
    r"\b(?:Bot\s+)?[A-Za-z0-9._-]{20,}\.[A-Za-z0-9._-]{6}\.[A-Za-z0-9._-]{27}\b"
)
_AUTH_BEARER_RE = re.compile(r"(?i)\bauthorization:\s*bearer\s+\S+")


def redact_secret_blob(text: str) -> str:
    """Scrub the obvious GitHub / Discord secret shapes from *text*.

    Used by every fake's error path so a test that intentionally
    exercises a credential can assert "redacted" appears in the
    surfaced output and "ghp_..." / "-----BEGIN" never does.
    """

    if not text:
        return text or ""
    out = _PEM_BLOCK_RE.sub("[REDACTED PEM]", text)
    out = _GH_PAT_RE.sub("[REDACTED gh-pat]", out)
    out = _GH_INSTALL_TOKEN_RE.sub("[REDACTED gh-install-token]", out)
    out = _DISCORD_BOT_TOKEN_RE.sub("[REDACTED discord-token]", out)
    out = _AUTH_BEARER_RE.sub("Authorization: Bearer [REDACTED]", out)
    return out


# ---------------------------------------------------------------------------
# G1 — GitHub App auth contract
# ---------------------------------------------------------------------------


class GitHubAppAuthProtocol(Protocol):
    """Minimum surface every G1 implementation must expose.

    The token is obtained from a GitHub App installation triple
    (``app_id``, ``installation_id``, ``private_key_pem``). The
    production implementation reads the PEM from disk; the fake
    accepts it inline. Both must redact the PEM from any error.
    """

    app_id: str
    installation_id: str

    def installation_token(self) -> str:
        """Return a short-lived installation access token."""

    def authenticated_headers(self) -> Mapping[str, str]:
        """Return the ``Authorization`` headers a caller can set."""


@dataclass
class FakeGitHubAppAuth:
    """In-memory G1 stub.

    *fail_with* is an optional callable that raises when the token
    is requested — exercises the "auth fails, redact the PEM"
    contract without involving the real PEM loader.
    """

    app_id: str = "123456"
    installation_id: str = "130485504"
    issued_token: str = "ghs_FAKE_INSTALL_TOKEN_DO_NOT_LEAK_xxxxxxxxxxxxxxxxxxxxxx"
    private_key_pem: str = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAJ7fakekeydonotuse=\n"
        "-----END RSA PRIVATE KEY-----\n"
    )
    fail_with: Optional[Callable[[], BaseException]] = None
    issued_count: int = 0

    def installation_token(self) -> str:
        if self.fail_with is not None:
            raise self.fail_with()
        self.issued_count += 1
        return self.issued_token

    def authenticated_headers(self) -> Mapping[str, str]:
        return {
            "Authorization": f"token {self.installation_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }


# ---------------------------------------------------------------------------
# Issue / repo data shapes — what the fake API hands back
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeIssue:
    number: int
    title: str
    body: str
    labels: Tuple[str, ...] = ()
    author: str = "masterway"
    state: str = "open"


@dataclass
class FakeBranch:
    name: str
    base: str
    sha: str = "0000000000000000000000000000000000000000"


@dataclass
class FakePullRequest:
    number: int
    title: str
    body: str
    head: str
    base: str
    draft: bool = True
    state: str = "open"


# ---------------------------------------------------------------------------
# G1 + low-level GitHub helpers — fake REST API
# ---------------------------------------------------------------------------


class FakeGitHubAPIError(RuntimeError):
    """Raised by :class:`FakeGitHubAPI` when a forbidden operation is
    attempted (network call without explicit allow-list, push to
    main, force push, etc.).
    """


@dataclass
class FakeGitHubAPI:
    """In-memory replacement for the GitHub REST + git client.

    Every method records the call in :attr:`calls`. Mutation paths
    refuse main-branch pushes / force pushes / unsafe operations.
    """

    owner: str = "yule-studio"
    repo: str = "yule-studio-agent"
    main_branch: str = "main"
    issues: Dict[int, FakeIssue] = field(default_factory=dict)
    branches: Dict[str, FakeBranch] = field(default_factory=dict)
    pull_requests: Dict[int, FakePullRequest] = field(default_factory=dict)
    next_pr_number: int = 100
    calls: List[Tuple[str, Mapping[str, Any]]] = field(default_factory=list)
    forbid_network: bool = True

    # ---- issue surface ----------------------------------------------------

    def get_issue(self, number: int) -> FakeIssue:
        self.calls.append(("get_issue", {"number": number}))
        if number not in self.issues:
            raise FakeGitHubAPIError(
                f"issue #{number} not found in fake repo {self.owner}/{self.repo}"
            )
        return self.issues[number]

    def add_issue_comment(self, *, number: int, body: str) -> None:
        self.calls.append(
            (
                "add_issue_comment",
                {"number": number, "body": redact_secret_blob(body)},
            )
        )

    # ---- branch surface ---------------------------------------------------

    def create_branch(self, *, name: str, base: Optional[str] = None) -> FakeBranch:
        if name == self.main_branch:
            raise FakeGitHubAPIError(
                "refusing to create / write to main branch via the agent"
            )
        if name in self.branches:
            raise FakeGitHubAPIError(f"branch {name!r} already exists")
        branch = FakeBranch(name=name, base=base or self.main_branch)
        self.branches[name] = branch
        self.calls.append(
            ("create_branch", {"name": name, "base": branch.base})
        )
        return branch

    def push_branch(
        self,
        *,
        name: str,
        force: bool = False,
        commits: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        # Hard rails — must match the production safety contract.
        if name == self.main_branch:
            raise FakeGitHubAPIError(
                "refusing to push to main branch — agent must work on feat/* branches"
            )
        if force:
            raise FakeGitHubAPIError(
                "force push is forbidden by the agent safety policy"
            )
        if name not in self.branches:
            raise FakeGitHubAPIError(f"branch {name!r} does not exist")
        self.calls.append(
            (
                "push_branch",
                {
                    "name": name,
                    "force": force,
                    "commit_count": len(list(commits)),
                },
            )
        )

    # ---- pull-request surface --------------------------------------------

    def open_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: Optional[str] = None,
        draft: bool = True,
    ) -> FakePullRequest:
        if head == self.main_branch:
            raise FakeGitHubAPIError(
                "refusing to open PR whose head is the main branch"
            )
        target_base = base or self.main_branch
        number = self.next_pr_number
        self.next_pr_number += 1
        pr = FakePullRequest(
            number=number,
            title=title,
            body=redact_secret_blob(body),
            head=head,
            base=target_base,
            draft=draft,
        )
        self.pull_requests[number] = pr
        self.calls.append(
            (
                "open_pull_request",
                {
                    "number": number,
                    "title": title,
                    "head": head,
                    "base": target_base,
                    "draft": draft,
                },
            )
        )
        return pr

    # ---- forbidden operations --------------------------------------------

    def merge(self, *_args, **_kwargs) -> None:
        raise FakeGitHubAPIError(
            "merge is forbidden by the agent — humans merge"
        )

    def deploy(self, *_args, **_kwargs) -> None:
        raise FakeGitHubAPIError(
            "deploy is forbidden by the agent — humans deploy"
        )

    def update_secret(self, *_args, **_kwargs) -> None:
        raise FakeGitHubAPIError(
            "secret modification is forbidden by the agent"
        )

    def http_get(self, url: str) -> None:
        if self.forbid_network:
            raise FakeGitHubAPIError(
                f"refusing live HTTP GET in tests: {url}"
            )


# ---------------------------------------------------------------------------
# G2 — Triage report contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleAssignment:
    """One role's slice of the work order.

    ``role`` is the engineering-agent role id (``backend-engineer``,
    ``qa-engineer``, etc.). ``responsibilities`` is the focused
    bullet list the work-order dispatcher hands to that role's
    runner. ``deliverables`` is the artifact list (files, tests,
    docs) the role must produce.
    """

    role: str
    responsibilities: Tuple[str, ...]
    deliverables: Tuple[str, ...]


@dataclass(frozen=True)
class TriageReport:
    """G2 output. Every field is mandatory — the sender is contract-
    enforced by the senior-quality gate before dispatch.
    """

    issue_number: int
    intent: str  # "feature" | "bugfix" | "refactor" | "docs" | "research"
    scope_summary: str
    role_assignments: Tuple[RoleAssignment, ...]
    branch_name_plan: str
    dry_run: bool = True


class TriageProtocol(Protocol):
    def triage(self, *, issue: FakeIssue) -> TriageReport: ...


# ---------------------------------------------------------------------------
# G3 — Senior-quality PR plan contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailureRecoveryEntry:
    """One row in the failure-recovery matrix — every entry MUST carry
    cause / impact / recovery so the on-call doesn't hunt for a
    missing column.
    """

    cause: str
    impact: str
    recovery: str


@dataclass(frozen=True)
class SeniorQualityPRPlan:
    """The PR body schema the executor must hand back.

    Senior-quality fields (the heart of the G3 contract):

      * ``hidden_risks`` — at least one bullet describing a risk the
        casual reader would miss.
      * ``out_of_scope`` — explicit non-goals.
      * ``test_plan`` — concrete test cases / steps.
      * ``approval_required_actions`` — list of human-required actions
        (deploys, secret rotation, third-party messages).
      * ``excluded_role_rationale`` — for every engineering role the
        triage left out, why.
      * ``failure_recovery`` — at least one populated
        :class:`FailureRecoveryEntry`.

    Plus the pure-mechanical fields a PR body needs (title / summary /
    branch).
    """

    title: str
    summary: str
    branch: str
    hidden_risks: Tuple[str, ...]
    out_of_scope: Tuple[str, ...]
    test_plan: Tuple[str, ...]
    approval_required_actions: Tuple[str, ...]
    excluded_role_rationale: Mapping[str, str]
    failure_recovery: Tuple[FailureRecoveryEntry, ...]

    def to_pr_body(self) -> str:
        """Render the markdown the executor will pass to GitHub.

        Used by the dry-run audit assertions — the rendered body
        must mention every senior-quality section so a human reading
        the PR can verify the agent thought through them.
        """

        sections: list[str] = []
        sections.append(f"# {self.title}\n")
        sections.append(f"## 요약\n{self.summary}\n")
        sections.append("## 숨은 리스크")
        sections.extend(f"- {r}" for r in self.hidden_risks)
        sections.append("\n## 비범위")
        sections.extend(f"- {r}" for r in self.out_of_scope)
        sections.append("\n## 테스트 계획")
        sections.extend(f"- {step}" for step in self.test_plan)
        sections.append("\n## 사람 승인 필요 작업")
        sections.extend(f"- {a}" for a in self.approval_required_actions)
        sections.append("\n## 제외 역할 사유")
        for role, why in sorted(self.excluded_role_rationale.items()):
            sections.append(f"- **{role}** — {why}")
        sections.append("\n## 실패 복구 시나리오")
        for entry in self.failure_recovery:
            sections.append(
                f"- 원인: {entry.cause} · 영향: {entry.impact} · 복구: {entry.recovery}"
            )
        return "\n".join(sections).rstrip() + "\n"


class SeniorQualityValidationError(ValueError):
    """Raised by :func:`validate_senior_quality_plan` when a required
    field is missing.

    The error message is a human-friendly bullet list of every
    missing field so the executor can echo it back to the operator.
    """


def validate_senior_quality_plan(plan: SeniorQualityPRPlan) -> None:
    """Enforce the senior-quality contract on *plan*.

    The G3 executor (and the contract tests) call this *before*
    enqueueing the GitHub PR open. A failure here MUST stop dispatch
    — silently shipping a PR without one of these sections is the
    regression this whole gate exists to prevent.
    """

    missing: list[str] = []
    if not plan.hidden_risks or not any(s.strip() for s in plan.hidden_risks):
        missing.append("hidden_risks (숨은 리스크)")
    if not plan.out_of_scope or not any(s.strip() for s in plan.out_of_scope):
        missing.append("out_of_scope (비범위)")
    if not plan.test_plan or not any(s.strip() for s in plan.test_plan):
        missing.append("test_plan (테스트 계획)")
    if not plan.approval_required_actions:
        missing.append("approval_required_actions (사람 승인 필요 작업)")
    if not plan.excluded_role_rationale:
        missing.append("excluded_role_rationale (제외 역할 사유)")
    else:
        for role, why in plan.excluded_role_rationale.items():
            if not (why or "").strip():
                missing.append(f"excluded_role_rationale[{role}] empty")
    if not plan.failure_recovery:
        missing.append("failure_recovery (실패 복구 시나리오)")
    else:
        for idx, entry in enumerate(plan.failure_recovery):
            if not (entry.cause or "").strip():
                missing.append(f"failure_recovery[{idx}].cause")
            if not (entry.impact or "").strip():
                missing.append(f"failure_recovery[{idx}].impact")
            if not (entry.recovery or "").strip():
                missing.append(f"failure_recovery[{idx}].recovery")
    if missing:
        joined = "\n - " + "\n - ".join(missing)
        raise SeniorQualityValidationError(
            "senior-quality 검증 실패 — 누락된 필드:" + joined
        )


# ---------------------------------------------------------------------------
# G3 — fake executor that runs the dry-run audit pipeline
# ---------------------------------------------------------------------------


class WorkOrderExecutorProtocol(Protocol):
    def run(
        self, *, triage: TriageReport, plan: SeniorQualityPRPlan
    ) -> "WorkOrderResult": ...


@dataclass(frozen=True)
class WorkOrderResult:
    branch: str
    pull_request_number: Optional[int]
    dry_run: bool
    audit_log: Tuple[str, ...]


@dataclass
class FakeWorkOrderExecutor:
    """Reference implementation for tests that need to drive the
    ``triage → branch → PR body → dry-run audit`` chain.

    The real executor will run real git plumbing; this fake operates
    on :class:`FakeGitHubAPI`. All branches go through ``feat/*``,
    no force push, dry-run by default. Every step appends a
    human-readable line to ``audit_log`` so callers can grep the
    sequence.
    """

    github: FakeGitHubAPI
    audit: List[str] = field(default_factory=list)

    def run(
        self,
        *,
        triage: TriageReport,
        plan: SeniorQualityPRPlan,
        force_dry_run: bool = True,
    ) -> WorkOrderResult:
        # Senior-quality gate — refuse to dispatch without it.
        validate_senior_quality_plan(plan)
        if not triage.branch_name_plan.startswith("feat/"):
            raise FakeGitHubAPIError(
                "branch_name_plan must live under feat/ — got "
                f"{triage.branch_name_plan!r}"
            )
        # Create branch (never main).
        self.github.create_branch(name=triage.branch_name_plan)
        self.audit.append(f"branch:created:{triage.branch_name_plan}")

        dry_run = bool(triage.dry_run or force_dry_run)
        pr_number: Optional[int] = None
        if not dry_run:
            self.github.push_branch(
                name=triage.branch_name_plan,
                force=False,
                commits=[],
            )
            pr = self.github.open_pull_request(
                title=plan.title,
                body=plan.to_pr_body(),
                head=triage.branch_name_plan,
                draft=True,
            )
            pr_number = pr.number
            self.audit.append(f"pull_request:opened:#{pr.number}")
        else:
            # Dry-run audit: render the PR body but DO NOT push or open.
            rendered = plan.to_pr_body()
            redacted = redact_secret_blob(rendered)
            assert (
                "BEGIN RSA PRIVATE KEY" not in redacted
            ), "PEM leaked into PR body"
            self.audit.append(
                f"dry_run:body_rendered:{len(redacted)}_chars"
            )
        return WorkOrderResult(
            branch=triage.branch_name_plan,
            pull_request_number=pr_number,
            dry_run=dry_run,
            audit_log=tuple(self.audit),
        )


# ---------------------------------------------------------------------------
# G4 — Discord intake → tech-lead → approval card → dispatch
# ---------------------------------------------------------------------------


@dataclass
class FakeDiscordChannel:
    name: str
    id: int
    posted: List[str] = field(default_factory=list)


@dataclass
class FakeDiscordSurface:
    """Fake Discord. Posts append to the named channel's log; no
    network. The harness asserts on ``posted`` lists.
    """

    intake_channel: FakeDiscordChannel = field(
        default_factory=lambda: FakeDiscordChannel(
            name="업무-접수", id=70001
        )
    )
    research_forum: FakeDiscordChannel = field(
        default_factory=lambda: FakeDiscordChannel(
            name="운영-리서치", id=70002
        )
    )
    approval_channel: FakeDiscordChannel = field(
        default_factory=lambda: FakeDiscordChannel(
            name="승인-대기", id=70003
        )
    )
    bot_status_channel: FakeDiscordChannel = field(
        default_factory=lambda: FakeDiscordChannel(
            name="봇-상태", id=70004
        )
    )

    def post(self, channel: FakeDiscordChannel, text: str) -> None:
        # Always redact before posting — Discord embeds carry verbatim.
        channel.posted.append(redact_secret_blob(text))


# ---------------------------------------------------------------------------
# Tech-lead verdict — input the Discord intake hands the dispatcher.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TechLeadVerdict:
    coding_required: bool
    rationale: str
    suggested_intent: str = "feature"


@dataclass(frozen=True)
class GitHubWorkOrder:
    """Pure data — what the Discord layer hands the executor after the
    operator approves the card. Mirrors the production type the G4
    worktree is expected to define.
    """

    session_id: str
    issue_number: Optional[int]
    intent: str
    summary: str
    requested_by: str
    approved_by: str
    approved_at: str
    dry_run: bool = True


class DiscordIntakeProtocol(Protocol):
    """The G4 surface the e2e harness drives.

    A concrete implementation must:

      1. Receive an "#업무-접수" coding request and create / find the
         session.
      2. Pick the active engineering roles (role-selection fan-out).
      3. Drive the operator forum thread for tech-lead synthesis.
      4. Post an approval card to ``#승인-대기``.
      5. Convert an "이대로 저장" / "이대로 진행" reply into a
         :class:`GitHubWorkOrder` and dispatch it.
    """

    def submit_intake(
        self, *, text: str, author: str, session_id: str
    ) -> "DiscordIntakeOutcome": ...


@dataclass(frozen=True)
class DiscordIntakeOutcome:
    session_id: str
    selected_roles: Tuple[str, ...]
    forum_thread_id: int
    approval_card_message_id: Optional[int]
    work_order: Optional[GitHubWorkOrder]


# ---------------------------------------------------------------------------
# Glue helpers
# ---------------------------------------------------------------------------


def make_default_pr_plan(
    *,
    title: str,
    branch: str = "feat/g5-fixture",
    extra_excluded: Optional[Mapping[str, str]] = None,
) -> SeniorQualityPRPlan:
    """Build a plan whose every senior-quality field is populated.

    Tests that want to verify validation success use this; the
    contract tests mutate one field to a missing value and assert the
    validator raises.
    """

    excluded = {
        "frontend-engineer": "이번 변경은 백엔드/CI 한정 — UI 영향 없음",
        "product-designer": "디자인 변경 없음",
    }
    if extra_excluded:
        excluded = {**excluded, **dict(extra_excluded)}
    return SeniorQualityPRPlan(
        title=title,
        summary="GitHub work-order 자동 처리 흐름 정리",
        branch=branch,
        hidden_risks=(
            "GitHub App rate limit (5000/h) 초과 시 트리아지 큐가 쌓이고 "
            "주기적 dispatch 가 지연될 수 있음.",
            "PEM 파일 권한이 644 면 다른 사용자가 읽음 — 600 강제 점검 필요.",
        ),
        out_of_scope=(
            "PR auto-merge — 이 단계 범위 밖, 사람이 손으로 머지.",
            "Production deploy — 별도 deploy gate.",
        ),
        test_plan=(
            "tests/github_workos/test_end_to_end_workos.py 통과",
            "tests/github_workos/test_senior_quality_contract.py 통과",
            "Dry-run 모드에서 실제 HTTP 호출이 0인지 확인",
        ),
        approval_required_actions=(
            "GitHub App private key rotation",
            "main branch 직접 머지 (사람 손)",
        ),
        excluded_role_rationale=excluded,
        failure_recovery=(
            FailureRecoveryEntry(
                cause="GitHub App auth 401",
                impact="모든 dispatch 차단",
                recovery="PEM 재발급 + installation_id 재확인 후 status 채널에 보고",
            ),
            FailureRecoveryEntry(
                cause="branch push 실패 (network)",
                impact="해당 work-order 만 실패",
                recovery="failed_retryable 큐 재시도 (지수 backoff)",
            ),
        ),
    )


__all__ = (
    "DiscordIntakeOutcome",
    "DiscordIntakeProtocol",
    "FailureRecoveryEntry",
    "FakeBranch",
    "FakeDiscordChannel",
    "FakeDiscordSurface",
    "FakeGitHubAPI",
    "FakeGitHubAPIError",
    "FakeGitHubAppAuth",
    "FakeIssue",
    "FakePullRequest",
    "FakeWorkOrderExecutor",
    "GitHubAppAuthProtocol",
    "GitHubWorkOrder",
    "RoleAssignment",
    "SeniorQualityPRPlan",
    "SeniorQualityValidationError",
    "TechLeadVerdict",
    "TriageProtocol",
    "TriageReport",
    "WorkOrderExecutorProtocol",
    "WorkOrderResult",
    "make_default_pr_plan",
    "redact_secret_blob",
    "validate_senior_quality_plan",
)
