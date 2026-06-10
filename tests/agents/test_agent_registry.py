"""AgentRegistry tests (F11 / #102 MVP)."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.extension.agent_registry import AgentRegistry
from yule_engineering.agents.extension.manifest import AgentManifest


def _make(agent_id: str, role: str | None = None) -> AgentManifest:
    return AgentManifest(
        id=agent_id,
        name=agent_id.title(),
        role=role or agent_id,
        version="0.1.0",
        capabilities=(),
        plugins_required=(),
        prompt_template_ref="",
        github_app_env_prefix="",
        autonomy_level="advisory",
        risk_class="LOW",
        module_path="",
    )


class AgentRegistryTests(unittest.TestCase):
    def test_register_and_get_round_trip(self) -> None:
        reg = AgentRegistry()
        m = _make("tech-lead")
        reg.register(m)
        self.assertIs(reg.get("tech-lead"), m)
        self.assertIn("tech-lead", reg)
        self.assertEqual(len(reg), 1)

    def test_get_missing_raises_key_error(self) -> None:
        reg = AgentRegistry()
        with self.assertRaises(KeyError):
            reg.get("nope")

    def test_duplicate_registration_raises(self) -> None:
        reg = AgentRegistry()
        reg.register(_make("tech-lead"))
        with self.assertRaises(ValueError):
            reg.register(_make("tech-lead"))

    def test_agents_for_role_collects_multiple(self) -> None:
        reg = AgentRegistry()
        reg.register(_make("backend-engineer-v1", role="backend-engineer"))
        reg.register(_make("backend-engineer-v2", role="backend-engineer"))
        reg.register(_make("tech-lead"))
        backend = reg.agents_for_role("backend-engineer")
        self.assertEqual([m.id for m in backend], ["backend-engineer-v1", "backend-engineer-v2"])

    def test_agents_for_role_returns_empty_tuple_for_unknown(self) -> None:
        reg = AgentRegistry()
        reg.register(_make("tech-lead"))
        self.assertEqual(reg.agents_for_role("unknown-role"), ())

    def test_agents_for_role_requires_string(self) -> None:
        reg = AgentRegistry()
        with self.assertRaises(TypeError):
            reg.agents_for_role(42)  # type: ignore[arg-type]

    def test_register_validates_manifest(self) -> None:
        reg = AgentRegistry()
        bogus = AgentManifest(
            id="tech-lead",
            name="x",
            role="Not Kebab",
            version="0.1.0",
            module_path="",
        )
        with self.assertRaises(Exception):
            reg.register(bogus)

    def test_all_is_sorted_by_id(self) -> None:
        reg = AgentRegistry()
        reg.register(_make("zeta"))
        reg.register(_make("alpha"))
        self.assertEqual([m.id for m in reg.all()], ["alpha", "zeta"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
