from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..agents.obsidian.writer import ENV_VAULT_PATH
from ..core.context_loader import ContextError, load_agent_context
from ..core.tls import apply_ca_bundle_fallback, resolve_ca_bundle


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str
    hint: Optional[str] = None


def run_doctor(repo_root: Path, agent_id: str = "engineering-agent") -> Sequence[DoctorCheck]:
    checks: List[DoctorCheck] = []
    checks.extend(_check_commands(["claude", "codex", "gemini", "ollama", "gh", "copilot"]))
    checks.append(_check_github_auth())
    checks.append(_check_discord_tls())
    checks.append(_check_obsidian_vault())

    manifest = _load_manifest_for_agent(repo_root, agent_id, checks)
    ollama_config = _find_ollama_config(manifest)
    checks.append(_check_ollama_api(ollama_config.get("endpoint")))

    model = ollama_config.get("model")
    if model:
        checks.append(_check_ollama_model(ollama_config.get("endpoint"), model))

    return checks


def render_doctor_report(checks: Sequence[DoctorCheck]) -> str:
    lines = ["Yule Orchestrator Doctor", ""]
    for check in checks:
        lines.append(f"{check.status:<5} {check.name:<18} {check.detail}")
        if check.hint:
            lines.append(f"      Hint: {check.hint}")

    if _has_failure(checks):
        lines.extend(["", "Doctor completed with issues."])
    else:
        lines.extend(["", "Doctor completed successfully."])

    return "\n".join(lines) + "\n"


def doctor_exit_code(checks: Sequence[DoctorCheck]) -> int:
    return 1 if _has_failure(checks) else 0


def _check_commands(commands: Iterable[str]) -> Sequence[DoctorCheck]:
    checks: List[DoctorCheck] = []
    for command in commands:
        path = shutil.which(command)
        if path:
            checks.append(DoctorCheck(name=f"cli:{command}", status="OK", detail=path))
        else:
            checks.append(
                DoctorCheck(
                    name=f"cli:{command}",
                    status="FAIL",
                    detail="not found in PATH",
                    hint=f"Install {command} or add it to PATH.",
                )
            )
    return checks


def _check_github_auth() -> DoctorCheck:
    if not shutil.which("gh"):
        return DoctorCheck(
            name="github auth",
            status="SKIP",
            detail="gh not installed",
            hint="Install GitHub CLI before checking auth.",
        )

    result = _run_command(["gh", "auth", "status"])
    if result.returncode == 0:
        return DoctorCheck(name="github auth", status="OK", detail="logged in")

    return DoctorCheck(
        name="github auth",
        status="FAIL",
        detail="not logged in or token invalid",
        hint="Run `gh auth login`.",
    )


def _check_discord_tls() -> DoctorCheck:
    bundle = resolve_ca_bundle()
    if bundle.source in {"env-missing", "missing"}:
        return DoctorCheck(
            name="discord tls",
            status="FAIL",
            detail=bundle.detail,
            hint="Install a CA bundle or set SSL_CERT_FILE to a valid certifi/OpenSSL bundle.",
        )

    active_bundle = apply_ca_bundle_fallback()
    try:
        _read_json("https://discord.com/api/v10/gateway")
    except urllib.error.URLError as exc:
        return DoctorCheck(
            name="discord tls",
            status="FAIL",
            detail=f"cannot reach Discord API: {exc.reason}",
            hint="Check your network, proxy, and local CA bundle.",
        )
    except ValueError as exc:
        return DoctorCheck(
            name="discord tls",
            status="FAIL",
            detail=str(exc),
            hint="Check that HTTPS traffic to Discord is not being intercepted or rewritten.",
        )

    detail = active_bundle.detail
    if active_bundle.cafile:
        detail += f" ({active_bundle.cafile})"
    return DoctorCheck(name="discord tls", status="OK", detail=detail)


