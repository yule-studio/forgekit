"""Compat shim — moved to yule_vcs.repo_contract (packages/vcs)."""
import sys
from yule_vcs import repo_contract as _module
sys.modules[__name__] = _module
