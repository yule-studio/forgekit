"""Security auto-dispatch gate — when does security-engineer review intercept?
(issue #185 follow-up C).

A pure, deterministic decision over a change's metadata (paths + summary +
labels + flags) that answers: *does this change require cross-cutting security
review?* It mirrors the ``intercept_triggers`` of the security-engineer role
contract (``agents/engineering-agent/security-engineer/manifest.json``) and the
workflow SSoT (``docs/security-review.md``).

The output is advisory-by-construction: it returns a
:class:`SecurityReviewDecision` (required / triggers / reasons) that callers
surface on the execution receipt and route to the security-engineer gate. It
does **not** mutate code or block by itself — enforcement is the operator/tech-lead
approval gate.

False-positive / false-negative tradeoff (documented, locked by tests):
  * Heuristics are **keyword/path** based, so they over-trigger (false positive)
    rather than miss (false negative) — security review is cheap relative to a
    missed auth/secret bug. A matched trigger always sets ``required=True``.
  * Detectors are intentionally broad (e.g. any path segment ``auth`` →
    AUTH). Callers can pass ``force_skip_reason`` to record an explicit,
    audited skip — silence is never a skip.

Security stance (hard rail, from the role contract): this gate never treats
"block the browser devtools" as a control. The recommended mitigation is always
**server-side enforcement**; client is a hostile surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

# Severities the gate can emit.
SEVERITY_NONE = "none"
SEVERITY_REQUIRED = "required"

# Anti-goal note attached so a downstream surface never misreads the gate as a
# "lock the client" recommendation.
ANTI_GOAL_NOTE = (
    "클라이언트는 신뢰 경계가 아니다 — 민감 제어는 서버측 검증으로 강제한다. "
    "'개발자 도구 차단' 류는 보안 목표가 아니다."
)


@dataclass(frozen=True)
class TriggerMatch:
    trigger: str          # category id (auth / secret / public_surface / ...)
    reason: str           # human reason
    evidence: str         # the matched token/path


@dataclass(frozen=True)
class SecurityReviewDecision:
    required: bool
    severity: str
    triggers: Tuple[str, ...]
    matches: Tuple[TriggerMatch, ...]
    reasons: Tuple[str, ...]
    skip_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "required": self.required,
            "severity": self.severity,
            "triggers": list(self.triggers),
            "reasons": list(self.reasons),
            "matches": [
                {"trigger": m.trigger, "reason": m.reason, "evidence": m.evidence}
                for m in self.matches
            ],
            "skip_reason": self.skip_reason,
            "anti_goal_note": ANTI_GOAL_NOTE,
        }

    def surface(self) -> str:
        if self.skip_reason:
            return f"security-review: skipped — {self.skip_reason}"
        if not self.required:
            return "security-review: not required (no sensitive change detected)"
        return f"security-review: REQUIRED — triggers={', '.join(self.triggers)}"


# Category id → (human label, keyword tokens). Tokens are matched case-
# insensitively against the joined summary + labels + path segments.
_DETECTORS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    (
        "auth",
        "authentication / authorization 변경",
        ("auth", "login", "logout", "authorize", "authorization", "permission",
         "rbac", "role-check", "session", "jwt", "oauth", "access-control", "idor"),
    ),
    (
        "secret",
        "secret / credential handling 변경",
        ("secret", "credential", ".env", "apikey", "api-key", "api_key",
         "private-key", "private_key", "password", "token-store", "vault", "keystore"),
    ),
    (
        "public_surface",
        "공개 surface 추가 (endpoint / webhook / upload / postMessage)",
        ("endpoint", "route", "webhook", "upload", "postmessage", "public-api",
         "open-api", "cors", "graphql", "handler", "controller"),
    ),
    (
        "deployment",
        "deployment / CI / supply-chain 변경",
        ("deploy", "ci/cd", "ci-cd", "github-actions", "workflow", "dockerfile",
         "container", "image", "dependency", "requirements", "package.json",
         "lockfile", "pipeline", "privilege"),
    ),
    (
        "client_security",
        "CSP / token storage / CSRF / XSS 민감 변경",
        ("csp", "xss", "csrf", "frame-ancestors", "clickjacking", "token-storage",
         "localstorage", "sessionstorage", "cookie", "samesite", "content-security-policy"),
    ),
    (
        "agent_safety",
        "agent tool / approval gate / autonomy 변경",
        ("prompt-injection", "prompt injection", "tool-grant", "tool grant",
         "approval-gate", "approval gate", "autonomy", "tool-overreach",
         "exfiltration", "grant", "guardrail"),
    ),
)


def _haystack(change: Mapping[str, Any]) -> Tuple[str, Tuple[str, ...]]:
    """Build a lowercased search blob + the discrete evidence tokens."""

    tokens: list[str] = []
    summary = str(change.get("summary") or "")
    if summary:
        tokens.append(summary)
    for key in ("labels", "diff_markers", "flags"):
        raw = change.get(key)
        if isinstance(raw, (list, tuple)):
            tokens.extend(str(x) for x in raw)
    paths = change.get("paths")
    path_list: list[str] = []
    if isinstance(paths, (list, tuple)):
        path_list = [str(p) for p in paths]
        tokens.extend(path_list)
    blob = " \n ".join(tokens).lower()
    return blob, tuple(path_list)


def assess_security_review(
    change: Mapping[str, Any],
    *,
    force_skip_reason: Optional[str] = None,
) -> SecurityReviewDecision:
    """Decide whether *change* needs security-engineer review.

    *change* keys (all optional): ``summary`` (str), ``paths`` (list[str]),
    ``labels`` / ``diff_markers`` / ``flags`` (list[str]).

    *force_skip_reason* records an explicit, audited skip (e.g. "operator
    waived — docs-only"). A skip is never implicit.
    """

    if force_skip_reason:
        return SecurityReviewDecision(
            required=False,
            severity=SEVERITY_NONE,
            triggers=(),
            matches=(),
            reasons=(),
            skip_reason=force_skip_reason,
        )

    blob, _paths = _haystack(change)
    matches: list[TriggerMatch] = []
    triggers: list[str] = []
    reasons: list[str] = []
    for trigger_id, label, tokens in _DETECTORS:
        hit = next((tok for tok in tokens if tok in blob), None)
        if hit is None:
            continue
        triggers.append(trigger_id)
        reasons.append(label)
        matches.append(TriggerMatch(trigger=trigger_id, reason=label, evidence=hit))

    required = bool(triggers)
    return SecurityReviewDecision(
        required=required,
        severity=SEVERITY_REQUIRED if required else SEVERITY_NONE,
        triggers=tuple(triggers),
        matches=tuple(matches),
        reasons=tuple(reasons),
        skip_reason=None,
    )


def security_review_required(change: Mapping[str, Any]) -> bool:
    return assess_security_review(change).required


__all__ = (
    "SEVERITY_NONE",
    "SEVERITY_REQUIRED",
    "ANTI_GOAL_NOTE",
    "TriggerMatch",
    "SecurityReviewDecision",
    "assess_security_review",
    "security_review_required",
)
