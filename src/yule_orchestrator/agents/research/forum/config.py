"""research_forum — env config + dataclass (leaf)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ResearchForumContext:
    """Resolved Forum channel target.

    Either ``channel_id`` or ``channel_name`` is enough to route. When both
    are missing, ``configured`` is False and forum publishing is disabled.
    """

    channel_id: Optional[int] = None
    channel_name: Optional[str] = None

    @property
    def configured(self) -> bool:
        return self.channel_id is not None or bool((self.channel_name or "").strip())

    @classmethod
    def from_env(cls) -> "ResearchForumContext":
        return cls(
            channel_id=_optional_int_env("DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_ID"),
            channel_name=_optional_string_env(
                "DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_NAME"
            ),
        )


def _optional_int_env(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer value, got: {raw!r}") from exc

def _optional_string_env(name: str) -> Optional[str]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    text = raw.strip()
    return text or None


__all__ = (
    "ResearchForumContext",
    "_optional_int_env",
    "_optional_string_env",
)
