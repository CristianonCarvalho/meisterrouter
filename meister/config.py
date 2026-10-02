"""Declarative Configuration System for MeisterRouter.

Loads and validates meister.config.yaml, providing typed dataclasses
for master decision model, architect model, worker tier progression,
and concurrency constraints.
"""

from __future__ import annotations

import os
import shutil
import copy
import math
from functools import lru_cache
from importlib.resources import files
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, List, Set, Union
import yaml


VALID_ARCHITECT_EFFORTS: Set[str] = {"low", "medium", "high", "xhigh", "max"}
KNOWN_HARNESSES: Set[str] = {
    "codex", "agy", "antigravity", "claude", "copilot", "github-copilot"
}


@lru_cache(maxsize=1)
def _cached_default_config() -> dict:
    resource = files("meister").joinpath("default_config.yaml")
    with resource.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def _default_config_data() -> dict:
    return copy.deepcopy(_cached_default_config())


def _default_section(section: str) -> dict:
    return _default_config_data().get(section, {})


@dataclass
class ConfigIssue:
    level: str  # "error" | "warning" | "info"
    path: str
    message: str


def ensure_meister_dir(root_or_cwd: str) -> str:
    """Garante que o diretório .meister existe com .gitignore contendo '*' para nunca poluir o git (Achado #4, E2E-4)."""
    m_dir = os.path.join(root_or_cwd, ".meister")
    os.makedirs(m_dir, exist_ok=True)
    gi = os.path.join(m_dir, ".gitignore")
    if not os.path.exists(gi):
        try:
            with open(gi, "w", encoding="utf-8") as f:
                f.write("*\n")
        except Exception:
            pass
    return m_dir


@dataclass
class MasterConfig:
    provider: str = field(default_factory=lambda: _default_section("master")["provider"])
    model: str = field(default_factory=lambda: _default_section("master")["model"])
    temperature: float = field(default_factory=lambda: _default_section("master")["temperature"])
    api_key_env: str = field(default_factory=lambda: _default_section("master")["api_key_env"])


@dataclass
class RouterConfig:
    mode: str = field(default_factory=lambda: _default_section("router")["mode"])
    timeout_seconds: float = field(default_factory=lambda: float(_default_section("router")["timeout_seconds"]))
    max_attempts: int = field(default_factory=lambda: _default_section("router")["max_attempts"])
    unavailable_cooldown_seconds: float = field(
        default_factory=lambda: float(_default_section("router")["unavailable_cooldown_seconds"])
    )


@dataclass
class ArchitectConfig:
    harness: str = field(default_factory=lambda: _default_section("architect")["harness"])
    model: str = field(default_factory=lambda: _default_section("architect")["model"])
    prompt_template: str = field(default_factory=lambda: _default_section("architect")["prompt_template"])
    effort: str = field(default_factory=lambda: _default_section("architect")["effort"])


@dataclass
class WorkerTier:
    name: str
    harness: str = field(default_factory=lambda: _default_worker_tiers()[0].harness)
    model: str = ""
    cost_per_m_tokens: float = 0.0
    max_retries: int = 2
    best_for: List[str] = field(default_factory=list)
    enabled: bool = True
    max_parallel: Optional[int] = None


def _all_default_worker_tiers() -> List[WorkerTier]:
    return [
        WorkerTier(
            name=_as_str(item.get("name")),
            harness=_as_str(item.get("harness")),
            model=_as_str(item.get("model")),
            cost_per_m_tokens=float(item.get("cost_per_m_tokens", 0.0)),
            max_retries=int(item.get("max_retries", 2)),
            best_for=list(item.get("best_for", [])),
            enabled=bool(item.get("enabled", True)),
            max_parallel=item.get("max_parallel"),
        )
        for item in _default_config_data()["workers"]["tier_order"]
    ]


