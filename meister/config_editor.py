"""Edicao das vias (workers.tier_order) do meister.config.yaml para a tela /config.

Logica pura, sem Flask. Le o arquivo em round-trip (ruamel.yaml) para que a
gravacao futura preserve os comentarios do dono.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ruamel.yaml import YAML

from meister.config import ConfigIssue, _default_config_data, _parse_config_dict, validate_config
from meister.harness_effort import HARNESS_EFFORT_VALUES
from meister.i18n import CATALOGS, t


EMPTY_STAMP = hashlib.sha256(b"").hexdigest()
EDITABLE_FIELDS = ("name", "harness", "model", "effort", "enabled", "max_parallel")

# Mensagens da tela /config; registradas nos catalogos do i18n ao importar este modulo.
_MESSAGES: Dict[str, Dict[str, str]] = {
    "en": {
        "config_editor.lanes_list": "lanes must be a list of objects.",
        "config_editor.lane_object": "each lane must be an object.",
        "config_editor.field_not_editable": "Field '{field}' cannot be edited here.",
        "config_editor.missing_name": "Each lane needs its name.",
        "config_editor.unknown_lane": "Unknown lane '{lane}'; lanes cannot be created here.",
        "config_editor.duplicate_lane": "Lane '{lane}' appears more than once.",
        "config_editor.missing_lanes": "Missing lane(s): {lanes}; lanes cannot be removed here.",
        "config_editor.invalid_harness": "Harness '{harness}' is invalid. Accepted values: {values}",
        "config_editor.invalid_effort": (
            "Effort '{effort}' is invalid for harness '{harness}'. Accepted values: {values}"
        ),
        "config_editor.empty_model": "Model cannot be empty.",
        "config_editor.enabled_boolean": "enabled must be true or false.",
        "config_editor.max_parallel": "max_parallel must be an integer >= 1 or empty.",
        "config_editor.no_enabled_lane": "At least one lane must be enabled.",
    },
    "pt-BR": {
        "config_editor.lanes_list": "lanes deve ser uma lista de objetos.",
        "config_editor.lane_object": "cada via deve ser um objeto.",
        "config_editor.field_not_editable": "O campo '{field}' n\u00e3o pode ser editado aqui.",
        "config_editor.missing_name": "Cada via precisa do seu nome.",
        "config_editor.unknown_lane": "Via desconhecida '{lane}'; n\u00e3o se criam vias aqui.",
        "config_editor.duplicate_lane": "A via '{lane}' aparece mais de uma vez.",
        "config_editor.missing_lanes": "Via(s) ausente(s): {lanes}; n\u00e3o se removem vias aqui.",
        "config_editor.invalid_harness": "Harness '{harness}' inv\u00e1lido. Valores aceitos: {values}",
        "config_editor.invalid_effort": (
            "Esfor\u00e7o '{effort}' inv\u00e1lido para o harness '{harness}'. Valores aceitos: {values}"
        ),
        "config_editor.empty_model": "O modelo n\u00e3o pode ficar vazio.",
        "config_editor.enabled_boolean": "enabled deve ser true ou false.",
        "config_editor.max_parallel": "max_parallel deve ser um inteiro >= 1 ou vazio.",
        "config_editor.no_enabled_lane": "Pelo menos uma via deve ficar ligada.",
    },
}
for _language, _messages in _MESSAGES.items():
    for _key, _value in _messages.items():
        CATALOGS[_language].setdefault(_key, _value)


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


def _project_data(config_path: str) -> dict:
    content = _read_bytes(config_path)
    data = _plain(_round_trip_yaml().load(content.decode("utf-8"))) if content else None
    return data if isinstance(data, dict) else {}


def _lane_field_issues(index: int, current: Dict[str, Any]) -> List[ConfigIssue]:
    """Regras proprias de uma via ja mesclada (current + pedido)."""
    issues: List[ConfigIssue] = []
    prefix = f"lanes[{index}]"
    harness = current.get("harness")
    if harness not in HARNESS_EFFORT_VALUES:
        issues.append(
            ConfigIssue(
                "error",
                f"{prefix}.harness",
                t("config_editor.invalid_harness", harness=harness, values=", ".join(HARNESS_EFFORT_VALUES)),
            )
        )
    else:
        effort = current.get("effort")
        accepted = HARNESS_EFFORT_VALUES[harness]
        if effort is not None and effort not in accepted:
            issues.append(
                ConfigIssue(
                    "error",
                    f"{prefix}.effort",
                    t("config_editor.invalid_effort", effort=effort, harness=harness, values=", ".join(accepted)),
                )
            )
    model = current.get("model")
    if not isinstance(model, str) or not model.strip():
        issues.append(ConfigIssue("error", f"{prefix}.model", t("config_editor.empty_model")))
    if not isinstance(current.get("enabled", True), bool):
        issues.append(ConfigIssue("error", f"{prefix}.enabled", t("config_editor.enabled_boolean")))
    max_parallel = current.get("max_parallel")
    if max_parallel is not None and (
        isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or max_parallel < 1
    ):
        issues.append(ConfigIssue("error", f"{prefix}.max_parallel", t("config_editor.max_parallel")))
    return issues


def _merge_lanes(document: LaneDocument, lanes: Any) -> tuple[List[Dict[str, Any]], List[ConfigIssue]]:
    """Aplica o pedido sobre as vias do documento, preservando os campos nao editaveis."""
    if not isinstance(lanes, list):
        return [], [ConfigIssue("error", "lanes", t("config_editor.lanes_list"))]
    existing = {lane.get("name"): lane for lane in document.lanes}
    merged: List[Dict[str, Any]] = []
    issues: List[ConfigIssue] = []
    seen: set = set()
    for index, raw in enumerate(lanes):
        prefix = f"lanes[{index}]"
        request = asdict(raw) if isinstance(raw, LaneEdit) else raw
        if not isinstance(request, dict):
            issues.append(ConfigIssue("error", prefix, t("config_editor.lane_object")))
            continue
        extra = [key for key in request if key not in EDITABLE_FIELDS]
        for key in extra:
            issues.append(ConfigIssue("error", f"{prefix}.{key}", t("config_editor.field_not_editable", field=key)))
        name = request.get("name")
        if not isinstance(name, str) or not name:
            issues.append(ConfigIssue("error", f"{prefix}.name", t("config_editor.missing_name")))
            continue
        if name not in existing:
            issues.append(ConfigIssue("error", f"{prefix}.name", t("config_editor.unknown_lane", lane=name)))
            continue
        if name in seen:
            issues.append(ConfigIssue("error", f"{prefix}.name", t("config_editor.duplicate_lane", lane=name)))
            continue
        seen.add(name)
        current = copy.deepcopy(existing[name])
        current.update({key: value for key, value in request.items() if key in EDITABLE_FIELDS})
        if not extra:
            issues.extend(_lane_field_issues(index, current))
        merged.append(current)
    missing = [name for name in existing if name not in seen]
    if missing and not issues:
        issues.append(
            ConfigIssue("error", "lanes", t("config_editor.missing_lanes", lanes=", ".join(map(str, missing))))
        )
    if not issues and not any(lane.get("enabled", True) is True for lane in merged):
        issues.append(ConfigIssue("error", "lanes", t("config_editor.no_enabled_lane")))
    return merged, issues


def validate_lanes(document: LaneDocument, lanes: Any) -> List[ConfigIssue]:
    """Valida o pedido de edicao das vias sobre uma copia em memoria do config; nada e gravado.

    Primeiro as regras proprias (campos editaveis, nomes, harness, esforco, modelo, max_parallel,
    pelo menos uma via ligada); se passarem, o documento resultante passa por _parse_config_dict e
    validate_config. Devolve erros e avisos.
    """
    merged, issues = _merge_lanes(document, lanes)
    if issues:
        return issues
    data = _project_data(document.path) if document.path else {}
    workers = data.get("workers")
    data["workers"] = dict(workers) if isinstance(workers, dict) else {}
    data["workers"]["tier_order"] = merged
    return validate_config(_parse_config_dict(data))
