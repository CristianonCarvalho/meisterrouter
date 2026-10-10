"""Harness de mentira para os testes e2e do host ``process``.

Cada CLI simulado (``codex``, ``copilot``...) é um wrapper (script de shell no POSIX,
``.cmd`` no Windows) que chama ``sys.executable`` com este módulo e um modo como primeiro
argumento, seguido dos argumentos que o MeisterRouter passaria ao CLI real:

- ``write``: grava no worktree o arquivo indicado por ``FAKE_WRITE=<nome>`` no prompt e sai com 0.
- ``silent``: sai com 1 sem gravar nada e sem imprimir resultado.
- ``quota``: imprime uma mensagem de cota esgotada e fica rodando até ser interrompido
  (limitado a ``_QUOTA_HOLD_SECONDS`` para não deixar processo órfão).
"""

from __future__ import annotations

import os
import re
import sys
import time

_WRITE_MARKER = re.compile(r"FAKE_WRITE=([\w./-]+)")
_QUOTA_MESSAGE = "Error: You exceeded your current quota, please check your plan and billing details."
_QUOTA_HOLD_SECONDS = 60.0


def _prompt_text(args: list[str]) -> str:
    return "\n".join(args)


def run(mode: str, args: list[str]) -> int:
    if mode == "write":
        match = _WRITE_MARKER.search(_prompt_text(args))
        if match is None:
            print("fake harness: prompt sem FAKE_WRITE=", file=sys.stderr)
            return 2
        name = match.group(1)
        with open(os.path.join(os.getcwd(), name), "w", encoding="utf-8") as handle:
            handle.write(f"written by fake harness: {name}\n")
        print(f"fake harness wrote {name}", flush=True)
        return 0
    if mode == "silent":
        return 1
    if mode == "quota":
        print(_QUOTA_MESSAGE, flush=True)
        deadline = time.monotonic() + _QUOTA_HOLD_SECONDS
        while time.monotonic() < deadline:
            time.sleep(0.05)
        return 1
    print(f"fake harness: modo desconhecido {mode!r}", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("uso: fake_harness.py <write|silent|quota> [args...]", file=sys.stderr)
        return 2
    return run(argv[1], argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
