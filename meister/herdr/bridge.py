"""Herdr Event Bridge & Parallel Failover Supervisor.

Integrates HerdrSocketClient, WorkerSpawner, TaskDAG, and meister.jev for
concurrent multi-pane subtask execution, dynamic screen splits bounded by
concurrency configuration, and reactive zero-latency quota failover.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Union

from meister.config import MeisterConfig, load_config
from meister.herdr.client import HerdrSocketClient
from meister.herdr.dag import SubtaskNode, TaskDAG, build_subtask_dag
from meister.herdr.workers import (
    WorkerSpawner,
    detect_quota_or_rate_limit,
)

logger = logging.getLogger(__name__)


def parse_architect_plan(output: str) -> List[Dict[str, Any]]:
    """Parse architect plan output into structured subtask dictionaries.

    Supports JSON blocks, pipe-delimited subtask lines, markdown task lists,
    or falls back to a single subtask envelope.
    """
    if not output or not output.strip():
        return []

    cleaned = output.strip()

    # 1. Attempt JSON block parsing
    json_match = re.search(r"```(?:json)?\s*(\[\s*\{.*?\}\s*\])\s*```", cleaned, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group(1))
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass

    # Direct JSON list
    if cleaned.startswith("[") and cleaned.endswith("]"):
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass

    # 2. Pipe-delimited structured format:
    # e.g., "1. id: step1 | files: [a.py] | depends: [step0]"
    pipe_pattern = re.compile(
        r"(?:^\s*(?:\d+[\.\)]|[-*])\s*)?(?:id:\s*([a-zA-Z0-9_\-]+))"
        r"(?:.*?files:\s*\[([^\]]*)\])?"
        r"(?:.*?depends(?:_on)?:\s*\[([^\]]*)\])?",
        re.MULTILINE | re.IGNORECASE,
    )
    matches = pipe_pattern.findall(cleaned)
    if matches and any(m[0] for m in matches):
        steps = []
        for task_id, raw_files, raw_deps in matches:
            if not task_id:
                continue
            files = [f.strip().strip("'\"") for f in raw_files.split(",") if f.strip()] if raw_files else []
            deps = [d.strip().strip("'\"") for d in raw_deps.split(",") if d.strip()] if raw_deps else []
            steps.append({
                "id": task_id.strip(),
                "description": f"Subtask {task_id.strip()}",
                "target_files": files,
                "depends_on": deps,
            })
        if steps:
            return steps

    # 3. Line-based list fallback
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    task_lines = [l for l in lines if re.match(r"^(?:\d+[\.\)]|[-*])\s+", l)]
    if task_lines:
        steps = []
        for idx, line in enumerate(task_lines):
            task_id = f"task_{idx + 1}"
            desc = re.sub(r"^(?:\d+[\.\)]|[-*])\s+", "", line)
            steps.append({
                "id": task_id,
                "description": desc,
                "target_files": [],
                "depends_on": [],
            })
        return steps

    # 4. Fallback: single task
    return [{
        "id": "task_1",
        "description": cleaned[:300],
        "target_files": [],
        "depends_on": [],
    }]


class HerdrEventBridge:
    """Central orchestration supervisor bridging Herdr socket events and task execution."""

    def __init__(
        self,
        config: Optional[MeisterConfig] = None,
        client: Optional[HerdrSocketClient] = None,
        spawner: Optional[WorkerSpawner] = None,
    ):
        self.config = config or load_config()
        self.client = client
        self.spawner = spawner or WorkerSpawner(self.config, herdr_client=self.client)
        if self.client is not None and self.spawner.herdr_client is None:
            self.spawner.herdr_client = self.client

        # Concurrency limit from config
        max_workers = 4
        if self.config and self.config.concurrency:
            max_workers = max(1, int(self.config.concurrency.max_parallel_workers))
        self._concurrency_semaphore = asyncio.Semaphore(max_workers)

        # Active workers tracking: pane_id -> worker metadata dict
        self.active_workers: Dict[str, Dict[str, Any]] = {}

        # Reactive quota notification events: pane_id -> asyncio.Event
        self._quota_events: Dict[str, asyncio.Event] = {}

    async def handle_herdr_event(self, event: Dict[str, Any]) -> None:
        """Process incoming Herdr socket notifications (e.g. quota errors, outputs)."""
        if not isinstance(event, dict):
            return

        params = event.get("params", event)
        pane_id = params.get("pane_id") or params.get("pane")
        output = params.get("output") or params.get("text") or params.get("data") or ""

        if not pane_id or pane_id not in self.active_workers:
            return

        worker_info = self.active_workers[pane_id]

        if detect_quota_or_rate_limit(str(output)):
            logger.warning(
                "Reactive quota/rate-limit detected in pane %s: %s",
                pane_id,
                output,
            )
            worker_info["status"] = "quota_error"

            # Interrupt runaway worker pane immediately to preserve tokens
            if self.client is not None:
                try:
                    await self.client.send_interrupt(pane_id)
                except Exception as e:
                    logger.debug("Failed sending interrupt to pane %s: %s", pane_id, e)

            # Signal any waiting subtask execution coroutine
            if pane_id in self._quota_events:
                self._quota_events[pane_id].set()

    async def execute_subtask(
        self,
        subtask: Union[Dict[str, Any], SubtaskNode],
        initial_tier: Optional[str] = None,
    ) -> bool:
        """Execute a single subtask with automated quota failover and tier escalation.

        Returns True if subtask completes successfully, False otherwise.
        """
        if isinstance(subtask, SubtaskNode):
            task_dict = subtask.to_dict()
        else:
            task_dict = dict(subtask)

        task_id = str(task_dict.get("id", "task"))
        description = str(task_dict.get("description", task_id))
        target_files = task_dict.get("target_files", [])

        # Determine starting tier
        current_tier = initial_tier
        if not current_tier:
            if self.config.workers and self.config.workers.tier_order:
                current_tier = self.config.workers.tier_order[0].name
            else:
                current_tier = "luna"

        direction = "right"
        split_ratio = 0.5

        while current_tier:
            tier_obj = self.spawner.get_tier(current_tier)
            if not tier_obj:
                logger.error("Tier '%s' not found in configuration", current_tier)
                return False

            if self.client is None:
                raise RuntimeError("Herdr client is required to execute subtask")

            try:
                pane_id, _ = await self.spawner.spawn_worker_pane(
                    tier_name=current_tier,
                    task_context=task_dict,
                    direction=direction,
                    split_ratio=split_ratio,
                )
            except Exception as e:
                logger.error("Failed to spawn worker pane for tier %s: %s", current_tier, e)
                return False

            # Register active worker state
            quota_event = asyncio.Event()
            self._quota_events[pane_id] = quota_event
            self.active_workers[pane_id] = {
                "task_id": task_id,
                "current_tier": current_tier,
                "status": "running",
                "subtask": task_dict,
            }

            prompt_text = (
                f"Execute subtask {task_id}: {description}\n"
                f"Target files: {target_files}\n"
            )

            prompt_task = asyncio.create_task(
                self.client.prompt_agent(
                    pane_id=pane_id,
                    prompt=prompt_text,
                    wait_until="done",
                )
            )
            quota_waiter = asyncio.create_task(quota_event.wait())

            done, pending = await asyncio.wait(
                [prompt_task, quota_waiter],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

            self._quota_events.pop(pane_id, None)

            # Check if reactive quota event triggered
            if quota_event.is_set() or self.active_workers.get(pane_id, {}).get("status") == "quota_error":
                logger.info("Reactive failover triggered for subtask %s in pane %s", task_id, pane_id)
                next_tier = self.spawner.get_next_tier(current_tier)
                if next_tier is None:
                    logger.error("Maximum tier reached, cannot escalate further for task %s", task_id)
                    return False
                current_tier = next_tier.name
                continue

            # Process prompt completion
            try:
                prompt_result = prompt_task.result()
            except Exception as e:
                if detect_quota_or_rate_limit(str(e)):
                    await self.client.send_interrupt(pane_id)
                    next_tier = self.spawner.get_next_tier(current_tier)
                    if next_tier is None:
                        return False
                    current_tier = next_tier.name
                    continue
                logger.error("Worker execution error in pane %s: %s", pane_id, e)
                return False

            # Inspect terminal output and prompt response for quota errors
            terminal_output = ""
            try:
                terminal_output = await self.client.read_pane(pane_id)
            except Exception:
                pass

            combined_output = f"{prompt_result.get('output', '')} {terminal_output}"
            if detect_quota_or_rate_limit(combined_output):
                logger.info("Quota detected in terminal output for pane %s, escalating tier", pane_id)
                await self.client.send_interrupt(pane_id)
                next_tier = self.spawner.get_next_tier(current_tier)
                if next_tier is None:
                    return False
                current_tier = next_tier.name
                continue

            if prompt_result.get("status") == "error":
                logger.warning("Subtask %s returned error status", task_id)
                return False

            self.active_workers[pane_id]["status"] = "done"
            return True

        return False

    async def execute_parallel_batch(
        self,
        subtasks: List[Union[Dict[str, Any], SubtaskNode]],
    ) -> bool:
        """Dispatch a batch of independent subtasks concurrently bounded by max_parallel_workers."""
        if not subtasks:
            return True

        async def _bounded_execute(task: Union[Dict[str, Any], SubtaskNode]) -> bool:
            async with self._concurrency_semaphore:
                return await self.execute_subtask(task)

        tasks = [_bounded_execute(task) for task in subtasks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, Exception):
                logger.error("Subtask raised exception during parallel batch execution: %s", res)
                return False
            if not res:
                return False

        return True

    async def execute_plan(
        self,
        steps: List[Union[Dict[str, Any], SubtaskNode]],
    ) -> bool:
        """Decompose steps into a TaskDAG and execute batches in topological dependency order."""
        if not steps:
            return True

        dag = build_subtask_dag(steps)
        batches = dag.get_execution_batches()

        for idx, batch in enumerate(batches):
            logger.info("Executing parallel batch %d/%d (%d subtasks)", idx + 1, len(batches), len(batch))
            batch_success = await self.execute_parallel_batch(batch)
            if not batch_success:
                logger.error("Batch %d failed. Halting plan execution.", idx + 1)
                return False

        return True

    async def run_orchestration_cycle(
        self,
        workspace_id: str,
        architect_pane_id: str,
    ) -> bool:
        """Run complete orchestration cycle: read plan, execute DAG batches, notify Herdr."""
        if self.client is None:
            raise RuntimeError("Herdr client is required for orchestration cycle")

        if not self.client.is_connected:
            await self.client.connect()

        # Subscribe to reactive socket events
        await self.client.subscribe_events(self.handle_herdr_event)

        # Read architect's plan from the architect pane
        raw_plan = await self.client.read_pane(architect_pane_id)
        steps = parse_architect_plan(raw_plan)

        logger.info(
            "Parsed %d actionable steps from architect pane %s in workspace %s",
            len(steps),
            architect_pane_id,
            workspace_id,
        )

        plan_success = await self.execute_plan(steps)

        if plan_success:
            msg = f"MeisterRouter: Plan executed successfully in workspace {workspace_id}!"
            await self.client.show_notification(msg)
            return True
        else:
            msg = f"MeisterRouter: Plan execution failed in workspace {workspace_id}."
            await self.client.show_notification(msg)
            return False
