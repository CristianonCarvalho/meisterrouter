"""Worker Spawner and Multi-Model Tier Hierarchy for Herdr.

Manages worker model progression, pane spawning in Herdr, command
resolution for native and third-party harnesses, and automated
quota / rate limit detection for rapid failover.
"""

from __future__ import annotations

import logging
import re
import json
from typing import Optional, Tuple, Union, Any, List
from meister.config import MeisterConfig, WorkerTier, load_config
from meister.herdr.client import HerdrSocketClient

logger = logging.getLogger(__name__)

# Structured error codes and types for CLI / JSON signals (Achado #22)
STRUCTURED_ERROR_CODES = {429, 402, "429", "402", "rate_limit_exceeded", "insufficient_quota", "resource_exhausted"}
STRUCTURED_ERROR_TYPES = {"rate_limit_error", "insufficient_quota", "overloaded_error", "resource_exhausted"}

ANCHORED_QUOTA_PATTERNS = [
    # HTTP error codes with explicit provider message
    re.compile(r"(?:^|[\s\[\(])(?:error|http|status|code)[\s:]*429\b[^\n]*?(?:too many requests|rate limit|quota|exceeded|error)", re.IGNORECASE),
    re.compile(r"(?:^|[\s\[\(])(?:error|http|status|code)[\s:]*402\b[^\n]*?(?:payment|credit|balance|quota|insufficient|error)", re.IGNORECASE),
    re.compile(r"(?:^|[\s\[\(])(?:429|402)\s+(?:Too Many Requests|Payment Required)", re.IGNORECASE),
    re.compile(r"Credit balance too low\s*\(\d+\)", re.IGNORECASE),

    # Provider-specific exception classes
    re.compile(r"\b(?:openai|anthropic|google|openrouter)\.(?:RateLimitError|OverloadedError|NotFoundError|APIStatusError|ResourceExhausted)\b", re.IGNORECASE),

    # Explicit quota/credit exhaustion phrases from provider API error responses
    re.compile(r"(?:^|[^\w])(?:insufficient_quota|quota\s+exceeded|exceeded\s+(?:your\s+)?(?:current\s+)?quota|resource_exhausted)\b", re.IGNORECASE),
    re.compile(r"\bYou exceeded your current quota\b", re.IGNORECASE),
    re.compile(r"\b(?:credit\s+balance(?:\s+is)?\s+too\s+low|out\s+of\s+credits?|insufficient\s+credits?)\b", re.IGNORECASE),
    re.compile(r"\b(?:error|fatal|exception)[\s:]+.*?(?:model\s+(?:['\"\w\.\-]+\s+)?(?:not\s+found|not\s+active|is\s+inactive|does\s+not\s+exist|unavailable)|unsupported\s+model|invalid\s+model|model_not_found)\b", re.IGNORECASE),
    re.compile(r"\bHTTP\s+404\s*:\s*(?:unsupported\s+model|model_not_found)", re.IGNORECASE),
]

FALSE_POSITIVE_INDICATORS = (
    "middleware",
    "handler for",
    "in test",
    "test_",
    "mock_",
    "def ",
    "class ",
    "assert ",
    "//",
    "/*",
)


