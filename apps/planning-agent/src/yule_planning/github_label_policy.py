from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

GITHUB_LABEL_POLICY_JSON_ENV = "YULE_GITHUB_LABEL_POLICY_JSON"
GITHUB_LABEL_POLICY_FILE_ENV = "YULE_GITHUB_LABEL_POLICY_FILE"
DEFAULT_GITHUB_LABEL_POLICY_PATH = Path(
    "policies/runtime/agents/planning-agent/github-label-policy.json"
)


@dataclass(frozen=True)
class GitHubLabelPolicy:
    label: str
    priority_boost: int = 0
    reason: str = ""


def resolve_github_label_policies(
    labels: Iterable[str],
) -> list[GitHubLabelPolicy]:
    if not labels:
        return []
    policies = load_github_label_policies()
    matched: list[GitHubLabelPolicy] = []
    for raw_label in labels:
        normalized = _normalize_label(raw_label)
        if normalized is None:
            continue
        policy = policies.get(normalized)
        if policy is not None:
            matched.append(policy)
    return matched


@lru_cache(maxsize=1)
def load_github_label_policies() -> dict[str, GitHubLabelPolicy]:
    payload = _load_policy_payload()
    raw_labels = payload.get("labels", {})
    if not isinstance(raw_labels, dict):
        return {}

    policies: dict[str, GitHubLabelPolicy] = {}
    for raw_label, raw_policy in raw_labels.items():
        label = _normalize_label(raw_label)
        if label is None or not isinstance(raw_policy, dict):
            continue
        policies[label] = GitHubLabelPolicy(
            label=label,
            priority_boost=_int_value(raw_policy.get("priority_boost"), default=0),
            reason=str(raw_policy.get("reason") or ""),
        )
    return policies


def reset_github_label_policy_cache() -> None:
    load_github_label_policies.cache_clear()


def _load_policy_payload() -> dict:
    raw_json = os.environ.get(GITHUB_LABEL_POLICY_JSON_ENV)
    if raw_json:
        return _parse_policy_json(raw_json)

    policy_path = _resolve_policy_path()
    if policy_path is None or not policy_path.exists():
        return {}

    return _parse_policy_json(policy_path.read_text(encoding="utf-8"))


def _resolve_policy_path() -> Optional[Path]:
    configured_path = os.environ.get(GITHUB_LABEL_POLICY_FILE_ENV)
    if configured_path:
        return Path(configured_path).expanduser()

    repo_root = os.environ.get("YULE_REPO_ROOT")
    if repo_root:
        return Path(repo_root) / DEFAULT_GITHUB_LABEL_POLICY_PATH

    return DEFAULT_GITHUB_LABEL_POLICY_PATH


def _parse_policy_json(raw_json: str) -> dict:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _normalize_label(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _int_value(value: object, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
