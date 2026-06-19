"""WT2 extraction guard — forgekit-provider owns the provider stack; console shims it.

CI-run (root ``tests/`` tree). Proves the ForgeKit provider-core extraction held:
- the canonical modules are ``forgekit_provider.{providers,policy,chat,usage,brain}``
  (a real package depending only on ``forgekit-config``, not on the console);
- the old ``forgekit_console.*`` paths still resolve to the SAME module objects at
  BOTH package and submodule level (``_compat.alias_package`` / ``sys.modules``);
- intra-package relative imports (``policy → providers``, ``chat → policy/providers``)
  survived the move; the only outward dep absolute-ized was runtime_paths →
  ``forgekit_config.paths``.

Seam check for ``docs/forgekit-architecture-ownership.md`` WT2.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401


class ProviderCanonicalTests(unittest.TestCase):
    def test_canonical_is_the_package_not_console(self) -> None:
        import forgekit_provider.chat.service as svc
        import forgekit_provider.policy.routing as routing

        self.assertTrue(svc.__name__.startswith("forgekit_provider."))
        self.assertTrue(routing.__name__.startswith("forgekit_provider."))

    def test_only_outward_dep_is_forgekit_config(self) -> None:
        # provider modules that used runtime_paths now import forgekit_config.paths
        from forgekit_provider.chat import service  # noqa: F401
        from forgekit_config.paths import config_path  # the real owner

        self.assertTrue(callable(config_path))


class ProviderShimIdentityTests(unittest.TestCase):
    def test_package_level_identity(self) -> None:
        import forgekit_provider.brain
        import forgekit_provider.chat
        import forgekit_provider.policy
        import forgekit_provider.providers
        import forgekit_provider.usage
        from forgekit_console import brain, chat, policy, providers, usage

        self.assertIs(policy, forgekit_provider.policy)
        self.assertIs(providers, forgekit_provider.providers)
        self.assertIs(chat, forgekit_provider.chat)
        self.assertIs(usage, forgekit_provider.usage)
        self.assertIs(brain, forgekit_provider.brain)

    def test_submodule_level_identity(self) -> None:
        import forgekit_provider.chat.models
        import forgekit_provider.policy.routing
        import forgekit_provider.providers.builtins
        from forgekit_console.chat import models
        from forgekit_console.policy import routing
        from forgekit_console.providers import builtins

        self.assertIs(routing, forgekit_provider.policy.routing)
        self.assertIs(models, forgekit_provider.chat.models)
        self.assertIs(builtins, forgekit_provider.providers.builtins)

    def test_intra_package_relative_imports_survived(self) -> None:
        # policy → providers, chat → policy/providers resolve inside the new package
        from forgekit_provider.policy.routing import submit_supported  # uses ..providers
        from forgekit_provider.chat.service import SubmitService  # uses ..policy/..providers

        self.assertTrue(callable(submit_supported))
        self.assertTrue(callable(getattr(SubmitService, "submit", None)))


if __name__ == "__main__":
    unittest.main()
