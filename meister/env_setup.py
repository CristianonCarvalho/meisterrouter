"""Shared project environment preparation for gates and worktrees."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Tuple

from meister.config import MeisterConfig
from meister.i18n import t


def _node_install_command(path: str) -> list[str]:
    if os.path.exists(os.path.join(path, "pnpm-lock.yaml")):
        return ["pnpm", "install", "--frozen-lockfile"]
    if os.path.exists(os.path.join(path, "package-lock.json")):
        return ["npm", "ci"]
    if os.path.exists(os.path.join(path, "yarn.lock")):
        return ["yarn", "install", "--frozen-lockfile"]

    package_path = os.path.join(path, "package.json")
    try:
        with open(package_path, "r", encoding="utf-8") as package_file:
            package = json.load(package_file)
    except (OSError, json.JSONDecodeError):
        return []
    package_manager = package.get("packageManager", "")
    if isinstance(package_manager, str):
        manager = package_manager.split("@", 1)[0]
        if manager in {"pnpm", "npm", "yarn"}:
            return [manager, "install"]
    return ["npm", "install"]


_NODE_INSTALL_SIGNALS = ("pnpm-lock.yaml", "package-lock.json", "yarn.lock", "pnpm-workspace.yaml")


def _node_install_is_needed(repo_path: str) -> bool:
    """Se há algo a instalar: lockfile, workspace pnpm, dependências ou `workspaces` no package.json.

    Só devolve False quando NADA sinaliza instalação (ex.: o wrapper npm do próprio MeisterRouter, só com
    `bin`/`scripts`). Package.json ilegível ou fora do formato conta como "sim": mantém o comportamento anterior.
    """
    if any(os.path.exists(os.path.join(repo_path, name)) for name in _NODE_INSTALL_SIGNALS):
        return True
    try:
        with open(os.path.join(repo_path, "package.json"), "r", encoding="utf-8") as package_file:
            package = json.load(package_file)
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(package, dict):
        return True
    if package.get("workspaces"):
        return True
    return any(package.get(key) for key in ("dependencies", "devDependencies", "optionalDependencies"))


_SUBPACKAGE_IGNORED_DIRS = {"node_modules", ".git", ".venv", "venv", "dist", "build", ".claude", ".meister"}
_SUBPACKAGE_MAX_LEVELS = 3
_NODE_LOCKFILES = ("pnpm-lock.yaml", "package-lock.json", "yarn.lock")


def _root_declares_workspaces(repo_path: str) -> bool:
    """Workspace na raiz (pnpm-workspace.yaml ou `workspaces`): a instalação da raiz já cobre os subpacotes."""
    if os.path.exists(os.path.join(repo_path, "pnpm-workspace.yaml")):
        return True
    try:
        with open(os.path.join(repo_path, "package.json"), "r", encoding="utf-8") as package_file:
            package = json.load(package_file)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(package, dict) and bool(package.get("workspaces"))


def _node_subpackages(repo_path: str) -> list[str]:
    """Diretórios abaixo da raiz (até 3 níveis) com package.json e lockfile próprio, em ordem determinística."""
    found: list[str] = []

    def collect(directory: str, level: int) -> None:
        if level >= _SUBPACKAGE_MAX_LEVELS:
            return
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return
        for name in names:
            if name in _SUBPACKAGE_IGNORED_DIRS:
                continue
            child = os.path.join(directory, name)
            if os.path.islink(child) or not os.path.isdir(child):
                continue
            if os.path.isfile(os.path.join(child, "package.json")) and any(
                os.path.exists(os.path.join(child, lockfile)) for lockfile in _NODE_LOCKFILES
            ):
                found.append(child)
            collect(child, level + 1)

    collect(repo_path, 0)
    return sorted(found)


def prepare_environment(path: str, config: MeisterConfig) -> Tuple[bool, str]:
    """Install the project's configured environment, or do nothing if not applicable."""
    if not config.environment.install_dependencies:
        return True, ""

    repo_path = os.path.abspath(path)
    subpackages: list[str] = []
    if config.gate.install:
        items = [config.gate.install] if isinstance(config.gate.install, str) else list(config.gate.install)
        commands = [shlex.split(item) for item in items]
    elif os.path.isfile(os.path.join(repo_path, "package.json")):
        commands = []
        if _node_install_is_needed(repo_path):
            commands.append(_node_install_command(repo_path))
        # nada a instalar na raiz (ex.: o wrapper npm do próprio MeisterRouter) não pula os subpacotes com lockfile
        if not _root_declares_workspaces(repo_path):
            subpackages = _node_subpackages(repo_path)
    else:
        return True, ""

    outputs: list[str] = []
    for command in commands:
        if not command:
            return False, t("commands.env.node_command_unknown")
        ok, output = _run_install_command(command, repo_path, config)
        if not ok:
            return False, output
        if output:
            outputs.append(output)
    for subpackage in subpackages:
        ok, output = _run_install_command(_node_install_command(subpackage), subpackage, config)
        if not ok:
            return False, f"{os.path.relpath(subpackage, repo_path)}: {output}"
        if output:
            outputs.append(output)
    return True, "\n".join(outputs)


def _run_install_command(command: list[str], repo_path: str, config: MeisterConfig) -> Tuple[bool, str]:
    # pnpm and npm use their shared global stores/caches by default; do not redirect them per worktree.
    try:
        result = subprocess.run(
            command,
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.environment.install_timeout_seconds,
            env=os.environ.copy(),
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, t(
            "commands.env.prepare_failed",
            command=" ".join(command),
            error=exc,
        )
    output = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else "")).strip()
    if result.returncode:
        return False, t(
            "commands.env.install_failed",
            command=" ".join(command),
            code=result.returncode,
            output=output,
        )
    return True, output
