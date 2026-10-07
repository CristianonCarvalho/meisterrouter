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
import ntpath
from functools import lru_cache
from importlib.resources import files
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, List, Set, Union

from meister.i18n import t
import yaml


VALID_ARCHITECT_EFFORTS: Set[str] = {"low", "medium", "high", "xhigh", "max"}
VALID_WORKER_CLASSES: Set[str] = {"SMALL", "MEDIUM", "HIGH", "ESCALATE"}
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
    """Ensure .meister exists with a .gitignore containing '*' so git stays clean (Finding #4, E2E-4)."""
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
    context_max_chars: int = field(default_factory=lambda: _default_section("router")["context_max_chars"])


@dataclass
class RetryConfig:
    pane_lost_attempts: int = field(default_factory=lambda: _default_section("retry")["pane_lost_attempts"])
    pane_lost_backoff_seconds: float = field(
        default_factory=lambda: float(_default_section("retry")["pane_lost_backoff_seconds"])
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
    idle_timeout_seconds: Optional[float] = None
    max_runtime_seconds: Optional[float] = None
    credit_usd: Optional[float] = None
    eligible_classes: List[str] = field(default_factory=list)


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
            idle_timeout_seconds=item.get("idle_timeout_seconds"),
            max_runtime_seconds=item.get("max_runtime_seconds"),
            credit_usd=item.get("credit_usd"),
            eligible_classes=[str(value).upper() for value in item.get("eligible_classes", [])],
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
    idle_timeout_seconds: float = field(
        default_factory=lambda: float(_default_section("workers")["idle_timeout_seconds"])
    )
    max_runtime_seconds: float = field(
        default_factory=lambda: float(_default_section("workers")["max_runtime_seconds"])
    )


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
    ok_exit_codes: List[int] = field(default_factory=lambda: [0])


@dataclass
class DocsOnlyGateConfig:
    enabled: bool = False
    paths: List[str] = field(
        default_factory=lambda: list(_default_section("gate")["docs_only"]["paths"])
    )
    commands: List[GateCommand] = field(default_factory=list)


@dataclass
class GateConfig:
    install: Optional[str] = field(default_factory=lambda: _default_section("gate")["install"])
    commands: List[GateCommand] = field(default_factory=list)
    allow_unverified: bool = field(default_factory=lambda: _default_section("gate")["allow_unverified"])
    python: Optional[str] = None
    cache: bool = field(default_factory=lambda: _default_section("gate")["cache"])
    docs_only: DocsOnlyGateConfig = field(default_factory=DocsOnlyGateConfig)


@dataclass
class MeisterConfig:
    version: str = field(default_factory=lambda: _default_config_data()["version"])
    language: str = field(default_factory=lambda: _as_str(_default_config_data().get("language", "en"), "en"))
    master: MasterConfig = field(default_factory=MasterConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    architect: ArchitectConfig = field(default_factory=ArchitectConfig)
    workers: WorkersConfig = field(default_factory=WorkersConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    scope: ScopeConfig = field(default_factory=ScopeConfig)
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)
    gate: GateConfig = field(default_factory=GateConfig)
    config_source: str = field(default_factory=lambda: t("reports.config.default_source"))
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


def _parse_gate_commands(
    raw_commands: Any,
    path_prefix: str,
    parse_issues: List[ConfigIssue],
) -> List[GateCommand]:
    commands: List[GateCommand] = []
    if not isinstance(raw_commands, list):
        parse_issues.append(ConfigIssue("error", path_prefix, t("reports.config.expected_list")))
        return commands
    for index, item in enumerate(raw_commands):
        prefix = f"{path_prefix}[{index}]"
        if not isinstance(item, dict):
            parse_issues.append(ConfigIssue("error", prefix, t("reports.config.expected_object")))
            continue
        name = item.get("name")
        run = item.get("run")
        timeout = item.get("timeout_seconds", 300)
        required = item.get("required", True)
        ok_exit_codes = item.get("ok_exit_codes", [0])
        valid = True
        if not isinstance(name, str) or not name.strip():
            parse_issues.append(ConfigIssue("error", f"{prefix}.name", t("reports.config.name_required")))
            valid = False
        if not (
            isinstance(run, str) and bool(run.strip())
            or isinstance(run, list) and bool(run) and all(isinstance(arg, str) and bool(arg) for arg in run)
        ):
            parse_issues.append(ConfigIssue("error", f"{prefix}.run", t("reports.config.run_required")))
            valid = False
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            parse_issues.append(ConfigIssue("error", f"{prefix}.timeout_seconds", t("reports.config.positive_number")))
            timeout = 300
            valid = False
        if not isinstance(required, bool):
            parse_issues.append(ConfigIssue("error", f"{prefix}.required", t("reports.config.expected_boolean")))
            required = True
            valid = False
        if (
            not isinstance(ok_exit_codes, list)
            or not ok_exit_codes
            or any(isinstance(code, bool) or not isinstance(code, int) for code in ok_exit_codes)
        ):
            parse_issues.append(
                ConfigIssue(
                    "error",
                    f"{prefix}.ok_exit_codes",
                    t("reports.config.exit_codes_required"),
                )
            )
            ok_exit_codes = [0]
            valid = False
        if valid:
            command_run: Union[str, List[str]] = (
                run if isinstance(run, str)
                else [arg for arg in (run or []) if isinstance(arg, str)]
            )
            commands.append(
                GateCommand(str(name), command_run, float(timeout), required, ok_exit_codes)
            )
    names = [command.name for command in commands]
    for index, name in enumerate(names):
        if name in names[:index]:
            parse_issues.append(
                ConfigIssue("error", f"{path_prefix}[{index}].name", t("reports.config.duplicate_name", name=repr(name)))
            )
    return commands


def _parse_config_dict(data: dict) -> MeisterConfig:
    data = _merge_config(_default_config_data(), data if isinstance(data, dict) else {})

    version = str(data.get("version", "1.0"))
    parse_issues: List[ConfigIssue] = []

    from meister.i18n import normalize_language

    raw_lang = data.get("language", "en")
    norm_lang = normalize_language(raw_lang)
    if norm_lang is None:
        parse_issues.append(
            ConfigIssue(
                level="error",
                path="language",
                message=t("config.language.invalid", value=repr(raw_lang)),
            )
        )
        language = str(raw_lang) if raw_lang is not None else ""
    else:
        language = norm_lang

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
                    message=t("reports.config.invalid_field_type", field=key, value=repr(value)),
                )
            )
            return default_router[key]
        return value

    timeout_seconds = router_number("timeout_seconds", (int, float))
    max_attempts = router_number("max_attempts", int)
    unavailable_cooldown_seconds = router_number("unavailable_cooldown_seconds", (int, float))
    context_max_chars = router_number("context_max_chars", int)
    router = RouterConfig(
        mode=_as_str(router_values.get("mode")),
        timeout_seconds=float(timeout_seconds),
        max_attempts=max_attempts,
        unavailable_cooldown_seconds=float(unavailable_cooldown_seconds),
        context_max_chars=context_max_chars,
    )

    retry_data = data.get("retry", {})
    if not isinstance(retry_data, dict):
        parse_issues.append(ConfigIssue("error", "retry", t("reports.config.retry_object")))
        retry_data = {}
    retry_defaults = _default_section("retry")
    pane_lost_attempts = retry_data.get("pane_lost_attempts", retry_defaults["pane_lost_attempts"])
    if isinstance(pane_lost_attempts, bool) or not isinstance(pane_lost_attempts, int):
        parse_issues.append(
            ConfigIssue("error", "retry.pane_lost_attempts", t("reports.config.nonnegative_integer"))
        )
        pane_lost_attempts = retry_defaults["pane_lost_attempts"]
    pane_lost_backoff = retry_data.get(
        "pane_lost_backoff_seconds", retry_defaults["pane_lost_backoff_seconds"]
    )
    if (
        isinstance(pane_lost_backoff, bool)
        or not isinstance(pane_lost_backoff, (int, float))
        or not math.isfinite(pane_lost_backoff)
    ):
        parse_issues.append(
            ConfigIssue("error", "retry.pane_lost_backoff_seconds", t("reports.config.nonnegative_number"))
        )
        pane_lost_backoff = retry_defaults["pane_lost_backoff_seconds"]
    retry = RetryConfig(
        pane_lost_attempts=pane_lost_attempts,
        pane_lost_backoff_seconds=float(pane_lost_backoff),
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
    worker_defaults = _default_section("workers")
    if not isinstance(workers_data, dict):
        parse_issues.append(ConfigIssue("error", "workers", t("reports.config.workers_object")))
        workers_data = worker_defaults

    def worker_timeout(value: Any, path: str, default: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            parse_issues.append(
                ConfigIssue("error", path, t("reports.config.finite_nonnegative_number", value=repr(value)))
            )
            return default
        return float(value)

    worker_idle_timeout = worker_timeout(
        workers_data.get("idle_timeout_seconds", worker_defaults["idle_timeout_seconds"]),
        "workers.idle_timeout_seconds",
        float(worker_defaults["idle_timeout_seconds"]),
    )
    worker_max_runtime = worker_timeout(
        workers_data.get("max_runtime_seconds", worker_defaults["max_runtime_seconds"]),
        "workers.max_runtime_seconds",
        float(worker_defaults["max_runtime_seconds"]),
    )

    if isinstance(workers_data, dict) and "tier_order" in workers_data:
        raw_tiers = workers_data["tier_order"] or []
        tier_list: List[WorkerTier] = []
        disabled_list: List[WorkerTier] = []
        for i, tier in enumerate(raw_tiers):
            if isinstance(tier, dict):
                raw_enabled = tier.get("enabled", True)
                raw_max_parallel = tier.get("max_parallel")
                raw_idle_timeout = tier.get("idle_timeout_seconds")
                raw_max_runtime = tier.get("max_runtime_seconds")
                raw_credit_usd = tier.get("credit_usd")
                credit_usd = None
                if raw_credit_usd is not None:
                    if (
                        isinstance(raw_credit_usd, bool)
                        or not isinstance(raw_credit_usd, (int, float))
                        or not math.isfinite(raw_credit_usd)
                        or raw_credit_usd <= 0
                    ):
                        parse_issues.append(
                            ConfigIssue(
                                "error",
                                f"workers.tier_order[{i}].credit_usd",
                                t("reports.config.finite_positive_number", value=repr(raw_credit_usd)),
                            )
                        )
                    else:
                        credit_usd = float(raw_credit_usd)
                raw_eligible_classes = tier.get("eligible_classes", [])
                eligible_classes: List[str] = []
                if (
                    not isinstance(raw_eligible_classes, list)
                    or any(
                        not isinstance(value, str)
                        or value.upper() not in VALID_WORKER_CLASSES
                        for value in raw_eligible_classes
                    )
                ):
                    parse_issues.append(
                        ConfigIssue(
                            "error",
                            f"workers.tier_order[{i}].eligible_classes",
                            t("reports.config.eligible_classes"),
                        )
                    )
                else:
                    eligible_classes = [value.upper() for value in raw_eligible_classes]
                tier_idle_timeout = (
                    None
                    if raw_idle_timeout is None
                    else worker_timeout(
                        raw_idle_timeout,
                        f"workers.tier_order[{i}].idle_timeout_seconds",
                        worker_idle_timeout,
                    )
                )
                tier_max_runtime = (
                    None
                    if raw_max_runtime is None
                    else worker_timeout(
                        raw_max_runtime,
                        f"workers.tier_order[{i}].max_runtime_seconds",
                        worker_max_runtime,
                    )
                )
                if raw_max_parallel is not None and (
                    isinstance(raw_max_parallel, bool) or not isinstance(raw_max_parallel, int)
                ):
                    parse_issues.append(
                        ConfigIssue(
                            level="error",
                            path=f"workers.tier_order[{i}].max_parallel",
                            message=(
                                t("reports.config.max_parallel_integer", value=repr(raw_max_parallel))
                            ),
                        )
                    )
                if not isinstance(raw_enabled, bool):
                    parse_issues.append(
                        ConfigIssue(
                            level="error",
                            path=f"workers.tier_order[{i}].enabled",
                            message=t("reports.config.enabled_boolean", value=repr(raw_enabled)),
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
                    idle_timeout_seconds=tier_idle_timeout,
                    max_runtime_seconds=tier_max_runtime,
                    credit_usd=credit_usd,
                    eligible_classes=eligible_classes,
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
                            message=t("reports.config.enabled_boolean", value=repr(tier.enabled)),
                        )
                    )
                if tier.enabled:
                    tier_list.append(tier)
                else:
                    disabled_list.append(tier)
        workers = WorkersConfig(
            tier_order=tier_list,
            disabled=disabled_list,
            idle_timeout_seconds=worker_idle_timeout,
            max_runtime_seconds=worker_max_runtime,
        )
    else:
        workers = WorkersConfig(
            idle_timeout_seconds=worker_idle_timeout,
            max_runtime_seconds=worker_max_runtime,
        )

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
        parse_issues.append(ConfigIssue("error", "scope", t("reports.config.scope_object")))
        scope_data = {}
    tolerated_files = scope_data.get("tolerated_files", _default_section("scope")["tolerated_files"])
    if not isinstance(tolerated_files, list) or any(not isinstance(item, str) for item in tolerated_files):
        parse_issues.append(ConfigIssue("error", "scope.tolerated_files", t("reports.config.string_list")))
        tolerated_files = _default_section("scope")["tolerated_files"]
    scope = ScopeConfig(tolerated_files=list(tolerated_files))

    environment_data = data.get("environment", {})
    if not isinstance(environment_data, dict):
        parse_issues.append(ConfigIssue("error", "environment", t("reports.config.environment_object")))
        environment_data = {}
    install_dependencies = environment_data.get(
        "install_dependencies", _default_section("environment")["install_dependencies"]
    )
    if not isinstance(install_dependencies, bool):
        parse_issues.append(
            ConfigIssue("error", "environment.install_dependencies", t("reports.config.expected_boolean"))
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
            ConfigIssue("error", "environment.install_timeout_seconds", t("reports.config.positive_number"))
        )
        install_timeout = _default_section("environment")["install_timeout_seconds"]
    environment = EnvironmentConfig(
        install_dependencies=install_dependencies,
        install_timeout_seconds=float(install_timeout),
    )

    gate_data = data.get("gate", {})
    if not isinstance(gate_data, dict):
        parse_issues.append(ConfigIssue("error", "gate", t("reports.config.gate_object")))
        gate_data = {}
    gate_defaults = _default_section("gate")
    install = gate_data.get("install", gate_defaults["install"])
    if install is not None and (not isinstance(install, str) or not install.strip()):
        parse_issues.append(ConfigIssue("error", "gate.install", t("reports.config.string_or_null")))
        install = gate_defaults["install"]
    python = gate_data.get("python", gate_defaults["python"])
    if python is not None and (not isinstance(python, str) or not python.strip()):
        parse_issues.append(ConfigIssue("error", "gate.python", t("reports.config.string_or_null")))
        python = gate_defaults["python"]
    allow_unverified = gate_data.get("allow_unverified", gate_defaults["allow_unverified"])
    if not isinstance(allow_unverified, bool):
        parse_issues.append(ConfigIssue("error", "gate.allow_unverified", t("reports.config.expected_boolean")))
        allow_unverified = gate_defaults["allow_unverified"]
    cache = gate_data.get("cache", gate_defaults["cache"])
    if not isinstance(cache, bool):
        parse_issues.append(ConfigIssue("error", "gate.cache", t("reports.config.expected_boolean")))
        cache = gate_defaults["cache"]
    raw_commands = gate_data.get("commands", gate_defaults["commands"])
    commands = _parse_gate_commands(raw_commands, "gate.commands", parse_issues)
    docs_only_data = gate_data.get("docs_only", {})
    if not isinstance(docs_only_data, dict):
        parse_issues.append(ConfigIssue("error", "gate.docs_only", t("reports.config.expected_object")))
        docs_only_data = {}
    docs_only_defaults = gate_defaults["docs_only"]
    docs_only_enabled = docs_only_data.get("enabled", docs_only_defaults["enabled"])
    if not isinstance(docs_only_enabled, bool):
        parse_issues.append(ConfigIssue("error", "gate.docs_only.enabled", t("reports.config.expected_boolean")))
        docs_only_enabled = docs_only_defaults["enabled"]
    raw_paths = docs_only_data.get("paths", docs_only_defaults["paths"])
    docs_only_paths = raw_paths
    if (
        not isinstance(raw_paths, list)
        or not raw_paths
        or any(
            not isinstance(path, str)
            or not path.strip()
            or os.path.isabs(path)
            or ntpath.isabs(path)
            or bool(ntpath.splitdrive(path)[0])
            or ".." in path.replace("\\", "/").split("/")
            for path in raw_paths
        )
    ):
        parse_issues.append(
            ConfigIssue(
                "error",
                "gate.docs_only.paths",
                t("reports.config.relative_paths_required"),
            )
        )
        docs_only_paths = docs_only_defaults["paths"]
    docs_only_commands = _parse_gate_commands(
        docs_only_data.get("commands", docs_only_defaults["commands"]),
        "gate.docs_only.commands",
        parse_issues,
    )
    if docs_only_enabled and not docs_only_commands:
        parse_issues.append(
            ConfigIssue("error", "gate.docs_only.commands", t("reports.config.required_when_enabled"))
        )
    docs_only = DocsOnlyGateConfig(
        enabled=docs_only_enabled,
        paths=list(docs_only_paths),
        commands=docs_only_commands,
    )
    gate = GateConfig(
        install=install,
        commands=commands,
        allow_unverified=allow_unverified,
        python=python,
        cache=cache,
        docs_only=docs_only,
    )

    return MeisterConfig(
        version=version,
        language=language,
        master=master,
        router=router,
        retry=retry,
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
        cfg.config_source = t("reports.config.default_source")
        return cfg

    with open(target_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    cfg = _parse_config_dict(data)
    cfg.config_source = str(target_path)
    return cfg


def validate_config(config: MeisterConfig) -> List[ConfigIssue]:
    """Validate a MeisterConfig object and return its ConfigIssue list (errors and warnings).

    Does not raise exceptions. The default configuration with no file or env has 0 errors.
    """
    issues: List[ConfigIssue] = list(getattr(config, "_parse_issues", []))

    from meister.i18n import normalize_language

    cfg_lang = getattr(config, "language", None)
    if normalize_language(cfg_lang) is None:
        if not any(issue.path == "language" for issue in issues):
            issues.append(
                ConfigIssue(
                    level="error",
                    path="language",
                    message=t("config.language.invalid", value=repr(cfg_lang)),
                )
            )

    for path, value in (
        ("workers.idle_timeout_seconds", config.workers.idle_timeout_seconds),
        ("workers.max_runtime_seconds", config.workers.max_runtime_seconds),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ) and not any(issue.path == path for issue in issues):
            issues.append(
                ConfigIssue("error", path, t("reports.config.finite_nonnegative_number", value=repr(value)))
            )

    if config.workers.idle_timeout_seconds == 0 and config.workers.max_runtime_seconds == 0:
        issues.append(
            ConfigIssue("warning", "workers", t("reports.config.worker_timeouts_disabled"))
        )

    # Erros
    if isinstance(config.retry.pane_lost_attempts, bool) or not isinstance(
        config.retry.pane_lost_attempts, int
    ):
        if not any(issue.path == "retry.pane_lost_attempts" for issue in issues):
            issues.append(
                ConfigIssue("error", "retry.pane_lost_attempts", t("reports.config.nonnegative_integer"))
            )
    elif config.retry.pane_lost_attempts < 0:
        issues.append(
            ConfigIssue(
                "error",
                "retry.pane_lost_attempts",
                t("reports.config.nonnegative_value", value=config.retry.pane_lost_attempts),
            )
        )

    if (
        isinstance(config.retry.pane_lost_backoff_seconds, bool)
        or not isinstance(config.retry.pane_lost_backoff_seconds, (int, float))
        or not math.isfinite(config.retry.pane_lost_backoff_seconds)
    ):
        if not any(issue.path == "retry.pane_lost_backoff_seconds" for issue in issues):
            issues.append(
                ConfigIssue(
                    "error", "retry.pane_lost_backoff_seconds", t("reports.config.nonnegative_number")
                )
            )
    elif config.retry.pane_lost_backoff_seconds < 0:
        issues.append(
            ConfigIssue(
                "error",
                "retry.pane_lost_backoff_seconds",
                t("reports.config.nonnegative_value", value=config.retry.pane_lost_backoff_seconds),
            )
        )

    if config.router.mode not in {"first", "jev"}:
        issues.append(
            ConfigIssue(
                level="error",
                path="router.mode",
                message=t("reports.config.invalid_router_mode", mode=config.router.mode),
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
                        message=t("reports.config.invalid_field_type", field=path.rsplit(".", 1)[1], value=repr(value)),
                    )
                )
            continue
        if path == "router.timeout_seconds" and value <= 0:
            message = t("reports.config.router_timeout_positive", value=value)
        elif path == "router.max_attempts" and value < 1:
            message = t("reports.config.router_attempts_positive", value=value)
        elif path == "router.unavailable_cooldown_seconds" and value < 0:
            message = t("reports.config.router_cooldown_nonnegative", value=value)
        else:
            continue
        issues.append(ConfigIssue(level="error", path=path, message=message))

    if isinstance(config.router.context_max_chars, bool) or not isinstance(
        config.router.context_max_chars, int
    ):
        if not any(issue.path == "router.context_max_chars" for issue in issues):
            issues.append(
                ConfigIssue(
                    "error",
                    "router.context_max_chars",
                    t("reports.config.context_integer"),
                )
            )
    elif config.router.context_max_chars < 500:
        issues.append(
            ConfigIssue(
                "error",
                "router.context_max_chars",
                t("reports.config.context_minimum", value=config.router.context_max_chars),
            )
        )

    # 1. tier_order efetivo vazio
    if not config.workers.tier_order:
        if config.workers.disabled:
            issues.append(
                ConfigIssue(
                    level="error",
                    path="workers.tier_order",
                    message=t("reports.config.no_enabled_lanes"),
                )
            )
        else:
            issues.append(
                ConfigIssue(
                    level="error",
                    path="workers.tier_order",
                    message=t("reports.config.no_lanes_configured"),
                )
            )

    # 2. nome de via vazio ou repetido
    seen_names: Set[str] = set()
    for i, tier in enumerate(config.workers.tier_order):
        if (
            not isinstance(tier.eligible_classes, list)
            or any(
                not isinstance(value, str) or value.upper() not in VALID_WORKER_CLASSES
                for value in tier.eligible_classes
            )
        ):
            path = f"workers.tier_order[{i}].eligible_classes"
            if not any(issue.path == path for issue in issues):
                issues.append(
                    ConfigIssue(
                        "error",
                        path,
                        t("reports.config.eligible_classes"),
                    )
                )
        if tier.credit_usd is not None and (
            isinstance(tier.credit_usd, bool)
            or not isinstance(tier.credit_usd, (int, float))
            or not math.isfinite(tier.credit_usd)
            or tier.credit_usd <= 0
        ):
            path = f"workers.tier_order[{i}].credit_usd"
            if not any(issue.path == path for issue in issues):
                issues.append(
                    ConfigIssue(
                        "error",
                        path,
                        t("reports.config.finite_positive_number", value=repr(tier.credit_usd)),
                    )
                )
        for field_name in ("idle_timeout_seconds", "max_runtime_seconds"):
            value = getattr(tier, field_name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                path = f"workers.tier_order[{i}].{field_name}"
                if not any(issue.path == path for issue in issues):
                    issues.append(
                        ConfigIssue("error", path, t("reports.config.finite_nonnegative_number", value=repr(value)))
                    )
        if tier.idle_timeout_seconds == 0 and tier.max_runtime_seconds == 0:
            issues.append(
                ConfigIssue(
                    "warning",
                    f"workers.tier_order[{i}]",
                    t("reports.config.lane_timeouts_disabled"),
                )
            )
        if not tier.name or not tier.name.strip():
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.tier_order[{i}].name",
                    message=t("reports.config.lane_name_required"),
                )
            )
        elif tier.name in seen_names:
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.tier_order[{i}].name",
                    message=t("reports.config.lane_name_duplicate", name=tier.name),
                )
            )
        else:
            seen_names.add(tier.name)

    for j, tier in enumerate(config.workers.disabled):
        if (
            not isinstance(tier.eligible_classes, list)
            or any(
                not isinstance(value, str) or value.upper() not in VALID_WORKER_CLASSES
                for value in tier.eligible_classes
            )
        ):
            path = f"workers.disabled[{j}].eligible_classes"
            if not any(issue.path == path for issue in issues):
                issues.append(
                    ConfigIssue(
                        "error",
                        path,
                        t("reports.config.eligible_classes"),
                    )
                )
        if tier.credit_usd is not None and (
            isinstance(tier.credit_usd, bool)
            or not isinstance(tier.credit_usd, (int, float))
            or not math.isfinite(tier.credit_usd)
            or tier.credit_usd <= 0
        ):
            path = f"workers.disabled[{j}].credit_usd"
            if not any(issue.path == path for issue in issues):
                issues.append(
                    ConfigIssue(
                        "error",
                        path,
                        t("reports.config.finite_positive_number", value=repr(tier.credit_usd)),
                    )
                )
        for field_name in ("idle_timeout_seconds", "max_runtime_seconds"):
            value = getattr(tier, field_name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                path = f"workers.disabled[{j}].{field_name}"
                if not any(issue.path == path for issue in issues):
                    issues.append(
                        ConfigIssue("error", path, t("reports.config.finite_nonnegative_number", value=repr(value)))
                    )
        if tier.idle_timeout_seconds == 0 and tier.max_runtime_seconds == 0:
            issues.append(
                ConfigIssue(
                    "warning",
                    f"workers.disabled[{j}]",
                    t("reports.config.lane_timeouts_disabled"),
                )
            )
        if not tier.name or not tier.name.strip():
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.disabled[{j}].name",
                    message=t("reports.config.lane_name_required"),
                )
            )
        elif tier.name in seen_names:
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.disabled[{j}].name",
                    message=t("reports.config.lane_name_duplicate", name=tier.name),
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
                    message=t("reports.config.retries_nonnegative", value=tier.max_retries),
                )
            )
    for j, tier in enumerate(config.workers.disabled):
        if tier.max_retries < 0:
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.disabled[{j}].max_retries",
                    message=t("reports.config.retries_nonnegative", value=tier.max_retries),
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
                                t("reports.config.max_parallel_integer", value=repr(tier.max_parallel))
                            ),
                        )
                    )
            elif tier.max_parallel < 1:
                issues.append(
                    ConfigIssue(
                        level="error",
                        path=path,
                        message=t("reports.config.max_parallel_minimum", value=tier.max_parallel),
                    )
                )

    # 5. concurrency.max_parallel_workers < 1
    if config.concurrency.max_parallel_workers < 1:
        issues.append(
            ConfigIssue(
                level="error",
                path="concurrency.max_parallel_workers",
                message=t("reports.config.parallel_workers_minimum", value=config.concurrency.max_parallel_workers),
            )
        )

    # 6. architect.effort fora dos valores válidos
    if config.architect.effort not in VALID_ARCHITECT_EFFORTS:
        issues.append(
            ConfigIssue(
                level="error",
                path="architect.effort",
                message=t("reports.config.invalid_architect_effort", effort=config.architect.effort, values=", ".join(sorted(VALID_ARCHITECT_EFFORTS))),
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
                        message=t("reports.config.enabled_boolean", value=repr(tier.enabled)),
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
                        message=t("reports.config.enabled_boolean", value=repr(tier.enabled)),
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
                    message=t("reports.config.openrouter_key_missing"),
                )
            )

    for i, tier in enumerate(config.workers.tier_order):
        h = (tier.harness or "").strip().lower()
        if h == "native":
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.tier_order[{i}].harness",
                    message=t("reports.config.native_harness_removed"),
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
                        message=t("reports.config.unknown_harness", harness=h),
                    )
                )

        # 2. Harness claude/copilot sem executável no PATH
        if h == "claude":
            if not shutil.which("claude"):
                issues.append(
                    ConfigIssue(
                        level="warning",
                        path=f"workers.tier_order[{i}].harness",
                        message=t("reports.config.executable_not_found", executable="claude"),
                    )
                )
        elif h in ("copilot", "github-copilot"):
            if not (shutil.which("copilot") or shutil.which("github-copilot-cli")):
                issues.append(
                    ConfigIssue(
                        level="warning",
                        path=f"workers.tier_order[{i}].harness",
                        message=t("reports.config.executable_not_found", executable="copilot"),
                    )
                )

        # 3. model vazio
        if not tier.model:
            issues.append(
                ConfigIssue(
                    level="warning",
                    path=f"workers.tier_order[{i}].model",
                    message=t("reports.config.empty_model", harness=h),
                )
            )

        # 4. best_for / cost_per_m_tokens preenchidos
        if tier.best_for and config.router.mode == "first":
            issues.append(
                ConfigIssue(
                    level="info",
                    path=f"workers.tier_order[{i}].best_for",
                    message=t("reports.config.best_for_ignored"),
                )
            )
        if tier.cost_per_m_tokens != 0.0 and config.router.mode == "first":
            issues.append(
                ConfigIssue(
                    level="info",
                    path=f"workers.tier_order[{i}].cost_per_m_tokens",
                    message=t("reports.config.cost_ignored"),
                )
            )

    for i, tier in enumerate(config.workers.disabled):
        if (tier.harness or "").strip().lower() == "native":
            issues.append(
                ConfigIssue(
                    level="error",
                    path=f"workers.disabled[{i}].harness",
                    message=t("reports.config.native_harness_removed"),
                )
            )

    return issues


def effective_worker_timeouts(
    config: MeisterConfig,
    tier_name: str,
    task_dict: Optional[dict] = None,
) -> Dict[str, float]:
    """Resolve task > tier > workers defaults, including the legacy timeout ceiling."""
    task = task_dict or {}
    tier = next(
        (item for item in [*config.workers.tier_order, *config.workers.disabled] if item.name == tier_name),
        None,
    )

    def choose(value: Any, label: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(t("reports.config.finite_nonnegative_label", label=label))
        return float(value)

    idle = task.get("idle_timeout_seconds")
    if idle is None:
        idle = tier.idle_timeout_seconds if tier and tier.idle_timeout_seconds is not None else config.workers.idle_timeout_seconds
    maximum = task.get("max_runtime_seconds")
    if maximum is None:
        maximum = task.get("timeout")
    if maximum is None:
        maximum = tier.max_runtime_seconds if tier and tier.max_runtime_seconds is not None else config.workers.max_runtime_seconds
    return {
        "idle_timeout_seconds": choose(idle, "idle_timeout_seconds"),
        "max_runtime_seconds": choose(maximum, "max_runtime_seconds"),
    }
