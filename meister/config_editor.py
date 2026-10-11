"""Edicao das vias (workers.tier_order) do meister.config.yaml para a tela /config.

Logica pura, sem Flask. Le e grava o arquivo em round-trip (ruamel.yaml) para
preservar os comentarios do dono; a gravacao segue o padrao de meister/lane_toggle.py
(.bak antes, troca atomica por arquivo temporario e restauracao em falha).

O ruamel.yaml e importado sob demanda (_yaml): sem o pacote, o modulo ainda importa e
so as funcoes que precisam dele levantam ConfigEditorUnavailable.
"""

from __future__ import annotations

import copy
import hashlib
import io
import os
import stat
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

from meister.config import ConfigIssue, _default_config_data, _parse_config_dict, validate_config
from meister.harness_effort import HARNESS_EFFORT_VALUES
from meister.i18n import CATALOGS, t
from meister.lane_toggle import _restore_backup, _write_temp


EMPTY_STAMP = hashlib.sha256(b"").hexdigest()
EDITABLE_FIELDS = ("name", "harness", "model", "effort", "enabled", "max_parallel")
GENERATED_COMMENT = "tier_order gerado pela tela /config do dashboard"
NEW_FILE_MODE = 0o644

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
        "config_editor.stamp_mismatch": "The file changed on disk; reload before saving.",
        "config_editor.invalid_lanes": "The lanes are invalid: {details}",
        "config_editor.read_failed": "Cannot read configuration file {path}: {error}",
        "config_editor.not_mapping": "Configuration file {path} must contain a YAML mapping.",
        "config_editor.workers_not_mapping": "Configuration value workers in {path} must be a mapping.",
        "config_editor.write_failed": "Cannot safely write configuration file {path}: {error}",
        "config_editor.restore_failed": "{message}; could not restore the original file: {error}",
        "config_editor.unavailable": (
            "Editing the lanes requires the ruamel.yaml package; install it with: pip install ruamel.yaml"
        ),
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
        "config_editor.stamp_mismatch": "O arquivo mudou no disco; recarregue antes de salvar.",
        "config_editor.invalid_lanes": "As vias s\u00e3o inv\u00e1lidas: {details}",
        "config_editor.read_failed": "N\u00e3o foi poss\u00edvel ler o arquivo de configura\u00e7\u00e3o {path}: {error}",
        "config_editor.not_mapping": "O arquivo de configura\u00e7\u00e3o {path} deve conter um mapeamento YAML.",
        "config_editor.workers_not_mapping": "O valor workers em {path} deve ser um mapeamento.",
        "config_editor.write_failed": (
            "N\u00e3o foi poss\u00edvel gravar com seguran\u00e7a o arquivo de configura\u00e7\u00e3o {path}: {error}"
        ),
        "config_editor.restore_failed": "{message}; n\u00e3o foi poss\u00edvel restaurar o arquivo original: {error}",
        "config_editor.unavailable": (
            "A edi\u00e7\u00e3o das vias precisa do pacote ruamel.yaml; instale com: pip install ruamel.yaml"
        ),
    },
}
for _language, _messages in _MESSAGES.items():
    for _key, _value in _messages.items():
        CATALOGS[_language].setdefault(_key, _value)


class ConfigEditorError(Exception):
    """A edicao das vias nao pode ser aplicada com seguranca."""


class ConfigEditorUnavailable(ConfigEditorError):
    """O pacote ruamel.yaml nao esta instalado; a edicao das vias fica indisponivel."""


def _yaml() -> SimpleNamespace:
    """Importa o ruamel.yaml sob demanda; sem o pacote, levanta ConfigEditorUnavailable."""
    try:
        from ruamel.yaml import YAML
        from ruamel.yaml.comments import CommentedMap, CommentedSeq
        from ruamel.yaml.error import YAMLError
    except ImportError as exc:
        raise ConfigEditorUnavailable(t("config_editor.unavailable")) from exc
    return SimpleNamespace(YAML=YAML, CommentedMap=CommentedMap, CommentedSeq=CommentedSeq, YAMLError=YAMLError)


