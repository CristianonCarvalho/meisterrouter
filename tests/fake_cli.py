"""CLIs de mentira executáveis em POSIX e Windows.

O corpo do CLI é código Python (rodado com ``sys.executable``), então não depende de
``bash``. O executável devolvido é um wrapper: script de shell sem extensão no POSIX e
``.cmd`` no Windows, que não executa scripts sem extensão (WinError 193).
"""

from __future__ import annotations

import shlex
import sys
import textwrap
from pathlib import Path


def make_fake_cli(directory: Path, name: str, body: str) -> Path:
    """Cria o CLI falso ``name`` em ``directory`` e devolve o caminho do executável.

    ``body`` é o código Python do CLI; os argumentos chegam em ``sys.argv[1:]``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / f"{name}_fake.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    if sys.platform == "win32":
        wrapper = directory / f"{name}.cmd"
        wrapper.write_text(
            f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="utf-8"
        )
    else:
        wrapper = directory / name
        wrapper.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return wrapper
