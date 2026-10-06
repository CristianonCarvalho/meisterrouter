"""Shared project environment preparation for gates and worktrees."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Tuple

from meister.config import MeisterConfig


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


def prepare_environment(path: str, config: MeisterConfig) -> Tuple[bool, str]:
    """Install the project's configured environment, or do nothing if not applicable."""
    if not config.environment.install_dependencies:
        return True, ""

    repo_path = os.path.abspath(path)
    if config.gate.install:
        command = shlex.split(config.gate.install)
    elif os.path.isfile(os.path.join(repo_path, "package.json")):
        if not _node_install_is_needed(repo_path):
            # nada a instalar (ex.: o wrapper npm do próprio MeisterRouter): `npm install` só geraria um
            # package-lock.json solto em cada worktree, que depois quebra o merge ("untracked files would be overwritten")
            return True, ""
        command = _node_install_command(repo_path)
    else:
        return True, ""
    if not command:
        return False, "Não foi possível determinar o comando de instalação Node"

    # pnpm and npm use their shared global stores/caches by default; do not redirect them per worktree.
    try:
        result = subprocess.run(
            command,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=config.environment.install_timeout_seconds,
            env=os.environ.copy(),
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Falha ao preparar ambiente com {' '.join(command)}: {exc}"
    output = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else "")).strip()
    if result.returncode:
        return False, f"Instalação {' '.join(command)} falhou (rc={result.returncode}): {output}"
    return True, output
