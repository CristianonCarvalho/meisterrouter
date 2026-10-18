"""Identificador do código do servidor meister."""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path

import meister


def _resolve_code_id() -> str:
    version = meister.__version__
    try:
        package_dir = Path(__file__).resolve().parent
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=package_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1.0,
        )
        if proc.returncode == 0:
            commit = proc.stdout.strip()
            if commit:
                return f"{version}+{commit}"
    except (OSError, subprocess.SubprocessError):
        pass
    return version


@functools.lru_cache(maxsize=1)
def current_code_id() -> str:
    """Retorna o identificador de código atual do pacote meister.

    Formato: `<versão>+<commit_curto>` em checkout git ou `<versão>` sem git.
    O resultado é mantido em cache de processo.
    """
    return _resolve_code_id()
