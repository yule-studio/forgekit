from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

NAVER_CATEGORY_POLICY_JSON_ENV = "YULE_NAVER_CATEGORY_POLICY_JSON"
NAVER_CATEGORY_POLICY_FILE_ENV = "YULE_NAVER_CATEGORY_POLICY_FILE"
DEFAULT_NAVER_CATEGORY_POLICY_PATH = Path("policies/runtime/agents/planning-agent/naver-category-policy.json")


@dataclass(frozen=True)
class NaverCategoryPolicy:
    color_code: str
    label: str
    priority_boost: int = 0
    reason: str = ""
    coding_candidate: bool = False
    alert_policy: Optional[str] = None
    flexible: bool = False

    @property
    def reason_label(self) -> str:
        if self.reason:
            return self.reason
        return self.label


def resolve_naver_category_policy(category_color: Optional[str]) -> Optional[NaverCategoryPolicy]:
    color_code = _normalize_color_code(category_color)
    if color_code is None:
        return None
    return load_naver_category_policies().get(color_code)


@lru_cache(maxsize=1)
def load_naver_category_policies() -> dict[str, NaverCategoryPolicy]:
    payload = _load_policy_payload()
    raw_colors = payload.get("colors", {})
    if not isinstance(raw_colors, dict):
        return {}

    policies: dict[str, NaverCategoryPolicy] = {}
    for raw_code, raw_policy in raw_colors.items():
        color_code = _normalize_color_code(raw_code)
        if color_code is None or not isinstance(raw_policy, dict):
            continue
        policies[color_code] = NaverCategoryPolicy(
            color_code=color_code,
            label=str(raw_policy.get("label") or color_code),
            priority_boost=_int_value(raw_policy.get("priority_boost"), default=0),
            reason=str(raw_policy.get("reason") or ""),
            coding_candidate=bool(raw_policy.get("coding_candidate") or False),
            alert_policy=_optional_string(raw_policy.get("alert_policy")),
            flexible=bool(raw_policy.get("flexible") or False),
        )
    return policies


def reset_naver_category_policy_cache() -> None:
    load_naver_category_policies.cache_clear()


def _load_policy_payload() -> dict:
    raw_json = os.environ.get(NAVER_CATEGORY_POLICY_JSON_ENV)
    if raw_json:
        return _parse_policy_json(raw_json)

    policy_path = _resolve_policy_path()
    if policy_path is None or not policy_path.exists():
        return {}

    return _parse_policy_json(policy_path.read_text(encoding="utf-8"))


def _resolve_policy_path() -> Optional[Path]:
    configured_path = os.environ.get(NAVER_CATEGORY_POLICY_FILE_ENV)
    if configured_path:
        return Path(configured_path).expanduser()

    repo_root = os.environ.get("YULE_REPO_ROOT")
    if repo_root:
        return Path(repo_root) / DEFAULT_NAVER_CATEGORY_POLICY_PATH

    return DEFAULT_NAVER_CATEGORY_POLICY_PATH


def _parse_policy_json(raw_json: str) -> dict:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _normalize_color_code(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _optional_string(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_value(value: object, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
