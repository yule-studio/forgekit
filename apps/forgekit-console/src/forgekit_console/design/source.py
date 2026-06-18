"""Restricted design source gate (design WT1) — design roles only, no fake-read.

The desktop Figma backup folder is a sensitive, repo-EXTERNAL asset. It is registered
as a READ-ONLY restricted source that ONLY design-family roles may access directly;
every other role sees a projection/packet, never the raw asset. Access is probed
honestly: a macOS TCC / permission failure → ``blocked`` (never a fabricated read),
a missing path → ``missing``. Raw ``.fig`` / exports are NEVER copied into the repo
or vault — only metadata + (later) packets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

# the restricted source path (repo-external, sensitive)
RAW_DESIGN_BACKUP_PATH = "/Users/masterway/Desktop/마스터웨이 피그마 백업"

# access states (honest)
ACCESS_OK = "ok"
ACCESS_BLOCKED = "blocked"     # TCC / permission denied — design_source_blocked
ACCESS_MISSING = "missing"     # path not present

VISIBILITY_RESTRICTED = "restricted"
INGEST_READ_ONLY = "read-only"

# only design-family roles may touch the raw source
DESIGN_ROLES: Tuple[str, ...] = (
    "ux-ui-designer", "design-systems-designer", "illustration-brand-designer", "design-lead",
)


@dataclass(frozen=True)
class RestrictedDesignSource:
    source_id: str
    source_path: str
    source_type: str = "figma-backup"
    visibility: str = VISIBILITY_RESTRICTED
    allowed_roles: Tuple[str, ...] = DESIGN_ROLES
    ingest_mode: str = INGEST_READ_ONLY
    publishable: bool = False
    last_scanned_at: str = ""
    access_state: str = ACCESS_MISSING

    def role_allowed(self, role: str) -> bool:
        return (role or "").strip() in self.allowed_roles

    @property
    def accessible(self) -> bool:
        return self.access_state == ACCESS_OK

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id, "source_path": self.source_path,
            "source_type": self.source_type, "visibility": self.visibility,
            "allowed_roles": list(self.allowed_roles), "ingest_mode": self.ingest_mode,
            "publishable": self.publishable, "last_scanned_at": self.last_scanned_at,
            "access_state": self.access_state,
        }


def probe_access(path: str) -> str:
    """Probe access HONESTLY: PermissionError(TCC) → blocked, absent → missing, else ok.

    Never reads file contents — only checks reachability. No fabricated success.
    """

    p = Path(path)
    try:
        # listdir touches the dir without reading any .fig contents
        os.listdir(p)
        return ACCESS_OK
    except PermissionError:
        return ACCESS_BLOCKED        # macOS TCC / sandbox — design_source_blocked
    except FileNotFoundError:
        return ACCESS_MISSING
    except OSError:
        return ACCESS_BLOCKED        # any other access error → treat as blocked (safe)


def register_design_backup(path: str = RAW_DESIGN_BACKUP_PATH, *, last_scanned_at: str = ""
                           ) -> RestrictedDesignSource:
    """Register the desktop Figma backup as a restricted source with an honest state."""

    return RestrictedDesignSource(
        source_id="figma-backup", source_path=path, source_type="figma-backup",
        last_scanned_at=last_scanned_at, access_state=probe_access(path),
    )


def access_request(source: RestrictedDesignSource, role: str) -> Tuple[bool, str]:
    """Can *role* read the RAW source? Returns ``(allowed, reason)``.

    Non-design role → refused (use a projection). Design role → allowed only if the
    source is actually accessible; blocked/missing is surfaced, never faked.
    """

    if not source.role_allowed(role):
        return False, f"'{role}' 은 design role 이 아님 — raw source 직접 접근 불가, projection/packet 만"
    if source.access_state == ACCESS_BLOCKED:
        return False, "design_source_blocked — macOS TCC/권한으로 접근 불가 (runbook 참조, fake-read 금지)"
    if source.access_state == ACCESS_MISSING:
        return False, "source missing — 경로 없음"
    return True, "design role + accessible"


def access_runbook() -> str:
    return (
        "# Runbook — restricted design source 접근\n\n"
        f"- 경로: `{RAW_DESIGN_BACKUP_PATH}` (repo 밖, 민감 자산, read-only).\n"
        "- macOS TCC 로 막히면 `design_source_blocked` — 절대 fake-read 하지 않음.\n"
        "- 허용: 터미널/forgekit 프로세스에 'Full Disk Access' 부여, 또는 design role 이\n"
        "  필요한 파일을 export 해 operator-provided reference 로 전달.\n"
        "- raw .fig/export 를 repo/vault 본문에 복사 금지 — 메타/packet 만 남김.\n"
        "- design role(ux-ui-designer/design-systems-designer/illustration-brand-designer/\n"
        "  design-lead)만 raw 접근, 그 외는 projection.\n"
    )


__all__ = (
    "RAW_DESIGN_BACKUP_PATH", "ACCESS_OK", "ACCESS_BLOCKED", "ACCESS_MISSING",
    "VISIBILITY_RESTRICTED", "INGEST_READ_ONLY", "DESIGN_ROLES",
    "RestrictedDesignSource", "probe_access", "register_design_backup",
    "access_request", "access_runbook",
)
