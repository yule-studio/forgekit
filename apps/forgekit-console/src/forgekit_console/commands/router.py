"""Command router — maps a parsed input to a :class:`CommandResult`. Pure.

The router holds no IO: status surfaces are reached through zero-arg loader
callables on the :class:`ConsoleContext`, which :func:`build_default_context`
binds to the real (best-effort) ``status_loader`` functions and tests replace
with fakes. So routing logic is fully unit-testable without a runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Tuple

from ..models import (
    KIND_AGENT_MODE,
    KIND_CLEAR,
    KIND_HELP,
    KIND_LAYOUT,
    KIND_QUIT,
    CommandResult,
    StatusSummary,
)
from .registry import (
    H_ABOUT,
    H_AGENT_ENTER,
    H_AGENTS,
    H_CLEAR,
    H_DOCTOR,
    H_HARNESS,
    H_HELP,
    H_MODE,
    H_BLOCKED,
    H_WHOAMI,
    H_RESOLVE,
    H_HEPHAISTOS,
    H_SKILLS,
    H_LOADOUT,
    H_PROVIDER,
    H_SETUP,
    H_TOOLCHAIN,
    H_NEXUS,
    H_DISCOVERY,
    H_DAEMON,
    H_GOAL,
    H_COUNCIL,
    H_LAYOUT,
    H_QUIT,
    H_RENDER,
    H_RUNTIME,
    H_STATUS,
    find_agent,
    find_command,
    load_agents,
    load_commands,
)

StatusLoader = Callable[[], StatusSummary]


def _unavailable(title: str) -> StatusSummary:
    return StatusSummary(title=title, available=False, error="loader not configured")


@dataclass
class ConsoleContext:
    """Everything the router needs — registries + zero-arg status loaders.

    ``env`` / ``config`` / ``nexus_role`` are threaded into the Hephaistos + Nexus
    surfaces so ``/nexus`` · ``/resolve`` · ``/hephaistos`` read the LIVE Nexus root
    (``FORGEKIT_NEXUS_ROOT`` env or ``config['nexus_root']``) instead of a static
    not_connected. ``nexus_role`` gates restricted-source raw vs projection_only.
    """

    repo_root: Path
    agent_id: str = "engineering-agent"
    profile: str = "operator"
    agents: Tuple = field(default_factory=load_agents)
    commands: Tuple = field(default_factory=load_commands)
    load_operator: StatusLoader = lambda: _unavailable("operator dashboard")
    load_runtime: StatusLoader = lambda: _unavailable("runtime status")
    load_doctor: StatusLoader = lambda: _unavailable("doctor")
    env: Mapping = field(default_factory=dict)
    config: Mapping = field(default_factory=dict)
    nexus_role: str = ""


def build_default_context(repo_root: Path, *, agent_id: str = "engineering-agent") -> ConsoleContext:
    """Bind the real best-effort loaders to *repo_root* + the live env/config (for Nexus)."""

    import os

    from ..chat.service import load_config
    from ..data import status_loader as sl

    root = Path(repo_root)
    env = dict(os.environ)
    config = load_config(env)
    return ConsoleContext(
        repo_root=root,
        agent_id=agent_id,
        load_operator=lambda: sl.load_operator_summary(root),
        load_runtime=lambda: sl.load_runtime_summary(root),
        load_doctor=lambda: sl.load_doctor_summary(root, agent_id),
        env=env,
        config=config,
        # operator may grant a restricted role for raw Nexus reads (else projection_only).
        nexus_role=str(env.get("FORGEKIT_NEXUS_ROLE", "") or "").strip(),
    )


def _summary_to_result(summary: StatusSummary) -> CommandResult:
    kind = "info" if summary.available else "error"
    return CommandResult(
        kind=kind,
        title=summary.title,
        lines=summary.flat_lines(),
        alerts=summary.alerts,
    )


def route(parsed, ctx: ConsoleContext) -> CommandResult:
    """Route a :class:`ParsedInput` to a :class:`CommandResult`."""

    if not parsed.is_slash:
        if not (parsed.raw or "").strip():
            return CommandResult.info("", ())
        # NOTE: in the TUI, free text is intercepted by the app and sent to the live
        # provider submit path (chat.service.SubmitService) — it does NOT reach here.
        # The router is pure (no provider IO), so this is only the non-TUI fallback.
        return CommandResult.info(
            "free text",
            (
                "일반 텍스트는 콘솔(TUI)에서 provider 로 live-submit 됩니다.",
                "이 순수 경로에서는 제출하지 않습니다 — 슬래시 명령은 `/help` 참고.",
            ),
        )
    if not parsed.name:
        return CommandResult.info("", ("`/` 뒤에 명령을 입력하세요 — `/help`.",))

    cmd = find_command(parsed.name, ctx.commands)
    if cmd is None:
        return CommandResult.error(
            f"unknown command: /{parsed.name}",
            ("`/help` 로 사용 가능한 명령을 확인하세요.",),
        )

    handler = cmd.handler
    if handler == H_HELP:
        return _help_result(ctx)
    if handler == H_ABOUT:
        # KIND_HELP with title "about" → the TUI opens the help view on the About
        # tab AND shows the wide hero art in the header (the 56-col art's home).
        return CommandResult(
            kind=KIND_HELP,
            title="about",
            lines=("forgekit — about / welcome", "와이드 hero 아트 + 브랜드 정보."),
        )
    if handler == H_AGENTS:
        return _agents_result(ctx)
    if handler == H_STATUS or handler == H_HARNESS:
        return _summary_to_result(ctx.load_operator())
    if handler == H_RUNTIME:
        return _summary_to_result(ctx.load_runtime())
    if handler == H_DOCTOR:
        return _summary_to_result(ctx.load_doctor())
    if handler == H_MODE:
        # The live runtime mode lives in the app (TUI intercepts /mode). This pure
        # fallback is for non-TUI callers / tests.
        return CommandResult.info(
            "mode",
            ("런타임 모드는 콘솔(TUI)에서 Shift+Tab 으로 순환되고 `/mode` 로 표시됩니다.",),
        )
    if handler == H_WHOAMI:
        return _whoami_result(parsed)
    if handler in (H_RESOLVE, H_HEPHAISTOS, H_SKILLS, H_LOADOUT):
        return _hephaistos_result(handler, parsed, ctx)
    if handler == H_PROVIDER:
        return _provider_result(parsed, ctx)
    if handler == H_SETUP:
        return _setup_result(parsed, ctx)
    if handler == H_TOOLCHAIN:
        return _toolchain_result(parsed, ctx)
    if handler == H_NEXUS:
        return _nexus_result(parsed, ctx)
    if handler == H_DISCOVERY:
        return _discovery_result(parsed, ctx)
    if handler == H_DAEMON:
        return _daemon_result(parsed, ctx)
    if handler == H_GOAL:
        return _goal_result(parsed, ctx)
    if handler == H_COUNCIL:
        return _council_result(parsed, ctx)
    if handler == H_RENDER:
        return _render_readiness_result()
    if handler == H_BLOCKED:
        return _blocked_result()
    if handler == H_AGENT_ENTER:
        return _agent_enter_result(cmd, ctx)
    if handler == H_LAYOUT:
        return CommandResult(kind=KIND_LAYOUT, title="layout")
    if handler == H_QUIT:
        return CommandResult(kind=KIND_QUIT, title="quit", lines=("콘솔을 종료합니다…",))
    if handler == H_CLEAR:
        return CommandResult(kind=KIND_CLEAR, title="clear")
    return CommandResult.error(f"no handler for /{parsed.name}")


def _help_result(ctx: ConsoleContext) -> CommandResult:
    # KIND_HELP signals the TUI to open the help overlay. Lines are kept as a
    # text fallback for non-TUI / test consumers.
    lines = ["사용 가능한 명령:"]
    for cmd in ctx.commands:
        lines.append(f"  /{cmd.name:<16} {cmd.summary}")
    lines.append("")
    lines.append("일반 텍스트는 provider 로 live-submit 됩니다 (provider 미설정 시 setup 안내).")
    return CommandResult(kind=KIND_HELP, title="help", lines=tuple(lines))


def _goal_result(parsed, ctx: ConsoleContext) -> CommandResult:
    # /goal operator surface over forgekit_goal (store read + small mutations).
    # Thin: rendering/CRUD only; goal logic lives in the package (ownership §3.1).
    from .. import goal_surface as gs

    args = list(getattr(parsed, "args", ()) or ())
    sub = args[0].lower() if args else "list"
    env = ctx.env
    if sub == "list":
        return CommandResult.info("goal", gs.goal_list_lines(env))
    if sub == "new":
        ok, msg = gs.apply_new(env, " ".join(args[1:]))
        return (CommandResult.info if ok else CommandResult.error)("goal new", (msg,))
    if sub == "show":
        return CommandResult.info("goal show", gs.goal_show_lines(env, args[1] if len(args) > 1 else ""))
    if sub == "activate":
        ok, msg = gs.apply_activate(env, args[1] if len(args) > 1 else "")
        return (CommandResult.info if ok else CommandResult.error)("goal activate", (msg,))
    if sub == "evidence":
        return CommandResult.info("goal evidence", gs.goal_evidence_lines(env, args[1] if len(args) > 1 else ""))
    if sub == "plan":
        ok, lines = gs.apply_plan(env, args[1] if len(args) > 1 else "", args[2:])
        return (CommandResult.info if ok else CommandResult.error)("goal plan", lines)
    if sub == "progress":
        return CommandResult.info("goal progress", gs.progress_lines(env, args[1] if len(args) > 1 else ""))
    if sub in ("awaiting", "pending"):
        return CommandResult.info("goal awaiting", gs.awaiting_lines(env))
    if sub == "approve":
        ok, msg = gs.apply_approve(env, args[1] if len(args) > 1 else "", " ".join(args[2:]))
        return (CommandResult.info if ok else CommandResult.error)("goal approve", (msg,))
    if sub == "deny":
        ok, msg = gs.apply_deny(env, args[1] if len(args) > 1 else "", " ".join(args[2:]))
        return (CommandResult.info if ok else CommandResult.error)("goal deny", (msg,))
    return CommandResult.info("goal", gs.usage_lines())


def _provider_result(parsed, ctx: ConsoleContext) -> CommandResult:
    # /provider operator surface over policy.provider_surface (read) + provider_ops (persist).
    from ..policy import provider_ops as ops
    from ..policy import provider_surface as ps

    args = list(getattr(parsed, "args", ()) or ())
    sub = args[0].lower() if args else ""
    env = getattr(ctx, "env", None) or None
    cfg = ops.load_raw_config(env=env)
    if sub == "budget":
        return _provider_budget_result(args, cfg, env, ps)
    if sub == "set":
        ok, msg = ps.apply_set_primary(args[1] if len(args) > 1 else "", env=env)
        return (CommandResult.info if ok else CommandResult.error)("provider set", (msg,))
    if sub == "list":
        return CommandResult.info("provider list", ps.provider_list_lines(cfg))
    if sub == "doctor":
        return CommandResult.info("provider doctor", ps.provider_doctor_lines(cfg))
    if sub == "preset":
        ok, msg = ps.apply_preset(args[1] if len(args) > 1 else "", env=env)
        return (CommandResult.info if ok else CommandResult.error)("provider preset", msg.split("\n"))
    if sub in ("connect", "disconnect", "test", "recommended"):
        from forgekit_provider_connect import surface as cs
        pid = args[1] if len(args) > 1 else ""
        if sub == "test":
            return CommandResult.info("provider test", cs.test_lines(pid, cfg))
        if sub == "recommended":
            return CommandResult.info("provider recommended", cs.recommended_lines(cfg))
        ok, msg = (cs.apply_connect(pid) if sub == "connect" else cs.apply_disconnect(pid))
        return (CommandResult.info if ok else CommandResult.error)(f"provider {sub}", msg.split("\n"))
    if sub == "link":
        ok, msg = ps.apply_link(args[1] if len(args) > 1 else "", env=env)
        return (CommandResult.info if ok else CommandResult.error)("provider link", (msg,))
    if sub == "unlink":
        ok, msg = ps.apply_unlink(args[1] if len(args) > 1 else "", env=env)
        return (CommandResult.info if ok else CommandResult.error)("provider unlink", (msg,))
    if sub == "route":
        op = args[1].lower() if len(args) > 1 else "show"
        if op == "set":
            ok, msg = ps.apply_route_set(args[2] if len(args) > 2 else "", args[3] if len(args) > 3 else "", env=env)
            return (CommandResult.info if ok else CommandResult.error)("provider route", (msg,))
        if op == "clear":
            ok, msg = ps.apply_route_clear(args[2] if len(args) > 2 else "", env=env)
            return (CommandResult.info if ok else CommandResult.error)("provider route", (msg,))
        return CommandResult.info("provider route", ps.route_show_lines(cfg, live_map=_provider_live_map(cfg, env)))
    return CommandResult.info("provider", ps.provider_status_lines(cfg, live_map=_provider_live_map(cfg, env)))


def _provider_live_map(cfg, env):
    """Probe-backed pid→verified-live_capable so `/provider` surfaces show ACTUAL readiness
    (gemini keyed / ollama daemon up), not transport capability faked as live. Best-effort:
    reuses the connect wizard's honest probe (the same signal `/setup` uses); on any failure
    returns ``None`` so the surface degrades to honest "live-capable(미검증)" instead of fake-live."""

    try:
        from forgekit_provider_connect import wizard
        statuses = wizard.assess(cfg, env=env).statuses
        return {s.provider_id: bool(s.live_capable) for s in statuses}
    except Exception:  # noqa: BLE001 - a probe failure must never break the surface
        return None


def _provider_budget_result(args, cfg, env, ps) -> CommandResult:
    # /provider budget [<id> <limit> | show] — set/show per-provider daily token budgets.
    # Thin: persist via provider_surface.apply_set_budget (logic in provider package); show
    # renders honest spent/over from TODAY's usage ledger (no fake numbers, env-scoped).
    op = args[1].lower() if len(args) > 1 else "show"
    if op == "show":
        from forgekit_provider.usage import read_events, today, usage_ledger_path

        try:
            rows = read_events(path=usage_ledger_path(env), day=today())
        except Exception:  # noqa: BLE001 - ledger read must never break the surface
            rows = ()
        return CommandResult.info("provider budget", ps.budget_lines(cfg, rows))
    # `/provider budget <id> <limit>` (op is the id; args[2] the limit).
    pid = args[1]
    limit = args[2] if len(args) > 2 else ""
    ok, msg = ps.apply_set_budget(pid, limit, env=env)
    return (CommandResult.info if ok else CommandResult.error)("provider budget", (msg,))


def _setup_result(parsed, ctx) -> CommandResult:
    # /setup [apply [preset]] — unified control-plane bootstrap (docs/forgekit-setup-bootstrap.md):
    # composes provider + knowledge(nexus/vault) + toolchain into ONE honest screen, persisted in
    # the single canonical ~/.forgekit/config.json. `apply` writes the recommended provider preset
    # (the only lane with a one-shot recommended default) then re-verifies; knowledge/toolchain are
    # connected per-lane (`/nexus set`, `/toolchain`). No lane is ever faked into green.
    args = list(getattr(parsed, "args", ()) or ())
    sub = args[0].lower() if args else ""
    if sub == "apply":
        from forgekit_provider_connect import surface as cs

        ok, msg = cs.apply_setup(args[1] if len(args) > 1 else "four-brain")
        return (CommandResult.info if ok else CommandResult.error)("setup", msg.split("\n"))
    from .. import bootstrap as bs

    env = getattr(ctx, "env", None) or None
    repo_root = getattr(ctx, "repo_root", None)
    return CommandResult.info("setup", bs.bootstrap_lines(env=env, repo_root=repo_root))


def _toolchain_result(parsed, ctx) -> CommandResult:
    # /toolchain [detect|recommend <loadout>|switch [global] [--approve]|verify|drift]
    # repo-local version detection + loadout→profile + mise switch/verify/drift.
    # Destructive/global writes are approval-gated; no fake switch (lazy import — the
    # package is optional infra and the console must boot without it installed).
    try:
        from forgekit_toolchain import surface as ts
    except ImportError:
        return CommandResult.error(
            "toolchain", ("forgekit-toolchain 미설치 — `pip install -e packages/forgekit-toolchain`.",))

    root = getattr(ctx, "repo_root", None) or Path(".")
    args = [a for a in (getattr(parsed, "args", ()) or ())]
    sub = args[0].lower() if args else "detect"
    rest = args[1:]
    # a loadout id is the first non-flag token after the subcommand
    loadout = next((a for a in rest if not a.startswith("-") and a not in ("global",)), "")
    if sub == "detect":
        return CommandResult.info("toolchain detect", ts.detect_lines(root))
    if sub == "recommend":
        return CommandResult.info("toolchain recommend", ts.recommend_lines(root, loadout))
    if sub == "verify":
        return CommandResult.info("toolchain verify", ts.verify_lines(root, loadout))
    if sub == "drift":
        return CommandResult.info("toolchain drift", ts.drift_lines(root, loadout))
    if sub == "switch":
        scope = "global" if "global" in rest else "local"
        approve = "--approve" in rest or "approve" in rest
        ok, lines = ts.apply_switch(root, loadout, approve=approve, scope=scope)
        return (CommandResult.info if ok else CommandResult.error)("toolchain switch", lines)
    return CommandResult.info("toolchain detect", ts.detect_lines(root))


def _nexus_result(parsed, ctx) -> CommandResult:
    # /nexus [set <path> | clear] — operator-driven connect, else live status.
    from ..hephaistos import nexus_ops as nops
    from ..hephaistos import projection as _proj

    args = list(getattr(parsed, "args", ()) or ())
    sub = args[0].lower() if args else ""
    env = getattr(ctx, "env", None) or None
    if sub == "set":
        ok, msg = nops.apply_set_root(args[1] if len(args) > 1 else "", env=env)
        return (CommandResult.info if ok else CommandResult.error)("nexus set", (msg,))
    if sub == "clear":
        ok, msg = nops.apply_clear_root(env=env)
        return (CommandResult.info if ok else CommandResult.error)("nexus clear", (msg,))
    return CommandResult.info(
        "nexus", _proj.nexus_surface_lines(env=ctx.env, config=ctx.config))


def _discovery_result(parsed, ctx) -> CommandResult:
    # /discovery [promote <n> | save <n>] — run a free-first discovery sweep and show
    # the operator digest (왜 올라왔는지/다음 질문). promote → PM handoff 제안(실행 아님),
    # save → 연결된 Nexus vault 에 retrieval-friendly authored note (미연결이면 정직 실패).
    from .. import discovery as D

    repo_root = getattr(ctx, "repo_root", None) or Path(".")
    args = list(getattr(parsed, "args", ()) or ())
    sub = args[0].lower() if args else ""

    def _pick(idx_token: str):
        sweep = D.run_discovery_sweep(repo_root)
        briefs = sweep.briefs
        if not briefs:
            return sweep, None, "승격할 brief 가 없습니다 — 먼저 `/discovery` 로 신호를 수집하세요."
        try:
            n = int(idx_token)
        except (TypeError, ValueError):
            n = 1
        if n < 1 or n > len(briefs):
            return sweep, None, f"brief 번호 범위 밖 (1~{len(briefs)})."
        return sweep, briefs[n - 1], ""

    if sub == "promote":
        sweep, brief, err = _pick(args[1] if len(args) > 1 else "1")
        if err:
            return CommandResult.error("discovery promote", (err,))
        ho = D.promote_brief(brief)
        lines = (
            f"승격(제안): {brief.title}",
            f"- handoff: {ho.trace[0].handoff_from} → … → {ho.trace[-1].handoff_to} "
            f"(최종 phase {ho.trace[-1].phase})",
            f"- role tasks {len(ho.split.tasks)}개 · blocked {len(ho.split.blocked)}개",
            "주의: PM→gateway→tech-lead 제안 packet 일 뿐, 실행은 승인 게이트 통과 후.",
        )
        return CommandResult.info("discovery promote", lines)

    if sub == "save":
        sweep, brief, err = _pick(args[1] if len(args) > 1 else "1")
        if err:
            return CommandResult.error("discovery save", (err,))
        from hephaistos.nexus_read import nexus_root

        root = nexus_root(getattr(ctx, "env", None), getattr(ctx, "config", None))
        if not root:
            return CommandResult.error(
                "discovery save",
                ("Nexus vault 미연결 — `/nexus set <path>` 로 먼저 연결하세요 (fake-write 안 함).",))
        path = D.persist_brief(brief, root)
        if not path:
            return CommandResult.error(
                "discovery save", (f"vault 쓰기 실패 (root={root}) — 권한/경로 확인.",))
        return CommandResult.info(
            "discovery save",
            (f"authored note 기록: {path}", "- author user-researcher · 00-inbox/discovery (raw intake)"))

    sweep = D.run_discovery_sweep(repo_root)
    return CommandResult.info("discovery", sweep.digest.lines())


def _daemon_result(parsed, ctx) -> CommandResult:
    # /daemon [stop] — surface the REAL always-on daemon heartbeat (state/tick/pid),
    # or set the kill-switch. Reads the same file `forgekit runtime status` reads.
    from ..runtime import surface as rsurface

    env = getattr(ctx, "env", None) or None
    args = list(getattr(parsed, "args", ()) or ())
    if args and args[0].lower() == "stop":
        ok, msg = rsurface.request_stop(env=env)
        return (CommandResult.info if ok else CommandResult.error)("daemon stop", (msg,))
    return CommandResult.info("daemon", rsurface.daemon_status_lines(env=env))


def _hephaistos_result(handler, parsed, ctx=None) -> CommandResult:
    # Hephaistos operator surfaces — projection over resolver/verifier/nexus_read (pure core).
    # env/config/role threaded from the context so Nexus reads are LIVE (not static).
    from ..hephaistos import projection as proj

    env = getattr(ctx, "env", None)
    config = getattr(ctx, "config", None)
    role = getattr(ctx, "nexus_role", "") or ""
    args = getattr(parsed, "args", ()) or ()
    request = " ".join(args).strip()
    if handler == H_HEPHAISTOS:
        return CommandResult.info("hephaistos", proj.hephaistos_status_lines(env=env, config=config))
    if handler == H_LOADOUT:
        return CommandResult.info("loadout", proj.loadout_lines(args[0] if args else "backend-java-local"))
    if handler == H_RESOLVE and args:
        sub = args[0].lower()
        # `/resolve ledger` — VIEW the append-only forge governance ledger (read-only).
        if sub == "ledger":
            return _forge_ledger_result(env=env)
        # `/resolve apply <요청>` — PERSIST the forge governance receipt (operator-triggered).
        if sub == "apply":
            return _forge_apply_result(" ".join(args[1:]).strip(), env=env)
    if not request:
        which = "/resolve" if handler == H_RESOLVE else "/skills"
        return CommandResult.info(handler, (f"요청을 입력하세요 — `{which} <요청>` "
                                            "(예: `/resolve Spring Boot JWT refresh token`).",))
    plan, read = proj.resolve_with_sources(request, env=env, config=config, role=role)
    if handler == H_RESOLVE:
        lines = list(proj.resolve_summary_lines(plan, read)) + list(_forge_governance_lines(request, env=env))
        return CommandResult.info("resolve", tuple(lines))
    return CommandResult.info("skills", proj.skills_lines(plan, read))


def _forge_governance_lines(request: str, *, env=None) -> tuple:
    """Append the forge GOVERNANCE verdict to /resolve — the operator sees whether the
    forged plan would be authorized (safe→인가), needs the operator (risky), or is blocked
    (destructive), bound to the same approval gate the runtime enforces. Lazy + best-effort:
    if forgekit_runtime is unavailable the resolve summary is returned unchanged."""

    try:
        from forgekit_runtime.forge import forge_execute
    except Exception:  # noqa: BLE001
        return ()
    try:
        receipt = forge_execute(request, env=env)
    except Exception:  # noqa: BLE001 — a render must never break /resolve
        return ()
    return ("", "── governance ──") + receipt.lines()


def _forge_apply_result(request: str, *, env=None) -> CommandResult:
    """`/resolve apply <요청>` — PERSIST the forge governance receipt to the append-only
    ledger (operator-triggered, never silent). Honest: a risky/blocked plan refuses to
    persist a fake success — only a validation-passing receipt enters the durable log."""

    if not request:
        return CommandResult.info(
            "resolve apply",
            ("요청을 입력하세요 — `/resolve apply <요청>` (forge receipt 를 ledger 에 영속).",),
        )
    try:
        from forgekit_runtime.forge import (
            FakeReceiptRefused, forge_execute, record_forge_receipt,
        )
    except Exception as e:  # noqa: BLE001
        return CommandResult.error("resolve apply", (f"forgekit_runtime 미가용: {e}",))

    receipt = forge_execute(request, env=env)
    # honest: a non-authorized (risky/blocked/error) receipt is never persisted as success.
    if receipt.outcome != "executed" or not receipt.authorized:
        return CommandResult.error(
            "resolve apply",
            ("forge plan 미인가 — ledger 에 영속하지 않음 (가짜 성공 금지).",) + receipt.lines(),
        )
    try:
        path = record_forge_receipt(receipt, env=env)
    except FakeReceiptRefused as e:  # anti-fake at the persistence boundary
        return CommandResult.error(
            "resolve apply",
            (f"ledger 거부 — fake receipt: {e}",) + receipt.lines(),
        )
    if path is None:
        return CommandResult.error(
            "resolve apply",
            ("ledger I/O 실패 — receipt 영속 못함 (verdict 는 유효).",) + receipt.lines(),
        )
    return CommandResult.info(
        "resolve apply",
        (f"forge receipt 를 governance ledger 에 영속함 → {path}",) + receipt.lines(),
    )


def _forge_ledger_result(*, env=None) -> CommandResult:
    """`/resolve ledger` — VIEW the append-only forge governance ledger (read-only)."""

    try:
        from forgekit_runtime.forge import forge_ledger_lines
    except Exception as e:  # noqa: BLE001
        return CommandResult.error("resolve ledger", (f"forgekit_runtime 미가용: {e}",))
    return CommandResult.info("resolve ledger", forge_ledger_lines(env=env))


def _council_result(parsed, ctx=None) -> CommandResult:
    """`/council <session>` — PM→tech-lead→specialist lane readiness from the replay-able
    governance decision log: what's confirmed, what's still missing, and whether a
    specialist may execute ("실행 전에 무엇이 확정돼야 하는지"). Reads the persisted log
    (replay) and reconstructs the readiness — no live artifacts needed. Best-effort: if the
    runtime is unavailable the surface degrades to an honest message."""

    env = getattr(ctx, "env", None)
    args = getattr(parsed, "args", ()) or ()
    session = (args[0] if args else "").strip()
    if not session:
        return CommandResult.info(
            "council",
            ("PM→tech-lead→specialist lane readiness 를 봅니다 — `/council <session>`.",
             "decision log(consult/meeting/decision/approval)을 replay 해 '실행 전에 무엇이 "
             "확정돼야 하는지'를 보여줍니다. 기록은 `decision_lane.record_lane_artifacts` 가 남깁니다.",
             "규칙: PM artifact 없으면 tech-lead lane 실행 불가, tech-lead decision 없으면 specialist 실행 불가."))
    try:
        from forgekit_runtime.decision_lane import (
            decision_trail_from_log,
            readiness_from_log,
            replay_governance_log,
        )
    except Exception:  # noqa: BLE001
        return CommandResult.error("council", ("governance 런타임 미가용.",))
    events = replay_governance_log(session, env=env)
    readiness = readiness_from_log(events)
    head = (f"council lane — session={session} · 기록 {len(events)}건 (replay):",)
    if not events:
        head = (f"council lane — session={session}: 기록 없음 "
                "(decision log 가 비어 있음 → readiness 는 PM brief 부재로 실행 불가).",)
        return CommandResult.info("council", head + readiness.lines())
    # decision trail — "누가 무엇을 결정했는지" (actor → kind → 결정 내용 from payload).
    trail = decision_trail_from_log(events)
    body = readiness.lines() + ("", "── 결정 트레일 (누가 무엇을) ──") + trail
    return CommandResult.info("council", head + body)


def _whoami_result(parsed) -> CommandResult:
    # agent identity surface — registry-backed git author / vault / GitHub App status.
    # `/whoami <agent>` = one agent's detail; `/whoami` = the audit across all agents.
    from ..identity import attribution as attr

    args = getattr(parsed, "args", ()) or ()
    if args:
        return CommandResult.info("whoami", attr.render_whoami_lines(args[0]))
    return CommandResult.info("whoami", attr.identity_audit_lines())


def _render_readiness_result() -> CommandResult:
    # Render readiness is computed from the live environment (pure given env), not a
    # runtime loader — so it works even with no yule_engineering install. Lazy import
    # keeps the router free of any TUI/textual dependency at module load.
    from ..tui.render_readiness import render_readiness_lines

    return CommandResult.info("render readiness", render_readiness_lines())


def _blocked_result() -> CommandResult:
    # Reads the persistent escalation ledger (lazy import; stdlib-only, no textual).
    from ..lifecycle.failure_escalation import open_escalation_lines

    return CommandResult.info("blocked", open_escalation_lines())


def _agents_result(ctx: ConsoleContext) -> CommandResult:
    lines = ["에이전트 레지스트리:"]
    for agent in ctx.agents:
        enter = f"  ({agent.enter_command})" if agent.enter_command else ""
        lines.append(f"  • {agent.label:<14} [{agent.status}] — {agent.description}{enter}")
    return CommandResult.info("agents", tuple(lines))


def _agent_enter_result(cmd, ctx: ConsoleContext) -> CommandResult:
    agent = find_agent(cmd.agent_id, ctx.agents)
    if agent is None:
        return CommandResult.error(f"unknown agent: {cmd.agent_id}")
    # Product (PM) is the engineering-front intake gate — show its real job.
    if agent.agent_id == "product-agent":
        return CommandResult(
            kind=KIND_AGENT_MODE,
            title=f"agent:{agent.agent_id}",
            lines=(
                "▶ Product (PM) — engineering 앞단 intake gate",
                "  raw 요청을 그대로 구현으로 넘기지 않고, 빠진 결정·기본 기능을 먼저 정리합니다.",
                "",
                "이 게이트가 하는 일:",
                "  - feature family 별 누락 기능 자동 보강 (implied features)",
                "  - 중요한 비즈니스 결정만 ≤3개 질문 (옵션 + 추천안)",
                "  - 안전한 기본값은 자동 채움 (loading/empty/error/validation 등)",
                "  - acceptance criteria / non-goals 정리 후 tech-lead 로 product packet handoff",
                "",
                "예: '영상 업로드 구현' → 공개 정책·업로드 주체·노출 순서를 먼저 묻고,",
                "    처리 상태·실패 재시도·썸네일 fallback 을 자동 보강합니다.",
                "",
                "[dim]이 모드에서 입력한 제품 요청은 실제 intake→gateway→tech-lead handoff 로 변환됩니다.[/dim]",
                "[dim]역할 분배 + 권한 없는 영역은 BLOCKED + evidence 기록.[/dim]",
            ),
        )
    lines = [
        f"▶ {agent.label} 에이전트 모드 진입 (stub)",
        f"  {agent.description}",
        "",
        "이 모드는 1차 콘솔 프레임의 stub 입니다 — live submit 은 아직 연결 안 됨.",
        "추천 다음 행동:",
        "  - `/status` 로 현재 운영 상태 확인",
        "  - `/doctor` 로 환경 점검",
    ]
    # Ops Observer 는 관측 역할이므로 운영 대시보드 alert 를 바로 곁들인다.
    if agent.agent_id == "ops-observer":
        summary = ctx.load_operator()
        if summary.alerts:
            lines.append("")
            lines.append("현재 alerts:")
            lines.extend(f"  [{a.level}] {a.message}" for a in summary.alerts)
    return CommandResult(kind=KIND_AGENT_MODE, title=f"agent:{agent.agent_id}", lines=tuple(lines))


__all__ = ("ConsoleContext", "build_default_context", "route")
