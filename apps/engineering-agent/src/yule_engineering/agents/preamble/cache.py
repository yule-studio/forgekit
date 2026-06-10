"""F14 PreambleCache — process-shared 캐시. mtime 기반 invalidation.

여러 agent 가 동시 spawn 되어도 단일 process 내에서는 1회 빌드 → 재사용.
별도 process 간 공유는 OS 파일 시스템 read 이므로 OS 페이지 캐시가 처리.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from .builder import Preamble, PreambleBuilder


@dataclass
class _CachedEntry:
    preamble: Preamble
    fingerprint: str


class PreambleCache:
    """thread-safe cache. ``get_or_build`` 가 mtime 비교로 stale 검출."""

    def __init__(self, builder: Optional[PreambleBuilder] = None) -> None:
        self._builder = builder or PreambleBuilder()
        self._lock = threading.RLock()
        self._entry: Optional[_CachedEntry] = None

    def _current_fingerprint(self) -> str:
        """파일 mtime 집계 — 정책 5 파일 중 하나라도 변경되면 fingerprint 달라짐."""

        parts: list = []
        for title, rel_path in self._builder.sources:
            path = self._builder._repo_root / rel_path  # noqa: SLF001 — internal
            mtime = path.stat().st_mtime if path.is_file() else 0.0
            parts.append(f"{rel_path}:{mtime}")
        return "|".join(parts)

    def get_or_build(self) -> Preamble:
        """캐시된 preamble 반환. mtime 변경 시 자동 재빌드."""

        with self._lock:
            fp = self._current_fingerprint()
            if self._entry is not None and self._entry.fingerprint == fp:
                return self._entry.preamble
            preamble = self._builder.build()
            self._entry = _CachedEntry(preamble=preamble, fingerprint=fp)
            return preamble

    def invalidate(self) -> None:
        """강제 무효화 — 테스트 / 운영자 명시 호출."""

        with self._lock:
            self._entry = None

    def is_cached(self) -> bool:
        with self._lock:
            return self._entry is not None


# ---------------------------------------------------------------------------
# Process-shared singleton (single yule run-service / discord process 안)
# ---------------------------------------------------------------------------


_SHARED_CACHE: Optional[PreambleCache] = None
_SHARED_LOCK = threading.Lock()


def get_shared_cache() -> PreambleCache:
    """프로세스 단일 인스턴스. 첫 호출 시 default builder 로 초기화."""

    global _SHARED_CACHE
    if _SHARED_CACHE is None:
        with _SHARED_LOCK:
            if _SHARED_CACHE is None:
                _SHARED_CACHE = PreambleCache()
    return _SHARED_CACHE


def reset_shared_cache_for_tests() -> None:
    """테스트 격리 — caller 가 모듈 import 사이 호출."""

    global _SHARED_CACHE
    with _SHARED_LOCK:
        _SHARED_CACHE = None


__all__ = (
    "PreambleCache",
    "get_shared_cache",
    "reset_shared_cache_for_tests",
)
