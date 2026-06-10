"""[G5] github_workos runtime smoke + regression dashboard.

Two responsibilities:

  * **Smoke** — confirm the github_workos service entrypoint name(s)
    that G6 will register with the runtime supervisor are reserved
    and not stepping on existing service ids. Done via Protocol /
    naming assertions so it stays green before G6 lands.
  * **Regression** — confirm the Obsidian forum handoff,
    role-selection fan-out, approval reply routing, and runtime
    status surfaces still pass after the github_workos test pack
    was added. We don't import / re-run them here; we import the
    test classes and assert the suite still finds them. The CI
    runner picks them up directly.

Anything that requires a real `discord` / `git` / `gh` binary stays
out of this file by design.
"""

from __future__ import annotations

import importlib
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


# ---------------------------------------------------------------------------
# Smoke — service id reservation
# ---------------------------------------------------------------------------


GITHUB_WORKOS_SERVICE_IDS_RESERVED = (
    # G1 — auth / token issuer service.
    "eng-github-app-auth",
    # G2 — issue triage worker.
    "eng-github-issue-triage",
    # G3 — work-order executor (branch / PR body / dry-run).
    "eng-github-workorder-executor",
    # G4 — Discord-side intake → dispatch glue. May share the gateway
    # process; reserved id keeps the heartbeat / status channel surface
    # consistent with the standalone-runners pattern.
    "eng-github-workorder-dispatch",
)


class ServiceIdReservationTests(unittest.TestCase):
    """Pin the service ids G1–G4 will register with the supervisor.

    Existing service ids live in
    ``yule_engineering.agents.job_queue`` (e.g.
    ``SERVICE_ID_RESEARCH_WORKER``). The new ids must NOT collide.
    """

    def test_reserved_ids_do_not_collide_with_existing_workers(self) -> None:
        # Import existing constants — collision would mean a typo in
        # the brief / a producer reusing an existing id.
        from yule_engineering.agents.job_queue.approval_worker import (
            SERVICE_ID_APPROVAL_WORKER,
        )
        from yule_engineering.agents.job_queue.obsidian_writer_worker import (
            SERVICE_ID_OBSIDIAN_WRITER,
        )
        from yule_engineering.agents.job_queue.research_worker import (
            SERVICE_ID_RESEARCH_WORKER,
        )

        existing = {
            SERVICE_ID_APPROVAL_WORKER,
            SERVICE_ID_OBSIDIAN_WRITER,
            SERVICE_ID_RESEARCH_WORKER,
        }
        for new_id in GITHUB_WORKOS_SERVICE_IDS_RESERVED:
            with self.subTest(service_id=new_id):
                self.assertNotIn(new_id, existing)
                self.assertTrue(new_id.startswith("eng-github-"))
                self.assertGreaterEqual(len(new_id), 12)

    def test_reserved_ids_unique_among_themselves(self) -> None:
        self.assertEqual(
            len(set(GITHUB_WORKOS_SERVICE_IDS_RESERVED)),
            len(GITHUB_WORKOS_SERVICE_IDS_RESERVED),
        )


# ---------------------------------------------------------------------------
# Regression — load test modules to confirm they parse + import cleanly
# ---------------------------------------------------------------------------


class _RegressionImportMixin:
    """Each subclass imports an existing test module by dotted path
    and asserts at least one TestCase class is defined. Catches the
    failure mode where the github_workos changes accidentally break
    a sibling import (e.g. circular import, duplicate symbol).
    """

    module_name: str = ""

    def test_module_imports_cleanly(self) -> None:
        module = importlib.import_module(self.module_name)
        # Find at least one TestCase subclass — the file is alive.
        test_classes = [
            obj
            for name, obj in vars(module).items()
            if isinstance(obj, type)
            and issubclass(obj, unittest.TestCase)
            and obj is not unittest.TestCase
        ]
        self.assertGreaterEqual(
            len(test_classes),
            1,
            f"{self.module_name} should expose at least one TestCase",
        )


class ForumHandoffRegressionTests(_RegressionImportMixin, unittest.TestCase):
    module_name = "tests.job_queue.test_forum_obsidian_handoff"


class RoleSelectionRegressionTests(_RegressionImportMixin, unittest.TestCase):
    # The role-selection fan-out tests live alongside the approval
    # worker / forum producer surface; pinning the import keeps the
    # G5 changes from accidentally breaking the role-selection seam.
    module_name = "tests.engineering.test_role_selection"


class ApprovalReplyRegressionTests(_RegressionImportMixin, unittest.TestCase):
    module_name = "tests.job_queue.test_approval_reply"


class RuntimeStatusRegressionTests(_RegressionImportMixin, unittest.TestCase):
    module_name = "tests.runtime.test_status"


class M6EndToEndHarnessRegressionTests(
    _RegressionImportMixin, unittest.TestCase
):
    module_name = "tests.runtime.test_m6_end_to_end_harness"


class TopicObsidianRegressionImportTests(
    _RegressionImportMixin, unittest.TestCase
):
    # M9/M10 regression pack lives at this path — keeping its import
    # green confirms the github_workos additions don't break the
    # earlier regression dashboard.
    module_name = "tests.runtime.test_topic_obsidian_integration"


# ---------------------------------------------------------------------------
# Cross-check — github_workos fakes module imports cleanly + redacts
# ---------------------------------------------------------------------------


class FakeModuleSmokeTests(unittest.TestCase):
    def test_fakes_module_imports_and_exposes_protocols(self) -> None:
        # Smoke: the helper module's __all__ must list every symbol
        # the test packs depend on, and Protocol classes must be
        # importable.
        fakes = importlib.import_module("tests.github_workos._fakes")
        for name in (
            "FakeGitHubAPI",
            "FakeGitHubAppAuth",
            "FakeWorkOrderExecutor",
            "FakeDiscordSurface",
            "GitHubWorkOrder",
            "SeniorQualityPRPlan",
            "validate_senior_quality_plan",
            "redact_secret_blob",
            "DiscordIntakeProtocol",
            "TriageProtocol",
            "WorkOrderExecutorProtocol",
            "GitHubAppAuthProtocol",
        ):
            with self.subTest(symbol=name):
                self.assertTrue(
                    hasattr(fakes, name),
                    f"fakes module is missing {name!r}",
                )

    def test_redact_secret_blob_handles_known_shapes(self) -> None:
        fakes = importlib.import_module("tests.github_workos._fakes")
        # PEM
        self.assertNotIn(
            "BEGIN RSA",
            fakes.redact_secret_blob(
                "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"
            ),
        )
        # GitHub install token (alphanumeric only).
        leak_token = "ghs_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        self.assertNotIn(
            leak_token,
            fakes.redact_secret_blob(f"token={leak_token}"),
        )
        # Authorization Bearer header.
        self.assertNotIn(
            "Bearer ghs_xyz",
            fakes.redact_secret_blob("Authorization: Bearer ghs_xyz_aaa"),
        )


if __name__ == "__main__":
    unittest.main()
