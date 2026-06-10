"""PR semantic CRUD-like slice classifier — P0-I stage 3 (#141).

Implements the policy in stage-1 ``github-workflow.md §5.1``
(refined 2026-05-14): PR splitting is **not** literal CRUD strict
partitioning, but *semantic* slice classes that help the
reviewer judge "what is this PR really doing?" in 5 minutes:

  * **C** (Create / Construct) — new structure / new feature / new policy
  * **R** (Read / Reveal)      — observe / diagnose / surface metrics
  * **U** (Update / Upgrade)   — change existing behavior or policy
  * **D** (Delete / Decommission) — remove / cleanup / deprecate
  * **MIXED**                  — multiple slices in one PR (red flag)

Plus **exception slices** (stage-1 §5.1):

  * **HOTFIX**     — incident response, no C/R/U/D classification
  * **DOCS_ONLY**  — docs / policy only
  * **TEST_ONLY**  — regression test only
  * **TINY_CONFIG** — ≤10 line config tweak

Size threshold (stage-1 §5.1): diff > 800 lines (excluding tests)
emits a split recommendation.

The classifier is **heuristic** — title prefix + file pattern +
diff size. It never raises and always emits a result so the gateway
can surface a warning without blocking. Caller decides whether the
classification triggers a split prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple


# Slice constants — stable identifiers.
SLICE_CREATE = "create"
SLICE_READ = "read"
SLICE_UPDATE = "update"
SLICE_DELETE = "delete"
SLICE_MIXED = "mixed"
SLICE_HOTFIX = "hotfix"
SLICE_DOCS_ONLY = "docs_only"
SLICE_TEST_ONLY = "test_only"
SLICE_TINY_CONFIG = "tiny_config"

SLICES = (
    SLICE_CREATE,
    SLICE_READ,
    SLICE_UPDATE,
    SLICE_DELETE,
    SLICE_MIXED,
    SLICE_HOTFIX,
    SLICE_DOCS_ONLY,
    SLICE_TEST_ONLY,
    SLICE_TINY_CONFIG,
)

# Stage-1 §5.1 — PR size guideline (test-excluded LOC).
PR_SIZE_WARNING_THRESHOLD = 800


# Korean title keyword → slice. Order matters (more specific first).
_TITLE_KEYWORDS: Tuple[Tuple[str, str], ...] = (
    # exceptions first
    ("hotfix:", SLICE_HOTFIX),
    ("hotfix ", SLICE_HOTFIX),
    ("🐞", SLICE_HOTFIX),
    # main C/R/U/D
    ("✨", SLICE_CREATE),
    ("📃", SLICE_DOCS_ONLY),
    ("📝", SLICE_DOCS_ONLY),
    ("✅", SLICE_TEST_ONLY),
    ("🔨", SLICE_UPDATE),
    ("♻️", SLICE_UPDATE),
    ("🗑️", SLICE_DELETE),
    ("🔥", SLICE_DELETE),
    ("⚙", SLICE_TINY_CONFIG),
    ("⚙️", SLICE_TINY_CONFIG),
)

# Korean / English keyword patterns by slice (for non-emoji titles + body scan).
_KEYWORD_PATTERNS: Mapping[str, Tuple[str, ...]] = {
    SLICE_CREATE: (
        "신규",
        "new ",
        "add ",
        "도입",
        "추가",
        "신설",
        "create",
        "introduce",
    ),
    SLICE_READ: (
        "감지",
        "조회",
        "진단",
        "노출",
        "surface",
        "diagnose",
        "observe",
        "metric",
        "telemetry",
        "status",
    ),
    SLICE_UPDATE: (
        "갱신",
        "수정",
        "변경",
        "refactor",
        "update",
        "리팩터",
        "리팩토링",
        "보강",
        "확장",
    ),
    SLICE_DELETE: (
        "제거",
        "삭제",
        "정리",
        "cleanup",
        "deprecate",
        "remove",
        "drop ",
    ),
    SLICE_HOTFIX: ("hotfix",),
    SLICE_DOCS_ONLY: ("docs:",),
}


@dataclass(frozen=True)
class PRSliceClassification:
    """Result of :func:`classify_pr_slice`.

    ``primary_slice`` is the best-guess single category. ``secondary_slices``
    contains additional categories detected (non-empty triggers MIXED).
    ``confidence`` is 1.0 when title emoji matches, 0.6 when keyword-only.

    ``size_warning`` is True when *changed_lines_excluding_tests* >
    :data:`PR_SIZE_WARNING_THRESHOLD`.

    ``split_recommendation`` is the human-readable suggestion text
    (Korean) or ``None`` when no split is recommended.
    """

    primary_slice: str
    secondary_slices: Tuple[str, ...] = ()
    confidence: float = 1.0
    size_warning: bool = False
    changed_lines_excluding_tests: int = 0
    split_recommendation: Optional[str] = None
    detected_keywords: Tuple[str, ...] = ()

    @property
    def is_mixed(self) -> bool:
        return self.primary_slice == SLICE_MIXED or bool(self.secondary_slices)

    @property
    def is_exception(self) -> bool:
        return self.primary_slice in {
            SLICE_HOTFIX,
            SLICE_DOCS_ONLY,
            SLICE_TEST_ONLY,
            SLICE_TINY_CONFIG,
        }

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "primary_slice": self.primary_slice,
            "secondary_slices": list(self.secondary_slices),
            "confidence": self.confidence,
            "size_warning": self.size_warning,
            "changed_lines_excluding_tests": self.changed_lines_excluding_tests,
            "split_recommendation": self.split_recommendation,
            "detected_keywords": list(self.detected_keywords),
        }

    def status_summary_line(self) -> str:
        if self.primary_slice == SLICE_MIXED:
            return (
                f"⚠️ PR slice: MIXED ({', '.join(self.secondary_slices)}) "
                "— 한 책임으로 분할 권장"
            )
        warning = " ⚠️ size > 800 lines" if self.size_warning else ""
        return f"📦 PR slice: {self.primary_slice}{warning}"


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def classify_pr_slice(
    *,
    title: str,
    body: str = "",
    changed_files: Sequence[str] = (),
    changed_lines: int = 0,
    test_lines: int = 0,
) -> PRSliceClassification:
    """Classify a PR into one of the semantic slices.

    Algorithm:

      1. **Exception files** — if every changed file is a test file
         → ``test_only``. If every file is under ``docs/`` or
         ``policies/`` or ``*.md`` → ``docs_only``.
      2. **Title emoji** — high-confidence single match.
      3. **Keyword scan** — title + body for slice keywords.
         Multiple distinct slices detected → ``mixed``.
      4. **Tiny config** — ≤10 changed lines + config-like files.
      5. **Size warning** — changed_lines - test_lines > 800 →
         ``size_warning=True`` + split recommendation.
    """

    title_text = (title or "").strip()
    body_text = (body or "").strip()
    files = tuple(str(f) for f in changed_files if f)
    impl_lines = max(changed_lines - test_lines, 0)

    # 1. Exception file scan.
    if files:
        if all(_is_test_file(f) for f in files):
            return PRSliceClassification(
                primary_slice=SLICE_TEST_ONLY,
                confidence=1.0,
                changed_lines_excluding_tests=impl_lines,
            )
        if all(_is_docs_file(f) for f in files):
            return PRSliceClassification(
                primary_slice=SLICE_DOCS_ONLY,
                confidence=1.0,
                changed_lines_excluding_tests=impl_lines,
            )

    # 2. Title emoji (most specific).
    emoji_slice = _slice_from_title_emoji(title_text)
    detected_keywords: list = []
    title_lower = title_text.lower()
    body_lower = body_text.lower()

    # 3. Keyword scan — collect every slice that shows up.
    keyword_hits: set = set()
    for slice_name, keywords in _KEYWORD_PATTERNS.items():
        for keyword in keywords:
            if keyword in title_lower or keyword in body_lower:
                keyword_hits.add(slice_name)
                detected_keywords.append(keyword)

    # 4. Tiny config.
    if files and impl_lines <= 10 and _looks_like_config_only(files):
        return PRSliceClassification(
            primary_slice=SLICE_TINY_CONFIG,
            confidence=0.9,
            changed_lines_excluding_tests=impl_lines,
            detected_keywords=tuple(detected_keywords),
        )

    # 5. Hotfix dominates.
    if emoji_slice == SLICE_HOTFIX or SLICE_HOTFIX in keyword_hits:
        return PRSliceClassification(
            primary_slice=SLICE_HOTFIX,
            confidence=1.0,
            changed_lines_excluding_tests=impl_lines,
            detected_keywords=tuple(detected_keywords),
        )

    # 6. Resolve primary slice.
    primary: Optional[str] = emoji_slice
    confidence = 1.0
    if primary is None:
        # No emoji → use keyword hits. Multiple distinct → mixed.
        crud_hits = keyword_hits & {
            SLICE_CREATE,
            SLICE_READ,
            SLICE_UPDATE,
            SLICE_DELETE,
        }
        if len(crud_hits) > 1:
            primary = SLICE_MIXED
            confidence = 0.5
        elif len(crud_hits) == 1:
            primary = next(iter(crud_hits))
            confidence = 0.6
        elif keyword_hits & {SLICE_DOCS_ONLY}:
            primary = SLICE_DOCS_ONLY
            confidence = 0.9
        else:
            # No signal at all → conservative update guess.
            primary = SLICE_UPDATE
            confidence = 0.3

    # 7. Secondary slices — distinct from primary, excludes exception slices.
    crud_only_hits = keyword_hits & {
        SLICE_CREATE,
        SLICE_READ,
        SLICE_UPDATE,
        SLICE_DELETE,
    }
    secondary = tuple(sorted(crud_only_hits - {primary}))

    # If emoji+secondary both real C/R/U/D types → MIXED.
    if primary in {SLICE_CREATE, SLICE_READ, SLICE_UPDATE, SLICE_DELETE} and secondary:
        # Promote to MIXED for visibility.
        return PRSliceClassification(
            primary_slice=SLICE_MIXED,
            secondary_slices=tuple(sorted({primary, *secondary})),
            confidence=0.5,
            size_warning=impl_lines > PR_SIZE_WARNING_THRESHOLD,
            changed_lines_excluding_tests=impl_lines,
            detected_keywords=tuple(detected_keywords),
            split_recommendation=_build_split_recommendation(
                primary_slice=SLICE_MIXED,
                impl_lines=impl_lines,
                secondary=tuple(sorted({primary, *secondary})),
            ),
        )

    size_warning = impl_lines > PR_SIZE_WARNING_THRESHOLD
    split_text = _build_split_recommendation(
        primary_slice=primary,
        impl_lines=impl_lines,
        secondary=secondary,
    )
    return PRSliceClassification(
        primary_slice=primary,
        secondary_slices=secondary,
        confidence=confidence,
        size_warning=size_warning,
        changed_lines_excluding_tests=impl_lines,
        detected_keywords=tuple(detected_keywords),
        split_recommendation=split_text,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _slice_from_title_emoji(title: str) -> Optional[str]:
    """Return the slice when title starts with a known gitmoji."""

    if not title:
        return None
    stripped = title.lstrip()
    for prefix, slice_name in _TITLE_KEYWORDS:
        if stripped.lower().startswith(prefix.lower()):
            return slice_name
        # Match prefix anywhere in the leading 6 chars (handles "(#123) ✨ ...").
        if prefix in stripped[:12]:
            return slice_name
    return None


def _is_test_file(path: str) -> bool:
    lower = path.lower()
    return (
        lower.startswith("tests/")
        or lower.startswith("test/")
        or lower.endswith("_test.py")
        or "/tests/" in lower
        or "/test/" in lower
    )


def _is_docs_file(path: str) -> bool:
    lower = path.lower()
    if lower.endswith((".md", ".mdx", ".rst", ".txt")):
        return True
    return (
        lower.startswith("docs/")
        or lower.startswith("policies/")
        or lower.startswith("notes/")
        or "/docs/" in lower
        or "/policies/" in lower
    )


_CONFIG_FILE_PATTERNS = (
    re.compile(r".*\.env(\.|$)"),
    re.compile(r".*\.toml$"),
    re.compile(r".*\.ya?ml$"),
    re.compile(r".*\.json5?$"),
    re.compile(r".*\.cfg$"),
    re.compile(r".*\.ini$"),
    re.compile(r"pyproject\.toml$"),
    re.compile(r"package\.json$"),
)


def _looks_like_config_only(files: Sequence[str]) -> bool:
    if not files:
        return False
    return all(
        any(pat.match(f.lower()) for pat in _CONFIG_FILE_PATTERNS) for f in files
    )


def _build_split_recommendation(
    *,
    primary_slice: str,
    impl_lines: int,
    secondary: Tuple[str, ...],
) -> Optional[str]:
    """Render a Korean split recommendation when warranted."""

    if primary_slice == SLICE_MIXED and secondary:
        return (
            f"⚠️ 한 PR 에 {', '.join(secondary)} 책임이 섞여 있어요. "
            "리뷰어가 5 분 룰을 지키려면 commit 단위 또는 PR 단위로 분할을 권장합니다."
        )
    if impl_lines > PR_SIZE_WARNING_THRESHOLD:
        return (
            f"⚠️ impl 라인 {impl_lines} > 800 (stage-1 §5.1). "
            "리뷰어 5 분 룰 위반 가능 — 분할 검토."
        )
    return None


__all__ = (
    "PRSliceClassification",
    "PR_SIZE_WARNING_THRESHOLD",
    "SLICES",
    "SLICE_CREATE",
    "SLICE_DELETE",
    "SLICE_DOCS_ONLY",
    "SLICE_HOTFIX",
    "SLICE_MIXED",
    "SLICE_READ",
    "SLICE_TEST_ONLY",
    "SLICE_TINY_CONFIG",
    "SLICE_UPDATE",
    "classify_pr_slice",
)
