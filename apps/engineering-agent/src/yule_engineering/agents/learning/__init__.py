"""Compatibility shim — moved to `yule_learning` (packages/learning).

Re-exports the new package under the old `yule_engineering.agents.learning`
path so existing imports keep resolving to the same objects.
"""
import yule_learning as _m
from yule_learning import *  # noqa: F401,F403
__all__ = list(getattr(_m, "__all__", []))
