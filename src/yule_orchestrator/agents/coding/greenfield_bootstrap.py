"""P1-H — greenfield repo bootstrap scaffold (planner + applier).

Canonical session ``11917bf1e75d`` 의 latest blocker 는 더 이상 mislabel
이 아니라 *실제* capability gap: target repo (``naver-search-clone``) 가
``.git`` + README 만 있는 greenfield 인데 executor 의 ``RecordOnlyCodeEditor``
는 코드 생성 불가 → ``bootstrap_required:no_stack_detected+editor_record_only_insufficient``.

본 모듈은 그 gap 을 실제로 닫는다. ordinary "edit existing code" path 대신
**explicit bootstrap mode** 를 제공:

  1. ``detect_bootstrap_mode(request, worktree_path) -> Optional[BootstrapMode]``
     repo emptiness + request stack signal 을 보고 ``greenfield_full_stack``
     / ``greenfield_python`` / None 결정.
  2. ``plan_greenfield_scaffold(mode, request) -> BootstrapPlan``
     deterministic file list (path + content). 옛 secrets / 무작위 infra
     사실 추측 금지 — placeholder + README 만.
  3. ``apply_bootstrap_plan(*, worktree_path, plan, write_scope) ->
     BootstrapApplyResult`` write_scope governance 준수 + idempotent
     (이미 존재하는 파일 절대 덮어쓰지 않음).

설계 원칙:
  * **deterministic**: 같은 (mode, request, scope) → 같은 output. LLM
    호출 없음. minimal viable scaffold만.
  * **idempotent**: 두 번 호출해도 새 파일만 만든다. 기존 파일 수정 X.
  * **governed**: write_scope 가 명시되면 그 범위 밖은 거부. 빈 scope 면
    repo root 허용 (greenfield 는 정의상 root 작업).
  * **no secrets**: ``.env.example`` 만 — 실제 ``.env`` 절대 안 만듦.
  * **operator-readable audit**: ``BootstrapApplyResult.files_created`` /
    ``files_skipped`` / ``files_refused_by_scope`` 모두 노출.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mode + plan + result dataclasses
# ---------------------------------------------------------------------------


MODE_GREENFIELD_FULL_STACK: str = "greenfield_full_stack"
MODE_GREENFIELD_PYTHON: str = "greenfield_python"


@dataclass(frozen=True)
class BootstrapFile:
    """One file in the scaffold. relative path + utf-8 content."""

    relative_path: str
    content: str
    overwrite_existing: bool = False  # default: never overwrite


@dataclass(frozen=True)
class BootstrapPlan:
    mode: str
    files: Tuple[BootstrapFile, ...]
    summary: str = ""
    stack_signals_expected: Tuple[str, ...] = ()  # e.g. ("package.json", "docker-compose.yml")


@dataclass(frozen=True)
class BootstrapApplyResult:
    mode: str
    files_created: Tuple[str, ...] = ()
    files_skipped_exists: Tuple[str, ...] = ()
    files_refused_by_scope: Tuple[str, ...] = ()
    write_errors: Tuple[Tuple[str, str], ...] = ()  # (path, reason)

    @property
    def succeeded(self) -> bool:
        return not self.write_errors and (
            self.files_created or self.files_skipped_exists
        )

    def to_audit(self) -> dict:
        return {
            "mode": self.mode,
            "files_created": list(self.files_created),
            "files_skipped_exists": list(self.files_skipped_exists),
            "files_refused_by_scope": list(self.files_refused_by_scope),
            "write_errors": [
                {"path": p, "reason": r} for p, r in self.write_errors
            ],
        }


# ---------------------------------------------------------------------------
# Detection — when do we bootstrap?
# ---------------------------------------------------------------------------


_GREENFIELD_BENIGN_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".gitignore",
        ".gitattributes",
        ".github",
        ".gitkeep",
        "README",
        "README.md",
        "README.MD",
        "README.rst",
        "README.txt",
        "LICENSE",
        "LICENSE.md",
        "LICENSE.txt",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        ".editorconfig",
        ".DS_Store",
    }
)


def _looks_greenfield(worktree: Path) -> bool:
    try:
        entries = list(worktree.iterdir())
    except OSError:
        return False
    for entry in entries:
        if entry.name in _GREENFIELD_BENIGN_NAMES:
            continue
        return False
    return True


def _request_text(request: Any) -> str:
    parts = []
    for attr in ("user_request", "generated_prompt"):
        value = getattr(request, attr, "") or ""
        if value:
            parts.append(str(value))
    return " ".join(parts).lower()


def detect_bootstrap_mode(
    *, request: Any, worktree_path: str
) -> Optional[str]:
    """Return a bootstrap mode token when the repo is greenfield AND the
    request signals a stack we can scaffold, else None.

    Inputs combined:
      * worktree 가 greenfield (`.git` + README 류만)
      * request user_request / generated_prompt 텍스트의 stack signal
        (또는 stack_detector 결과)

    Modes:
      * ``greenfield_full_stack`` — Next/Nest/Postgres/Docker compose
        같은 full-stack 키워드, 또는 명시적 ``full-stack`` / ``mvp``.
      * ``greenfield_python`` — python/fastapi/django 등.
      * None — bootstrap 안 함.
    """

    root = Path(worktree_path)
    if not root.is_dir():
        return None
    if not _looks_greenfield(root):
        return None

    text = _request_text(request)
    if not text:
        return None
    full_stack_signals = (
        "next.js", "nextjs", "next js",
        "nestjs", "nest.js", "nest js",
        "docker compose", "docker-compose",
        "full-stack", "full stack", "fullstack",
        "풀스택",
        "postgres", "postgresql", "psql",
    )
    if any(signal in text for signal in full_stack_signals):
        return MODE_GREENFIELD_FULL_STACK

    python_signals = (
        "fastapi", "django", "flask", "pytest",
        "python ", " python", "파이썬",
    )
    if any(signal in text for signal in python_signals):
        return MODE_GREENFIELD_PYTHON

    return None


# ---------------------------------------------------------------------------
# Planners
# ---------------------------------------------------------------------------


def plan_greenfield_scaffold(
    *, mode: str, request: Any
) -> BootstrapPlan:
    if mode == MODE_GREENFIELD_FULL_STACK:
        return _plan_full_stack(request)
    if mode == MODE_GREENFIELD_PYTHON:
        return _plan_python(request)
    raise ValueError(f"unknown bootstrap mode: {mode!r}")


def _plan_full_stack(request: Any) -> BootstrapPlan:
    """Minimal Next.js (apps/web) + NestJS (apps/api) + Postgres +
    docker-compose monorepo scaffold.

    Every file is a placeholder — runnable shape, not real product code.
    Real product implementation lands via subsequent coding jobs on this
    same repo once stack signals exist.
    """

    repo_name = ""
    raw_repo = getattr(request, "repo_full_name", "") or ""
    if "/" in raw_repo:
        repo_name = raw_repo.partition("/")[2].strip().rstrip(".git")
    package_name = repo_name or "greenfield-app"

    files = (
        BootstrapFile(
            relative_path="package.json",
            content=json.dumps(
                {
                    "name": package_name,
                    "private": True,
                    "version": "0.0.1",
                    "workspaces": ["apps/*"],
                    "scripts": {
                        "test": "echo \"no real tests yet — bootstrap scaffold\" && exit 0",
                        "dev": "echo \"run 'pnpm --filter ./apps/web dev'\"",
                    },
                    "packageManager": "[email protected]",
                },
                indent=2,
            )
            + "\n",
        ),
        BootstrapFile(
            relative_path="pnpm-workspace.yaml",
            content="packages:\n  - apps/*\n",
        ),
        BootstrapFile(
            relative_path="docker-compose.yml",
            content=(
                "version: \"3.9\"\n"
                "services:\n"
                "  web:\n"
                "    build: ./apps/web\n"
                "    ports:\n"
                "      - \"3000:3000\"\n"
                "    depends_on:\n"
                "      - api\n"
                "  api:\n"
                "    build: ./apps/api\n"
                "    ports:\n"
                "      - \"4000:4000\"\n"
                "    environment:\n"
                "      DATABASE_URL: postgres://app:app@db:5432/app\n"
                "    depends_on:\n"
                "      - db\n"
                "  db:\n"
                "    image: postgres:16-alpine\n"
                "    environment:\n"
                "      POSTGRES_USER: app\n"
                "      POSTGRES_PASSWORD: app\n"
                "      POSTGRES_DB: app\n"
                "    ports:\n"
                "      - \"5432:5432\"\n"
            ),
        ),
        BootstrapFile(
            relative_path=".env.example",
            content=(
                "# Greenfield scaffold — copy to .env and fill real values.\n"
                "# Never commit .env. Operator-only.\n"
                "DATABASE_URL=postgres://app:app@localhost:5432/app\n"
                "NEXT_PUBLIC_API_BASE_URL=http://localhost:4000\n"
            ),
        ),
        BootstrapFile(
            relative_path=".gitignore",
            content=(
                "node_modules/\n"
                ".next/\n"
                "dist/\n"
                ".env\n"
                ".env.local\n"
                "*.log\n"
            ),
        ),
        BootstrapFile(
            relative_path="apps/web/package.json",
            content=json.dumps(
                {
                    "name": "@app/web",
                    "version": "0.0.1",
                    "private": True,
                    "scripts": {
                        "dev": "next dev",
                        "build": "next build",
                        "test": "echo \"web scaffold — add real tests\" && exit 0",
                    },
                    "dependencies": {
                        "next": "^14.0.0",
                        "react": "^18.0.0",
                        "react-dom": "^18.0.0",
                    },
                },
                indent=2,
            )
            + "\n",
        ),
        BootstrapFile(
            relative_path="apps/web/pages/index.tsx",
            content=(
                "export default function Home() {\n"
                "  return <main>{'web scaffold ready — implement real UI'}</main>;\n"
                "}\n"
            ),
        ),
        BootstrapFile(
            relative_path="apps/api/package.json",
            content=json.dumps(
                {
                    "name": "@app/api",
                    "version": "0.0.1",
                    "private": True,
                    "scripts": {
                        "start": "nest start",
                        "build": "nest build",
                        "test": "echo \"api scaffold — add real tests\" && exit 0",
                    },
                    "dependencies": {
                        "@nestjs/common": "^10.0.0",
                        "@nestjs/core": "^10.0.0",
                        "@nestjs/platform-express": "^10.0.0",
                    },
                },
                indent=2,
            )
            + "\n",
        ),
        BootstrapFile(
            relative_path="apps/api/src/main.ts",
            content=(
                "import { NestFactory } from '@nestjs/core';\n"
                "import { Module, Controller, Get } from '@nestjs/common';\n"
                "\n"
                "@Controller()\n"
                "class HealthController {\n"
                "  @Get('/health')\n"
                "  health() {\n"
                "    return { ok: true, service: 'api', stage: 'scaffold' };\n"
                "  }\n"
                "}\n"
                "\n"
                "@Module({ controllers: [HealthController] })\n"
                "class AppModule {}\n"
                "\n"
                "async function bootstrap() {\n"
                "  const app = await NestFactory.create(AppModule);\n"
                "  await app.listen(4000);\n"
                "}\n"
                "bootstrap();\n"
            ),
        ),
        BootstrapFile(
            relative_path="GREENFIELD_BOOTSTRAP.md",
            content=(
                f"# Greenfield scaffold for {package_name}\n"
                "\n"
                "Generated by yule-studio-agent coding-executor greenfield bootstrap.\n"
                "Contains minimum runnable shape:\n"
                "\n"
                "- `package.json` + `pnpm-workspace.yaml` (monorepo)\n"
                "- `apps/web` (Next.js placeholder)\n"
                "- `apps/api` (NestJS placeholder w/ /health)\n"
                "- `docker-compose.yml` (web + api + postgres)\n"
                "- `.env.example` (placeholders only — no real secrets)\n"
                "\n"
                "Next step: implement real product code in subsequent coding jobs.\n"
                "This scaffold provides the stack signals the executor needs to\n"
                "switch out of bootstrap mode on the next run.\n"
            ),
        ),
    )
    return BootstrapPlan(
        mode=MODE_GREENFIELD_FULL_STACK,
        files=files,
        summary=(
            "Next.js (apps/web) + NestJS (apps/api) + Postgres + docker-compose "
            "minimal monorepo scaffold."
        ),
        stack_signals_expected=(
            "package.json",
            "pnpm-workspace.yaml",
            "docker-compose.yml",
            "apps/web/package.json",
            "apps/api/package.json",
        ),
    )


def _plan_python(request: Any) -> BootstrapPlan:
    files = (
        BootstrapFile(
            relative_path="pyproject.toml",
            content=(
                "[project]\n"
                "name = \"app\"\n"
                "version = \"0.0.1\"\n"
                "requires-python = \">=3.11\"\n"
                "\n"
                "[tool.pytest.ini_options]\n"
                "testpaths = [\"tests\"]\n"
            ),
        ),
        BootstrapFile(
            relative_path="src/app/__init__.py",
            content="__version__ = \"0.0.1\"\n",
        ),
        BootstrapFile(
            relative_path="tests/test_smoke.py",
            content=(
                "def test_smoke():\n"
                "    assert True\n"
            ),
        ),
        BootstrapFile(
            relative_path=".gitignore",
            content=(
                "__pycache__/\n"
                "*.pyc\n"
                ".venv/\n"
                ".pytest_cache/\n"
                ".env\n"
            ),
        ),
        BootstrapFile(
            relative_path="GREENFIELD_BOOTSTRAP.md",
            content=(
                "# Greenfield Python scaffold\n"
                "\n"
                "Minimal pytest-runnable shape. Implement real product modules\n"
                "under `src/app/` in subsequent coding jobs.\n"
            ),
        ),
    )
    return BootstrapPlan(
        mode=MODE_GREENFIELD_PYTHON,
        files=files,
        summary="Python project scaffold (pyproject + pytest + src layout).",
        stack_signals_expected=("pyproject.toml", "tests/"),
    )


# ---------------------------------------------------------------------------
# Applier — write-scope governed, idempotent
# ---------------------------------------------------------------------------


def _normalize_write_scope(write_scope: Sequence[str]) -> Tuple[str, ...]:
    """Return scope as a tuple of relative path prefixes.

    Empty scope (``()``) means "no restriction" — caller is expected to
    have validated this with operator policy (CodingAuthorizationProposal
    typically supplies a scope). Greenfield bootstrap default behavior is
    "allow everything" because the repo is by definition empty.
    """

    return tuple(
        str(s).strip().rstrip("/")
        for s in write_scope or ()
        if str(s).strip()
    )


def _is_in_write_scope(
    rel_path: str, scope: Sequence[str]
) -> bool:
    """Is *rel_path* allowed by the *scope* prefixes?

    Empty scope → True (no restriction).
    ``"**"`` → True.
    Each scope item is treated as a path prefix (with optional ``**``
    suffix) — a real Glob library is overkill for our deterministic
    scaffolding paths.
    """

    if not scope:
        return True
    norm = rel_path.lstrip("./")
    for entry in scope:
        token = entry.strip().rstrip("/").rstrip("*").rstrip("/")
        if not token:
            return True  # "**" or "/**" → no restriction
        if norm == token or norm.startswith(token + "/"):
            return True
    return False


def apply_bootstrap_plan(
    *,
    worktree_path: str,
    plan: BootstrapPlan,
    write_scope: Sequence[str] = (),
) -> BootstrapApplyResult:
    """Write every file in *plan* under *worktree_path*, honoring scope
    and the idempotency rule (never overwrite existing).
    """

    root = Path(worktree_path)
    if not root.is_dir():
        return BootstrapApplyResult(
            mode=plan.mode,
            write_errors=(("<worktree>", "worktree path not a directory"),),
        )
    scope = _normalize_write_scope(write_scope)
    created: list[str] = []
    skipped: list[str] = []
    refused: list[str] = []
    errors: list[Tuple[str, str]] = []

    for entry in plan.files:
        rel = entry.relative_path.lstrip("/")
        if not _is_in_write_scope(rel, scope):
            refused.append(rel)
            continue
        target = root / rel
        if target.exists() and not entry.overwrite_existing:
            skipped.append(rel)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry.content, encoding="utf-8")
            created.append(rel)
        except OSError as exc:
            errors.append((rel, f"{type(exc).__name__}: {exc}"))

    return BootstrapApplyResult(
        mode=plan.mode,
        files_created=tuple(created),
        files_skipped_exists=tuple(skipped),
        files_refused_by_scope=tuple(refused),
        write_errors=tuple(errors),
    )


__all__ = (
    "BootstrapApplyResult",
    "BootstrapFile",
    "BootstrapPlan",
    "MODE_GREENFIELD_FULL_STACK",
    "MODE_GREENFIELD_PYTHON",
    "apply_bootstrap_plan",
    "detect_bootstrap_mode",
    "plan_greenfield_scaffold",
)
