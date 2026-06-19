"""Switch planning + verify/drift — pure given a manager.

``plan_switch`` turns a profile into the ordered manager commands needed to reach it,
classifying each by scope so the surface can approval-gate the destructive/global ones.
``verify`` / ``drift`` compare the profile against the manager's *actual* active
versions — never a guess. With no manager, both honestly report ``manager_missing``.
"""

from __future__ import annotations

from typing import Dict, Optional

from .manager import ToolchainManager, default_manager
from .models import (
    ToolchainProfile, ToolStatus, SwitchAction, SwitchPlan, ToolchainReport,
    SCOPE_LOCAL, SCOPE_INSTALL,
    STATE_MATCH, STATE_MISMATCH, STATE_MISSING, STATE_UNPINNED, STATE_MANAGER_MISSING,
)


def _satisfies(active: str, required: str) -> bool:
    """Does ``active`` satisfy a (possibly partial / aliased) ``required`` pin?

    Honest + conservative: prefix match on the dotted version ("20" ⊇ "20.11.0"),
    exact for aliases (lts/latest can't be verified numerically → require manager echo).
    """

    a, r = (active or "").strip(), (required or "").strip()
    if not r:
        return True                      # repo pins no version → any active is fine
    if not a:
        return False
    if r.lower() in ("lts", "latest", "stable"):
        return True                      # mise resolved the alias; we trust its echo
    ra = r.lstrip("^~>=< ")
    return a == ra or a.startswith(ra + ".") or ra.startswith(a + ".") or a.startswith(ra)


def verify(profile: ToolchainProfile, *, manager: Optional[ToolchainManager] = None
           ) -> ToolchainReport:
    """required-vs-active for every tool in the profile, via the real manager."""

    mgr = manager or default_manager()
    if not mgr.available():
        statuses = tuple(
            ToolStatus(t.tool, t.version, "", STATE_MANAGER_MISSING,
                       "mise(toolchain manager) 미설치 — 활성 버전 확인 불가")
            for t in profile.tools)
        return ToolchainReport(profile.name, statuses, manager_available=False)
    active: Dict[str, str] = mgr.current()
    statuses = []
    for t in profile.tools:
        cur = active.get(t.tool, "")
        if not cur:
            statuses.append(ToolStatus(t.tool, t.version, "", STATE_MISSING,
                                       "활성 버전 없음 (미설치/미선택)"))
        elif not t.pinned:
            statuses.append(ToolStatus(t.tool, "", cur, STATE_UNPINNED,
                                       "repo 가 버전 미지정 — 활성만 표시"))
        elif _satisfies(cur, t.version):
            statuses.append(ToolStatus(t.tool, t.version, cur, STATE_MATCH))
        else:
            statuses.append(ToolStatus(t.tool, t.version, cur, STATE_MISMATCH,
                                       f"요구 {t.version} ≠ 활성 {cur}"))
    return ToolchainReport(profile.name, tuple(statuses), manager_available=True)


# verify and drift answer the same question from the two operator angles; drift is the
# verify report filtered to the mismatches (kept as a name the surface/CLI can call).
def drift(profile: ToolchainProfile, *, manager: Optional[ToolchainManager] = None
          ) -> ToolchainReport:
    return verify(profile, manager=manager)


def plan_switch(profile: ToolchainProfile, *, manager: Optional[ToolchainManager] = None,
                scope: str = "local") -> SwitchPlan:
    """Ordered manager commands to reach the profile, each scoped for approval gating.

    A tool already satisfied is skipped (no-op). A pinned-but-missing tool needs an
    ``install`` (gated — network/disk). A present-but-wrong tool gets a repo-local
    ``use`` (``scope=local``) unless ``scope='global'`` is requested (gated).
    """

    mgr = manager or default_manager()
    available = mgr.available()
    active: Dict[str, str] = mgr.current() if available else {}
    actions = []
    want_global = scope == "global"
    for t in profile.tools:
        if not t.pinned:
            continue                                     # nothing concrete to switch to
        cur = active.get(t.tool, "")
        if cur and _satisfies(cur, t.version):
            continue                                     # already satisfied — no action
        spec = f"{t.tool}@{t.version}"
        if not cur:
            # not installed → must install the runtime first (gated: network/disk).
            actions.append(SwitchAction(
                t.tool, t.version, ("mise", "install", spec), SCOPE_INSTALL,
                reason=f"{t.tool} {t.version} 미설치 — 설치 필요 (네트워크/디스크)"))
        if want_global:
            actions.append(SwitchAction(
                t.tool, t.version, ("mise", "use", "--global", spec),
                "global", reason="user-global 핀 변경 — 다른 프로젝트에 영향"))
        else:
            actions.append(SwitchAction(
                t.tool, t.version, ("mise", "use", spec), SCOPE_LOCAL,
                reason="repo-local 핀 (./.mise.toml) — 가역적"))
    return SwitchPlan(profile.name, tuple(actions),
                      manager=mgr.name if available else "",
                      manager_available=available)


__all__ = ("verify", "drift", "plan_switch")
