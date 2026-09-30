"""Declarative Configuration System for MeisterRouter.

Loads and validates meister.config.yaml, providing typed dataclasses
for master decision model, architect model, worker tier progression,
and concurrency constraints.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Set
import yaml


VALID_ARCHITECT_EFFORTS: Set[str] = {"low", "medium", "high", "xhigh", "max"}
KNOWN_HARNESSES: Set[str] = {"native", "claude", "copilot", "github-copilot"}


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
    provider: str = "openrouter"
    model: str = "typesafe/jev-1.13"
    temperature: float = 0.0
    api_key_env: str = "OPENROUTER_API_KEY"


@dataclass
class ArchitectConfig:
    harness: str = "claude"
    model: str = "claude-sonnet-5-5"
    prompt_template: str = "templates/architect_prompt.md"
    effort: str = "high"


@dataclass
class WorkerTier:
    name: str
    harness: str = "native"
    model: str = ""
    cost_per_m_tokens: float = 0.0
    max_retries: int = 2
    best_for: List[str] = field(default_factory=list)
    enabled: bool = True


def _default_worker_tiers() -> List[WorkerTier]:
    disable_luna = os.environ.get("MEISTER_DISABLE_LUNA", "").lower() in ("true", "1", "yes")
    primary = os.environ.get("MEISTER_PRIMARY_WORKER", "").lower().strip()

    tiers = [
        WorkerTier(
            name="luna",
            harness="native",
            model=os.environ.get("MEISTER_LUNA_MODEL", "gpt-6-luna"),
            cost_per_m_tokens=0.077,
            max_retries=2,
            best_for=["small_edits", "single_file", "css_fixes", "unit_test_additions"],
        ),
        WorkerTier(
            name="gemini_flash",
            harness="native",
            model=os.environ.get("MEISTER_GEMINI_MODEL", "gemini-3.8-flash-high"),
            cost_per_m_tokens=0.577,
            max_retries=2,
            best_for=["deep_reasoning", "complex_algorithms", "hard_bugs"],
        ),
        WorkerTier(
            name="haiku",
            harness="claude",
            model=os.environ.get("MEISTER_HAIKU_MODEL", "haiku"),
            cost_per_m_tokens=0.77,
            max_retries=2,
            best_for=["medium_features", "refactoring"],
        ),
        WorkerTier(
            name="sonnet",
            harness="claude",
            model=os.environ.get("MEISTER_SONNET_MODEL", "sonnet"),
            cost_per_m_tokens=3.00,
            max_retries=1,
            best_for=["architectural_recovery", "systemic_regressions"],
        ),
    ]

    # NOTA: O adaptador Copilot é configurado como tier complementar opt-in.
    # Pode ser habilitado explicitamente via MEISTER_ENABLE_COPILOT=true ou meister.config.yaml.
    enable_copilot = os.environ.get("MEISTER_ENABLE_COPILOT", "").lower() in ("true", "1", "yes")
    if enable_copilot:
        copilot_tier = WorkerTier(
            name="copilot",
            harness="copilot",
            model=os.environ.get("MEISTER_COPILOT_MODEL", "gpt-6-luna"),
            cost_per_m_tokens=0.20,
            max_retries=2,
            best_for=["github_integration", "code_completion"],
            enabled=True,
        )
        tiers.insert(0, copilot_tier)

    if disable_luna:
        tiers = [t for t in tiers if t.name != "luna"]
    elif primary and any(t.name == primary for t in tiers):
        primary_tier = next(t for t in tiers if t.name == primary)
        tiers = [primary_tier] + [t for t in tiers if t.name != primary]

    return tiers


@dataclass
class WorkersConfig:
    tier_order: List[WorkerTier] = field(default_factory=_default_worker_tiers)
    disabled: List[WorkerTier] = field(default_factory=list)


@dataclass
class ConcurrencyConfig:
    parallel_tasks: bool = True
    max_parallel_workers: int = 4
    layout_strategy: str = "tabs"
    isolation_mode: str = "git_worktree"


@dataclass
class MeisterConfig:
    version: str = "1.0"
    master: MasterConfig = field(default_factory=MasterConfig)
    architect: ArchitectConfig = field(default_factory=ArchitectConfig)
    workers: WorkersConfig = field(default_factory=WorkersConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    config_source: str = "padrao"
    _parse_issues: List[ConfigIssue] = field(default_factory=list)


def _as_str(value: object, default: str = "") -> str:
    return default if value is None else str(value)


def _parse_config_dict(data: dict) -> MeisterConfig:
    if not isinstance(data, dict):
        return MeisterConfig()

    version = str(data.get("version", "1.0"))
    parse_issues: List[ConfigIssue] = []

    # Master
    master_data = data.get("master") or {}
    master = MasterConfig(
        provider=master_data.get("provider", "openrouter"),
        model=master_data.get("model", "typesafe/jev-1.13"),
        temperature=float(master_data.get("temperature", 0.0)),
        api_key_env=master_data.get("api_key_env", "OPENROUTER_API_KEY"),
    )

    # Architect
    architect_data = data.get("architect") or {}
    raw_effort = architect_data.get("effort")
    effort_val = "high" if raw_effort is None else str(raw_effort)
    architect = ArchitectConfig(
        harness=architect_data.get("harness", "claude"),
        model=architect_data.get("model", "claude-sonnet-5-5"),
        prompt_template=architect_data.get("prompt_template", "templates/architect_prompt.md"),
        effort=effort_val,
    )

    # Workers
    workers_data = data.get("workers")
    if workers_data is not None and isinstance(workers_data, dict) and "tier_order" in workers_data:
        raw_tiers = workers_data.get("tier_order") or []
        tier_list: List[WorkerTier] = []
        disabled_list: List[WorkerTier] = []
        for i, tier in enumerate(raw_tiers):
            if isinstance(tier, dict):
                raw_enabled = tier.get("enabled", True)
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
                    harness=_as_str(tier.get("harness"), "native"),
                    model=_as_str(tier.get("model"), ""),
                    cost_per_m_tokens=float(tier.get("cost_per_m_tokens", 0.0)),
                    max_retries=int(tier.get("max_retries", 2)),
                    best_for=list(tier.get("best_for", [])),
                    enabled=enabled_val,
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
    concurrency_data = data.get("concurrency") or {}
    concurrency = ConcurrencyConfig(
        parallel_tasks=concurrency_data.get("parallel_tasks", True),
        max_parallel_workers=int(concurrency_data.get("max_parallel_workers", 4)),
        layout_strategy=concurrency_data.get("layout_strategy", "tabs"),
        isolation_mode=concurrency_data.get("isolation_mode", "git_worktree"),
    )

    return MeisterConfig(
        version=version,
        master=master,
        architect=architect,
        workers=workers,
        concurrency=concurrency,
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
        cfg.config_source = "padrao"
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

    # 4. concurrency.max_parallel_workers < 1
    if config.concurrency.max_parallel_workers < 1:
        issues.append(
            ConfigIssue(
                level="error",
                path="concurrency.max_parallel_workers",
                message=f"concurrency.max_parallel_workers deve ser >= 1 ({config.concurrency.max_parallel_workers})",
            )
        )

    # 5. architect.effort fora dos valores válidos
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
    for i, tier in enumerate(config.workers.tier_order):
        h = (tier.harness or "native").strip().lower()
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

        # 3. model vazio em via não native
        if h != "native" and not tier.model:
            issues.append(
                ConfigIssue(
                    level="warning",
                    path=f"workers.tier_order[{i}].model",
                    message=f"model vazio em via com harness não nativo ('{h}')",
                )
            )

        # 4. best_for / cost_per_m_tokens preenchidos
        if tier.best_for:
            issues.append(
                ConfigIssue(
                    level="info",
                    path=f"workers.tier_order[{i}].best_for",
                    message="Campo 'best_for' preenchido nao influencia o roteamento",
                )
            )
        if tier.cost_per_m_tokens != 0.0:
            issues.append(
                ConfigIssue(
                    level="info",
                    path=f"workers.tier_order[{i}].cost_per_m_tokens",
                    message="Campo 'cost_per_m_tokens' preenchido nao influencia o roteamento",
                )
            )

    return issues
