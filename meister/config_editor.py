"""Edicao das vias (workers.tier_order) do meister.config.yaml para a tela /config.

Logica pura, sem Flask. Le o arquivo em round-trip (ruamel.yaml) para que a
gravacao futura preserve os comentarios do dono.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ruamel.yaml import YAML

from meister.config import _default_config_data


EMPTY_STAMP = hashlib.sha256(b"").hexdigest()


@dataclass
class LaneEdit:
    """Campos editaveis de uma via; os demais sao preservados como estao."""

    name: str
    harness: str
    model: str
    effort: Optional[str] = None
    enabled: bool = True
    max_parallel: Optional[int] = None


@dataclass
class LaneDocument:
    lanes: List[Dict[str, Any]] = field(default_factory=list)
    path: str = ""
    has_tier_order: bool = False
    stamp: str = EMPTY_STAMP


def _round_trip_yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    return yaml


def _plain(value: Any) -> Any:
    """Converte os nos do ruamel (CommentedMap, CommentedSeq, escalares) em tipos simples."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    return value


def _read_bytes(config_path: str) -> Optional[bytes]:
    try:
        with open(config_path, "rb") as stream:
            return stream.read()
    except FileNotFoundError:
        return None


def _project_tier_order(content: bytes) -> Optional[list]:
    data = _round_trip_yaml().load(content.decode("utf-8"))
    if not isinstance(data, dict):
        return None
    workers = data.get("workers")
    if not isinstance(workers, dict):
        return None
    tier_order = workers.get("tier_order")
    return tier_order if isinstance(tier_order, list) else None


def load_effective_lanes(config_path: str) -> LaneDocument:
    """Devolve as vias efetivas: as do projeto se houver tier_order, senao as do padrao (inclusive desligadas)."""
    content = _read_bytes(config_path)
    tier_order = _project_tier_order(content) if content else None
    if tier_order is not None:
        lanes = [_plain(lane) for lane in tier_order]
    else:
        lanes = list(_default_config_data()["workers"]["tier_order"])
    return LaneDocument(
        lanes=lanes,
        path=config_path,
        has_tier_order=tier_order is not None,
        stamp=hashlib.sha256(content).hexdigest() if content is not None else EMPTY_STAMP,
    )