def _default_worker_tiers() -> List[WorkerTier]:
    """Vias ligadas do arquivo padrao (as com enabled: false ficam em disabled)."""
    return [tier for tier in _all_default_worker_tiers() if tier.enabled]


def _default_disabled_worker_tiers() -> List[WorkerTier]:
    return [tier for tier in _all_default_worker_tiers() if not tier.enabled]


@dataclass
class WorkersConfig:
    tier_order: List[WorkerTier] = field(default_factory=_default_worker_tiers)
    disabled: List[WorkerTier] = field(default_factory=_default_disabled_worker_tiers)


@dataclass
class ConcurrencyConfig:
    parallel_tasks: bool = field(default_factory=lambda: _default_section("concurrency")["parallel_tasks"])
    max_parallel_workers: int = field(default_factory=lambda: _default_section("concurrency")["max_parallel_workers"])
    layout_strategy: str = field(default_factory=lambda: _default_section("concurrency")["layout_strategy"])
    isolation_mode: str = field(default_factory=lambda: _default_section("concurrency")["isolation_mode"])


@dataclass
class ScopeConfig:
    tolerated_files: List[str] = field(
        default_factory=lambda: list(_default_section("scope")["tolerated_files"])
    )


@dataclass
class EnvironmentConfig:
    install_dependencies: bool = field(
        default_factory=lambda: _default_section("environment")["install_dependencies"]
    )
    install_timeout_seconds: float = field(
        default_factory=lambda: float(_default_section("environment")["install_timeout_seconds"])
    )


@dataclass
class GateCommand:
    name: str
    run: Union[str, List[str]]
    timeout_seconds: float
    required: bool


@dataclass
class GateConfig:
    install: Optional[str] = field(default_factory=lambda: _default_section("gate")["install"])
    commands: List[GateCommand] = field(default_factory=list)
    allow_unverified: bool = field(default_factory=lambda: _default_section("gate")["allow_unverified"])


