"""Leitura dos logs de worker (`<run>_<task>_<tentativa>.log`).

Funções puras e sem efeito colateral sobre o estado do Meister: só leem arquivos.
Mensagens ao usuário ficam a cargo de quem chama; aqui os erros carregam um `kind`.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Iterator, Optional, Sequence

MIN_RUN_PREFIX = 6
_BLOCK_SIZE = 8192
_NAME_RE = re.compile(r"^([^_]+)_(.+)_(\d+)\.log$")


@dataclass(frozen=True)
class WorkerLog:
    path: str
    run_id: str
    task_id: str
    attempt: int
    size: int
    mtime: float


class LogSelectionError(Exception):
    """Falha ao escolher um log. `kind` é "short_prefix", "ambiguous" ou "not_found"."""

    def __init__(self, kind: str, run_id: Optional[str] = None, candidates: Sequence[str] = ()) -> None:
        super().__init__(kind)
        self.kind = kind
        self.run_id = run_id
        self.candidates = tuple(candidates)


def list_worker_logs(log_dir: str) -> list[WorkerLog]:
    """Lista os logs de worker do diretório, do mais recente para o mais antigo.

    Nomes que não seguem o padrão são ignorados; diretório inexistente devolve lista vazia.
    """
    try:
        entries = list(os.scandir(log_dir))
    except OSError:
        return []
    logs: list[WorkerLog] = []
    for entry in entries:
        match = _NAME_RE.match(entry.name)
        if match is None:
            continue
        try:
            if not entry.is_file():
                continue
            stat = entry.stat()
        except OSError:
            continue
        run_id, task_id, attempt = match.group(1), match.group(2), int(match.group(3))
        logs.append(
            WorkerLog(
                path=entry.path,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                size=stat.st_size,
                mtime=stat.st_mtime,
            )
        )
    logs.sort(key=lambda log: (log.mtime, log.path), reverse=True)
    return logs


def select_log(
    logs: Sequence[WorkerLog],
    run_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> WorkerLog:
    """Escolhe um log. `run_id` aceita prefixo de pelo menos 6 caracteres, como em `meister timeline`.

    Sem filtros devolve o de `mtime` mais recente. Com `task_id` (exato) restringe os candidatos.
    Levanta `LogSelectionError` em prefixo curto, prefixo ambíguo ou nada encontrado.
    """
    candidates = [log for log in logs if task_id is None or log.task_id == task_id]
    if run_id is not None:
        if len(run_id) < MIN_RUN_PREFIX:
            raise LogSelectionError("short_prefix", run_id=run_id)
        exact = [log for log in candidates if log.run_id == run_id]
        if exact:
            candidates = exact
        else:
            candidates = [log for log in candidates if log.run_id.startswith(run_id)]
            run_ids = sorted({log.run_id for log in candidates})
            if len(run_ids) > 1:
                raise LogSelectionError("ambiguous", run_id=run_id, candidates=run_ids)
    if not candidates:
        raise LogSelectionError("not_found", run_id=run_id)
    return max(candidates, key=lambda log: (log.mtime, log.path))


def read_tail(path: str, lines: int) -> list[str]:
    """Devolve as últimas `lines` linhas do arquivo, lendo apenas o seu final.

    Tolera arquivo sem quebra de linha final e arquivo vazio; remove o `\\r` de finais CRLF.
    """
    if lines <= 0:
        return []
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        if pos == 0:
            return []
        chunks: list[bytes] = []
        newlines = 0
        while pos > 0 and newlines <= lines:
            step = min(_BLOCK_SIZE, pos)
            pos -= step
            fh.seek(pos)
            block = fh.read(step)
            chunks.insert(0, block)
            newlines += block.count(b"\n")
    text = b"".join(chunks).decode("utf-8", errors="replace")
    if text.endswith("\n"):
        text = text[:-1]
    return [line.rstrip("\r") for line in text.split("\n")[-lines:]]


def follow(
    path: str,
    *,
    poll: float = 0.5,
    stop: Optional[Callable[[], bool]] = None,
) -> Iterator[str]:
    """Gerador que devolve as linhas acrescentadas ao arquivo a partir de agora.

    Espera o arquivo aparecer se ainda não existir (e o lê desde o início), reinicia do começo se ele for truncado
    e encerra quando `stop()` devolver verdadeiro.
    """
    should_stop = stop if stop is not None else (lambda: False)
    fh = None
    pending = ""
    appeared_later = False
    try:
        while not should_stop():
            if fh is None:
                try:
                    fh = open(path, "r", encoding="utf-8", errors="replace", newline="")
                except FileNotFoundError:
                    appeared_later = True
                    time.sleep(poll)
                    continue
                if not appeared_later:
                    fh.seek(0, os.SEEK_END)
            chunk = fh.read()
            if chunk:
                pending += chunk
                *complete, pending = pending.split("\n")
                for line in complete:
                    yield line.rstrip("\r")
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = None
            if size is not None and size < fh.tell():
                fh.seek(0)
                pending = ""
                continue
            time.sleep(poll)
    finally:
        if fh is not None:
            fh.close()
