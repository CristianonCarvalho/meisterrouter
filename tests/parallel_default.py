"""Decide se a suíte inteira deve rodar em paralelo sozinha (pytest-xdist), sem quebrar quem não o tem."""

import os
from typing import Mapping, Optional, Sequence

ENV_VAR = "MEISTER_TEST_PARALLEL"


def default_numprocesses(
    args: Sequence[str],
    numprocesses: object,
    xdist_available: bool,
    env: Mapping[str, str],
) -> Optional[str]:
    """Devolve o valor para `--numprocesses` ("auto" ou um número) ou None para manter a execução em série.

    Liga o paralelismo só quando TUDO isto vale:
    - o xdist está carregado (instalado e não bloqueado por `-p no:xdist`);
    - ninguém escolheu `-n`/`--numprocesses` (inclusive `-n0`, que pede série);
    - não somos um worker do próprio xdist;
    - a rodada é a suíte inteira (só diretórios nos argumentos: escolher arquivos ou testes já é rápido
      e o xdist custaria alguns segundos de partida);
    - `MEISTER_TEST_PARALLEL` não é `0`/`off` (também aceita um número de processos).
    """
    if not xdist_available or numprocesses is not None or env.get("PYTEST_XDIST_WORKER"):
        return None
    if args and not all(os.path.isdir(arg) for arg in args):
        return None
    choice = (env.get(ENV_VAR) or "auto").strip().lower()
    if choice in {"0", "off", "no", "false"}:
        return None
    return choice if choice.isdigit() else "auto"
