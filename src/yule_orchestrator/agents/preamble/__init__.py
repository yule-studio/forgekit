"""F14 — gstack-style preamble cache.

매 agent spawn 시 conventions / policy / governance 5 파일을 re-read 하는 대신,
PreambleBuilder 가 1회 해석 → PreambleCache 가 보유 → 모든 agent 가 재사용.

토큰 절약 핵심:
  - 5 파일 (≈ 15KB) × N agent → 1 회 해석.
  - mtime 기반 invalidation: 파일 변경 시에만 재빌드.
  - PasteGuard 통과 (secret 포함된 policy 파일 redact).
"""

from .builder import (
    Preamble,
    PreambleBuilder,
    PreambleSection,
    build_default_preamble,
)
from .cache import PreambleCache, get_shared_cache


__all__ = (
    "Preamble",
    "PreambleBuilder",
    "PreambleCache",
    "PreambleSection",
    "build_default_preamble",
    "get_shared_cache",
)
