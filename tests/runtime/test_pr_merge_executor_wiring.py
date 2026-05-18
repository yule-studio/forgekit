"""P1-O — `merge_executor=no` silent regression 회귀.

옛 wiring 은 ``_maybe_build_live_pr_merge_executor`` 가
``coding_executor_live`` 에서 정의되지 않은 ``ENV_GITHUB_APP_MERGE_OPT_IN``
를 import 하다 ``ImportError`` → broad ``except Exception: return None``
로 빠져 startup log 에 거짓 ``merge_executor=no`` 만 노출했다.

본 모듈은:
  1. constant 가 runner 모듈에서 SSoT 로 정의되어 있음을 강제
  2. 4-stage diagnostic 가 silent None 대신 정확한 stage 반환
  3. runner / bot helper 양쪽이 동일 contract 로 동작
  4. approval enqueuer path regression 없음
"""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.runtime.coding_executor_runner import (
    ENV_GITHUB_APP_MERGE_OPT_IN,
    MERGE_EXEC_STAGE_CONFIG_ERROR,
    MERGE_EXEC_STAGE_EXECUTOR_BUILD_FAILED,
    MERGE_EXEC_STAGE_IMPORT_FAILED,
    MERGE_EXEC_STAGE_LIVE_CLIENT_FAILED,
    MERGE_EXEC_STAGE_OK,
    MERGE_EXEC_STAGE_OPT_IN_OFF,
    _maybe_build_approval_enqueuer,
    _maybe_build_live_pr_merge_executor,
    build_live_pr_merge_executor_with_stage,
)


# ---------------------------------------------------------------------------
# 1. SSoT constant exists at expected location (regression guard)
# ---------------------------------------------------------------------------


class EnvConstantOwnershipTests(unittest.TestCase):
    def test_env_constant_defined_in_runner_module(self) -> None:
        """옛 회귀: ``coding_executor_live`` 에서 정의 없는 symbol 을
        import 하다 silent fail.  본 가드는 SSoT 가 runner 모듈에 있고
        값이 정확히 ``YULE_GITHUB_APP_MERGE_OPT_IN`` 임을 강제."""

        self.assertEqual(ENV_GITHUB_APP_MERGE_OPT_IN, "YULE_GITHUB_APP_MERGE_OPT_IN")

    def test_no_other_module_owns_constant(self) -> None:
        """다른 모듈에 같은 이름의 constant 가 동시에 정의되어 있으면
        SSoT 분기점이 모호해진다 — coding_executor_live 에는 없어야 함."""

        from yule_orchestrator.agents.job_queue import coding_executor_live

        self.assertFalse(
            hasattr(coding_executor_live, "ENV_GITHUB_APP_MERGE_OPT_IN"),
            "ENV_GITHUB_APP_MERGE_OPT_IN must NOT be defined in coding_executor_live "
            "(its previous home was the import bug that silently produced merge_executor=no)",
        )


# ---------------------------------------------------------------------------
# 2-5. 4-stage diagnostic
# ---------------------------------------------------------------------------


class FourStageDiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in (
                ENV_GITHUB_APP_MERGE_OPT_IN,
                "YULE_GITHUB_APP_ID",
                "YULE_GITHUB_APP_INSTALLATION_ID",
                "YULE_GITHUB_APP_PRIVATE_KEY_PATH",
                "YULE_GITHUB_OWNER",
                "YULE_GITHUB_REPO",
            )
        }

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_opt_in_off_returns_opt_in_off_stage(self) -> None:
        os.environ.pop(ENV_GITHUB_APP_MERGE_OPT_IN, None)
        executor, stage = build_live_pr_merge_executor_with_stage(log=False)
        self.assertIsNone(executor)
        self.assertEqual(stage, MERGE_EXEC_STAGE_OPT_IN_OFF)

    def test_opt_in_on_but_no_config_returns_config_error(self) -> None:
        os.environ[ENV_GITHUB_APP_MERGE_OPT_IN] = "1"
        for k in (
            "YULE_GITHUB_APP_ID",
            "YULE_GITHUB_APP_INSTALLATION_ID",
            "YULE_GITHUB_APP_PRIVATE_KEY_PATH",
        ):
            os.environ.pop(k, None)
        executor, stage = build_live_pr_merge_executor_with_stage(log=False)
        self.assertIsNone(executor)
        self.assertEqual(stage, MERGE_EXEC_STAGE_CONFIG_ERROR)

    def test_ok_stage_when_full_env_valid(self) -> None:
        """env contract 충족 + live client build OK → callable executor."""

        os.environ[ENV_GITHUB_APP_MERGE_OPT_IN] = "1"

        # Fake live client / executor — 실제 GitHub 호출은 안 한다.
        fake_live_client = object()

        def fake_build_live_client_from_env(env=None, *, http=None):
            return fake_live_client

        fake_executor = lambda dispatch: {"merge_sha": "fake"}

        def fake_build_pr_merge_executor(*, client, **_):
            assert client is fake_live_client
            return fake_executor

        with mock.patch(
            "yule_orchestrator.github_app.live_client.build_live_client_from_env",
            fake_build_live_client_from_env,
        ), mock.patch(
            "yule_orchestrator.github_app.pr_merge_executor.build_pr_merge_executor",
            fake_build_pr_merge_executor,
        ):
            executor, stage = build_live_pr_merge_executor_with_stage(log=False)
        self.assertIs(executor, fake_executor)
        self.assertEqual(stage, MERGE_EXEC_STAGE_OK)

    def test_live_client_failure_returns_live_client_stage(self) -> None:
        os.environ[ENV_GITHUB_APP_MERGE_OPT_IN] = "1"

        def fake_build_live_client_from_env(env=None, *, http=None):
            raise RuntimeError("network simulated failure")

        with mock.patch(
            "yule_orchestrator.github_app.live_client.build_live_client_from_env",
            fake_build_live_client_from_env,
        ):
            executor, stage = build_live_pr_merge_executor_with_stage(log=False)
        self.assertIsNone(executor)
        self.assertEqual(stage, MERGE_EXEC_STAGE_LIVE_CLIENT_FAILED)

    def test_executor_build_failure_returns_executor_stage(self) -> None:
        os.environ[ENV_GITHUB_APP_MERGE_OPT_IN] = "1"

        fake_live_client = object()

        def fake_build_live_client_from_env(env=None, *, http=None):
            return fake_live_client

        def fake_build_pr_merge_executor(*, client, **_):
            raise ValueError("executor wiring broken")

        with mock.patch(
            "yule_orchestrator.github_app.live_client.build_live_client_from_env",
            fake_build_live_client_from_env,
        ), mock.patch(
            "yule_orchestrator.github_app.pr_merge_executor.build_pr_merge_executor",
            fake_build_pr_merge_executor,
        ):
            executor, stage = build_live_pr_merge_executor_with_stage(log=False)
        self.assertIsNone(executor)
        self.assertEqual(stage, MERGE_EXEC_STAGE_EXECUTOR_BUILD_FAILED)


