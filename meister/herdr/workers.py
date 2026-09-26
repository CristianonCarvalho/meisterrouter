"""Worker Spawner and Multi-Model Tier Hierarchy for Herdr.

Manages worker model progression, pane spawning in Herdr, command
resolution for native and third-party harnesses, and automated
quota / rate limit detection for rapid failover.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple, Union, Any, List
from meister.config import MeisterConfig, WorkerTier, load_config
from meister.herdr.client import HerdrSocketClient

logger = logging.getLogger(__name__)

# Patterns indicating quota exhaustion, rate limit, or model overload
QUOTA_RATE_LIMIT_PATTERNS = [
    re.compile(r"(?:error|http|status|code)[\s:]*429\b", re.IGNORECASE),
    re.compile(r"[\(\[]429[\)\]]"),
    re.compile(r"\b429\s*[:\-\s]*(?:too many requests|rate limit|quota|error)", re.IGNORECASE),
    re.compile(r"(?:error|http|status|code)[\s:]*402\b", re.IGNORECASE),
    re.compile(r"[\(\[]402[\)\]]"),
    re.compile(r"\b402\s*[:\-\s]*(?:payment|credit|balance|quota|error)", re.IGNORECASE),
    re.compile(r"rate\s*limit", re.IGNORECASE),
    re.compile(r"too\s+many\s+requests", re.IGNORECASE),
    re.compile(r"credit\s+balance\s+too\s+low", re.IGNORECASE),
    re.compile(r"insufficient\s+(?:quota|credits?|funds|balance)", re.IGNORECASE),
    re.compile(r"quota\s+exceeded", re.IGNORECASE),
    re.compile(r"exceeded\s+(?:your\s+)?(?:current\s+)?quota", re.IGNORECASE),
    re.compile(r"resource_exhausted", re.IGNORECASE),
    re.compile(r"payment\s+required", re.IGNORECASE),
    re.compile(r"overloaded(?:error|_error)?\b", re.IGNORECASE),
    re.compile(r"model\s+is\s+overloaded", re.IGNORECASE),
]


def detect_quota_or_rate_limit(output: Optional[str]) -> bool:
    """Detect if terminal or API output contains quota exhaustion or rate limits.

    Matches HTTP 429, 402, credit balance warnings, RESOURCE_EXHAUSTED,
    and provider overload signals.
    """
    if not output:
        return False
    return any(pattern.search(output) for pattern in QUOTA_RATE_LIMIT_PATTERNS)


class WorkerSpawner:
    """Spawns and manages worker agent panes across model tiers."""

    def __init__(
        self,
        config: MeisterConfig,
        herdr_client: Optional[HerdrSocketClient] = None,
    ):
        self.config = config
        self.herdr_client = herdr_client

    def get_tier(self, name: str) -> Optional[WorkerTier]:
        """Lookup a WorkerTier by its identifier in config.workers.tier_order."""
        for tier in self.config.workers.tier_order:
            if tier.name == name:
                return tier
        return None

    def get_next_tier(self, current_tier_name: str) -> Optional[WorkerTier]:
        """Return the next escalated tier in sequence, or None if at top tier."""
        tiers = self.config.workers.tier_order
        for i, tier in enumerate(tiers):
            if tier.name == current_tier_name:
                if i + 1 < len(tiers):
                    return tiers[i + 1]
                return None
        return None

    def resolve_command(
        self,
        tier: Union[WorkerTier, str],
        task_context: Optional[dict] = None,
    ) -> List[str]:
        """Resolve command arguments to spawn a worker pane for a given tier.

        Supports native MeisterRouter workers (`meister worker --model <tier>`)
        and external CLI harnesses (`claude`, `codex`, etc.).
        """
        if task_context and "command" in task_context and task_context["command"]:
            return list(task_context["command"])

        if isinstance(tier, str):
            tier_obj = self.get_tier(tier)
            if tier_obj is None:
                tier_obj = WorkerTier(name=tier)
        else:
            tier_obj = tier

        harness = (tier_obj.harness or "native").strip().lower()

        if harness == "native":
            model_arg = tier_obj.name or tier_obj.model
            cmd = ["meister", "worker", "--model", model_arg]
        elif harness in ("claude", "codex"):
            cmd = [harness]
            if tier_obj.model:
                cmd.extend(["--model", tier_obj.model])
        else:
            cmd = [harness]
            if tier_obj.model:
                cmd.extend(["--model", tier_obj.model])

        if task_context and "args" in task_context and isinstance(task_context["args"], list):
            cmd.extend(task_context["args"])

        return cmd

    async def spawn_worker_pane(
        self,
        tier_name: str,
        task_context: Optional[dict] = None,
        direction: str = "right",
        split_ratio: float = 0.5,
    ) -> Tuple[str, WorkerTier]:
        """Split a new Herdr pane and launch the appropriate worker command.

        Returns:
            Tuple of (pane_id, WorkerTier)
        """
        tier = self.get_tier(tier_name)
        if tier is None:
            raise ValueError(f"Worker tier '{tier_name}' not found in configuration")

        if self.herdr_client is None:
            raise RuntimeError("Herdr client is required to spawn worker panes")

        cmd = self.resolve_command(tier, task_context)
        pane_id = await self.herdr_client.split_pane(
            direction=direction,
            command=cmd,
            split_ratio=split_ratio,
        )
        return pane_id, tier

    async def escalate_worker(
        self,
        current_pane_id: str,
        current_tier_name: str,
        task_context: Optional[dict] = None,
        direction: str = "right",
        split_ratio: float = 0.5,
    ) -> Optional[Tuple[str, WorkerTier]]:
        """Interrupt a failing or quota-blocked worker pane and spawn the next higher tier."""
        next_tier = self.get_next_tier(current_tier_name)
        if next_tier is None:
            logger.warning(
                "Worker tier '%s' reached maximum tier level, cannot escalate.",
                current_tier_name,
            )
            return None

        if self.herdr_client is not None:
            try:
                await self.herdr_client.send_interrupt(current_pane_id)
            except Exception as e:
                logger.warning("Failed to interrupt pane '%s': %s", current_pane_id, e)

        return await self.spawn_worker_pane(
            next_tier.name,
            task_context=task_context,
            direction=direction,
            split_ratio=split_ratio,
        )


def get_next_tier(
    current_tier_name: str,
    spawner: Optional[WorkerSpawner] = None,
    config: Optional[MeisterConfig] = None,
) -> Optional[WorkerTier]:
    """Module-level helper to lookup next tier in hierarchy."""
    if spawner is not None:
        return spawner.get_next_tier(current_tier_name)
    cfg = config or load_config()
    s = WorkerSpawner(cfg)
    return s.get_next_tier(current_tier_name)


async def spawn_worker_pane(
    tier_name: str,
    task_context: Optional[dict] = None,
    spawner: Optional[WorkerSpawner] = None,
    herdr_client: Optional[HerdrSocketClient] = None,
    config: Optional[MeisterConfig] = None,
    direction: str = "right",
    split_ratio: float = 0.5,
) -> Tuple[str, WorkerTier]:
    """Module-level helper to spawn a worker pane."""
    if spawner is not None:
        return await spawner.spawn_worker_pane(
            tier_name,
            task_context=task_context,
            direction=direction,
            split_ratio=split_ratio,
        )
    cfg = config or load_config()
    s = WorkerSpawner(cfg, herdr_client=herdr_client)
    return await s.spawn_worker_pane(
        tier_name,
        task_context=task_context,
        direction=direction,
        split_ratio=split_ratio,
    )
