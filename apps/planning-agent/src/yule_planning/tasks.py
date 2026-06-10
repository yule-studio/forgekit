from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Optional, Sequence

from yule_engineering.integrations.calendar.models import CalendarTodo
from yule_engineering.integrations.github.issues import GitHubIssue
from yule_engineering.integrations.github.pulls import GitHubPullRequest
from yule_engineering.storage import (
    UserPatternSignals,
    compute_user_pattern_signals,
    compute_user_pattern_signals_batch,
)
from .category_policy import resolve_naver_category_policy
from .github_label_policy import resolve_github_label_policies
from .models import PlanningInputs, PlanningTaskCandidate, ReminderItem

USER_PATTERN_MIN_HISTORY = 2
USER_PATTERN_SKIP_THRESHOLD = 0.5
USER_PATTERN_MAX_SKIP_PENALTY = 15
USER_PATTERN_DONE_THRESHOLD = 0.7
USER_PATTERN_DONE_BONUS = 5


def build_task_candidates(inputs: PlanningInputs) -> list[PlanningTaskCandidate]:
    tasks: list[PlanningTaskCandidate] = []

    for todo in inputs.calendar_todos:
        if todo.completed:
            continue
        tasks.append(_build_todo_candidate(inputs.plan_date, todo))

    for issue in inputs.github_issues:
        tasks.append(_build_issue_candidate(issue))

    for pull_request in inputs.github_pull_requests:
        tasks.append(_build_pull_request_candidate(pull_request))

    for reminder in inputs.reminders:
        tasks.append(_build_reminder_candidate(inputs.plan_date, reminder))

    signal_cache = compute_user_pattern_signals_batch(
        source_event_titles=(task.title for task in tasks),
    )
    tasks = [_apply_user_pattern_signals(task, signal_cache.get(task.title)) for task in tasks]

    tasks.sort(key=lambda task: (-task.priority_score, task.due_date or "9999-12-31", task.title.lower()))
    return tasks


def _apply_user_pattern_signals(
    task: PlanningTaskCandidate,
    signals: Optional[UserPatternSignals] = None,
) -> PlanningTaskCandidate:
    if signals is None:
        signals = compute_user_pattern_signals(source_event_title=task.title)
    if signals.total_count < USER_PATTERN_MIN_HISTORY:
        return task

    score_delta = 0
    extra_reasons: list[str] = []

    if signals.skip_ratio >= USER_PATTERN_SKIP_THRESHOLD:
        penalty = min(
            USER_PATTERN_MAX_SKIP_PENALTY,
            int(round(USER_PATTERN_MAX_SKIP_PENALTY * signals.skip_ratio)),
        )
        if penalty > 0:
            score_delta -= penalty
            extra_reasons.append(
                f"최근 {signals.total_count}회 중 {signals.skipped_count}회 건너뛴 패턴 (-{penalty})"
            )
    elif signals.done_ratio >= USER_PATTERN_DONE_THRESHOLD:
        score_delta += USER_PATTERN_DONE_BONUS
        extra_reasons.append(
            f"최근 {signals.total_count}회 중 {signals.done_count}회 완료한 패턴 (+{USER_PATTERN_DONE_BONUS})"
        )

    estimated_minutes = task.estimated_minutes
    typical_minutes = signals.typical_block_minutes
    if typical_minutes is not None and typical_minutes > 0:
        if abs(typical_minutes - estimated_minutes) >= 15:
            extra_reasons.append(
                f"평소 {typical_minutes}분 슬롯에서 마무리하는 패턴 → estimated_minutes 조정"
            )
            estimated_minutes = typical_minutes

    if score_delta == 0 and estimated_minutes == task.estimated_minutes:
        return task

    new_score = task.priority_score + score_delta
    return replace(
        task,
        priority_score=new_score,
        priority_level=_priority_level(new_score),
        estimated_minutes=estimated_minutes,
        reasons=tuple([*task.reasons, *extra_reasons]),
    )


