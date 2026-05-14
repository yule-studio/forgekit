"""Discord launcher supervisor.

planning-bot + engineering-agent gateway + engineering members 봇을 한 번에
띄우기 위한 inventory + 시작 흐름. 한 사람이 토큰만 .env.local에 채워두면
``yule discord up`` 명령으로 일괄 실행할 수 있게 한다.

설계 원칙:
- 본 supervisor는 ``bot.py``/``member_bot.py``/``commands.py``/``workflow.py``를
  수정하지 않는다. 이미 정의된 진입점만 호출한다.
- 토큰이 비어 있는 역할은 ``skipped (token missing)``으로 분류해 출력만 하고
  실행은 하지 않는다.
- dry-run은 inventory를 그대로 보여주되 실제 Discord 연결은 하지 않는다.
- 프로세스 관리는 MVP 수준: 봇별 ``multiprocessing.Process`` 한 개씩, 종료 시
  graceful 시도 후 강제 terminate. 운영 안정화는 후속 마일스톤에서.

테스트 친화성:
- ``build_inventory``는 외부 env를 받을 수 있다 (기본은 ``os.environ``).
- ``start_all``은 ``spawn_fn`` 주입을 지원해 실제 multiprocessing 없이도 검증 가능하다.
- 실제 봇 실행 모듈(``discord.bot``, ``discord.member_bot``)은 lazy import로
  처리해 ``discord.py`` 미설치 환경에서도 inventory만 만질 수 있게 한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence, Tuple

from ..member.bots import (
    GATEWAY_ROLE_KEY,
    MemberBotConfig,
    MemberBotProfile,
    env_key_for,
    load_member_bot_config,
)


PLANNING_BOT_FAMILY = "planning"
PLANNING_BOT_ENV_KEY = "DISCORD_BOT_TOKEN"
PLANNING_BOT_ROLE = "main"
PLANNING_BOT_DISPLAY_LABEL = "planning-bot"

ENGINEERING_AGENT_FAMILY = "engineering-agent"

BOT_RUNNER_PLANNING = "planning-bot-runner"
BOT_RUNNER_ENGINEERING_GATEWAY = "engineering-gateway-runner"
BOT_RUNNER_MEMBER = "member-bot-runner"


@dataclass(frozen=True)
class BotEntry:
    """One launchable bot in the supervisor inventory."""

    bot_id: str
    family: str
    role: str
    env_key: str
    has_token: bool
    runner: str
    display_label: str
    member_profile: Optional[MemberBotProfile] = None
    skip_reason: Optional[str] = None

    @property
    def status(self) -> str:
        if self.skip_reason:
            return f"skipped ({self.skip_reason})"
        return "active" if self.has_token else "skipped (token missing)"

    @property
    def runnable(self) -> bool:
        """True iff supervisor should spawn this bot.

        A bot is runnable when it has a token AND that token passes the
        shape check (or there's no profile to run shape check against —
        e.g. the planning bot uses a different env). ``skip_reason``
        being non-None means "do not spawn".
        """

        return self.has_token and not self.skip_reason


@dataclass(frozen=True)
class SupervisorInventory:
    """All bots that the supervisor knows about, including ones to skip."""

    bots: Tuple[BotEntry, ...]
    warnings: Tuple[str, ...] = field(default_factory=tuple)

    def active(self) -> Tuple[BotEntry, ...]:
        return tuple(bot for bot in self.bots if bot.runnable)

    def skipped(self) -> Tuple[BotEntry, ...]:
        return tuple(bot for bot in self.bots if not bot.runnable)

    def by_family(self, family: str) -> Tuple[BotEntry, ...]:
        return tuple(bot for bot in self.bots if bot.family == family)


@dataclass(frozen=True)
class SpawnResult:
    """Outcome of attempting to start one bot."""

    bot_id: str
    started: bool
    skipped_reason: Optional[str] = None
    handle: Optional[object] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class StartReport:
    """Aggregate result of ``start_all``."""

    dry_run: bool
    results: Tuple[SpawnResult, ...]

    def started_count(self) -> int:
        return sum(1 for result in self.results if result.started)

    def skipped_count(self) -> int:
        return sum(1 for result in self.results if not result.started and result.error is None)

    def failed_count(self) -> int:
        return sum(1 for result in self.results if result.error is not None)


def build_inventory(
    repo_root: Path,
    *,
    agent_ids: Sequence[str] = (ENGINEERING_AGENT_FAMILY,),
    env: Optional[Mapping[str, str]] = None,
) -> SupervisorInventory:
    """Compose the planning bot + each agent's gateway/members into one list.

    Token presence is decided by ``env`` (default ``os.environ``). The order
    is: planning-bot first, then each agent in the given order, gateway before
    members so the operator sees the dispatcher entry point near the top.
    """

    env_map = env if env is not None else os.environ
    bots: list[BotEntry] = []
    warnings: list[str] = []

    bots.append(_build_planning_entry(env_map))

    for agent_id in agent_ids:
        try:
            member_config = load_member_bot_config(repo_root=repo_root, agent_id=agent_id)
        except ValueError as exc:
            warnings.append(f"{agent_id}: {exc}")
            continue
        warnings.extend(member_config.warnings)
        for profile in member_config.profiles:
            bots.append(_build_member_entry(profile, env_map))

    return SupervisorInventory(bots=tuple(bots), warnings=tuple(warnings))


def render_inventory_summary(inventory: SupervisorInventory) -> Tuple[str, ...]:
    """Lines for ``yule discord up`` to print before starting bots."""

    lines: list[str] = ["discord launcher inventory:"]
    for bot in inventory.bots:
        lines.append(f"  - {bot.display_label}: {bot.status} [{bot.env_key}]")
    if inventory.warnings:
        lines.append("warnings:")
        for warning in inventory.warnings:
            lines.append(f"  ! {warning}")
    active_count = len(inventory.active())
    skipped_count = len(inventory.skipped())
    lines.append(f"summary: {active_count} active / {skipped_count} skipped")
    return tuple(lines)


def start_all(
    inventory: SupervisorInventory,
    *,
    dry_run: bool = False,
    spawn_fn: Optional[Callable[[BotEntry], object]] = None,
) -> StartReport:
    """Start each active bot. Skipped bots produce a ``SpawnResult`` with no handle.

    ``spawn_fn`` lets tests inject a fake spawner; default uses
    ``multiprocessing.Process`` via ``_default_spawn``.
    """

    if spawn_fn is None:
        spawn_fn = _default_spawn

    results: list[SpawnResult] = []
    for bot in inventory.bots:
        if not bot.runnable:
            reason = bot.skip_reason or f"{bot.env_key} is empty"
            results.append(
                SpawnResult(
                    bot_id=bot.bot_id,
                    started=False,
                    skipped_reason=reason,
                )
            )
            continue
        if dry_run:
            results.append(
                SpawnResult(
                    bot_id=bot.bot_id,
                    started=False,
                    skipped_reason="dry-run",
                )
            )
            continue
        try:
            handle = spawn_fn(bot)
        except Exception as exc:  # pragma: no cover - exercised via tests with raising spawn
            results.append(
                SpawnResult(
                    bot_id=bot.bot_id,
                    started=False,
                    error=str(exc),
                )
            )
            continue
        results.append(SpawnResult(bot_id=bot.bot_id, started=True, handle=handle))
    return StartReport(dry_run=dry_run, results=tuple(results))


def _build_planning_entry(env_map: Mapping[str, str]) -> BotEntry:
    raw = env_map.get(PLANNING_BOT_ENV_KEY, "")
    has_token = bool(raw and raw.strip())
    return BotEntry(
        bot_id=PLANNING_BOT_DISPLAY_LABEL,
        family=PLANNING_BOT_FAMILY,
        role=PLANNING_BOT_ROLE,
        env_key=PLANNING_BOT_ENV_KEY,
        has_token=has_token,
        runner=BOT_RUNNER_PLANNING,
        display_label=PLANNING_BOT_DISPLAY_LABEL,
    )


def _build_member_entry(profile: MemberBotProfile, env_map: Mapping[str, str]) -> BotEntry:
    # ``profile.token`` was filled from ``os.environ`` at load time; for tests
    # we re-resolve against the supplied env to keep injection consistent.
    from ..member.bots import looks_like_real_discord_token

    raw = env_map.get(profile.env_key, "")
    raw_stripped = raw.strip() if isinstance(raw, str) else ""
    effective_token = raw_stripped or (profile.token or "")
    has_token = bool(effective_token)
    skip_reason: Optional[str] = None
    if not has_token:
        skip_reason = f"{profile.env_key} is empty"
    elif not looks_like_real_discord_token(effective_token):
        skip_reason = (
            f"{profile.env_key} is set but doesn't look like a real Discord token "
            "(placeholder or wrong shape) — replace it in .env.local"
        )
    bot_id = f"{profile.agent_id}/{profile.role}"
    return BotEntry(
        bot_id=bot_id,
        family=profile.agent_id,
        role=profile.role,
        env_key=profile.env_key,
        has_token=has_token,
        runner=(
            BOT_RUNNER_ENGINEERING_GATEWAY
            if profile.role == GATEWAY_ROLE_KEY
            else BOT_RUNNER_MEMBER
        ),
        display_label=profile.display_label,
        member_profile=profile,
        skip_reason=skip_reason,
    )


def _default_spawn(bot: BotEntry) -> object:
    """Production spawner: one ``multiprocessing.Process`` per bot.

    Imported lazily so unit tests don't need ``discord.py`` available.
    """

    import multiprocessing

    target, args = _target_callable(bot)
    process = multiprocessing.Process(
        target=target,
        args=args,
        name=f"yule-discord-{bot.bot_id}",
        daemon=False,
    )
    process.start()
    return process


def _target_callable(bot: BotEntry) -> tuple[Callable[..., None], tuple]:
    """Pick the right entry point for the bot's runner type."""

    if bot.runner == BOT_RUNNER_PLANNING:
        return (_run_planning_in_subprocess, (str(_resolve_repo_root()),))
    if bot.runner == BOT_RUNNER_ENGINEERING_GATEWAY:
        if bot.member_profile is None:
            raise ValueError(f"gateway bot {bot.bot_id} has no profile attached")
        return (
            _run_engineering_gateway_in_subprocess,
            (str(_resolve_repo_root()), bot.member_profile.env_key),
        )
    if bot.runner == BOT_RUNNER_MEMBER:
        if bot.member_profile is None:
            raise ValueError(f"member bot {bot.bot_id} has no profile attached")
        return (_run_member_in_subprocess, (bot.member_profile,))
    raise ValueError(f"unknown runner type for bot {bot.bot_id}: {bot.runner}")


