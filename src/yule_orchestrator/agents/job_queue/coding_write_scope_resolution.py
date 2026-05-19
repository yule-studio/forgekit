"""P1-Z4 D — write_scope vs worktree layout 정합성 점검.

배경
----
canonical session ``000f13fb121b`` 의 coding_proposal write_scope:

  ``("src/<service>/api/**", "src/<service>/domain/**",
     "src/<service>/repository/**", "src/<service>/security/**",
     "migrations/**", "tests/<service>/api/**")``

target repo ``naver-search-clone`` 은 ``apps/`` 중심 monorepo 구조라
위 prefix 들이 실제 디렉터리와 0건 매칭.  결과: LiveCodeEditor 가 LLM
호출까지 했지만 worktree 안에 편집 가능한 candidate 가 0개라 modified
file 0건 → generic ``live_editor_no_edits_produced`` 로 종료.

본 모듈
========
* :func:`resolve_write_scope_against_worktree(worktree_path, write_scope)
  -> WriteScopeResolution` — 각 scope prefix 마다 worktree 안에 실제
  매칭되는 경로 (dir 또는 file) 가 있는지 평가.  결과 dataclass:

    * ``matched_prefixes`` — 매칭된 prefix 들
    * ``unmatched_prefixes`` — 매칭 0건인 prefix 들
    * ``sample_paths`` — 매칭된 실제 경로 샘플 (operator surface 용)
    * ``has_any_match`` — True 면 LiveCodeEditor 진행 가능, False 면
      worker 가 ``write_scope_resolved_empty`` 로 정직 차단

storage I/O 없음 — 파일시스템 read 만.  caller (worker) 가 결과를 보고
다음 단계 결정.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Tuple


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WriteScopeResolution:
    matched_prefixes: Tuple[str, ...] = ()
    unmatched_prefixes: Tuple[str, ...] = ()
    sample_paths: Tuple[str, ...] = field(default_factory=tuple)
    worktree_path: str = ""
    write_scope: Tuple[str, ...] = field(default_factory=tuple)
    worktree_exists: bool = False

    @property
    def has_any_match(self) -> bool:
        return bool(self.matched_prefixes)

    @property
    def can_decide_mismatch(self) -> bool:
        """worktree 가 실제로 존재하고 scope 가 비어있지 않을 때만
        mismatch 판단 가능.  worktree 가 없으면 caller 는 generic
        no-edit 로 떨어뜨려야 함."""

        return self.worktree_exists and bool(self.write_scope)

    @property
    def is_placeholder_scope(self) -> bool:
        """``src/<service>/...`` 같은 angle-bracket placeholder 가 있는지."""

        for entry in self.write_scope:
            if "<" in entry and ">" in entry:
                return True
        return False

    def to_audit(self) -> dict:
        return {
            "worktree_path": self.worktree_path,
            "worktree_exists": self.worktree_exists,
            "write_scope": list(self.write_scope),
            "matched_prefixes": list(self.matched_prefixes),
            "unmatched_prefixes": list(self.unmatched_prefixes),
            "sample_paths": list(self.sample_paths),
            "has_any_match": self.has_any_match,
            "can_decide_mismatch": self.can_decide_mismatch,
            "is_placeholder_scope": self.is_placeholder_scope,
        }


def _normalize_prefix(entry: str) -> str:
    """write_scope entry 를 디렉터리 prefix 로 정규화.

    ``src/<service>/api/**`` → ``src/<service>/api``
    ``services/auth/**`` → ``services/auth``
    """

    text = (entry or "").strip()
    text = text.rstrip("/").rstrip("*").rstrip("/")
    return text


def resolve_write_scope_against_worktree(
    *,
    worktree_path: str,
    write_scope: Sequence[str],
    sample_limit: int = 5,
) -> WriteScopeResolution:
    """worktree 안에 write_scope 가 실제로 매칭되는 path 가 있는지 점검.

    각 prefix 는 (1) 그 자체가 디렉터리/파일로 존재하거나 (2) glob 와
    매칭하는 자식이 존재하면 matched.  placeholder (``<service>`` 같은
    각괄호 literal) 가 들어있으면 보통 매칭 0건 → unmatched.
    """

    write_scope_tuple = tuple(str(x) for x in (write_scope or ()) if str(x).strip())
    if not worktree_path or not write_scope_tuple:
        return WriteScopeResolution(
            worktree_path=worktree_path,
            write_scope=write_scope_tuple,
            worktree_exists=False,
        )

    root = Path(worktree_path)
    if not root.is_dir():
        return WriteScopeResolution(
            worktree_path=worktree_path,
            write_scope=write_scope_tuple,
            worktree_exists=False,
        )

    matched: list[str] = []
    unmatched: list[str] = []
    samples: list[str] = []

    for entry in write_scope_tuple:
        prefix = _normalize_prefix(entry)
        if not prefix:
            # ``**`` 같은 universal scope — 어떤 worktree 라도 매칭.
            matched.append(entry)
            continue
        candidate = root / prefix
        try:
            if candidate.exists():
                matched.append(entry)
                if candidate.is_dir():
                    for child in candidate.iterdir():
                        rel = str(child.relative_to(root))
                        if rel not in samples:
                            samples.append(rel)
                        if len(samples) >= sample_limit:
                            break
                else:
                    rel = str(candidate.relative_to(root))
                    if rel not in samples:
                        samples.append(rel)
                continue
        except OSError:
            pass

        # glob fallback — prefix 가 dir 로는 안 보이지만 그 안의 file 이
        # 있을 수 있다 (예: ``src/auth.py``).  segment 단위로 검사.
        segments = [s for s in prefix.split("/") if s]
        if segments:
            parent = root
            try:
                # parent 디렉터리부터 단계별로 존재성 검사
                for idx, seg in enumerate(segments[:-1]):
                    parent = parent / seg
                    if not parent.is_dir():
                        parent = None
                        break
                if parent is not None:
                    leaf = segments[-1]
                    # leaf 와 정확히 일치하는 file / dir 가 있는지
                    matching = list(parent.glob(leaf))
                    if matching:
                        matched.append(entry)
                        for m in matching[: sample_limit - len(samples)]:
                            try:
                                rel = str(m.relative_to(root))
                            except ValueError:
                                rel = str(m)
                            if rel not in samples:
                                samples.append(rel)
                        continue
            except (OSError, ValueError):
                pass

        unmatched.append(entry)

    return WriteScopeResolution(
        matched_prefixes=tuple(matched),
        unmatched_prefixes=tuple(unmatched),
        sample_paths=tuple(samples[:sample_limit]),
        worktree_path=worktree_path,
        write_scope=write_scope_tuple,
        worktree_exists=True,
    )


__all__ = (
    "WriteScopeResolution",
    "resolve_write_scope_against_worktree",
)
