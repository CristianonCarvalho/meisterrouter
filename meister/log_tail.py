"""Leitura incremental e somente leitura do log de orquestração."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from meister.logger import DEFAULT_LOG_DIR, find_project_root

LOG_NAME = "orchestration_log.jsonl"


def resolve_log_file(log_dir: Optional[str] = None) -> str:
    """Resolve o caminho do log sem criar diretórios."""
    if log_dir:
        return os.path.join(os.path.abspath(log_dir), LOG_NAME)
    env_dir = os.environ.get("MEISTER_LOG_DIR")
    if env_dir:
        return os.path.join(os.path.abspath(env_dir), LOG_NAME)
    root = find_project_root()
    if root:
        return os.path.join(root, ".meister", "logs", LOG_NAME)
    return os.path.join(os.path.abspath(DEFAULT_LOG_DIR), LOG_NAME)


class LogTail:
    """Devolve eventos novos desde a última chamada sem escrever no arquivo."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.reset = False
        self._offset = 0
        self._partial = b""

    def poll(self) -> List[Dict[str, Any]]:
        self.reset = False
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self._offset:
            self._offset = 0
            self._partial = b""
            self.reset = True
        if size == self._offset:
            return []
        with open(self.path, "rb") as handle:
            handle.seek(self._offset)
            chunk = handle.read(size - self._offset)
        self._offset += len(chunk)
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()
        events: List[Dict[str, Any]] = []
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(event, dict):
                events.append(event)
        return events