# ---------------------------------------------------------------------------
# 6. backwards-compat shim _maybe_build_live_pr_merge_executor
# ---------------------------------------------------------------------------


class BackwardsCompatShimTests(unittest.TestCase):
    def test_legacy_shim_returns_callable_under_ok_path(self) -> None:
        prev = os.environ.get(ENV_GITHUB_APP_MERGE_OPT_IN)
        os.environ[ENV_GITHUB_APP_MERGE_OPT_IN] = "1"
        try:
            fake_live_client = object()
            with mock.patch(
                "yule_orchestrator.github_app.live_client.build_live_client_from_env",
                lambda env=None, *, http=None: fake_live_client,
            ), mock.patch(
                "yule_orchestrator.github_app.pr_merge_executor.build_pr_merge_executor",
                lambda *, client, **_: (lambda d: {"merge_sha": "x"}),
            ):
                executor = _maybe_build_live_pr_merge_executor()
            self.assertIsNotNone(executor)
            self.assertTrue(callable(executor))
        finally:
            if prev is None:
                os.environ.pop(ENV_GITHUB_APP_MERGE_OPT_IN, None)
            else:
                os.environ[ENV_GITHUB_APP_MERGE_OPT_IN] = prev


# ---------------------------------------------------------------------------
# 7. bot helper parity (재사용 회귀)
# ---------------------------------------------------------------------------


class BotHelperParityTests(unittest.TestCase):
    def test_bot_helper_delegates_to_runner_helper(self) -> None:
        """`_build_pr_merge_executor_for_bot` 가 runner 의 helper 를 그대로
        재사용하는지 source-grep 가드 — 다른 코드 경로가 같은 contract 를
        두 번 구현해 분기되는 사고 차단."""

        from pathlib import Path

        src = Path(
            "src/yule_orchestrator/discord/bot/_legacy.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_maybe_build_live_pr_merge_executor", src)

    def test_bot_helper_runs_under_same_env_contract(self) -> None:
        """runner helper 와 bot helper 가 동일 env 에서 동일 결과."""

        from yule_orchestrator.discord.bot._legacy import (
            _build_pr_merge_executor_for_bot,
        )

        prev = os.environ.get(ENV_GITHUB_APP_MERGE_OPT_IN)
        os.environ[ENV_GITHUB_APP_MERGE_OPT_IN] = "1"
        try:
            fake_live_client = object()
            fake_executor = lambda d: {"merge_sha": "x"}
            with mock.patch(
                "yule_orchestrator.github_app.live_client.build_live_client_from_env",
                lambda env=None, *, http=None: fake_live_client,
            ), mock.patch(
                "yule_orchestrator.github_app.pr_merge_executor.build_pr_merge_executor",
                lambda *, client, **_: fake_executor,
            ):
                bot_result = _build_pr_merge_executor_for_bot()
                runner_result = _maybe_build_live_pr_merge_executor()
            # 둘 다 callable
            self.assertIsNotNone(bot_result)
            self.assertIsNotNone(runner_result)
            self.assertTrue(callable(bot_result))
            self.assertTrue(callable(runner_result))
        finally:
            if prev is None:
                os.environ.pop(ENV_GITHUB_APP_MERGE_OPT_IN, None)
            else:
                os.environ[ENV_GITHUB_APP_MERGE_OPT_IN] = prev


# ---------------------------------------------------------------------------
# 8. approval enqueuer regression 가드
# ---------------------------------------------------------------------------


class ApprovalEnqueuerRegressionGuard(unittest.TestCase):
    def test_approval_enqueuer_still_yes(self) -> None:
        """P1-M B 에서 고친 ApprovalWorker wiring 이 본 round 수정으로
        깨지지 않는지 — local diagnostic 가 보여준 ``approval_enqueuer=yes``
        contract 가 그대로 유지."""

        enqueuer = _maybe_build_approval_enqueuer()
        self.assertIsNotNone(enqueuer)
        self.assertTrue(callable(enqueuer))


if __name__ == "__main__":
    unittest.main()
