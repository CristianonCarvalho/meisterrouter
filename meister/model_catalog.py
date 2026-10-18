"""Catálogo de modelos por harness, montado a partir de fontes independentes.

Cada fonte é lida à parte: uma fonte que falha vira ``unavailable`` e não derruba as outras.
A primeira fonte que traz um id vence; ids repetidos não aparecem duas vezes.
A fonte ``config`` (modelos já usados pelas vias do catálogo padrão e do config do projeto)
só entra quando as outras fontes do harness não trazem nenhum modelo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from meister.config import _all_default_worker_tiers, load_config


CACHE_TTL_SECONDS = 300.0

AGY_EXECUTABLE = "agy"
AGY_MODELS_TIMEOUT_SECONDS = 10.0

SOURCE_AGY_MODELS = "agy models"
SOURCE_CODEX_CACHE = "codex cache"
SOURCE_CLAUDE_ALIASES = "claude aliases"
SOURCE_COPILOT_CACHE = "copilot cache"
SOURCE_CONFIG = "config"

# Mesmo agrupamento de meister/harness_effort.py: nomes alternativos do mesmo harness.
_HARNESS_GROUPS: Dict[str, str] = {
    "copilot": "copilot",
    "github-copilot": "copilot",
    "claude": "claude",
    "agy": "agy",
    "antigravity": "agy",
    "codex": "codex",
    "kiro-cli": "kiro-cli",
    "kiro": "kiro-cli",
}

# Aliases aceitos pelo CLI claude (--help) e os ids completos dos modelos.
_CLAUDE_ALIASES: Tuple[str, ...] = (
    "haiku",
    "sonnet",
    "opus",
    "fable",
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-fable-5-1",
)

ModelEntry = Dict[str, Any]
SourceReader = Callable[[], Optional[List[ModelEntry]]]

_cache_lock = threading.Lock()
_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def clear_model_catalog_cache() -> None:
    """Descarta o resultado em cache de todos os harness."""
    with _cache_lock:
        _cache.clear()


def list_models(harness: str) -> Dict[str, Any]:
    """Lista os modelos de um harness, com a origem de cada fonte consultada.

    Retorna ``{"harness", "models", "sources"}``; cada modelo traz ``id``, ``label``,
    ``cost_per_m_tokens`` e ``source``. Harness desconhecido devolve listas vazias.
    """
    group = _HARNESS_GROUPS.get(harness.strip().lower())
    if group is None:
        return {"harness": harness, "models": [], "sources": []}

    cached = _cache_get(group)
    if cached is None:
        cached = _build_catalog(group)
        _cache_put(group, cached)

    return {
        "harness": harness,
        "models": [dict(model) for model in cached["models"]],
        "sources": [dict(source) for source in cached["sources"]],
    }


def _cache_get(group: str) -> Optional[Dict[str, Any]]:
    with _cache_lock:
        entry = _cache.get(group)
    if entry is None or time.monotonic() - entry[0] >= CACHE_TTL_SECONDS:
        return None
    return entry[1]


def _cache_put(group: str, catalog: Dict[str, Any]) -> None:
    with _cache_lock:
        _cache[group] = (time.monotonic(), catalog)


def _build_catalog(group: str) -> Dict[str, Any]:
    sources: List[Dict[str, Any]] = []
    models: List[ModelEntry] = []
    seen: Set[str] = set()

    def consume(name: str, found: Optional[List[ModelEntry]]) -> None:
        if found is None:
            status = "unavailable"
        else:
            status = "ok" if found else "empty"
        sources.append({"name": name, "status": status})
        for model in found or []:
            if model["id"] in seen:
                continue
            seen.add(model["id"])
            models.append(model)

    for name, reader in _fixed_sources(group):
        consume(name, reader())
    if not models:
        consume(SOURCE_CONFIG, _config_models(group))

    costs = _default_costs()
    for model in models:
        model["cost_per_m_tokens"] = costs.get(model["id"])

    return {"models": models, "sources": sources}


def _fixed_sources(group: str) -> List[Tuple[str, SourceReader]]:
    if group == "agy":
        return [(SOURCE_AGY_MODELS, _agy_models)]
    if group == "codex":
        return [(SOURCE_CODEX_CACHE, _codex_cache)]
    if group == "claude":
        return [(SOURCE_CLAUDE_ALIASES, _claude_aliases)]
    if group == "copilot":
        return [(SOURCE_COPILOT_CACHE, _copilot_cache)]
    return []


def _entry(model_id: str, label: Optional[str], source: str) -> ModelEntry:
    return {"id": model_id, "label": label, "cost_per_m_tokens": None, "source": source}


def _agy_models() -> Optional[List[ModelEntry]]:
    # Sem shell e só se o executável existir: nunca roda um comando que não foi encontrado no PATH.
    if shutil.which(AGY_EXECUTABLE) is None:
        return None
    try:
        result = subprocess.run(
            [AGY_EXECUTABLE, "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=AGY_MODELS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    models: List[ModelEntry] = []
    for line in result.stdout.splitlines():
        # Linhas válidas são "id<TAB>nome"; o aviso ("Fetching available models...") não tem tab.
        if "\t" not in line:
            continue
        model_id, _, name = line.partition("\t")
        model_id = model_id.strip()
        if not model_id or any(char.isspace() for char in model_id):
            continue
        models.append(_entry(model_id, name.strip() or None, SOURCE_AGY_MODELS))
    return models


def _codex_cache() -> Optional[List[ModelEntry]]:
    data = _read_json_file(os.path.join(os.path.expanduser("~"), ".codex", "models_cache.json"))
    items = data.get("models") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None

    models: List[ModelEntry] = []
    for item in items:
        if not isinstance(item, dict) or item.get("visibility") != "list":
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        display_name = item.get("display_name")
        label = display_name.strip() if isinstance(display_name, str) and display_name.strip() else None
        models.append(_entry(slug.strip(), label, SOURCE_CODEX_CACHE))
    return models


def _read_json_file(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _claude_aliases() -> Optional[List[ModelEntry]]:
    return [_entry(alias, None, SOURCE_CLAUDE_ALIASES) for alias in _CLAUDE_ALIASES]


def _copilot_cache() -> Optional[List[ModelEntry]]:
    data = _read_json_file(os.path.join(os.path.expanduser("~"), ".meister", "model_catalog", "copilot.json"))
    items = data.get("models") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None

    models: List[ModelEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        name = item.get("name")
        label = name.strip() if isinstance(name, str) and name.strip() else None
        models.append(_entry(model_id.strip(), label, SOURCE_COPILOT_CACHE))
    return models


def _config_models(group: str) -> Optional[List[ModelEntry]]:
    try:
        project_tiers = load_config().workers.tier_order
    except Exception:  # config do projeto inválido: a fonte fica indisponível
        return None

    default_tiers = _all_default_worker_tiers()
    tiers = [tier for tier in default_tiers + list(project_tiers) if _HARNESS_GROUPS.get(tier.harness) == group]
    return [_entry(tier.model, None, SOURCE_CONFIG) for tier in tiers if tier.model]


def _default_costs() -> Dict[str, float]:
    """Custo por 1M tokens de cada modelo do catálogo padrão (o primeiro vence)."""
    costs: Dict[str, float] = {}
    for tier in _all_default_worker_tiers():
        if tier.model and tier.model not in costs:
            costs[tier.model] = tier.cost_per_m_tokens
    return costs
