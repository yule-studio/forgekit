"""Compat shim — moved to yule_vcs.github_url (packages/vcs)."""
import sys
from yule_vcs import github_url as _module
sys.modules[__name__] = _module
