"""Smoke test for yule_vcs."""
import unittest

import yule_vcs
from yule_vcs import github_url, repo_contract


class VcsSmokeTests(unittest.TestCase):
    def test_submodules_importable(self) -> None:
        self.assertTrue(hasattr(github_url, "__name__"))
        self.assertTrue(hasattr(repo_contract, "__name__"))


if __name__ == "__main__":
    unittest.main()
