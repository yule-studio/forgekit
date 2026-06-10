"""Compatibility shim — moved to `yule_agent_runtime` (packages/runtime).

Re-exports the new package under the old `yule_engineering.agents.runtime`
path so existing imports keep resolving to the same objects.
"""
import yule_agent_runtime as _m
from yule_agent_runtime import *  # noqa: F401,F403
__all__ = list(getattr(_m, "__all__", []))
