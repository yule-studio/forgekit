"""Compat shim — moved to yule_learning.preflight (packages/learning)."""
import sys
from yule_learning import preflight as _module
sys.modules[__name__] = _module
