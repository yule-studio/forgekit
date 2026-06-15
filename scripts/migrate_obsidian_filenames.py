"""Obsidian 파일명 컨벤션 마이그레이션 (F8 / #99).

기존 ``YYYY-MM-DD_<rest>.md`` 형태의 노트를 컨벤션 §4.1 기준의
``<rest>.md`` 형태로 일괄 rename + wikilink (``[[YYYY-MM-DD_xxx]]``)
일괄 갱신한다.

기본은 ``dry-run`` — 영향을 받는 파일/링크를 식별만 한다. 실제
rename 은 ``--apply`` 플래그가 명시된 경우에만 수행하며, git mv 로
history 를 유지한다. 본 스크립트는 다음 두 단계로 구성된다:

  1. rename 단계: 매칭되는 파일을 ``git mv`` 로 옮긴다. 기존 frontmatter
     의 ``created_at`` 이 없으면 stem 의 날짜 prefix 에서 추출하여
     자동 주입한다 (있으면 건드리지 않음).
  2. wikilink 단계: 모든 ``.md`` 파일을 스캔하여 ``[[YYYY-MM-DD_*]]``
     형태의 wikilink 를 일괄 새 이름으로 갱신한다.

Hard rails:

  * dry-run 이 기본. ``--apply`` 명시만 실제 변경.
  * ``--apply`` 라도 보호 브랜치 (``main`` / ``master``) 에서는 작동하지
    않는다 (PR 기반 흐름 강제).
  * rename 충돌이 발생하면 (target 파일이 이미 존재) 그 항목은 SKIP
    + 명확한 reason 으로 결과 dict 에 기록.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple


FILENAME_PATTERN = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<rest>.+)\.md$")
WIKILINK_PATTERN = re.compile(r"\[\[(?P<date>\d{4}-\d{2}-\d{2})_(?P<rest>[^\[\]\|#]+?)(?P<tail>[\|#][^\[\]]*)?\]\]")

PROTECTED_BRANCHES = ("main", "master")

# 컨벤션 §4.1 허용 kind 들
ALLOWED_KINDS = (
    "task-log",
    "decision",
    "research",
    "knowledge",
    "meeting",
    "work-report",
    "report",  # legacy alias (work-report 로 흡수되기 전 기록)
)


@dataclass(frozen=True)
class RenamePlan:
    """rename 단계 1건의 dry-run/apply 결과."""

    source: Path
    target: Path
    performed: bool
    skipped_reason: Optional[str] = None
    injected_created_at: bool = False


@dataclass(frozen=True)
class WikilinkUpdate:
    """wikilink 갱신 1건 결과."""

    path: Path
    replacements: int
    performed: bool


@dataclass(frozen=True)
class MigrationReport:
    """전체 마이그레이션 결과 요약."""

    apply: bool
    renames: Tuple[RenamePlan, ...] = field(default_factory=tuple)
    wikilink_updates: Tuple[WikilinkUpdate, ...] = field(default_factory=tuple)
    blocker: Optional[str] = None

    @property
    def rename_count(self) -> int:
        return sum(1 for p in self.renames if p.performed or not self.apply)

    @property
    def wikilink_count(self) -> int:
        return sum(u.replacements for u in self.wikilink_updates)


# ---------------------------------------------------------------------------
# Pure helpers (테스트 친화적 — 디스크 IO 없음)
# ---------------------------------------------------------------------------


def _canonicalise(rest: str) -> str:
    """``YYYY-MM-DD_`` 제거 후 잔여 stem 을 컨벤션 §4.1 캐논으로 정규화.

    잔여 stem 형태별 처리:
      * ``<kind>_<topic>`` → ``<kind>-<topic>`` (underscore → hyphen)
      * ``issue-<n>-<kind>-<topic>`` → ``<kind>-<topic>-issue-<n>`` (재배열)
      * ``report-*`` → ``work-report-*`` (legacy kind 정규화)
      * 그 외엔 그대로 (이미 ``<kind>-<topic>`` 형태) — 단 underscore 는 hyphen 으로 통일.
    """

    # 1. ``issue-<n>-<kind>-…`` 패턴이면 issue 를 뒤로 이동
    head_issue = re.match(r"^issue-(\d+)-(.+)$", rest)
    if head_issue:
        issue_no, tail = head_issue.group(1), head_issue.group(2)
        rest = f"{tail}-issue-{issue_no}"
    # 2. underscore 를 hyphen 으로 통일
    normalised = rest.replace("_", "-")
    # 3. legacy kind alias 정규화: ``report-`` → ``work-report-``
    if normalised.startswith("report-"):
        normalised = "work-" + normalised
    return normalised


def compute_new_name(old_name: str) -> Optional[str]:
    """``YYYY-MM-DD_xxx.md`` → 컨벤션 캐논 이름 매핑. 매칭 안 되면 None."""

    match = FILENAME_PATTERN.match(old_name)
    if not match:
        return None
    canon = _canonicalise(match.group("rest"))
    return f"{canon}.md"


def extract_date_prefix(old_name: str) -> Optional[str]:
    """파일명에서 ``YYYY-MM-DD`` 추출. 매칭 안 되면 None."""

    match = FILENAME_PATTERN.match(old_name)
    if not match:
        return None
    return match.group("date")


def update_wikilinks(text: str, rename_map: Mapping[str, str]) -> Tuple[str, int]:
    """``text`` 안의 ``[[YYYY-MM-DD_*]]`` 를 ``rename_map`` 으로 치환.

    ``rename_map`` 은 ``old_stem -> new_stem`` 형태 (``.md`` suffix 제외).
    교체된 wikilink 개수를 함께 반환.
    """

    count = 0

    def _replace(match: "re.Match[str]") -> str:
        nonlocal count
        old_stem = f"{match.group('date')}_{match.group('rest')}"
        new_stem = rename_map.get(old_stem)
        if new_stem is None:
            return match.group(0)
        count += 1
        tail = match.group("tail") or ""
        return f"[[{new_stem}{tail}]]"

    new_text = WIKILINK_PATTERN.sub(_replace, text)
    return new_text, count


def ensure_created_at_in_frontmatter(content: str, *, date: str) -> Tuple[str, bool]:
    """frontmatter 에 ``created_at`` 이 없으면 ``date`` 기반 ISO8601 KST 로 주입.

    이미 존재하면 변경 없이 ``(content, False)`` 반환.
    """

    if not content.startswith("---\n"):
        return content, False
    end_idx = content.find("\n---\n", 4)
    if end_idx == -1:
        return content, False
    header = content[4:end_idx]
    if re.search(r"^created_at\s*:", header, flags=re.MULTILINE):
        return content, False
    iso = f"{date}T00:00:00+09:00"
    new_header = header.rstrip("\n") + f"\ncreated_at: {iso}\n"
    new_content = "---\n" + new_header + content[end_idx:]
    return new_content, True


# ---------------------------------------------------------------------------
# Disk + git wrappers
# ---------------------------------------------------------------------------


def discover_candidates(notes_root: Path) -> List[Path]:
    """``notes_root`` 아래에서 ``YYYY-MM-DD_*.md`` 모두 수집."""

    if not notes_root.exists():
        return []
    out: List[Path] = []
    for path in sorted(notes_root.rglob("*.md")):
        if compute_new_name(path.name) is not None:
            out.append(path)
    return out


def discover_all_markdown(notes_root: Path) -> List[Path]:
    if not notes_root.exists():
        return []
    return sorted(notes_root.rglob("*.md"))


def _safe_git_runner():
    """Lazy-load the repo-local git safety guardrail (run_safe_git)."""

    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "apps" / "engineering-agent" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from yule_engineering.agents.governance.git_path_safety import run_safe_git

    return run_safe_git


def git_mv(source: Path, target: Path, *, repo_root: Path) -> None:
    # Routed through the safety guardrail: `git -C <validated repo> mv ...`,
    # HOME / ambiguous-path refused.
    run_safe_git = _safe_git_runner()
    run_safe_git(repo_root, ["mv", str(source), str(target)], check=True)


def current_branch(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def plan_renames(candidates: Sequence[Path]) -> List[RenamePlan]:
    """충돌 검사를 포함한 rename 계획 (dry-run 안전)."""

    plans: List[RenamePlan] = []
    for source in candidates:
        new_name = compute_new_name(source.name)
        if new_name is None:
            continue
        target = source.with_name(new_name)
        if target.exists():
            plans.append(
                RenamePlan(
                    source=source,
                    target=target,
                    performed=False,
                    skipped_reason=f"target already exists: {target.name}",
                )
            )
            continue
        plans.append(RenamePlan(source=source, target=target, performed=False))
    return plans


def apply_renames(
    plans: Sequence[RenamePlan], *, repo_root: Path
) -> List[RenamePlan]:
    """``--apply`` 단계: git mv + frontmatter created_at 주입."""

    out: List[RenamePlan] = []
    for plan in plans:
        if plan.skipped_reason is not None:
            out.append(plan)
            continue
        # frontmatter 주입 — rename 이전에 원본 파일 위에서 수행.
        date = extract_date_prefix(plan.source.name) or ""
        injected = False
        if date:
            try:
                content = plan.source.read_text(encoding="utf-8")
            except OSError:
                content = ""
            new_content, did = ensure_created_at_in_frontmatter(content, date=date)
            if did and new_content != content:
                plan.source.write_text(new_content, encoding="utf-8")
                injected = True
        git_mv(plan.source, plan.target, repo_root=repo_root)
        out.append(
            RenamePlan(
                source=plan.source,
                target=plan.target,
                performed=True,
                injected_created_at=injected,
            )
        )
    return out


def plan_wikilink_updates(
    notes_root: Path, rename_map: Mapping[str, str]
) -> List[WikilinkUpdate]:
    """``rename_map`` 기준 wikilink 치환 dry-run 계획."""

    updates: List[WikilinkUpdate] = []
    for path in discover_all_markdown(notes_root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        _, count = update_wikilinks(text, rename_map)
        if count > 0:
            updates.append(WikilinkUpdate(path=path, replacements=count, performed=False))
    return updates


def apply_wikilink_updates(
    updates: Sequence[WikilinkUpdate], rename_map: Mapping[str, str]
) -> List[WikilinkUpdate]:
    out: List[WikilinkUpdate] = []
    for update in updates:
        try:
            text = update.path.read_text(encoding="utf-8")
        except OSError:
            out.append(update)
            continue
        new_text, count = update_wikilinks(text, rename_map)
        if count > 0:
            update.path.write_text(new_text, encoding="utf-8")
        out.append(
            WikilinkUpdate(path=update.path, replacements=count, performed=True)
        )
    return out


def build_rename_map(plans: Iterable[RenamePlan]) -> Mapping[str, str]:
    """``stem -> stem`` (``.md`` 제외) 매핑."""

    mapping: dict = {}
    for plan in plans:
        if plan.skipped_reason is not None:
            continue
        old_stem = plan.source.stem
        new_stem = plan.target.stem
        mapping[old_stem] = new_stem
    return mapping


def run_migration(
    *,
    notes_root: Path,
    repo_root: Path,
    apply: bool,
    branch_override: Optional[str] = None,
) -> MigrationReport:
    """End-to-end orchestration. ``apply=False`` 면 디스크 변경 없음."""

    if apply:
        branch = branch_override or current_branch(repo_root)
        if branch in PROTECTED_BRANCHES:
            return MigrationReport(
                apply=apply,
                blocker=f"protected branch direct apply blocked: {branch}",
            )

    candidates = discover_candidates(notes_root)
    plans = plan_renames(candidates)
    rename_map = build_rename_map(plans)
    wikilink_plans = plan_wikilink_updates(notes_root, rename_map)

    if not apply:
        return MigrationReport(
            apply=False,
            renames=tuple(plans),
            wikilink_updates=tuple(wikilink_plans),
        )

    # 순서 주의: wikilink 갱신을 **rename 전** 에 수행한다. rename 후에는
    # ``wikilink_plans`` 의 ``path`` 가 더 이상 존재하지 않기 때문이다.
    applied_wikilinks = apply_wikilink_updates(wikilink_plans, rename_map)
    applied_renames = apply_renames(plans, repo_root=repo_root)
    return MigrationReport(
        apply=True,
        renames=tuple(applied_renames),
        wikilink_updates=tuple(applied_wikilinks),
    )


def _format_report(report: MigrationReport) -> str:
    lines: List[str] = []
    mode = "APPLY" if report.apply else "DRY-RUN"
    lines.append(f"[{mode}] obsidian filename migration")
    if report.blocker:
        lines.append(f"  BLOCKER: {report.blocker}")
        return "\n".join(lines)
    lines.append(f"  rename candidates: {len(report.renames)}")
    for plan in report.renames:
        marker = "SKIP" if plan.skipped_reason else ("OK" if plan.performed else "PLAN")
        suffix = f" — {plan.skipped_reason}" if plan.skipped_reason else ""
        lines.append(
            f"    [{marker}] {plan.source.name} → {plan.target.name}{suffix}"
        )
    lines.append(f"  wikilink updates: {len(report.wikilink_updates)} files / {report.wikilink_count} links")
    for update in report.wikilink_updates:
        lines.append(
            f"    {update.path.name} (×{update.replacements})"
        )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="F8 Obsidian 파일명 컨벤션 마이그레이션 — dry-run default",
    )
    parser.add_argument(
        "--notes-root",
        type=Path,
        default=Path("notes"),
        help="vault-mirror 루트 (default: notes)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="git mv 가 실행될 작업 디렉터리 (default: .)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 rename / wikilink 갱신 수행 (지정 없으면 dry-run)",
    )
    args = parser.parse_args(argv)

    report = run_migration(
        notes_root=args.notes_root.resolve(),
        repo_root=args.repo_root.resolve(),
        apply=args.apply,
    )
    sys.stdout.write(_format_report(report) + "\n")
    if report.blocker:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