def _build_todo_candidate(plan_date: date, todo: CalendarTodo) -> PlanningTaskCandidate:
    score = 40
    reasons = ["calendar todo"]
    due_date = _date_only(todo.due or todo.start)
    category_policy = resolve_naver_category_policy(todo.category_color)

    if due_date is not None:
        offset = (due_date - plan_date).days
        if offset < 0:
            score += 50
            reasons.append("overdue")
        elif offset == 0:
            score += 35
            reasons.append("due today")
        elif offset == 1:
            score += 15
            reasons.append("due tomorrow")

    keyword_score, keyword_reasons = _keyword_boost(todo.title, todo.description)
    score += keyword_score
    reasons.extend(keyword_reasons)

    if category_policy is not None:
        score += category_policy.priority_boost
        reasons.append(f"naver category: {category_policy.reason_label}")

    return PlanningTaskCandidate(
        task_id=f"todo:{todo.item_uid}",
        source_type="calendar_todo",
        title=todo.title,
        description=todo.description,
        due_date=todo.due or todo.start,
        priority_score=score,
        priority_level=_priority_level(score),
        estimated_minutes=60,
        reasons=reasons,
        coding_candidate=_looks_like_coding_work(todo.title, todo.description)
        or bool(category_policy and category_policy.coding_candidate),
        category_color=todo.category_color,
        category_label=category_policy.label if category_policy is not None else None,
        flexible=bool(category_policy and category_policy.flexible),
    )


def _build_issue_candidate(issue: GitHubIssue) -> PlanningTaskCandidate:
    score = 35
    reasons = ["open GitHub issue", "coding candidate"]

    if issue.scope == "personal":
        score += 10
        reasons.append("personal repository")
    elif issue.scope.startswith("org:"):
        score += 5
        reasons.append("organization repository")

    keyword_score, keyword_reasons = _keyword_boost(issue.title, issue.body)
    score += keyword_score
    reasons.extend(keyword_reasons)

    sequence_score, sequence_reasons = _dev_sequence_boost(issue.title)
    score += sequence_score
    reasons.extend(sequence_reasons)

    label_policies = resolve_github_label_policies(issue.labels)
    for policy in label_policies:
        score += policy.priority_boost
        if policy.reason:
            reasons.append(f"label `{policy.label}`: {policy.reason}")
        else:
            reasons.append(f"label `{policy.label}`")

    return PlanningTaskCandidate(
        task_id=f"issue:{issue.repository}#{issue.number}",
        source_type="github_issue",
        title=issue.title,
        description=issue.url,
        due_date=None,
        priority_score=score,
        priority_level=_priority_level(score),
        estimated_minutes=90,
        reasons=reasons,
        coding_candidate=True,
    )


def _build_pull_request_candidate(pull_request: GitHubPullRequest) -> PlanningTaskCandidate:
    score = 50
    reasons = ["open GitHub pull request", "coding candidate"]

    if pull_request.scope == "personal":
        score += 10
        reasons.append("personal repository")
    elif pull_request.scope.startswith("org:"):
        score += 5
        reasons.append("organization repository")

    if pull_request.draft:
        score -= 10
        reasons.append("draft PR")
    else:
        score += 10
        reasons.append("ready for review")

    if pull_request.assignees:
        score += 5
        reasons.append(f"{len(pull_request.assignees)}명 담당자 지정됨")

    keyword_score, keyword_reasons = _keyword_boost(pull_request.title, pull_request.body)
    score += keyword_score
    reasons.extend(keyword_reasons)

    sequence_score, sequence_reasons = _dev_sequence_boost(pull_request.title)
    score += sequence_score
    reasons.extend(sequence_reasons)

    label_policies = resolve_github_label_policies(pull_request.labels)
    for policy in label_policies:
        score += policy.priority_boost
        if policy.reason:
            reasons.append(f"label `{policy.label}`: {policy.reason}")
        else:
            reasons.append(f"label `{policy.label}`")

    return PlanningTaskCandidate(
        task_id=f"pr:{pull_request.repository}#{pull_request.number}",
        source_type="github_pull_request",
        title=pull_request.title,
        description=pull_request.url,
        due_date=None,
        priority_score=score,
        priority_level=_priority_level(score),
        estimated_minutes=60,
        reasons=reasons,
        coding_candidate=True,
    )