class ConfigStampError(ConfigEditorError):
    """O arquivo mudou no disco desde a leitura (carimbo diferente)."""


class ConfigValidationError(ConfigEditorError):
    """O pedido de edicao das vias nao passou na validacao."""

    def __init__(self, issues: List[ConfigIssue]):
        self.issues = issues
        details = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        super().__init__(t("config_editor.invalid_lanes", details=details))


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
    yaml = _yaml().YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
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


def _stamp(content: Optional[bytes]) -> str:
    return hashlib.sha256(content).hexdigest() if content is not None else EMPTY_STAMP


def load_effective_lanes(config_path: str) -> LaneDocument:
    """Devolve as vias efetivas: as do projeto se houver tier_order, senao as do padrao (inclusive desligadas)."""
    return _document_from_content(config_path, _read_bytes(config_path))


def _document_from_content(config_path: str, content: Optional[bytes]) -> LaneDocument:
    tier_order = _project_tier_order(content) if content else None
    if tier_order is not None:
        lanes = [_plain(lane) for lane in tier_order]
    else:
        lanes = list(_default_config_data()["workers"]["tier_order"])
    return LaneDocument(
        lanes=lanes,
        path=config_path,
        has_tier_order=tier_order is not None,
        stamp=_stamp(content),
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


def _read_checked(document: LaneDocument, expected_stamp: str) -> Optional[bytes]:
    """Le o arquivo uma vez e recusa se o carimbo atual diferir do esperado."""
    try:
        content = _read_bytes(document.path)
    except OSError as exc:
        raise ConfigEditorError(t("config_editor.read_failed", path=document.path, error=exc)) from exc
    if _stamp(content) != expected_stamp:
        raise ConfigStampError(t("config_editor.stamp_mismatch"))
    return content


def _load_round_trip(path: str, content: Optional[bytes]) -> CommentedMap:
    ruamel = _yaml()
    if not content:
        return ruamel.CommentedMap()
    try:
        data = _round_trip_yaml().load(content.decode("utf-8"))
    except (UnicodeError, ruamel.YAMLError) as exc:
        raise ConfigEditorError(t("config_editor.read_failed", path=path, error=exc)) from exc
    if data is None:
        return ruamel.CommentedMap()
    if not isinstance(data, ruamel.CommentedMap):
        raise ConfigEditorError(t("config_editor.not_mapping", path=path))
    return data


def _workers_map(path: str, data: CommentedMap, create: bool) -> Optional[CommentedMap]:
    commented_map = _yaml().CommentedMap
    workers = data.get("workers")
    if workers is None and create:
        workers = commented_map()
        data["workers"] = workers
    if workers is not None and not isinstance(workers, commented_map):
        raise ConfigEditorError(t("config_editor.workers_not_mapping", path=path))
    return workers


def _update_lane_node(node: CommentedMap, lane: Dict[str, Any]) -> None:
    """Grava os campos editaveis no no existente; os demais campos e comentarios ficam como estao."""
    for key in EDITABLE_FIELDS[1:]:
        value = lane.get(key)
        if value is None or (key == "enabled" and value is True and key not in node):
            node.pop(key, None)
        elif node.get(key) != value or type(node.get(key)) is not type(value):
            node[key] = value


def _new_lane_node(lane: Dict[str, Any]) -> CommentedMap:
    node = _yaml().CommentedMap()
    for key, value in lane.items():
        if (key in ("effort", "max_parallel") and value is None) or (key == "enabled" and value is True):
            continue
        node[key] = copy.deepcopy(value)
    return node


def _tier_order_seq(current: Any, merged: List[Dict[str, Any]]) -> CommentedSeq:
    ruamel = _yaml()
    nodes: Dict[Any, CommentedMap] = {}
    if isinstance(current, list):
        nodes = {node.get("name"): node for node in current if isinstance(node, ruamel.CommentedMap)}
    seq = ruamel.CommentedSeq()
    for lane in merged:
        node = nodes.get(lane["name"])
        if node is None:
            node = _new_lane_node(lane)
        else:
            _update_lane_node(node, lane)
        seq.append(node)
    return seq


def _serialize(data: CommentedMap) -> bytes:
    stream = io.StringIO()
    _round_trip_yaml().dump(data, stream)
    return stream.getvalue().encode("utf-8")


def _check_written(path: str, content: bytes) -> None:
    """Confere que o arquivo gravado e o esperado e que continua um YAML legivel."""
    written = _read_bytes(path)
    if written != content:
        raise ConfigEditorError(t("config_editor.write_failed", path=path, error="content mismatch"))
    _load_round_trip(path, written)


def _write_safely(path: str, original: Optional[bytes], serialized: bytes) -> None:
    """Grava com .bak antes e troca atomica; se algo falhar, devolve o arquivo e o .bak anteriores."""
    directory = os.path.dirname(os.path.abspath(path))
    mode = stat.S_IMODE(os.stat(path).st_mode) if original is not None else NEW_FILE_MODE
    backup_path = f"{path}.bak"
    backup_existed = os.path.exists(backup_path)
    previous_backup = _read_bytes(backup_path) if backup_existed else None

    config_temp: Optional[str] = None
    backup_temp: Optional[str] = None
    backup_installed = False
    config_replaced = False
    try:
        os.makedirs(directory, exist_ok=True)
        config_temp = _write_temp(directory, ".config-editor-config-", serialized, mode)
        if original is not None:
            backup_temp = _write_temp(directory, ".config-editor-backup-", original, mode)
            os.replace(backup_temp, backup_path)
            backup_temp = None
            backup_installed = True
        os.replace(config_temp, path)
        config_temp = None
        config_replaced = True
        _check_written(path, serialized)
    except Exception as exc:
        rollback_error: Optional[Exception] = None
        try:
            if config_replaced:
                if original is None:
                    os.unlink(path)
                else:
                    restore_temp = _write_temp(directory, ".config-editor-restore-", original, mode)
                    os.replace(restore_temp, path)
            if backup_installed:
                _restore_backup(backup_path, previous_backup, directory)
        except Exception as restore_exc:
            rollback_error = restore_exc
        for temporary in (config_temp, backup_temp):
            if temporary is not None and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        message = t("config_editor.write_failed", path=path, error=exc)
        if rollback_error is not None:
            message = t("config_editor.restore_failed", message=message, error=rollback_error)
        raise ConfigEditorError(message) from exc


def apply_lanes(document: LaneDocument, lanes: Any, expected_stamp: str) -> LaneDocument:
    """Grava workers.tier_order inteiro em round-trip, preservando comentarios e campos nao editaveis.

    Recusa com ConfigStampError se o arquivo mudou no disco e com ConfigValidationError se o pedido
    for invalido. Devolve o documento relido do disco, com o carimbo novo.
    """
    path = document.path
    content = _read_checked(document, expected_stamp)
    current = _document_from_content(path, content)
    issues = validate_lanes(current, lanes)
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        raise ConfigValidationError(errors)
    merged, _ = _merge_lanes(current, lanes)

    data = _load_round_trip(path, content)
    workers = _workers_map(path, data, create=True)
    assert workers is not None
    workers["tier_order"] = _tier_order_seq(workers.get("tier_order"), merged)
    if content is None or GENERATED_COMMENT.encode("utf-8") not in content:
        workers.yaml_set_comment_before_after_key("tier_order", before=GENERATED_COMMENT, indent=2)

    _write_safely(path, content, _serialize(data))
    return load_effective_lanes(path)


def restore_default_lanes(document: LaneDocument, expected_stamp: str) -> LaneDocument:
    """Remove workers.tier_order do arquivo (e workers, se ficar vazio) para voltar as vias do padrao."""
    path = document.path
    content = _read_checked(document, expected_stamp)
    data = _load_round_trip(path, content)
    workers = _workers_map(path, data, create=False)
    if workers is None or "tier_order" not in workers:
        return _document_from_content(path, content)
    del workers["tier_order"]
    if not workers:
        del data["workers"]

    _write_safely(path, content, _serialize(data))
    return load_effective_lanes(path)
