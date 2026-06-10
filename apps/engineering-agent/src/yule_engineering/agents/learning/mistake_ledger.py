"""Compat shim — moved to yule_learning.mistake_ledger (packages/learning)."""
import sys
from yule_learning import mistake_ledger as _module
sys.modules[__name__] = _module
