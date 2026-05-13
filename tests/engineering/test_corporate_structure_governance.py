"""F15 — corporate org chart ↔ agents/ directory governance.

회사 조직 골격 (CTO / CPO / CMO / CHRO / CFO / CRO / GC) 의 부서 / 역할이
silent 하게 사라지거나 schema drift 가 발생하지 않도록 lock down.

검사 대상:
  * `policies/runtime/agents/corporate-org-chart.md` (single source of truth)
  * `agents/<dept>/<role>/manifest.json` — F11 AgentManifest schema 준수
  * `agents/<dept>/<role>/prompt.md` — manifest 의 prompt_template_ref 와 매칭
  * `plugins_required` — `plugins/<id>/manifest.json` 에 실제 등록된 plugin
  * `prompts/skills/pm/` — PM skills 10+ (issue #126 acceptance criteria #3)

본 test 가 통과한다는 것은 회사 조직 governance 가 살아 있다는 뜻이다.
새 부서 / 역할 추가 시 org-chart + manifest + (옵션) prompt 셋이 1:1 매칭.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


_REPO_ROOT = Path(__file__).resolve().parents[2]
_ORG_CHART = _REPO_ROOT / "policies" / "runtime" / "agents" / "corporate-org-chart.md"
_AGENTS_DIR = _REPO_ROOT / "agents"
_PLUGINS_DIR = _REPO_ROOT / "plugins"
_SKILLS_PM_DIR = _REPO_ROOT / "prompts" / "skills" / "pm"


# Departments that the F15 PR contracts must contain — single source of truth.
# Format: (department_dir_id, c_level_label, required_roles).
# `required_roles` 는 corporate-org-chart 의 "부서 × 역할 매트릭스" row 와 매칭.
_REQUIRED_DEPARTMENTS = (
    ("engineering-agent", "CTO", (
        "tech-lead",
        "backend-engineer",
        "frontend-engineer",
        "qa-engineer",
        "devops-engineer",
        "ai-engineer",
        "product-designer",
    )),
    ("product-agent", "CPO", (
        "product-manager",
        "user-researcher",
        "growth-analyst",
    )),
    ("marketing-agent", "CMO", (
        "growth-marketer",
        "content-strategist",
        "seo-specialist",
        "brand-manager",
    )),
    ("hr-agent", "CHRO", (
        "recruiter",
        "people-ops",
        "culture-coach",
    )),
    ("finance-agent", "CFO", (
        "budget-analyst",
    )),
    ("sales-cs-agent", "CRO", (
        "sales-rep",
        "customer-success",
    )),
    ("legal-agent", "GC", (
        "contract-reviewer",
        "privacy-officer",
    )),
)


# Legacy 부서 — F15 scope 밖. 별도 migration issue 에서 새 컨벤션으로 정렬.
# 본 test 는 legacy 부서의 id 컨벤션 / plugin 등록 strict-check 를 skip 하고,
# 새 부서 (F15 land) 만 fully strict 하게 검증한다.
#
# 본 lock 자체는 "F15 가 새 부서에 적용한 컨벤션이 무엇이며,
# legacy 부서가 그 컨벤션과 어떻게 다른지" 의 단일 진실로 동작한다.
_LEGACY_DEPARTMENTS = frozenset({"engineering-agent"})


# F11 AgentManifest required keys for *role-level* manifest.
# (department-level manifest 는 다른 schema — engineering-agent 만 갖고 있음, 본 test scope 밖.)
_ROLE_MANIFEST_REQUIRED_KEYS = (
    "id",
    "name",
    "role",
    "version",
    "capabilities",
    "plugins_required",
    "prompt_template_ref",
    "autonomy_level",
    "risk_class",
    "module_path",
)


_VALID_RISK_CLASSES = {"LOW", "MEDIUM", "HIGH"}
_VALID_AUTONOMY_LEVELS = {"advisory", "supervised", "autonomous"}


def _read(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _registered_plugin_ids() -> set[str]:
    ids: set[str] = set()
    if not _PLUGINS_DIR.is_dir():
        return ids
    for child in _PLUGINS_DIR.iterdir():
        manifest = child / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = _load_json(manifest)
        except json.JSONDecodeError:
            continue
        plugin_id = data.get("id")
        if isinstance(plugin_id, str):
            ids.add(plugin_id)
    return ids


# ---------------------------------------------------------------------------
# org-chart doc
# ---------------------------------------------------------------------------


class CorporateOrgChartDocTests(unittest.TestCase):
    """corporate-org-chart.md 는 모든 부서 / 역할의 single source of truth."""

    def setUp(self) -> None:
        self.path = _ORG_CHART
        self.text = _read(self.path)

    def test_doc_exists(self) -> None:
        self.assertTrue(self.path.is_file(), f"missing {self.path}")

    def test_doc_lists_every_required_department(self) -> None:
        for dept_id, _c_level, _ in _REQUIRED_DEPARTMENTS:
            with self.subTest(department=dept_id):
                self.assertIn(
                    dept_id,
                    self.text,
                    f"corporate-org-chart 가 {dept_id} 를 참조하지 않음",
                )

    def test_doc_lists_every_c_level(self) -> None:
        for _dept_id, c_level, _ in _REQUIRED_DEPARTMENTS:
            with self.subTest(c_level=c_level):
                self.assertRegex(
                    self.text,
                    rf"\b{re.escape(c_level)}\b",
                    f"corporate-org-chart 의 C-level 매트릭스에서 {c_level} 누락",
                )

    def test_doc_lists_every_role_short_id(self) -> None:
        for dept_id, _c_level, roles in _REQUIRED_DEPARTMENTS:
            for role in roles:
                with self.subTest(department=dept_id, role=role):
                    self.assertIn(
                        f"{dept_id}/{role}",
                        self.text,
                        f"corporate-org-chart 가 {dept_id}/{role} 를 row 에 누락",
                    )

    def test_doc_includes_new_department_procedure(self) -> None:
        """새 부서 추가 절차 — 사라지면 governance 가 비기록 drift 됨."""
        self.assertIn("새 부서", self.text)


# ---------------------------------------------------------------------------
# Department directory existence
# ---------------------------------------------------------------------------


class DepartmentDirectoryTests(unittest.TestCase):
    """`agents/<dept>/` 6 부서 디렉터리 모두 존재 (Acceptance Criteria #1)."""

    def test_each_department_directory_exists(self) -> None:
        for dept_id, _c_level, _roles in _REQUIRED_DEPARTMENTS:
            with self.subTest(department=dept_id):
                self.assertTrue(
                    (_AGENTS_DIR / dept_id).is_dir(),
                    f"missing agents/{dept_id}/",
                )

    def test_each_role_directory_exists(self) -> None:
        for dept_id, _c_level, roles in _REQUIRED_DEPARTMENTS:
            for role in roles:
                with self.subTest(department=dept_id, role=role):
                    self.assertTrue(
                        (_AGENTS_DIR / dept_id / role).is_dir(),
                        f"missing agents/{dept_id}/{role}/",
                    )


# ---------------------------------------------------------------------------
# Role-level manifest schema + prompt referencing
# ---------------------------------------------------------------------------


class RoleManifestSchemaTests(unittest.TestCase):
    """각 역할 manifest.json — F11 AgentManifest schema 준수 (Hard rails #1)."""

    def setUp(self) -> None:
        self.plugin_ids = _registered_plugin_ids()

    def _iter_role_manifests(self):
        for dept_id, _c_level, roles in _REQUIRED_DEPARTMENTS:
            for role in roles:
                manifest_path = _AGENTS_DIR / dept_id / role / "manifest.json"
                yield dept_id, role, manifest_path

    def test_each_role_has_manifest(self) -> None:
        for dept_id, role, manifest_path in self._iter_role_manifests():
            with self.subTest(department=dept_id, role=role):
                self.assertTrue(
                    manifest_path.is_file(),
                    f"missing {manifest_path.relative_to(_REPO_ROOT)}",
                )

    def test_each_manifest_has_required_keys(self) -> None:
        for dept_id, role, manifest_path in self._iter_role_manifests():
            if not manifest_path.is_file():
                continue
            data = _load_json(manifest_path)
            for key in _ROLE_MANIFEST_REQUIRED_KEYS:
                with self.subTest(department=dept_id, role=role, key=key):
                    self.assertIn(
                        key,
                        data,
                        f"{manifest_path.relative_to(_REPO_ROOT)} missing key {key!r}",
                    )

    def test_manifest_id_follows_dept_role_convention(self) -> None:
        for dept_id, role, manifest_path in self._iter_role_manifests():
            if not manifest_path.is_file():
                continue
            if dept_id in _LEGACY_DEPARTMENTS:
                # Legacy 부서 — 별도 migration issue 에서 정렬. 본 test 는 skip.
                continue
            data = _load_json(manifest_path)
            expected = f"{dept_id}-{role}"
            with self.subTest(department=dept_id, role=role):
                self.assertEqual(
                    data.get("id"),
                    expected,
                    f"manifest id should be {expected!r}, got {data.get('id')!r}",
                )

    def test_manifest_role_matches_directory(self) -> None:
        for dept_id, role, manifest_path in self._iter_role_manifests():
            if not manifest_path.is_file():
                continue
            data = _load_json(manifest_path)
            with self.subTest(department=dept_id, role=role):
                self.assertEqual(
                    data.get("role"),
                    role,
                    f"manifest role should match directory {role!r}",
                )

    def test_risk_class_and_autonomy_level_valid(self) -> None:
        for dept_id, role, manifest_path in self._iter_role_manifests():
            if not manifest_path.is_file():
                continue
            data = _load_json(manifest_path)
            with self.subTest(department=dept_id, role=role, field="risk_class"):
                self.assertIn(
                    data.get("risk_class"),
                    _VALID_RISK_CLASSES,
                    f"invalid risk_class {data.get('risk_class')!r}",
                )
            with self.subTest(department=dept_id, role=role, field="autonomy_level"):
                self.assertIn(
                    data.get("autonomy_level"),
                    _VALID_AUTONOMY_LEVELS,
                    f"invalid autonomy_level {data.get('autonomy_level')!r}",
                )

    def test_plugins_required_reference_registered_plugins(self) -> None:
        """Hard rails #2: plugins_required 는 실제 등록된 plugin id 만."""
        for dept_id, role, manifest_path in self._iter_role_manifests():
            if not manifest_path.is_file():
                continue
            if dept_id in _LEGACY_DEPARTMENTS:
                # Legacy 부서 — 미등록 plugin id 잔재 가능 (별도 migration).
                continue
            data = _load_json(manifest_path)
            required = data.get("plugins_required", [])
            self.assertIsInstance(required, list)
            for plugin_id in required:
                with self.subTest(department=dept_id, role=role, plugin=plugin_id):
                    self.assertIn(
                        plugin_id,
                        self.plugin_ids,
                        f"{manifest_path.relative_to(_REPO_ROOT)} requires "
                        f"unregistered plugin {plugin_id!r}",
                    )

    def test_prompt_template_ref_points_to_existing_file(self) -> None:
        for dept_id, role, manifest_path in self._iter_role_manifests():
            if not manifest_path.is_file():
                continue
            data = _load_json(manifest_path)
            ref = data.get("prompt_template_ref", "")
            if not ref:
                # skeleton 상태 — 빈 값 허용 (corporate-org-chart 운영 정책 참고)
                continue
            prompt_path = _REPO_ROOT / ref
            with self.subTest(department=dept_id, role=role):
                self.assertTrue(
                    prompt_path.is_file(),
                    f"prompt_template_ref {ref!r} not found",
                )


# ---------------------------------------------------------------------------
# PM skills catalog (Acceptance Criteria #3)
# ---------------------------------------------------------------------------


class PmSkillsCatalogTests(unittest.TestCase):
    """PM skills .md 10+ — github.com/phuryn/pm-skills 패턴."""

    def test_pm_skills_directory_exists(self) -> None:
        self.assertTrue(
            _SKILLS_PM_DIR.is_dir(),
            f"missing {_SKILLS_PM_DIR.relative_to(_REPO_ROOT)}",
        )

    def test_pm_skills_has_at_least_ten(self) -> None:
        skills = sorted(_SKILLS_PM_DIR.glob("pm-*.md"))
        self.assertGreaterEqual(
            len(skills),
            10,
            f"expected 10+ PM skills, got {len(skills)}",
        )

    def test_each_skill_has_five_canonical_sections(self) -> None:
        """portable skill: When to use / Inputs / Steps / Output / Quality bar."""
        required = ("When to use", "Inputs", "Steps", "Output", "Quality bar")
        for skill_path in sorted(_SKILLS_PM_DIR.glob("pm-*.md")):
            text = _read(skill_path)
            for section in required:
                with self.subTest(skill=skill_path.name, section=section):
                    self.assertIn(
                        f"## {section}",
                        text,
                        f"{skill_path.name} missing '## {section}' section",
                    )


# ---------------------------------------------------------------------------
# Skills README portability
# ---------------------------------------------------------------------------


class SkillsReadmeTests(unittest.TestCase):
    def test_readme_exists_and_mentions_portability(self) -> None:
        readme = _REPO_ROOT / "prompts" / "skills" / "README.md"
        self.assertTrue(readme.is_file(), f"missing {readme.relative_to(_REPO_ROOT)}")
        text = _read(readme)
        self.assertIn("Portable", text)


if __name__ == "__main__":
    unittest.main()
