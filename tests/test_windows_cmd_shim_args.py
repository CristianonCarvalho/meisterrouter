"""Integridade de argumentos ao executar um shim `.cmd` no Windows.

O `HarnessWorker` chama `subprocess.Popen` com uma lista de argumentos; quando o
harness é um `.cmd` (npm/shims), o `cmd.exe` reinterpreta essa linha. Estes
testes medem o que chega ao processo final. Casos que hoje corrompem o prompt
ficam `xfail` (não strict) para o CI do Windows registrar a corrupção sem ficar
vermelho.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="testa o caminho de shim .cmd do cmd.exe, só existe no Windows",
)

RECORDER_SOURCE = """\
import json
import sys

out_path = sys.argv[1]
with open(out_path, "w", encoding="utf-8") as fh:
    for arg in sys.argv[2:]:
        fh.write(json.dumps(arg) + "\\n")
"""


def _make_fake_cmd(tmp_path: Path) -> tuple[Path, Path]:
    recorder = tmp_path / "recorder.py"
    recorder.write_text(RECORDER_SOURCE, encoding="utf-8")
    out_file = tmp_path / "args.jsonl"
    shim = tmp_path / "fake.cmd"
    shim.write_text(
        f'@echo off\r\n"{sys.executable}" "{recorder}" "{out_file}" %*\r\n',
        encoding="utf-8",
    )
    return shim, out_file


def _run_shim(tmp_path: Path, prompt: str) -> List[str]:
    shim, out_file = _make_fake_cmd(tmp_path)
    process = subprocess.Popen(
        [str(shim), "-p", prompt],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
    )
    try:
        process.communicate(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    lines = out_file.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def _assert_prompt_intact(tmp_path: Path, prompt: str) -> None:
    received = _run_shim(tmp_path, prompt)
    assert received == ["-p", prompt]


def test_prompt_with_spaces(tmp_path: Path) -> None:
    _assert_prompt_intact(tmp_path, "corrija o bug do parser de datas agora")


def test_prompt_with_double_quotes(tmp_path: Path) -> None:
    _assert_prompt_intact(tmp_path, 'use "aspas duplas" no texto')


@pytest.mark.xfail(
    strict=False,
    reason='aspas duplas viram \\" (list2cmdline) e o cmd.exe alterna o estado de aspas',
)
def test_prompt_with_double_quotes_inside_cmd(tmp_path: Path) -> None:
    _assert_prompt_intact(tmp_path, 'diga "olá" e "tchau" & pare')


def test_prompt_with_single_quotes(tmp_path: Path) -> None:
    _assert_prompt_intact(tmp_path, "ajuste o 'nome' do arquivo")


@pytest.mark.xfail(
    strict=False,
    reason="%PATH% é expandido pelo cmd.exe antes de chegar ao processo final",
)
def test_prompt_with_percent_expansion(tmp_path: Path) -> None:
    _assert_prompt_intact(tmp_path, "o caminho é %PATH% no ambiente")


@pytest.mark.xfail(
    strict=False,
    reason="& | ^ < > sem aspas são operadores do cmd.exe e truncam a linha",
)
def test_prompt_with_cmd_metacharacters(tmp_path: Path) -> None:
    _assert_prompt_intact(tmp_path, "a & b | c ^ d < e > f")


@pytest.mark.xfail(
    strict=False,
    reason="quebra de linha encerra o comando no cmd.exe; o restante é perdido",
)
def test_prompt_with_newlines(tmp_path: Path) -> None:
    _assert_prompt_intact(tmp_path, "linha um\nlinha dois\nlinha três")


@pytest.mark.xfail(
    strict=False,
    reason="linha de comando do cmd.exe limitada a 8191 caracteres; prompt longo é truncado",
)
def test_prompt_with_20k_characters(tmp_path: Path) -> None:
    prompt = ("trecho de prompt longo " * 900)[:20000]
    assert len(prompt) == 20000
    _assert_prompt_intact(tmp_path, prompt)
