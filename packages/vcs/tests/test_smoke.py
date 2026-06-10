"""Smoke test for the relocated yule_vcs package + old-path identity."""
import unittest

import yule_vcs
from yule_vcs import github_url, repo_contract


class VcsSmokeTests(unittest.TestCase):
    def test_submodules_importable(self) -> None:
        self.assertTrue(hasattr(github_url, "__name__"))
        self.assertTrue(hasattr(repo_contract, "__name__"))

    def test_legacy_path_aliases_same_objects(self) -> None:
        from yule_engineering.agents.git import github_url as gu
        from yule_engineering.agents.git import repo_contract as rc
        self.assertIs(gu, yule_vcs.github_url)
        self.assertIs(rc, yule_vcs.repo_contract)


if __name__ == "__main__":
    unittest.main()