def _run_planning_in_subprocess(repo_root_str: str) -> None:  # pragma: no cover - subprocess only
    from ..bot import run_discord_bot

    _apply_env_overrides(
        {
            # Keep the planning bot out of #업무-접수. The engineering
            # gateway process owns that channel so visible replies use the
            # yule-eng-gateway account.
            "DISCORD_ENGINEERING_INTAKE_CHANNEL_ID": "",
            "DISCORD_ENGINEERING_INTAKE_CHANNEL_NAME": "",
            # Slash command ownership: planning-bot must NOT register
            # /engineer_* commands. Without this discriminator the bot's
            # setup_hook registers every command, planning-bot's app gets
            # picked for /engineer_intake, and Discord shows
            # "애플리케이션이 응답하지 않았어요" because no orchestrator
            # is wired into this process.
            "DISCORD_BOT_ROLE": "planning",
        }
    )
    run_discord_bot(repo_root=Path(repo_root_str))


def _run_engineering_gateway_in_subprocess(
    repo_root_str: str,
    gateway_token_env_key: str,
) -> None:  # pragma: no cover - subprocess only
    from ...runtime.gateway_env import build_gateway_env_overrides
    from ..bot import run_discord_bot

    gateway_token = os.environ.get(gateway_token_env_key, "").strip()
    if not gateway_token:
        raise ValueError(f"{gateway_token_env_key} is required to start engineering gateway")

    overrides = build_gateway_env_overrides(
        gateway_token=gateway_token, base_env={}
    )
    # Engineering gateway owns ``/engineer_*`` and nothing else (planning
    # commands like ``/plan_today`` stay on the planning-bot process).
    overrides["DISCORD_BOT_ROLE"] = "engineering-gateway"
    _apply_env_overrides(overrides)
    run_discord_bot(repo_root=Path(repo_root_str))


def _run_member_in_subprocess(profile: MemberBotProfile) -> None:  # pragma: no cover - subprocess only
    from ..member.bot import run_member_bot

    run_member_bot(profile)


def _apply_env_overrides(overrides: Mapping[str, str]) -> None:
    for key, value in overrides.items():
        os.environ[key] = value


def _resolve_repo_root() -> Path:
    configured = os.environ.get("YULE_REPO_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.cwd()
