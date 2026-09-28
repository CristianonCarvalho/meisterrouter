"""Declarative Configuration System for MeisterRouter.

Loads and validates meister.config.yaml, providing typed dataclasses
for master decision model, architect model, worker tier progression,
and concurrency constraints.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List
import yaml


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
    model: str = "anthropic/claude-sonnet-5"
    prompt_template: str = "templates/architect_prompt.md"


@dataclass
class WorkerTier:
    name: str
    harness: str = "native"
    model: str = ""
    cost_per_m_tokens: float = 0.0
    max_retries: int = 2
    best_for: List[str] = field(default_factory=list)


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
        tiers.append(
            WorkerTier(
                name="copilot",
                harness="copilot",
                model=os.environ.get("MEISTER_COPILOT_MODEL", "auto"),
                cost_per_m_tokens=0.20,
                max_retries=2,
                best_for=["github_integration", "code_completion"],
            )
        )

    if disable_luna:
        tiers = [t for t in tiers if t.name != "luna"]
    elif primary and any(t.name == primary for t in tiers):
        primary_tier = next(t for t in tiers if t.name == primary)
        tiers = [primary_tier] + [t for t in tiers if t.name != primary]

    return tiers


@dataclass
class WorkersConfig:
    tier_order: List[WorkerTier] = field(default_factory=_default_worker_tiers)


@dataclass
class ConcurrencyConfig:
    parallel_tasks: bool = True
    max_parallel_workers: int = 4
    layout_strategy: str = "tiled"
    isolation_mode: str = "git_worktree"


@dataclass
class MeisterConfig:
    version: str = "1.0"
    master: MasterConfig = field(default_factory=MasterConfig)
    architect: ArchitectConfig = field(default_factory=ArchitectConfig)
    workers: WorkersConfig = field(default_factory=WorkersConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)


def _parse_config_dict(data: dict) -> MeisterConfig:
    if not isinstance(data, dict):
        return MeisterConfig()

    version = str(data.get("version", "1.0"))

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
    architect = ArchitectConfig(
        harness=architect_data.get("harness", "claude"),
        model=architect_data.get("model", "anthropic/claude-sonnet-5"),
        prompt_template=architect_data.get("prompt_template", "templates/architect_prompt.md"),
    )

    # Workers
    workers_data = data.get("workers")
    if workers_data is not None and isinstance(workers_data, dict) and "tier_order" in workers_data:
        raw_tiers = workers_data.get("tier_order") or []
        tier_list: List[WorkerTier] = []
        for tier in raw_tiers:
            if isinstance(tier, dict):
                tier_list.append(
                    WorkerTier(
                        name=tier.get("name", ""),
                        harness=tier.get("harness", "native"),
                        model=tier.get("model", ""),
                        cost_per_m_tokens=float(tier.get("cost_per_m_tokens", 0.0)),
                        max_retries=int(tier.get("max_retries", 2)),
                        best_for=list(tier.get("best_for", [])),
                    )
                )
            elif isinstance(tier, WorkerTier):
                tier_list.append(tier)
        workers = WorkersConfig(tier_order=tier_list)
    else:
        workers = WorkersConfig()

    # Concurrency
    concurrency_data = data.get("concurrency") or {}
    concurrency = ConcurrencyConfig(
        parallel_tasks=concurrency_data.get("parallel_tasks", True),
        max_parallel_workers=int(concurrency_data.get("max_parallel_workers", 4)),
        layout_strategy=concurrency_data.get("layout_strategy", "tiled"),
        isolation_mode=concurrency_data.get("isolation_mode", "git_worktree"),
    )

    return MeisterConfig(
        version=version,
        master=master,
        architect=architect,
        workers=workers,
        concurrency=concurrency,
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
        return MeisterConfig()

    with open(target_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return _parse_config_dict(data)
