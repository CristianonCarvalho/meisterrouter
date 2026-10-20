"""Registro de projetos do dashboard (~/.meister/projects.json).

Cada projeto é identificado pelos 12 primeiros caracteres hexadecimais do SHA-1 do caminho real.
A página e a API só usam esse identificador: um caminho vindo da rede nunca vira destino de leitura
ou gravação, só os caminhos já registrados.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

REGISTRY_VERSION = 1
PROJECT_MARKERS = (".meister", "meister.config.yaml", ".git")
DISCOVERY_MARKERS = (".meister", "meister.config.yaml")
DISCOVERY_IGNORED = frozenset(
    {
        "node_modules",
        ".git",
        ".venv",
        "venv",
        "dist",
        "build",
        "__pycache__",
        "worktrees",
    }
)


class ProjectError(Exception):
    """Caminho recusado; `code` é o motivo (not_absolute, not_a_directory, not_a_project, forbidden)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def projects_file() -> str:
    return os.path.join(os.path.expanduser("~"), ".meister", "projects.json")


def project_id(path: str) -> str:
    return hashlib.sha1(os.path.realpath(path).encode("utf-8")).hexdigest()[:12]


def validate_project_path(path: str) -> str:
    if not isinstance(path, str) or not os.path.isabs(path):
        raise ProjectError("not_absolute")
    if not os.path.isdir(path):
        raise ProjectError("not_a_directory")
    real = os.path.realpath(path)
    home = os.path.realpath(os.path.expanduser("~"))
    if real == os.path.dirname(real) or real == home:
        raise ProjectError("forbidden")
    if not any(os.path.exists(os.path.join(real, marker)) for marker in PROJECT_MARKERS):
        raise ProjectError("not_a_project")
    return real


def list_projects() -> List[Dict[str, Any]]:
    return [_public(entry) for entry in _read_registry()]


def get_project(project_id_value: str) -> Optional[Dict[str, Any]]:
    for entry in _read_registry():
        if project_id(entry["path"]) == project_id_value:
            return _public(entry)
    return None


def register_project(path: str) -> Dict[str, Any]:
    return register_projects([path])[0]


def register_projects(paths: Iterable[str]) -> List[Dict[str, Any]]:
    """Valida todos os caminhos antes de gravar; um caminho recusado não grava nada."""
    reals = [validate_project_path(path) for path in paths]
    entries = _read_registry()
    now = _now()
    by_id = {project_id(entry["path"]): entry for entry in entries}
    touched: Dict[str, Dict[str, Any]] = {}
    for real in reals:
        identifier = project_id(real)
        entry = by_id.get(identifier)
        if entry is None:
            entry = {"path": real, "name": os.path.basename(real), "last_seen": now}
            entries.append(entry)
            by_id[identifier] = entry
        else:
            entry["last_seen"] = now
        touched[identifier] = entry
    _write_registry(entries)
    return [_public(entry) for entry in touched.values()]


def discover_projects(
    base_dir: str,
    max_depth: int = 3,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Procura projetos (com .meister/ ou meister.config.yaml) até `max_depth` níveis abaixo da base.

    Não segue links simbólicos e pula as pastas de DISCOVERY_IGNORED. Cada resultado indica se o
    projeto já está registrado.
    """
    if not isinstance(base_dir, str) or not os.path.isabs(base_dir):
        raise ProjectError("not_absolute")
    if not os.path.isdir(base_dir):
        raise ProjectError("not_a_directory")
    root = os.path.realpath(base_dir)
    registered = {project_id(entry["path"]) for entry in _read_registry()}
    found: List[Dict[str, Any]] = []
    queue: deque = deque([(root, 0)])
    while queue and len(found) < limit:
        current, depth = queue.popleft()
        if any(os.path.exists(os.path.join(current, marker)) for marker in DISCOVERY_MARKERS):
            identifier = project_id(current)
            found.append(
                {
                    "id": identifier,
                    "name": os.path.basename(current),
                    "path": current,
                    "registered": identifier in registered,
                }
            )
        if depth >= max_depth:
            continue
        try:
            with os.scandir(current) as children:
                subdirs = sorted(
                    child.path
                    for child in children
                    if child.name not in DISCOVERY_IGNORED
                    and child.is_dir(follow_symlinks=False)
                )
        except OSError:
            continue
        queue.extend((subdir, depth + 1) for subdir in subdirs)
    return found[:limit]


def _public(entry: Dict[str, Any]) -> Dict[str, Any]:
    path = entry["path"]
    return {
        "id": project_id(path),
        "name": entry.get("name") or os.path.basename(path),
        "path": path,
        "exists": os.path.isdir(path),
        "last_seen": entry.get("last_seen"),
    }


def _read_registry() -> List[Dict[str, Any]]:
    try:
        with open(projects_file(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    raw = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry["path"]]


def _write_registry(entries: List[Dict[str, Any]]) -> None:
    target = projects_file()
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    payload = json.dumps({"version": REGISTRY_VERSION, "projects": entries}, ensure_ascii=False, indent=2)
    descriptor, temp_path = tempfile.mkstemp(prefix=".projects-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
