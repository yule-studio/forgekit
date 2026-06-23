"""Command + agent registry — the single source the palette/router read from.

Data-driven on purpose: adding a slash command or an agent is a list edit, not a
code change. This is the seam where, later, ``skills/*.md`` / grant tables / the
agent projection can hydrate the registry instead of the static defaults — see
:func:`load_agents` / :func:`load_commands`, which today return the built-ins but
are the documented extension point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from ..models import AgentInfo

# Handler keys the router dispatches on (kept stable; the router maps these).
H_HELP = "help"
H_ABOUT = "about"
H_MODE = "mode"
H_AGENTS = "agents"
H_STATUS = "status"
H_RUNTIME = "runtime"
H_HARNESS = "harness"
H_DOCTOR = "doctor"
H_RENDER = "render"
H_BLOCKED = "blocked"
H_WHOAMI = "whoami"
H_RESOLVE = "resolve"
H_HEPHAISTOS = "hephaistos"
H_SKILLS = "skills"
H_LOADOUT = "loadout"
H_PROVIDER = "provider"
H_SETUP = "setup"
H_TOOLCHAIN = "toolchain"
H_NEXUS = "nexus"
H_DISCOVERY = "discovery"
H_DAEMON = "daemon"
H_GOAL = "goal"
H_COUNCIL = "council"
H_HANDOFF = "handoff"
H_COPY = "copy"
H_ATTACH = "attach"
H_PASTE = "paste"
H_AGENT_ENTER = "agent_enter"
H_LAYOUT = "layout"
H_QUIT = "quit"
H_CLEAR = "clear"


@dataclass(frozen=True)
class SlashCommand:
    name: str          # without leading slash
    summary: str
    handler: str       # H_*
    category: str = "general"
    agent_id: str = "" # set for agent-entry commands


# --- Agents (quick list / registry) ----------------------------------------
# These are the operator-facing agent surfaces. enter_command links to the slash
# command that enters that agent's (stub) mode.
_AGENTS: Tuple[AgentInfo, ...] = (
    AgentInfo("engineering-agent", "Engineering", "개발 intake / 계획 / deliberation / GitHub", ""),
    AgentInfo("planning-agent", "Planning", "일정 / 계획 / 브리핑", "/planning-agent"),
    AgentInfo("product-agent", "Product (PM)", "intake gate — 요구 보강·결정 질문·spec packet", "/pm-agent"),
    AgentInfo("backend-agent", "Backend", "백엔드 설계 / 구현 / 리뷰", "/backend-agent"),
    AgentInfo("security-agent", "Security", "보안 리뷰 / 위협 모델 / 게이트 (note-only)", "/security-agent"),
    AgentInfo("platform-runtime-engineer", "Platform Runtime", "설치/연결/runtime/provider/doctor (code+commit)", ""),
    AgentInfo("knowledge-engineer", "Knowledge", "vault/brain/retrieval 구조화 (note-only)", ""),
    AgentInfo("ops-observer", "Ops Observer", "24h 관측 / budget·alert / fallback triage (note-only)", "/ops-observer"),
    AgentInfo("marketing-agent", "Marketing", "메시징 / 캠페인 (예정)", ""),
    AgentInfo("legal-agent", "Legal", "약관 / 컴플라이언스 (예정)", ""),
    AgentInfo("finance-agent", "Finance", "비용 / 예산 (예정)", ""),
)

# --- Slash commands ---------------------------------------------------------
_COMMANDS: Tuple[SlashCommand, ...] = (
    SlashCommand("help", "이 콘솔의 명령 목록", H_HELP),
    SlashCommand("about", "forgekit hero/소개 — 와이드 아트 + 브랜드 정보", H_ABOUT),
    SlashCommand("welcome", "환영 화면 (/about alias)", H_ABOUT),
    SlashCommand("agents", "에이전트 레지스트리 표시", H_AGENTS),
    SlashCommand("status", "운영 대시보드 요약 (provider/eval/self-improve/token)", H_STATUS, "status"),
    SlashCommand("runtime", "runtime status 요약", H_RUNTIME, "status"),
    SlashCommand("harness", "harness/operator 대시보드 요약", H_HARNESS, "status"),
    SlashCommand("doctor", "환경 진단 (doctor) 요약", H_DOCTOR, "status"),
    SlashCommand("render", "렌더 readiness — true-raster vs fallback + 권장 터미널", H_RENDER, "status"),
    SlashCommand("mode", "런타임 모드 보기/순환 (Shift+Tab) — routing/budget/approval posture", H_MODE, "status"),
    SlashCommand("always-on", "bounded 운영 사이클 — 관측→분류→패킷→handoff→대기 (실행 없음)", H_MODE, "status"),
    SlashCommand("auto", "auto 오케스트레이션 — 상황 분류 → 모드 추천/안전 전환 (`/auto <요청>`)", H_MODE, "status"),
    SlashCommand("sources", "수집원 레지스트리 — live(무료 우선) vs planned(미연결)", H_MODE, "status"),
    SlashCommand("self-improve", "레포 개선 스캔 — gap → risk class 패킷 (safe만 자동, 실행 없음)", H_MODE, "status"),
    SlashCommand("red-blue", "보안 드릴 — 내 자산 allowlist plan-only (`/red-blue <target>`), 실행 없음", H_MODE, "status"),
    SlashCommand("autopilot", "repo-autopilot 사이클 — 내부 승인 체계, safe-class만 실행 (`/autopilot <repo>`)", H_MODE, "status"),
    SlashCommand("digest", "operator digest — 발견/자동실행(내부승인)/승인필요/차단 요약", H_MODE, "status"),
    SlashCommand("design", "restricted design source 상태 — design role만 raw, 그외 projection", H_MODE, "status"),
    SlashCommand("usage", "토큰 사용량 — today rollup(provider/mode/live·estimate) + budget", H_MODE, "status"),
    SlashCommand("blocked", "반복 실패 에스컬레이션 목록 (왜·대안·다음 단계)", H_BLOCKED, "status"),
    SlashCommand("copy", "OS clipboard 로 복사 — `/copy [last|all|turn <n>|block <n>|paste <id>]` (plain-text, pbcopy/xclip)", H_COPY, "status"),
    SlashCommand("attach", "첨부 staging — `/attach [<path>|status|clear]` 또는 이미지 붙여넣기. 실제 stage(미전송 staged_only — provider 텍스트 전용)", H_ATTACH, "status"),
    SlashCommand("paste", "보존된 large paste 조작 — `/paste [list|expand <id>|resend <id>]` (원문 보존, placeholder 아님)", H_PASTE, "status"),
    SlashCommand("whoami", "agent identity — git author / vault / GitHub App 자격 (`/whoami <agent>`)", H_WHOAMI, "status"),
    SlashCommand("resolve", "Hephaistos — 요청을 skill/loadout/weapon/source/packet 으로 resolve + governance verdict (`/resolve <요청>` 미리보기, `/resolve apply <요청>` = receipt 를 ledger 에 영속, `/resolve ledger` = forge governance ledger 보기)", H_RESOLVE, "status"),
    SlashCommand("council", "PM→tech-lead→specialist lane readiness — 실행 전에 무엇이 확정돼야 하는지(replay 가능 decision log). `/council <session>` (없으면 사용법)", H_COUNCIL, "status"),
    SlashCommand("handoff", "specialist work order — 목표/제안 스택/선택 이유/탈락안/컨벤션/디자인·API·infra/scope/test/acceptance (`/handoff <session>`, replay 된 handoff packet)", H_HANDOFF, "status"),
    SlashCommand("hephaistos", "Hephaistos skill-forge 상태 — armory/nexus/resolver/loadout", H_HEPHAISTOS, "status"),
    SlashCommand("skills", "최근/지정 요청의 선택 skill + 선택 이유 (`/skills <요청>`)", H_SKILLS, "status"),
    SlashCommand("loadout", "loadout readiness — 실 env weapon 검증 (`/loadout <id>`)", H_LOADOUT, "status"),
    SlashCommand("provider", "provider 설정/연결 — `/provider [set|link|unlink|connect <id>|disconnect <id>|test <id>|recommended|preset four-brain|route show|route set <slot> <id>|budget <id> <limit>|budget show|list|doctor]`", H_PROVIDER, "status"),
    SlashCommand("setup", "컨트롤플레인 부트스트랩 — provider · knowledge(nexus/vault) · toolchain 한 화면 정직 집계 + 추천 preset 저장/검증 (`/setup [apply [preset]]`)", H_SETUP, "status"),
    SlashCommand("toolchain", "language/runtime 버전 전환 — `/toolchain [detect|recommend <loadout>|switch [global] [--approve]|verify|drift]` (repo-local 감지·mise 기반, global/install 은 승인 게이트)", H_TOOLCHAIN, "status"),
    SlashCommand("nexus", "Nexus 지식 source — `/nexus [set <path>|clear]` 연결/해제 + live 상태(connected/not_connected/missing/blocked/restricted)", H_NEXUS, "status"),
    SlashCommand("discovery", "discovery 누적 루프 — free-first 수집→idea brief→**ledger 누적**(새 vs 추적중, lifecycle)+operator digest(왜/다음 질문). `/discovery [intake | pending | candidates | evidence | promote <n> | save <n> | park <n>]` (intake=외부 skill/plugin/tool 후보 수집+curation gate(Armory 승격 전), pending=결정대기 목록, candidates=교차 관측·신선도 통과한 '물어볼 후보', evidence=경쟁gap·self-improve 신호를 vault evidence note 로 영속, promote=PM handoff 제안, save=연결 vault authored note, park=보류)", H_DISCOVERY, "status"),
    SlashCommand("daemon", "always-on 데몬 heartbeat — state/tick/last_tick/pid/kill-switch (`/daemon [stop]`); CLI `forgekit runtime serve|status|stop`", H_DAEMON, "status"),
    SlashCommand("goal", "장기 목표 control plane — `/goal [list|new <제목>|show <id>|activate <id>|evidence <id>|awaiting|approve <id> [메모]|deny <id> [메모]]` (forgekit_goal 영속, 승인은 awaiting_approval→active/blocked + decision evidence, tick/실행은 runtime/GW4)", H_GOAL, "status"),
    SlashCommand("pm-agent", "Product intake gate — 요구 보강·결정 질문·handoff (stub)", H_AGENT_ENTER, "agent", "product-agent"),
    SlashCommand("planning-agent", "Planning 에이전트 모드 진입 (stub)", H_AGENT_ENTER, "agent", "planning-agent"),
    SlashCommand("backend-agent", "Backend 에이전트 모드 진입 (stub)", H_AGENT_ENTER, "agent", "backend-agent"),
    SlashCommand("security-agent", "Security 에이전트 모드 진입 (stub)", H_AGENT_ENTER, "agent", "security-agent"),
    SlashCommand("ops-observer", "Ops Observer 모드 진입 (stub)", H_AGENT_ENTER, "agent", "ops-observer"),
    SlashCommand("layout", "레이아웃 전환 (focus ↔ dashboard)", H_LAYOUT),
    SlashCommand("clear", "로그 지우기", H_CLEAR),
    SlashCommand("quit", "콘솔 종료", H_QUIT),
    SlashCommand("exit", "콘솔 종료 (/quit alias)", H_QUIT),
)


def load_agents() -> Tuple[AgentInfo, ...]:
    """Return the agent registry. Extension seam: hydrate from agent projection
    / grants later; today returns the static built-ins."""

    return _AGENTS


def load_commands() -> Tuple[SlashCommand, ...]:
    """Return the slash-command registry. Extension seam: merge ``skills/*.md`` /
    grant-derived commands later; today returns the static built-ins."""

    return _COMMANDS


def find_command(name: str, commands: Optional[Sequence[SlashCommand]] = None) -> Optional[SlashCommand]:
    name = (name or "").strip().lstrip("/").lower()
    for cmd in commands if commands is not None else _COMMANDS:
        if cmd.name == name:
            return cmd
    return None


def find_agent(agent_id: str, agents: Optional[Sequence[AgentInfo]] = None) -> Optional[AgentInfo]:
    for agent in agents if agents is not None else _AGENTS:
        if agent.agent_id == agent_id:
            return agent
    return None


__all__ = (
    "SlashCommand",
    "H_HELP", "H_AGENTS", "H_STATUS", "H_RUNTIME", "H_HARNESS", "H_DOCTOR",
    "H_AGENT_ENTER", "H_LAYOUT", "H_QUIT", "H_CLEAR",
    "load_agents", "load_commands", "find_command", "find_agent",
)
