"""Compatibility shim — moved to `yule_agent_memory` (packages/memory).

Re-exports the new package under the old `yule_engineering.agents.memory`
path so existing imports keep resolving to the same objects.
"""
import yule_agent_memory as _m
from yule_agent_memory import *  # noqa: F401,F403
__all__ = list(getattr(_m, "__all__", []))