@dataclass
class MeisterConfig:
    version: str = field(default_factory=lambda: _default_config_data()["version"])
    master: MasterConfig = field(default_factory=MasterConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    architect: ArchitectConfig = field(default_factory=ArchitectConfig)
    workers: WorkersConfig = field(default_factory=WorkersConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    scope: ScopeConfig = field(default_factory=ScopeConfig)
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)
    gate: GateConfig = field(default_factory=GateConfig)
    config_source: str = "padrao (meister/default_config.yaml)"
    _parse_issues: List[ConfigIssue] = field(default_factory=list)


def _as_str(value: object, default: str = "") -> str:
    return default if value is None else str(value)


def _merge_config(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _parse_config_dict(data: dict) -> MeisterConfig:
    data = _merge_config(_default_config_data(), data if isinstance(data, dict) else {})

    version = str(data.get("version", "1.0"))
    parse_issues: List[ConfigIssue] = []

    # Master
    master_data = data["master"]
    master = MasterConfig(
        provider=master_data["provider"],
        model=master_data["model"],
        temperature=float(master_data["temperature"]),
        api_key_env=master_data["api_key_env"],
    )

    # Router
    router_data = data["router"]
    if isinstance(router_data, dict):
        router_values = router_data
    else:
        router_values = {"mode": router_data}

    default_router = _default_section("router")

    def router_number(key: str, valid_type: Any) -> Any:
        value = router_values.get(key, default_router[key])
        path = f"router.{key}"
        valid = (
            isinstance(value, valid_type)
            and not isinstance(value, bool)
            and (valid_type is int or math.isfinite(value))
        )
        if not valid:
            parse_issues.append(
                ConfigIssue(
                    level="error",
                    path=path,
                    message=f"Campo '{key}' tem tipo inválido: {value!r}",
                )
            )
            return default_router[key]
        return value

    timeout_seconds = router_number("timeout_seconds", (int, float))
    max_attempts = router_number("max_attempts", int)
    unavailable_cooldown_seconds = router_number("unavailable_cooldown_seconds", (int, float))
    router = RouterConfig(
        mode=_as_str(router_values.get("mode")),
        timeout_seconds=float(timeout_seconds),
        max_attempts=max_attempts,
        unavailable_cooldown_seconds=float(unavailable_cooldown_seconds),
    )

    # Architect
    architect_data = data["architect"]
    raw_effort = architect_data.get("effort")
    effort_val = str(raw_effort)
    architect = ArchitectConfig(
        harness=architect_data["harness"],
        model=architect_data["model"],
        prompt_template=architect_data["prompt_template"],
        effort=effort_val,
    )

    # Workers
    workers_data = data["workers"]
    if isinstance(workers_data, dict) and "tier_order" in workers_data:
        raw_tiers = workers_data["tier_order"] or []
        tier_list: List[WorkerTier] = []
        disabled_list: List[WorkerTier] = []
        for i, tier in enumerate(raw_tiers):
            if isinstance(tier, dict):
                raw_enabled = tier.get("enabled", True)
                raw_max_parallel = tier.get("max_parallel")
                if raw_max_parallel is not None and (
                    isinstance(raw_max_parallel, bool) or not isinstance(raw_max_parallel, int)
                ):
                    parse_issues.append(
                        ConfigIssue(
                            level="error",
                            path=f"workers.tier_order[{i}].max_parallel",
                            message=(
                                "Campo 'max_parallel' deve ser um inteiro >= 1 (booleanos não são aceitos), "
                                f"recebido: {raw_max_parallel!r}"
                            ),
                        )
                    )
                if not isinstance(raw_enabled, bool):
                    parse_issues.append(
                        ConfigIssue(
                            level="error",
                            path=f"workers.tier_order[{i}].enabled",
                            message=f"Campo 'enabled' deve ser booleano (true/false), recebido: {raw_enabled!r}",
                        )
                    )
                    enabled_val = bool(raw_enabled)
                else:
                    enabled_val = raw_enabled

                tier_obj = WorkerTier(
                    name=_as_str(tier.get("name"), ""),
                    harness=_as_str(tier.get("harness"), ""),
                    model=_as_str(tier.get("model"), ""),
                    cost_per_m_tokens=float(tier.get("cost_per_m_tokens", 0.0)),
                    max_retries=int(tier.get("max_retries", 2)),
                    best_for=list(tier.get("best_for", [])),
                    enabled=enabled_val,
                    max_parallel=raw_max_parallel,
                )
                if enabled_val:
                    tier_list.append(tier_obj)
                else:
                    disabled_list.append(tier_obj)
            elif isinstance(tier, WorkerTier):
                if not isinstance(tier.enabled, bool):
                    parse_issues.append(
                        ConfigIssue(
                            level="error",
                            path=f"workers.tier_order[{i}].enabled",
                            message=f"Campo 'enabled' deve ser booleano (true/false), recebido: {tier.enabled!r}",
                        )
                    )
                if tier.enabled:
                    tier_list.append(tier)
                else:
                    disabled_list.append(tier)
        workers = WorkersConfig(tier_order=tier_list, disabled=disabled_list)
    else:
        workers = WorkersConfig()

    # Concurrency
    concurrency_data = data["concurrency"]
    concurrency = ConcurrencyConfig(
        parallel_tasks=concurrency_data["parallel_tasks"],
        max_parallel_workers=int(concurrency_data["max_parallel_workers"]),
        layout_strategy=concurrency_data["layout_strategy"],
        isolation_mode=concurrency_data["isolation_mode"],
    )

    scope_data = data.get("scope", {})
    if not isinstance(scope_data, dict):
        parse_issues.append(ConfigIssue("error", "scope", "scope deve ser um objeto"))
        scope_data = {}
    tolerated_files = scope_data.get("tolerated_files", _default_section("scope")["tolerated_files"])
    if not isinstance(tolerated_files, list) or any(not isinstance(item, str) for item in tolerated_files):
        parse_issues.append(ConfigIssue("error", "scope.tolerated_files", "deve ser uma lista de strings"))
        tolerated_files = _default_section("scope")["tolerated_files"]
    scope = ScopeConfig(tolerated_files=list(tolerated_files))

    environment_data = data.get("environment", {})
    if not isinstance(environment_data, dict):
        parse_issues.append(ConfigIssue("error", "environment", "environment deve ser um objeto"))
        environment_data = {}
    install_dependencies = environment_data.get(
        "install_dependencies", _default_section("environment")["install_dependencies"]
    )
    if not isinstance(install_dependencies, bool):
        parse_issues.append(
            ConfigIssue("error", "environment.install_dependencies", "deve ser booleano")
        )
        install_dependencies = _default_section("environment")["install_dependencies"]
    install_timeout = environment_data.get(
        "install_timeout_seconds", _default_section("environment")["install_timeout_seconds"]
    )
    if (
        isinstance(install_timeout, bool)
        or not isinstance(install_timeout, (int, float))
        or not math.isfinite(install_timeout)
        or install_timeout <= 0
    ):
        parse_issues.append(
            ConfigIssue("error", "environment.install_timeout_seconds", "deve ser número > 0")
        )
        install_timeout = _default_section("environment")["install_timeout_seconds"]
    environment = EnvironmentConfig(
        install_dependencies=install_dependencies,
        install_timeout_seconds=float(install_timeout),
    )

    gate_data = data.get("gate", {})
    if not isinstance(gate_data, dict):
        parse_issues.append(ConfigIssue("error", "gate", "gate deve ser um objeto"))
        gate_data = {}
    gate_defaults = _default_section("gate")
    install = gate_data.get("install", gate_defaults["install"])
    if install is not None and (not isinstance(install, str) or not install.strip()):
        parse_issues.append(ConfigIssue("error", "gate.install", "deve ser string não vazia ou nulo"))
        install = gate_defaults["install"]
    allow_unverified = gate_data.get("allow_unverified", gate_defaults["allow_unverified"])
    if not isinstance(allow_unverified, bool):
        parse_issues.append(ConfigIssue("error", "gate.allow_unverified", "deve ser booleano"))
        allow_unverified = gate_defaults["allow_unverified"]
    raw_commands = gate_data.get("commands", gate_defaults["commands"])
    commands: List[GateCommand] = []
    if not isinstance(raw_commands, list):
        parse_issues.append(ConfigIssue("error", "gate.commands", "deve ser uma lista"))
        raw_commands = []
    for index, item in enumerate(raw_commands):
        prefix = f"gate.commands[{index}]"
        if not isinstance(item, dict):
            parse_issues.append(ConfigIssue("error", prefix, "deve ser um objeto"))
            continue
        name = item.get("name")
        run = item.get("run")
        timeout = item.get("timeout_seconds", 300)
        required = item.get("required", True)
        valid = True
        if not isinstance(name, str) or not name.strip():
            parse_issues.append(ConfigIssue("error", f"{prefix}.name", "nome não pode ser vazio"))
            valid = False
        if not (
            isinstance(run, str) and bool(run.strip())
            or isinstance(run, list) and bool(run) and all(isinstance(arg, str) and bool(arg) for arg in run)
        ):
            parse_issues.append(ConfigIssue("error", f"{prefix}.run", "deve ser string ou lista de strings não vazia"))
            valid = False
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            parse_issues.append(ConfigIssue("error", f"{prefix}.timeout_seconds", "deve ser número > 0"))
            timeout = 300
            valid = False
        if not isinstance(required, bool):
            parse_issues.append(ConfigIssue("error", f"{prefix}.required", "deve ser booleano"))
            required = True
            valid = False
        if valid:
            command_name = name if isinstance(name, str) else ""
            command_run: Union[str, List[str]] = (
                run
                if isinstance(run, str)
                else [arg for arg in run if isinstance(arg, str)]
                if isinstance(run, list)
                else []
            )
            commands.append(GateCommand(command_name, command_run, float(timeout), required))
    command_names = [command.name for command in commands]
    for index, name in enumerate(command_names):
        if name in command_names[:index]:
            parse_issues.append(
                ConfigIssue("error", f"gate.commands[{index}].name", f"nome repetido: {name!r}")
            )
    gate = GateConfig(install=install, commands=commands, allow_unverified=allow_unverified)

    return MeisterConfig(
        version=version,
        master=master,
        router=router,
        architect=architect,
        workers=workers,
        concurrency=concurrency,
        scope=scope,
        environment=environment,
        gate=gate,
        _parse_issues=parse_issues,
    )


def load_config(config_path: Optional[str] = None, cwd: Optional[str] = None) -> MeisterConfig:
    """Load configuration from a YAML file or defaults.

    Args:
        config_path: Optional explicit path to meister.config.yaml.
                     If None, checks MEISTER_CONFIG_PATH env var,
                     then cwd/meister.config.yaml, cwd/meister.config.yml,
                     then ./meister.config.yaml, ./meister.config.yml.
                     If no file exists, returns default MeisterConfig.

    Returns:
        MeisterConfig object populated from file or defaults.

    Raises:
        FileNotFoundError: If an explicit config_path is passed but does not exist.
    """
    target_path: Optional[Path] = None

    if config_path:
        target_path = Path(config_path)
        if not target_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
    else:
        env_path = os.environ.get("MEISTER_CONFIG_PATH")
        if env_path and Path(env_path).exists():
            target_path = Path(env_path)
        elif cwd and (Path(cwd) / "meister.config.yaml").exists():
            target_path = Path(cwd) / "meister.config.yaml"
        elif cwd and (Path(cwd) / "meister.config.yml").exists():
            target_path = Path(cwd) / "meister.config.yml"
        elif Path("meister.config.yaml").exists():
            target_path = Path("meister.config.yaml")
        elif Path("meister.config.yml").exists():
            target_path = Path("meister.config.yml")

    if not target_path:
        cfg = MeisterConfig()
        cfg.config_source = "padrao (meister/default_config.yaml)"
        return cfg

    with open(target_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    cfg = _parse_config_dict(data)
    cfg.config_source = str(target_path)
    return cfg


def validate_config(config: MeisterConfig) -> List[ConfigIssue]:
    """Valida um objeto MeisterConfig retornando lista de ConfigIssue (erros e avisos).

    Sem levantar exceção. A configuração padrão sem arquivo e sem env resulta em 0 erros.
    """
    issues: List[ConfigIssue] = list(getattr(config, "_parse_issues", []))

    # Erros
    if config.router.mode not in {"first", "jev"}:
        issues.append(
            ConfigIssue(
                level="error",
                path="router.mode",
                message=f"router.mode inválido: '{config.router.mode}'. Valores válidos: first, jev",
            )
        )

    for path, value in (
        ("router.timeout_seconds", config.router.timeout_seconds),
        ("router.max_attempts", config.router.max_attempts),
        ("router.unavailable_cooldown_seconds", config.router.unavailable_cooldown_seconds),
    ):
        is_attempt_count = path == "router.max_attempts"
        is_valid_type = (
            isinstance(value, int) and not isinstance(value, bool)
            if is_attempt_count
            else isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
        if not is_valid_type:
            if not any(issue.path == path for issue in issues):
                issues.append(
                    ConfigIssue(
                        level="error",
                        path=path,
                        message=f"Campo '{path.rsplit('.', 1)[1]}' tem tipo inválido: {value!r}",
                    )
                )
            continue
        if path == "router.timeout_seconds" and value <= 0:
            message = f"router.timeout_seconds deve ser > 0 ({value})"
        elif path == "router.max_attempts" and value < 1:
            message = f"router.max_attempts deve ser >= 1 ({value})"
        elif path == "router.unavailable_cooldown_seconds" and value < 0:
            message = f"router.unavailable_cooldown_seconds não pode ser negativo ({value})"
        else:
            continue
        issues.append(ConfigIssue(level="error", path=path, message=message))

    # 1. tier_order efetivo vazio
    if not config.workers.tier_order:
        if config.workers.disabled:
            issues.append(
                ConfigIssue(
                    level="error",
                    path="workers.tier_order",
                    message="tier_order efetivo está vazio (todas as vias estão desabilitadas)",
                )
            )
        else:
            issues.append(
                ConfigIssue(
                    level="error",
                    path="workers.tier_order",
                    message="tier_order efetivo está vazio (nenhuma via configurada)",
                )
            )

    # 2. nome de via vazio ou repetido
    seen_names: Set[str] = set()
    for i, tier in enumerate(config.workers.tier_order):
        if not tier.name or not tier.name.strip():
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.tier_order[{i}].name",
                    message="Nome da via não pode ser vazio",
                )
            )
        elif tier.name in seen_names:
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.tier_order[{i}].name",
                    message=f"Nome de via repetido: '{tier.name}'",
                )
            )
        else:
            seen_names.add(tier.name)

    for j, tier in enumerate(config.workers.disabled):
        if not tier.name or not tier.name.strip():
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.disabled[{j}].name",
                    message="Nome da via não pode ser vazio",
                )
            )
        elif tier.name in seen_names:
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.disabled[{j}].name",
                    message=f"Nome de via repetido: '{tier.name}'",
                )
            )
        else:
            seen_names.add(tier.name)

    # 3. max_retries negativo
    for i, tier in enumerate(config.workers.tier_order):
        if tier.max_retries < 0:
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.tier_order[{i}].max_retries",
                    message=f"max_retries não pode ser negativo ({tier.max_retries})",
                )
            )
    for j, tier in enumerate(config.workers.disabled):
        if tier.max_retries < 0:
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.disabled[{j}].max_retries",
                    message=f"max_retries não pode ser negativo ({tier.max_retries})",
                )
            )

    # 4. Limite de paralelismo por via deve ser inteiro positivo.
    for group_name, tiers in (
        ("workers.tier_order", config.workers.tier_order),
        ("workers.disabled", config.workers.disabled),
    ):
        for i, tier in enumerate(tiers):
            if tier.max_parallel is None:
                continue
            path = f"{group_name}[{i}].max_parallel"
            if isinstance(tier.max_parallel, bool) or not isinstance(tier.max_parallel, int):
                if not any(issue.path == path for issue in issues):
                    issues.append(
                        ConfigIssue(
                            level="error",
                            path=path,
                            message=(
                                "Campo 'max_parallel' deve ser um inteiro >= 1 (booleanos não são aceitos), "
                                f"recebido: {tier.max_parallel!r}"
                            ),
                        )
                    )
            elif tier.max_parallel < 1:
                issues.append(
                    ConfigIssue(
                        level="error",
                        path=path,
                        message=f"max_parallel deve ser >= 1 ({tier.max_parallel})",
                    )
                )

    # 5. concurrency.max_parallel_workers < 1
    if config.concurrency.max_parallel_workers < 1:
        issues.append(
            ConfigIssue(
                level="error",
                path="concurrency.max_parallel_workers",
                message=f"concurrency.max_parallel_workers deve ser >= 1 ({config.concurrency.max_parallel_workers})",
            )
        )

    # 6. architect.effort fora dos valores válidos
    if config.architect.effort not in VALID_ARCHITECT_EFFORTS:
        issues.append(
            ConfigIssue(
                level="error",
                path="architect.effort",
                message=f"architect.effort inválido: '{config.architect.effort}'. Valores válidos: {', '.join(sorted(VALID_ARCHITECT_EFFORTS))}",
            )
        )

    # 6. enabled não booleano (para objetos construídos diretamente em Python sem parse)
    for i, tier in enumerate(config.workers.tier_order):
        if not isinstance(tier.enabled, bool):
            path_str = f"workers.tier_order[{i}].enabled"
            if not any(iss.path == path_str for iss in issues):
                issues.append(
                    ConfigIssue(
                        level="error",
                        path=path_str,
                        message=f"Campo 'enabled' deve ser booleano (true/false), recebido: {tier.enabled!r}",
                    )
                )
    for j, tier in enumerate(config.workers.disabled):
        if not isinstance(tier.enabled, bool):
            path_str = f"workers.disabled[{j}].enabled"
            if not any(iss.path == path_str for iss in issues):
                issues.append(
                    ConfigIssue(
                        level="error",
                        path=path_str,
                        message=f"Campo 'enabled' deve ser booleano (true/false), recebido: {tier.enabled!r}",
                    )
                )

    # Avisos
    if config.router.mode == "jev":
        try:
            from meister.jev import get_api_key

            get_api_key()
        except ValueError:
            issues.append(
                ConfigIssue(
                    level="warning",
                    path="router.mode",
                    message="OPENROUTER_API_KEY ausente; sem chave, o roteamento cai na primeira via",
                )
            )

    for i, tier in enumerate(config.workers.tier_order):
        h = (tier.harness or "").strip().lower()
        if h == "native":
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.tier_order[{i}].harness",
                    message="harness 'native' foi removido; declare codex, agy, claude ou copilot",
                )
            )
            continue
        # 1. Harness desconhecido
        if h not in KNOWN_HARNESSES:
            is_abs_path = os.path.isabs(h) and os.path.exists(h)
            is_in_path = bool(shutil.which(h))
            if not is_abs_path and not is_in_path:
                issues.append(
                    ConfigIssue(
                        level="warning",
                        path=f"workers.tier_order[{i}].harness",
                        message=f"Harness '{h}' desconhecido e não encontrado no PATH nem como arquivo executável",
                    )
                )

        # 2. Harness claude/copilot sem executável no PATH
        if h == "claude":
            if not shutil.which("claude"):
                issues.append(
                    ConfigIssue(
                        level="warning",
                        path=f"workers.tier_order[{i}].harness",
                        message="Executável 'claude' não encontrado no PATH",
                    )
                )
        elif h in ("copilot", "github-copilot"):
            if not (shutil.which("copilot") or shutil.which("github-copilot-cli")):
                issues.append(
                    ConfigIssue(
                        level="warning",
                        path=f"workers.tier_order[{i}].harness",
                        message="Executável 'copilot' não encontrado no PATH",
                    )
                )

        # 3. model vazio
        if not tier.model:
            issues.append(
                ConfigIssue(
                    level="warning",
                    path=f"workers.tier_order[{i}].model",
                    message=f"model vazio na via com harness '{h}'",
                )
            )

        # 4. best_for / cost_per_m_tokens preenchidos
        if tier.best_for and config.router.mode == "first":
            issues.append(
                ConfigIssue(
                    level="info",
                    path=f"workers.tier_order[{i}].best_for",
                    message="Campo 'best_for' preenchido nao influencia o roteamento",
                )
            )
        if tier.cost_per_m_tokens != 0.0 and config.router.mode == "first":
            issues.append(
                ConfigIssue(
                    level="info",
                    path=f"workers.tier_order[{i}].cost_per_m_tokens",
                    message="Campo 'cost_per_m_tokens' preenchido nao influencia o roteamento",
                )
            )

    for i, tier in enumerate(config.workers.disabled):
        if (tier.harness or "").strip().lower() == "native":
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.disabled[{i}].harness",
                    message="harness 'native' foi removido; declare codex, agy, claude ou copilot",
                )
            )

    return issues