def _build_reminder_candidate(plan_date: date, reminder: ReminderItem) -> PlanningTaskCandidate:
    score = 45
    reasons = ["reminder item"]
    due_date = _date_only(reminder.due_date)

    if due_date is not None:
        offset = (due_date - plan_date).days
        if offset < 0:
            score += 40
            reasons.append("review overdue")
        elif offset == 0:
            score += 25
            reasons.append("review today")
        elif offset == 1:
            score += 10
            reasons.append("review tomorrow")

    if reminder.priority_hint:
        hint = reminder.priority_hint.strip().lower()
        if hint in {"high", "urgent", "critical"}:
            score += 20
            reasons.append("high priority hint")
        elif hint == "medium":
            score += 8
            reasons.append("medium priority hint")

    keyword_score, keyword_reasons = _keyword_boost(reminder.title, reminder.description)
    score += keyword_score
    reasons.extend(keyword_reasons)

    return PlanningTaskCandidate(
        task_id=f"reminder:{reminder.item_id}",
        source_type="review_reminder",
        title=reminder.title,
        description=reminder.description,
        due_date=reminder.due_date,
        priority_score=score,
        priority_level=_priority_level(score),
        estimated_minutes=max(15, reminder.estimated_minutes),
        reasons=reasons,
        coding_candidate=_looks_like_coding_work(reminder.title, reminder.description, reminder.tags),
    )


def _date_only(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    if "T" in value:
        return datetime.fromisoformat(value).date()
    return date.fromisoformat(value)


def _priority_level(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


FOUNDATION_KEYWORDS = (
    "도메인",
    "엔티티",
    "스키마",
    "마이그레이션",
    "schema",
    "migration",
    "domain",
    "entity",
    "model",
    "infrastructure",
    "infra",
    "repository",
    "base",
    "core",
    "설계",
    "auth",
    "인증",
    "회원",
)
SURFACE_KEYWORDS = (
    "ui",
    "ux",
    "design",
    "디자인",
    "댓글",
    "comment",
    "색상",
    "color",
    "폰트",
    "font",
    "스타일",
    "style",
)


def _dev_sequence_boost(title: str) -> tuple[int, list[str]]:
    haystack = title.lower()
    score = 0
    reasons: list[str] = []
    if any(keyword in haystack for keyword in FOUNDATION_KEYWORDS):
        score += 25
        reasons.append("foundation layer")
    if any(keyword in haystack for keyword in SURFACE_KEYWORDS):
        score -= 10
        reasons.append("surface layer")
    return score, reasons


def _keyword_boost(title: str, description: str) -> tuple[int, list[str]]:
    haystack = f"{title}\n{description}".lower()
    score = 0
    reasons: list[str] = []

    high_keywords = ["오늘", "urgent", "긴급", "마감", "시험", "신청", "fix", "bug", "hotfix", "error"]
    medium_keywords = ["정리", "문서", "분석", "review", "복습"]

    if any(keyword in haystack for keyword in high_keywords):
        score += 20
        reasons.append("urgent keyword")
    if any(keyword in haystack for keyword in medium_keywords):
        score += 8
        reasons.append("review or documentation keyword")

    return score, reasons


def _looks_like_coding_work(title: str, description: str, tags: Optional[Sequence[str]] = None) -> bool:
    combined = f"{title}\n{description}".lower()
    tag_values = [tag.lower() for tag in (tags or [])]
    coding_keywords = [
        "api",
        "backend",
        "bug",
        "code",
        "coding",
        "dev",
        "docs",
        "issue",
        "pr",
        "refactor",
        "repository",
        "server",
        "test",
        "agent",
    ]
    if any(keyword in combined for keyword in coding_keywords):
        return True
    return any(tag in {"coding", "dev", "backend", "review"} for tag in tag_values)