def _check_obsidian_vault() -> DoctorCheck:
    raw = (os.environ.get(ENV_VAULT_PATH) or "").strip()
    if not raw:
        return DoctorCheck(
            name="obsidian vault",
            status="SKIP",
            detail=f"{ENV_VAULT_PATH} not set",
            hint="Set OBSIDIAN_VAULT_PATH in .env.local to enable `yule obsidian sync`.",
        )

    expanded = os.path.expanduser(raw)
    path = Path(expanded)
    if not path.is_absolute():
        return DoctorCheck(
            name="obsidian vault",
            status="FAIL",
            detail=f"{ENV_VAULT_PATH} must be absolute (got {raw!r})",
            hint="Use the full /Users/<you>/... path in .env.local.",
        )
    if not path.exists():
        return DoctorCheck(
            name="obsidian vault",
            status="FAIL",
            detail=f"vault path does not exist: {path}",
            hint="Open the vault in Obsidian once or fix OBSIDIAN_VAULT_PATH.",
        )
    if not path.is_dir():
        return DoctorCheck(
            name="obsidian vault",
            status="FAIL",
            detail=f"vault path is not a directory: {path}",
            hint="OBSIDIAN_VAULT_PATH must point to the vault root folder.",
        )
    return DoctorCheck(name="obsidian vault", status="OK", detail=str(path))


def _load_manifest_for_agent(repo_root: Path, agent_id: str, checks: List[DoctorCheck]) -> Dict[str, Any]:
    try:
        loaded_context = load_agent_context(repo_root=repo_root, agent_id=agent_id)
    except ContextError as exc:
        checks.append(
            DoctorCheck(
                name="agent context",
                status="FAIL",
                detail=str(exc),
                hint="Run `yule context engineering-agent` for more detail.",
            )
        )
        return {}

    checks.append(
        DoctorCheck(
            name="agent context",
            status="OK",
            detail=str(_relative_path(loaded_context.manifest_path, repo_root)),
        )
    )
    return loaded_context.manifest


def _find_ollama_config(manifest: Dict[str, Any]) -> Dict[str, str]:
    for participant in manifest.get("participants", []):
        if isinstance(participant, dict) and participant.get("id") == "ollama":
            endpoint = participant.get("endpoint")
            model = participant.get("model")
            return {
                "endpoint": endpoint if isinstance(endpoint, str) else "http://localhost:11434",
                "model": model if isinstance(model, str) else "",
            }

    return {"endpoint": "http://localhost:11434", "model": ""}


def _check_ollama_api(endpoint: Optional[str]) -> DoctorCheck:
    endpoint = endpoint or "http://localhost:11434"
    tags_url = _ollama_tags_url(endpoint)

    try:
        _read_json(tags_url)
    except urllib.error.URLError:
        return DoctorCheck(
            name="ollama api",
            status="FAIL",
            detail=f"cannot reach {endpoint}",
            hint="Start Ollama with `open -a Ollama` or `ollama serve`.",
        )
    except ValueError as exc:
        return DoctorCheck(
            name="ollama api",
            status="FAIL",
            detail=str(exc),
            hint="Check that Ollama is serving a valid API response.",
        )

    return DoctorCheck(name="ollama api", status="OK", detail=endpoint)


def _check_ollama_model(endpoint: Optional[str], expected_model: str) -> DoctorCheck:
    endpoint = endpoint or "http://localhost:11434"
    tags_url = _ollama_tags_url(endpoint)

    try:
        data = _read_json(tags_url)
    except (urllib.error.URLError, ValueError):
        return DoctorCheck(
            name="ollama model",
            status="SKIP",
            detail=f"{expected_model} not checked",
            hint="Ollama API must be reachable before checking models.",
        )

    models = data.get("models", [])
    names = {model.get("name") for model in models if isinstance(model, dict)}
    if expected_model in names:
        return DoctorCheck(name="ollama model", status="OK", detail=expected_model)

    return DoctorCheck(
        name="ollama model",
        status="FAIL",
        detail=f"{expected_model} not installed",
        hint=f"Run `ollama run {expected_model}`.",
    )


def _ollama_tags_url(endpoint: str) -> str:
    return endpoint.rstrip("/") + "/api/tags"


def _read_json(url: str) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = response.read().decode("utf-8")

    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError(f"unexpected JSON response from {url}")
    return data


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _has_failure(checks: Sequence[DoctorCheck]) -> bool:
    return any(check.status == "FAIL" for check in checks)


def _relative_path(path: Path, repo_root: Path) -> Path:
    try:
        return path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return path
