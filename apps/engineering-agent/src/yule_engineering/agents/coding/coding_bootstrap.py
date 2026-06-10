"""Coding bootstrap pre-check — P0-J (#145).

The collector's ``NEEDS_USER_INPUT`` mode tells the gateway to ask
the user for more material ("자료 부족"). For coding requests the
heuristic is too aggressive: if the user pasted a GitHub repo URL +
an issue + clearly described what to build (e.g. "Next.js+NestJS+
Postgres+Docker Compose 회원가입/로그인/검색"), there *is* anchor
material — just not in the form the legacy collector recognizes.

This module gives the gateway a **bypass decision**: when the
request carries (a) a GitHub repo target, (b) a clear write intent,
and (c) ≥1 recognized engineering stack, the gateway treats the
combination as a *code_context bootstrap pending* signal and
proceeds with coding handoff *without* surfacing "자료 더 주세요".

The local repo clone is optional — repo target *alone* (issue or
repo root) is enough to register the bootstrap signal. The actual
clone / file tree extraction wires in P0-J 후속 (the
``vault repo workspace`` follow-up referenced in stage-1 §6).

The decision is **read-only**. Returns a :class:`CodingBootstrap`
that the caller persists into ``session.extra`` and uses to gate
the insufficiency surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple


# Status codes — stable identifiers.
STATUS_BYPASS = "bypass_insufficiency"
STATUS_REQUIRES_USER_INPUT = "requires_user_input"
STATUS_NOT_CODING_REQUEST = "not_coding_request"

STATUSES = (
    STATUS_BYPASS,
    STATUS_REQUIRES_USER_INPUT,
    STATUS_NOT_CODING_REQUEST,
)


@dataclass(frozen=True)
class CodingBootstrap:
    """Outcome of :func:`evaluate_coding_bootstrap`.

    ``status``                  — one of the STATUS_* constants.
    ``bypass_insufficiency``    — True when gateway should NOT
                                  surface "자료 부족" follow-up.
    ``code_context_pending``    — True when repo target + write
                                  intent + stack ≥1 → workspace
                                  bootstrap is "pending" (work to
                                  happen when local clone wires).
    ``seeded_docs``             — list of canonical stack names
                                  whose official docs are seeded.
    ``reason``                  — short Korean status text.
    ``has_github_repo``         — True when github_target kind ∈
                                  (repo/issue/pull_request).
    ``stacks_mentioned``        — tuple of canonical stack names.
    ``write_intent``            — True when the message has write
                                  / build / implement verbs.
    """

    status: str
    bypass_insufficiency: bool
    code_context_pending: bool
    seeded_docs: Tuple[str, ...] = field(default_factory=tuple)
    reason: Optional[str] = None
    has_github_repo: bool = False
    stacks_mentioned: Tuple[str, ...] = ()
    write_intent: bool = False

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "status": self.status,
            "bypass_insufficiency": self.bypass_insufficiency,
            "code_context_pending": self.code_context_pending,
            "seeded_docs": list(self.seeded_docs),
            "reason": self.reason,
            "has_github_repo": self.has_github_repo,
            "stacks_mentioned": list(self.stacks_mentioned),
            "write_intent": self.write_intent,
        }

    def status_summary_line(self) -> str:
        """One-line summary for the status diagnostic surface."""

        if self.status == STATUS_BYPASS:
            seed_count = len(self.seeded_docs)
            return (
                f"🚀 coding bootstrap: insufficiency 우회 — "
                f"repo target + {len(self.stacks_mentioned)} stacks + write intent "
                f"(+{seed_count} docs seeded)"
            )
        if self.status == STATUS_REQUIRES_USER_INPUT:
            return "📝 coding bootstrap: 추가 정보 필요 — anchor 미충분"
        return "ℹ️ coding bootstrap: 코딩 요청 아님"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_coding_bootstrap(
    *,
    message_text: str,
    user_links: Sequence[str] = (),
    existing_extra: Optional[Mapping[str, Any]] = None,
) -> CodingBootstrap:
    """Decide whether to bypass the collector's insufficiency surface.

    Three outcomes:

      * **bypass** — github_target is a repo/issue/PR + ≥1
        recognized stack + write intent.
      * **requires_user_input** — partial signals (e.g. repo URL
        but no write intent; or write intent but no repo).
      * **not_coding_request** — neither stack nor write intent —
        the message is not a coding request at all.
    """

    extra_in = dict(existing_extra or {})

    has_github_repo = _has_github_repo(extra_in, user_links)

    # Stack detection — defer import to keep module isolated.
    try:
        from .stack_detector import detect_stacks, has_write_intent
        from .official_docs_seed import seed_official_docs
    except Exception:  # noqa: BLE001 - partial install fallback
        return CodingBootstrap(
            status=STATUS_REQUIRES_USER_INPUT,
            bypass_insufficiency=False,
            code_context_pending=False,
            reason="stack_detector_unavailable",
        )

    detection = detect_stacks(message_text or "")
    write_intent = has_write_intent(message_text or "")

    if not detection.has_any and not write_intent:
        return CodingBootstrap(
            status=STATUS_NOT_CODING_REQUEST,
            bypass_insufficiency=False,
            code_context_pending=False,
            reason="no_stack_no_write_intent",
            has_github_repo=has_github_repo,
        )

    # Bypass when all 3 signals are present.
    if has_github_repo and detection.has_any and write_intent:
        seeded = seed_official_docs(detection.stacks)
        return CodingBootstrap(
            status=STATUS_BYPASS,
            bypass_insufficiency=True,
            code_context_pending=True,
            seeded_docs=tuple(s.canonical for s in seeded),
            reason=(
                "repo target + stack mention + write intent — "
                "coding bootstrap 활성, insufficiency 우회"
            ),
            has_github_repo=True,
            stacks_mentioned=detection.stacks,
            write_intent=True,
        )

    return CodingBootstrap(
        status=STATUS_REQUIRES_USER_INPUT,
        bypass_insufficiency=False,
        code_context_pending=False,
        reason=_partial_signal_reason(
            has_github_repo=has_github_repo,
            has_stacks=detection.has_any,
            write_intent=write_intent,
        ),
        has_github_repo=has_github_repo,
        stacks_mentioned=detection.stacks,
        write_intent=write_intent,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _has_github_repo(
    extra: Mapping[str, Any], user_links: Sequence[str]
) -> bool:
    """Return True when extra OR user_links carries a GitHub repo/issue/PR target."""

    github_target = extra.get("github_target")
    if isinstance(github_target, Mapping) and github_target:
        kind = str(github_target.get("kind") or "")
        if kind in ("repo", "issue", "pull_request"):
            return True
    # Fall back to scanning user_links — defer parser import.
    try:
        from yule_vcs.github_url import parse_github_targets
    except Exception:  # noqa: BLE001
        return False
    targets = parse_github_targets(user_links or ())
    for t in targets:
        if t.kind in ("repo", "issue", "pull_request"):
            return True
    return False


def _partial_signal_reason(
    *, has_github_repo: bool, has_stacks: bool, write_intent: bool
) -> str:
    missing: list[str] = []
    if not has_github_repo:
        missing.append("repo target")
    if not has_stacks:
        missing.append("stack mention")
    if not write_intent:
        missing.append("write intent")
    if missing:
        return "anchor 부족: " + ", ".join(missing)
    return "unknown"


__all__ = (
    "CodingBootstrap",
    "STATUSES",
    "STATUS_BYPASS",
    "STATUS_NOT_CODING_REQUEST",
    "STATUS_REQUIRES_USER_INPUT",
    "evaluate_coding_bootstrap",
)
