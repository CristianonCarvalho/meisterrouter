"""Gerenciamento de estado do servidor do painel e heartbeat por projeto."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from meister import osops


@dataclass
class ServerState:
    pid: int
    port: int
    url: str
    started_at: str
    last_seen: str


def format_iso_utc(dt: datetime) -> str:
    """Formata datetime em ISO 8601 UTC sem microssegundos excessivos e com sufixo Z."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _state_file_path(project_root: Union[str, Path]) -> Path:
    return Path(project_root) / ".meister" / "dashboard.json"


def read_state(project_root: Union[str, Path]) -> Optional[ServerState]:
    """Lê o estado do servidor gravado em `<project_root>/.meister/dashboard.json`.

    Retorna None se o arquivo estiver ausente, vazio, corrompido ou inválido.
    """
    target = _state_file_path(project_root)
    if not target.is_file():
        return None
    try:
        with open(target, "r", encoding="utf-8") as source:
            data = json.load(source)
        if not isinstance(data, dict):
            return None
        pid = int(data["pid"])
        port = int(data["port"])
        url = str(data["url"])
        started_at = str(data["started_at"])
        last_seen = str(data["last_seen"])
        return ServerState(
            pid=pid,
            port=port,
            url=url,
            started_at=started_at,
            last_seen=last_seen,
        )
    except Exception:
        return None


def write_state(project_root: Union[str, Path], state: ServerState) -> None:
    """Gravação atômica do estado do servidor via arquivo temporário e os.replace."""
    target = _state_file_path(project_root)
    meister_dir = target.parent
    meister_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(state), indent=2)

    tmp_path = target.with_name(f"dashboard.json.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def remove_state(project_root: Union[str, Path]) -> None:
    """Remove `<project_root>/.meister/dashboard.json` de forma idempotente."""
    target = _state_file_path(project_root)
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass


def is_alive(
    state: ServerState,
    *,
    now: datetime,
    max_age_s: float = 5.0,
) -> bool:
    """Verifica se o servidor está vivo baseado em `last_seen` recente e `url` respondendo `/api/meta`.

    Nunca confia apenas no PID para evitar decisões erradas com processos órfãos ou PIDs reciclados.
    """
    if state is None:
        return False

    # 1. Validação temporal de last_seen
    try:
        last_seen_str = state.last_seen.replace("Z", "+00:00")
        last_seen_dt = datetime.fromisoformat(last_seen_str)
        if last_seen_dt.tzinfo is None:
            last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return False

    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    age_s = (now_utc - last_seen_dt).total_seconds()
    if age_s > max_age_s or age_s < -max_age_s:
        return False

    # 2. Verificação do processo local
    if state.pid <= 0:
        return False
    if not osops.pid_alive(state.pid):
        return False

    # 3. Consulta ao endpoint /api/meta
    if not state.url:
        return False
    endpoint = f"{state.url.rstrip('/')}/api/meta"
    try:
        req = urllib.request.Request(endpoint)
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode("utf-8"))
            if not isinstance(data, dict):
                return False
    except Exception:
        return False

    return True