def detect_quota_or_rate_limit(output: Optional[str], is_stderr: bool = False) -> bool:
    """Detecta deterministicamente se a saída de terminal ou API contém exaustão de cota ou rate limit.

    Elimina falsos positivos em código/testes (Achado #22) através de:
    1. Detecção de sinais estruturados JSON (ex: codex exec --json, respostas OpenAI/Anthropic/Google).
    2. Ancoragem estrita de regex com descarte de linhas de código, middleware e fixtures de teste.
    """
    if not output:
        return False

    raw_text = str(output).strip()
    if not raw_text:
        return False

    # 1. Sinal estruturado por CLI / JSON (Achado #22)
    try:
        if (raw_text.startswith("{") and raw_text.endswith("}")) or (raw_text.startswith("[") and raw_text.endswith("]")):
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                ev_type = parsed.get("type")
                if ev_type in ("rate_limits", "rate_limit_error"):
                    return True
                err = parsed.get("error")
                if isinstance(err, dict):
                    if err.get("code") in STRUCTURED_ERROR_CODES or err.get("type") in STRUCTURED_ERROR_TYPES:
                        return True
                    if any(pattern.search(str(err.get("message", ""))) for pattern in ANCHORED_QUOTA_PATTERNS):
                        return True
    except Exception:
        pass

    # 2. Varredura linha a linha com eliminação de falsos positivos
    for line in raw_text.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue

        line_lower = line_clean.lower()

        # Descarta linhas que são declarações de código ou testes (evita falso positivo 4/4)
        if any(ind in line_lower for ind in FALSE_POSITIVE_INDICATORS):
            continue

        if any(pattern.search(line_clean) for pattern in ANCHORED_QUOTA_PATTERNS):
            return True

    return False


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

    def get_next_available_tier(
        self,
        current_tier_name: str,
        state_manager: Optional[Any] = None,
    ) -> Optional[WorkerTier]:
        """Retorna o próximo tier disponível na sequência que não esteja com circuit breaker aberto (Achado #23)."""
        tiers = self.config.workers.tier_order
        curr_idx = -1
        for i, tier in enumerate(tiers):
            if tier.name == current_tier_name:
                curr_idx = i
                break

        if curr_idx == -1:
            return None

        for next_tier in tiers[curr_idx + 1:]:
            if state_manager is not None:
                # Verifica circuit breaker para o tier e harness
                harness_ok = state_manager.is_harness_available(next_tier.harness)
                tier_ok = state_manager.is_harness_available(next_tier.name)
                if not harness_ok or not tier_ok:
                    logger.info("Tier %s (%s) está em cooldown no circuit breaker. Pulando...", next_tier.name, next_tier.harness)
                    continue
            return next_tier

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
            if task_context and "description" in task_context and task_context["description"]:
                cmd.extend(["--task", str(task_context["description"])])
                if "target_files" in task_context and task_context["target_files"]:
                    files_str = ",".join(str(f) for f in task_context["target_files"])
                    cmd.extend(["--files", files_str])
        elif harness == "claude":
            cmd = ["claude"]
            if task_context and "description" in task_context and task_context["description"]:
                cmd.append("--dangerously-skip-permissions")
                if tier_obj.model:
                    cmd.extend(["--model", tier_obj.model])
                cmd.extend(["-p", str(task_context["description"])])
            elif tier_obj.model:
                cmd.extend(["--model", tier_obj.model])
        elif harness == "codex":
            cmd = ["codex"]
            if task_context and "description" in task_context and task_context["description"]:
                cmd.extend(["exec", "--dangerously-bypass-approvals-and-sandbox"])
                if tier_obj.model:
                    cmd.extend(["-m", tier_obj.model])
                cmd.append(str(task_context["description"]))
            elif tier_obj.model:
                cmd.extend(["--model", tier_obj.model])
        elif harness in ("antigravity", "agy"):
            import shutil
            bin_name = "agy" if shutil.which("agy") else "antigravity"
            cmd = [bin_name]
            if task_context and "description" in task_context and task_context["description"]:
                cmd.append("--dangerously-skip-permissions")
                if tier_obj.model:
                    cmd.extend(["--model", tier_obj.model])
                cmd.extend(["-p", str(task_context["description"])])
            elif tier_obj.model:
                cmd.extend(["--model", tier_obj.model])
        elif harness in ("copilot", "github-copilot"):
            # Adaptador GitHub Copilot CLI (verificado contra GitHub Copilot CLI 1.0.88).
            cmd = ["copilot"]
            if task_context and "description" in task_context and task_context["description"]:
                cmd.extend(["-p", str(task_context["description"]), "--allow-all", "--no-ask-user"])
                if tier_obj.model and tier_obj.model not in ("default", "copilot", "auto"):
                    cmd.extend(["--model", tier_obj.model])
            elif tier_obj.model and tier_obj.model not in ("default", "copilot", "auto"):
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
        cwd: Optional[str] = None,
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
        split_kwargs: dict[str, Any] = {
            "direction": direction,
            "command": cmd,
            "split_ratio": split_ratio,
        }
        if cwd is not None:
            split_kwargs["cwd"] = cwd
        pane_id = await self.herdr_client.split_pane(**split_kwargs)
        return pane_id, tier

    async def spawn_worker_tab(
        self,
        tier_name: str,
        task_context: Optional[dict] = None,
        cwd: Optional[str] = None,
        label: Optional[str] = None,
        focus: bool = False,
    ) -> Tuple[str, str, WorkerTier]:
        """Create a dedicated background tab in Herdr for the worker (Achado #13).

        Returns:
            Tuple of (tab_id, pane_id, WorkerTier)
        """
        tier = self.get_tier(tier_name)
        if tier is None:
            raise ValueError(f"Worker tier '{tier_name}' not found in configuration")

        if self.herdr_client is None:
            raise RuntimeError("Herdr client is required to spawn worker tabs")

        cmd = self.resolve_command(tier, task_context)
        tab_label = label or (f"worker:{task_context.get('id')}" if task_context else f"worker:{tier_name}")
        tab_id, pane_id = await self.herdr_client.create_tab(
            cwd=cwd,
            label=tab_label,
            focus=focus,
        )

        if cmd and pane_id:
            cmd_str = (
                task_context.get("command_str")
                if task_context and "command_str" in task_context
                else (" ".join(cmd) if isinstance(cmd, list) else str(cmd))
            )
            try:
                if hasattr(self.herdr_client, "wait_pane_ready"):
                    await self.herdr_client.wait_pane_ready(pane_id)
                await self.herdr_client.send_text(pane_id, f"{cmd_str}\n")
            except Exception as e:
                logger.debug("Could not send command to new tab pane %s: %s", pane_id, e)

        return tab_id, pane_id, tier

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
    cwd: Optional[str] = None,
) -> Tuple[str, WorkerTier]:
    """Module-level helper to spawn a worker pane."""
    if spawner is not None:
        return await spawner.spawn_worker_pane(
            tier_name,
            task_context=task_context,
            direction=direction,
            split_ratio=split_ratio,
            cwd=cwd,
        )
    cfg = config or load_config()
    s = WorkerSpawner(cfg, herdr_client=herdr_client)
    return await s.spawn_worker_pane(
        tier_name,
        task_context=task_context,
        direction=direction,
        split_ratio=split_ratio,
        cwd=cwd,
    )
