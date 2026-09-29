"""Herdr Event Bridge & Parallel Failover Supervisor.

Integrates HerdrSocketClient, WorkerSpawner, TaskDAG, and meister.jev for
concurrent multi-pane subtask execution, dynamic screen splits bounded by
concurrency configuration, and reactive zero-latency quota failover.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
import shlex
import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Union

from meister.config import MeisterConfig, load_config
from meister.herdr.client import HerdrSocketClient, HerdrRPCError, HerdrConnectionError
from meister.herdr.dag import SubtaskNode, build_subtask_dag
from meister.herdr.workers import (
    WorkerSpawner,
    detect_quota_or_rate_limit,
)
from meister.state import StateManager, RunState, SubtaskState, compute_subtask_id
from meister.faults import crash_point
from meister.logger import log_event
from meister.worker import (
    write_atomic_json,
    read_atomic_json,
    WorkerInfrastructureError,
    ensure_meister_dir,
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
    task_lines = [item for item in lines if re.match(r"^(?:\d+[\.\)]|[-*])\s+", item)]
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
        gate: Optional[Any] = None,
        state_manager: Optional[StateManager] = None,
    ):
        self.config = config or load_config()
        self.client = client
        self.spawner = spawner or WorkerSpawner(self.config, herdr_client=self.client)
        if self.client is not None and self.spawner.herdr_client is None:
            self.spawner.herdr_client = self.client
        self.gate = gate
        self.state_manager = state_manager
        self.current_run_id: Optional[str] = None
        self._integration_pipeline: Optional[Any] = None
        self._merge_lock = asyncio.Lock()

        # Concurrency limit from config
        max_workers = 4
        if self.config and self.config.concurrency:
            if not getattr(self.config.concurrency, "parallel_tasks", True):
                max_workers = 1
            else:
                max_workers = max(1, int(self.config.concurrency.max_parallel_workers))
        self._concurrency_semaphore = asyncio.Semaphore(max_workers)

        # Active workers tracking: pane_id -> worker metadata dict
        self.active_workers: Dict[str, Dict[str, Any]] = {}

        # Reactive quota notification events: pane_id -> asyncio.Event
        self._quota_events: Dict[str, asyncio.Event] = {}

        # Reactive pane exit notification events: pane_id -> asyncio.Event (Achado #10)
        self._exit_events: Dict[str, asyncio.Event] = {}

    def get_state_manager(self) -> StateManager:
        """Retorna o gerenciador de estado SQLite instanciado ou inicializa um novo."""
        if self.state_manager is None:
            self.state_manager = StateManager()
        return self.state_manager

    async def handle_herdr_event(self, event: Dict[str, Any]) -> None:
        """Process incoming Herdr socket notifications (e.g. quota errors, outputs)."""
        if not isinstance(event, dict):
            return

        params = event.get("params", event)
        pane_id = params.get("pane_id") or params.get("pane")
        ev_type = params.get("type") or event.get("type") or event.get("method") or event.get("event")

        # Tratamento reativo de evento pane.exited (Achado #10)
        if ev_type in ("pane_exited", "pane.exited") and pane_id:
            if pane_id in self._exit_events:
                self._exit_events[pane_id].set()

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

            current_tier = worker_info.get("current_tier")
            if current_tier:
                sm = self.get_state_manager()
                sm.record_harness_failure(current_tier, is_quota=True)

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
        run_id: Optional[str] = None,
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

        active_run_id = run_id or self.current_run_id
        sm = self.get_state_manager()
        subtask_id = compute_subtask_id(active_run_id or "default", task_id, description)

        # Retomada idempotente: se a subtask já estiver COMPLETED no SQLite, pula reexecução (Achados #7, #28)
        if active_run_id:
            existing = sm.get_subtask(subtask_id)
            if existing and existing.get("status") == SubtaskState.COMPLETED.value:
                logger.info("Subtask %s (%s) já concluída na execução %s. Pulando.", task_id, subtask_id, active_run_id)
                return True

        # Determine starting tier
        current_tier = initial_tier
        if not current_tier:
            if self.config.workers and self.config.workers.tier_order:
                current_tier = self.config.workers.tier_order[0].name
            else:
                current_tier = "luna"

        direction = "right"
        split_ratio = 0.5

        use_tabs = True
        if self.config and self.config.concurrency:
            if getattr(self.config.concurrency, "layout_strategy", "tabs") in ("tiled", "split"):
                use_tabs = False

        subtask_wt = None
        try:
            if self._integration_pipeline is not None and self._integration_pipeline.integration_info is not None:
                try:
                    nonce = uuid.uuid4().hex[:6]
                    base_branch = self._integration_pipeline.integration_info.branch_name
                    subtask_wt = await asyncio.to_thread(
                        self._integration_pipeline.wt_mgr.create_worktree,
                        task_id=f"{active_run_id}_{task_id}_{nonce}",
                        base_ref=base_branch,
                    )
                    task_dict["cwd"] = subtask_wt.worktree_path
                    task_dict["worktree"] = subtask_wt.worktree_path
                except Exception as e:
                    logger.warning("Falha ao criar worktree para subtask %s: %s", task_id, e)

            attempt_count = 0

            while current_tier:
                attempt_count += 1
                tier_obj = self.spawner.get_tier(current_tier)
                if not tier_obj:
                    logger.error("Tier '%s' not found in configuration", current_tier)
                    if active_run_id:
                        try:
                            sm.transition_subtask(subtask_id, to_state=SubtaskState.FAILED, error=f"Tier {current_tier} not found")
                        except Exception:
                            pass
                    return False

                if self.client is None:
                    raise RuntimeError("Herdr client is required to execute subtask")

                if active_run_id:
                    try:
                        sm.transition_subtask(
                            subtask_id,
                            to_state=SubtaskState.RUNNING,
                            assigned_tier=current_tier,
                        )
                    except Exception as e:
                        logger.debug("State transition error: %s", e)

                resolved_cwd = task_dict.get("cwd") or task_dict.get("worktree") or os.getcwd()
                ensure_meister_dir(resolved_cwd)
                runs_dir = os.path.join(resolved_cwd, ".meister", "runs")
                os.makedirs(runs_dir, exist_ok=True)

                task_file = os.path.join(runs_dir, f"{active_run_id}_{task_id}_{attempt_count}_task.json")
                result_file = os.path.join(runs_dir, f"{active_run_id}_{task_id}_{attempt_count}.json")

                resolved_config_path = getattr(self.config, "config_path", None) or os.environ.get("MEISTER_CONFIG_PATH")
                if not resolved_config_path:
                    for fname in ("meister.config.yaml", "meister.config.yml"):
                        cand = os.path.join(resolved_cwd, fname)
                        if os.path.exists(cand):
                            resolved_config_path = os.path.abspath(cand)
                            break

                timeout_val = float(task_dict.get("timeout", 300.0))
                task_payload = {
                    "run_id": active_run_id,
                    "task_id": task_id,
                    "model": current_tier,
                    "task": description,
                    "target_files": target_files or [],
                    "cwd": resolved_cwd,
                    "config_path": resolved_config_path,
                    "timeout": timeout_val,
                    "result_file": result_file,
                    "log_dir": os.environ.get("MEISTER_LOG_DIR"),
                }
                write_atomic_json(task_file, task_payload)
                task_dict["task_file"] = task_file
                task_dict["result_file"] = result_file

                cmd_parts = [sys.executable, "-m", "meister.cli", "run-task", task_file]
                env_vars = ["MEISTER_IN_PANE=1"]
                if os.environ.get("MEISTER_LOG_DIR"):
                    env_vars.append(f"MEISTER_LOG_DIR={shlex.quote(os.environ['MEISTER_LOG_DIR'])}")
                if active_run_id:
                    env_vars.append(f"MEISTER_RUN_ID={shlex.quote(active_run_id)}")
                task_dict["command"] = cmd_parts
                task_dict["command_str"] = f"{' '.join(env_vars)} {' '.join(shlex.quote(p) for p in cmd_parts)}"

                tab_id = None
                pane_id = None
                spawn_cwd = subtask_wt.worktree_path if subtask_wt else (task_dict.get("cwd") or task_dict.get("worktree"))
                try:
                    try:
                        if use_tabs and hasattr(self.spawner, "spawn_worker_tab"):
                            tab_id, pane_id, _ = await self.spawner.spawn_worker_tab(
                                tier_name=current_tier,
                                task_context=task_dict,
                                cwd=spawn_cwd,
                                label=f"worker:{task_id}",
                                focus=False,
                            )
                        else:
                            pane_id, _ = await self.spawner.spawn_worker_pane(
                                tier_name=current_tier,
                                task_context=task_dict,
                                direction=direction,
                                split_ratio=split_ratio,
                                cwd=spawn_cwd,
                            )
                    except Exception as e:
                        is_infra = isinstance(e, (WorkerInfrastructureError, HerdrRPCError, HerdrConnectionError, RuntimeError, OSError)) or "agent" in str(e).lower()
                        if is_infra:
                            logger.error("Infrastructure error spawning worker pane/tab for tier %s: %s. Aborting without tier escalation.", current_tier, e)
                            log_event(
                                event_type="worker_error",
                                run_id=active_run_id,
                                task_id=task_id,
                                attempt=attempt_count,
                                tier=current_tier,
                                exit_code=2,
                                status="infrastructure_error",
                                error=str(e),
                            )
                            if active_run_id:
                                try:
                                    sm.transition_subtask(subtask_id, to_state=SubtaskState.FAILED, error=f"Infrastructure error: {e}")
                                except Exception:
                                    pass
                            return False

                        logger.warning("Failed to spawn worker pane/tab for tier %s: %s. Escalating to next tier...", current_tier, e)
                        sm.record_harness_failure(current_tier, is_quota=False)
                        next_tier = self.spawner.get_next_available_tier(current_tier, state_manager=sm)
                        if next_tier is not None:
                            current_tier = next_tier.name
                            continue
                        if active_run_id:
                            try:
                                sm.transition_subtask(subtask_id, to_state=SubtaskState.FAILED, error=str(e))
                            except Exception:
                                pass
                        return False

                    # Register active worker state and active pane
                    if active_run_id:
                        sm.register_pane(pane_id, run_id=active_run_id, subtask_id=subtask_id, tab_id=tab_id)

                    log_event(
                        event_type="worker_spawn",
                        run_id=active_run_id,
                        task_id=task_id,
                        attempt=attempt_count,
                        tier=current_tier,
                        pane_id=pane_id,
                    )
                    crash_point("after_worker_spawned", task_id=task_id, run_id=active_run_id)

                    quota_event = asyncio.Event()
                    self._quota_events[pane_id] = quota_event
                    exit_event = asyncio.Event()
                    self._exit_events[pane_id] = exit_event
                    self.active_workers[pane_id] = {
                        "task_id": task_id,
                        "current_tier": current_tier,
                        "status": "running",
                        "subtask": task_dict,
                    }

                    prompt_result: Optional[Dict[str, Any]] = None
                    infra_error: Optional[str] = None
                    start_wait = time.monotonic()
                    poll_interval = 0.2

                    while time.monotonic() - start_wait < timeout_val:
                        if os.path.exists(result_file):
                            res_data = read_atomic_json(result_file)
                            if res_data is not None:
                                prompt_result = res_data
                                try:
                                    os.remove(result_file)
                                except Exception:
                                    pass
                                try:
                                    if os.path.exists(task_file):
                                        os.remove(task_file)
                                except Exception:
                                    pass
                                break

                        if quota_event.is_set() or self.active_workers.get(pane_id, {}).get("status") == "quota_error":
                            break

                        if exit_event.is_set():
                            await asyncio.sleep(0.5)
                            if os.path.exists(result_file):
                                res_data = read_atomic_json(result_file)
                                if res_data is not None:
                                    prompt_result = res_data
                                    try:
                                        os.remove(result_file)
                                        if os.path.exists(task_file):
                                            os.remove(task_file)
                                    except Exception:
                                        pass
                                    break
                            infra_error = f"Worker no pane {pane_id} encerrou prematuramente (pane.exited) sem gerar resultado (erro de infraestrutura)"
                            break

                        await asyncio.sleep(poll_interval)

                    self._quota_events.pop(pane_id, None)
                    self._exit_events.pop(pane_id, None)

                    # Check if reactive quota event triggered
                    if quota_event.is_set() or self.active_workers.get(pane_id, {}).get("status") == "quota_error":
                        logger.info("Reactive failover triggered for subtask %s in pane %s", task_id, pane_id)
                        sm.record_harness_failure(current_tier, is_quota=True)
                        log_event(
                            event_type="quota_error",
                            run_id=active_run_id,
                            task_id=task_id,
                            attempt=attempt_count,
                            tier=current_tier,
                            exit_code=429,
                            error="Reactive quota detected in pane",
                        )
                        if active_run_id:
                            sm.unregister_pane(pane_id)
                            try:
                                sm.transition_subtask(subtask_id, to_state=SubtaskState.RETRYING, error="Quota/Rate-limit error in pane")
                            except Exception:
                                pass
                        if tab_id and hasattr(self.client, "close_tab"):
                            try:
                                await self.client.close_tab(tab_id)
                            except Exception:
                                pass

                        # Backoff requeue before jumping directly to expensive tiers (Achado #23)
                        base_backoff = float(os.environ.get("MEISTER_RETRY_BACKOFF", "0.01"))
                        await asyncio.sleep(base_backoff * (2 ** min(4, attempt_count - 1)))

                        next_tier = self.spawner.get_next_available_tier(current_tier, state_manager=sm)
                        if next_tier is None:
                            logger.error("Maximum tier reached, cannot escalate further for task %s", task_id)
                            if active_run_id:
                                try:
                                    sm.transition_subtask(subtask_id, to_state=SubtaskState.FAILED, error="Maximum tier reached")
                                except Exception:
                                    pass
                            return False
                        current_tier = next_tier.name
                        continue

                    if prompt_result is None:
                        terminal_output = ""
                        try:
                            terminal_output = await self.client.read_pane(pane_id)
                        except Exception:
                            pass

                        if detect_quota_or_rate_limit(terminal_output):
                            logger.info("Quota detected in terminal output for pane %s, escalating tier", pane_id)
                            sm.record_harness_failure(current_tier, is_quota=True)
                            log_event(
                                event_type="quota_error",
                                run_id=active_run_id,
                                task_id=task_id,
                                attempt=attempt_count,
                                tier=current_tier,
                                exit_code=429,
                                error="Terminal quota detected",
                            )
                            try:
                                await self.client.send_interrupt(pane_id)
                            except Exception:
                                pass
                            if active_run_id:
                                sm.unregister_pane(pane_id)
                                try:
                                    sm.transition_subtask(subtask_id, to_state=SubtaskState.RETRYING, error="Quota detected in terminal output")
                                except Exception:
                                    pass
                            if tab_id and hasattr(self.client, "close_tab"):
                                try:
                                    await self.client.close_tab(tab_id)
                                except Exception:
                                    pass
                            base_backoff = float(os.environ.get("MEISTER_RETRY_BACKOFF", "0.01"))
                            await asyncio.sleep(base_backoff * (2 ** min(4, attempt_count - 1)))
                            next_tier = self.spawner.get_next_available_tier(current_tier, state_manager=sm)
                            if next_tier is None:
                                if active_run_id:
                                    try:
                                        sm.transition_subtask(subtask_id, to_state=SubtaskState.FAILED, error="Quota error at maximum tier")
                                    except Exception:
                                        pass
                                return False
                            current_tier = next_tier.name
                            continue

                        # Falha rápida sem escalar tier em caso de erro de infraestrutura (Achados E2E-2 / P-2)
                        err_msg = infra_error or f"Worker no pane {pane_id} encerrou prematuramente (pane.exited) sem gerar resultado (erro de infraestrutura)"
                        logger.error("Infrastructure error in worker pane %s (%s): %s. Aborting without tier escalation.", pane_id, current_tier, err_msg)
                        log_event(
                            event_type="worker_error",
                            run_id=active_run_id,
                            task_id=task_id,
                            attempt=attempt_count,
                            tier=current_tier,
                            exit_code=2,
                            status="infrastructure_error",
                            error=err_msg,
                        )
                        try:
                            await self.client.send_interrupt(pane_id)
                        except Exception:
                            pass
                        if active_run_id:
                            sm.unregister_pane(pane_id)
                            try:
                                sm.transition_subtask(subtask_id, to_state=SubtaskState.FAILED, error=err_msg)
                            except Exception:
                                pass
                        if tab_id and hasattr(self.client, "close_tab"):
                            try:
                                await self.client.close_tab(tab_id)
                            except Exception:
                                pass
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
                        sm.record_harness_failure(current_tier, is_quota=True)
                        log_event(
                            event_type="quota_error",
                            run_id=active_run_id,
                            task_id=task_id,
                            attempt=attempt_count,
                            tier=current_tier,
                            exit_code=429,
                            error="Terminal quota detected",
                        )
                        await self.client.send_interrupt(pane_id)
                        if active_run_id:
                            sm.unregister_pane(pane_id)
                            try:
                                sm.transition_subtask(subtask_id, to_state=SubtaskState.RETRYING, error="Quota detected in terminal output")
                            except Exception:
                                pass
                        if tab_id and hasattr(self.client, "close_tab"):
                            try:
                                await self.client.close_tab(tab_id)
                            except Exception:
                                pass

                        # Backoff requeue before jumping directly to expensive tiers (Achado #23)
                        base_backoff = float(os.environ.get("MEISTER_RETRY_BACKOFF", "0.01"))
                        await asyncio.sleep(base_backoff * (2 ** min(4, attempt_count - 1)))

                        next_tier = self.spawner.get_next_available_tier(current_tier, state_manager=sm)
                        if next_tier is None:
                            if active_run_id:
                                try:
                                    sm.transition_subtask(subtask_id, to_state=SubtaskState.FAILED, error="Quota error at maximum tier")
                                except Exception:
                                    pass
                            return False
                        current_tier = next_tier.name
                        continue

                    if prompt_result.get("status") == "error":
                        logger.warning("Subtask %s returned error status", task_id)
                        err_text = str(prompt_result.get("error", "Worker returned error status"))[:200]
                        self.last_failure_reason = f"Subtask {task_id} error: {err_text}"
                        log_event(
                            event_type="subtask_rejected",
                            run_id=active_run_id,
                            task_id=task_id,
                            attempt=attempt_count,
                            tier=current_tier,
                            reason="worker_error",
                            error=err_text,
                            output=err_text,
                            exit_code=1,
                        )
                        if subtask_wt is not None and self._integration_pipeline is not None:
                            await asyncio.to_thread(
                                self._integration_pipeline.wt_mgr.cleanup_worktree,
                                subtask_wt.task_id,
                                delete_branch=True,
                                force=True,
                                archive_unmerged=True,
                            )
                            subtask_wt = None
                        if active_run_id:
                            sm.unregister_pane(pane_id)
                            try:
                                sm.transition_subtask(subtask_id, to_state=SubtaskState.FAILED, error=err_text)
                            except Exception:
                                pass
                        return False

                    if subtask_wt is not None and self._integration_pipeline is not None:
                        crash_point("after_worker_result", task_id=task_id, run_id=active_run_id)
                        async with self._merge_lock:
                            ok_int, int_err = await asyncio.to_thread(
                                self._integration_pipeline.integrate_subtask,
                                subtask_wt=subtask_wt,
                                target_files=target_files,
                                commit_message=f"subtask({task_id}): {description}",
                            )
                            await asyncio.to_thread(
                                self._integration_pipeline.wt_mgr.cleanup_worktree,
                                subtask_wt.task_id,
                                delete_branch=True,
                                force=True,
                                archive_unmerged=True,
                            )
                            subtask_wt = None
                            if not ok_int:
                                logger.warning("Falha na validação/integração da subtask %s: %s", task_id, int_err)
                                reason = "integration"
                                if "sem alterações" in int_err.lower():
                                    reason = "no_changes"
                                elif "portão" in int_err.lower() or "portao" in int_err.lower() or "gate" in int_err.lower():
                                    reason = "gate"
                                elif "escopo" in int_err.lower() or "scope violation" in int_err.lower():
                                    reason = "scope"
                                elif "merge" in int_err.lower() or "conflito" in int_err.lower():
                                    reason = "merge"

                                short_err = int_err.strip()[:200]
                                self.last_failure_reason = f"Subtask {task_id} rejected: {reason} ({short_err})"
                                log_event(
                                    event_type="subtask_rejected",
                                    run_id=active_run_id,
                                    task_id=task_id,
                                    attempt=attempt_count,
                                    tier=current_tier,
                                    reason=reason,
                                    output=short_err,
                                    error=short_err,
                                    exit_code=1,
                                )
                                if active_run_id:
                                    sm.unregister_pane(pane_id)
                                    try:
                                        sm.transition_subtask(subtask_id, to_state=SubtaskState.FAILED, error=int_err)
                                    except Exception:
                                        pass
                                return False

                    self.active_workers[pane_id]["status"] = "done"
                    sm.record_harness_success(current_tier)
                    cost_val = 0.0
                    if hasattr(sm, "record_quota_usage") and isinstance(prompt_result, dict):
                        usage: Any = prompt_result.get("usage", {})
                        if isinstance(usage, dict):
                            tokens_in = usage.get("prompt_tokens") or usage.get("tokens_in", 0)
                            tokens_out = usage.get("completion_tokens") or usage.get("tokens_out", 0)
                            cost_val = float(usage.get("cost") or prompt_result.get("cost", 0.0) or 0.0)
                        else:
                            tokens_in = 0
                            tokens_out = 0
                            cost_val = float(prompt_result.get("cost", 0.0) or 0.0)
                        sm.record_quota_usage(
                            harness=current_tier,
                            model=tier_obj.model if tier_obj else "",
                            tokens_in=int(tokens_in or 0),
                            tokens_out=int(tokens_out or 0),
                            cost=cost_val,
                        )
                    log_event(
                        event_type="subtask_completed",
                        run_id=active_run_id,
                        task_id=task_id,
                        attempt=attempt_count,
                        tier=current_tier,
                        cost=cost_val,
                        exit_code=0,
                    )
                    if active_run_id:
                        integrated_sha = getattr(self._integration_pipeline, "last_integrated_sha", None)
                        crash_point("after_merge_before_state", task_id=task_id, run_id=active_run_id)
                        try:
                            sm.transition_subtask(
                                subtask_id,
                                to_state=SubtaskState.COMPLETED,
                                result=prompt_result,
                                pane_id=pane_id,
                                integrated_sha=integrated_sha,
                            )
                        except Exception as e:
                            logger.debug("State transition to COMPLETED error: %s", e)
                        crash_point("after_subtask_completed", task_id=task_id, run_id=active_run_id)
                    return True
                finally:
                    if tab_id and hasattr(self.client, "close_tab"):
                        try:
                            await self.client.close_tab(tab_id)
                        except Exception as e:
                            logger.debug("Failed closing worker tab %s: %s", tab_id, e)
                    if pane_id:
                        if hasattr(self.client, "close_pane") and not tab_id:
                            try:
                                await self.client.close_pane(pane_id)
                            except Exception as e:
                                logger.debug("Failed closing worker pane %s: %s", pane_id, e)
                        self._quota_events.pop(pane_id, None)
                        self._exit_events.pop(pane_id, None)
                        self.active_workers.pop(pane_id, None)
                        if active_run_id:
                            try:
                                sm.unregister_pane(pane_id)
                            except Exception:
                                pass


            if subtask_wt is not None and self._integration_pipeline is not None:
                await asyncio.to_thread(
                    self._integration_pipeline.wt_mgr.cleanup_worktree,
                    subtask_wt.task_id,
                    delete_branch=True,
                    force=True,
                    archive_unmerged=True,
                )
                subtask_wt = None

            log_event(
                event_type="subtask_failed",
                run_id=active_run_id,
                task_id=task_id,
                attempt=attempt_count,
                tier=current_tier,
                exit_code=1,
                error="Execution loop ended without success",
            )
            if active_run_id:
                try:
                    sm.transition_subtask(subtask_id, to_state=SubtaskState.FAILED, error="Execution loop ended without success")
                except Exception:
                    pass
            return False

        finally:
            if subtask_wt is not None and self._integration_pipeline is not None:
                try:
                    await asyncio.to_thread(
                        self._integration_pipeline.wt_mgr.cleanup_worktree,
                        subtask_wt.task_id,
                        delete_branch=True,
                        force=True,
                        archive_unmerged=True,
                    )
                except Exception as e:
                    logger.debug("Falha na limpeza do worktree %s em finally: %s", subtask_wt.task_id, e)
                subtask_wt = None

    async def execute_parallel_batch(
        self,
        subtasks: Sequence[Union[Dict[str, Any], SubtaskNode]],
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
        steps: Sequence[Union[Dict[str, Any], SubtaskNode]],
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

    async def cleanup_run_panes(self, run_id: str) -> None:
        """Fecha panes e tabs registrados no SQLite para o run e encerra grupos de processos órfãos."""
        sm = self.get_state_manager()
        pane_records = sm.get_active_panes_details(run_id)
        if not pane_records:
            pane_ids = sm.get_active_panes(run_id)
            pane_records = [{"pane_id": p} for p in pane_ids]

        known_panes = {rec.get("pane_id") for rec in pane_records if rec.get("pane_id")}
        for p in list(self.active_workers.keys()):
            if p not in known_panes:
                pane_records.append({"pane_id": p})

        for rec in pane_records:
            pane_id = rec.get("pane_id")
            tab_id = rec.get("tab_id")
            rec_pid = rec.get("pid")

            if not pane_id and not tab_id:
                continue

            # 1. Encerra o grupo de processos do worker órfão
            if self.client is not None and self.client.is_connected and pane_id:
                try:
                    await self.client.send_interrupt(pane_id)
                except Exception as e:
                    logger.debug("Falha ao enviar interrupção para pane %s: %s", pane_id, e)

                try:
                    pinfo = await self.client._call("pane.process_info", {"pane_id": pane_id})
                    if isinstance(pinfo, dict):
                        pgid = pinfo.get("foreground_process_group_id") or pinfo.get("shell_pid")
                        if pgid:
                            try:
                                os.killpg(int(pgid), 15)
                            except (OSError, ProcessLookupError):
                                pass
                except Exception as e:
                    logger.debug("Não foi possível obter process_info do pane %s: %s", pane_id, e)

            if rec_pid:
                try:
                    os.killpg(os.getpgid(int(rec_pid)), 15)
                except (OSError, ProcessLookupError):
                    try:
                        os.kill(int(rec_pid), 15)
                    except (OSError, ProcessLookupError):
                        pass

            # 2. Fecha tab e pane no Herdr
            if self.client is not None and self.client.is_connected:
                if tab_id and hasattr(self.client, "close_tab"):
                    try:
                        await self.client.close_tab(tab_id)
                    except Exception as e:
                        logger.debug("Falha ao fechar tab %s: %s", tab_id, e)

                if pane_id and hasattr(self.client, "close_pane"):
                    try:
                        await self.client.close_pane(pane_id)
                    except Exception as e:
                        logger.debug("Falha ao fechar pane %s: %s", pane_id, e)

            # 3. Remove registro do SQLite
            if pane_id:
                try:
                    sm.unregister_pane(pane_id)
                except Exception:
                    pass

        self.active_workers.clear()

    async def run_orchestration_cycle(
        self,
        workspace_id: Optional[str] = None,
        architect_pane_id: Optional[str] = None,
        task: Optional[str] = None,
    ) -> bool:
        """Run complete orchestration cycle: read plan, execute DAG batches, notify Herdr."""
        if self.client is None:
            raise RuntimeError("Herdr client is required for orchestration cycle")

        if not self.client.is_connected:
            await self.client.connect()

        # Auto-detect workspace and architect pane if not provided or default
        if not workspace_id or workspace_id == "default" or not architect_pane_id or architect_pane_id == "architect":
            try:
                current_info = await self.client.get_current_pane()
                if current_info:
                    if not workspace_id or workspace_id == "default":
                        workspace_id = current_info.get("workspace_id") or workspace_id or "default"
                    if not architect_pane_id or architect_pane_id == "architect":
                        architect_pane_id = current_info.get("pane_id") or architect_pane_id or "architect"
            except Exception as e:
                logger.debug("Could not auto-detect current pane from Herdr: %s", e)

        workspace_id = workspace_id or "default"
        architect_pane_id = architect_pane_id or "architect"

        # Subscribe to reactive socket events
        await self.client.subscribe_events(self.handle_herdr_event)

        # Notify user in Herdr UI that orchestration has started
        await self.client.show_notification(
            f"Iniciando ciclo de orquestração no workspace {workspace_id}...",
            title="MeisterRouter",
        )

        # Obtain plan from direct task argument or read from architect pane
        if task and task.strip():
            raw_plan = task.strip()
        else:
            raw_plan = await self.client.read_pane(architect_pane_id)

        steps = parse_architect_plan(raw_plan)

        sm = self.get_state_manager()
        run_record = sm.create_or_get_run(task_prompt=raw_plan, cwd=os.getcwd())
        run_id: str = str(run_record["run_id"])
        self.current_run_id = run_id
        sm.transition_run(run_id, to_state=RunState.RUNNING)
        log_event(
            event_type="orchestration_start",
            run_id=run_id,
            task_id="orchestrator",
            task=task or raw_plan[:200],
        )
        sm.add_subtasks(run_id, steps)
        sm.recover_stranded_tasks(run_id)
        crash_point("after_run_created", run_id=run_id)

        # R-2: Ao retomar, ANTES de limpar worktrees: fechar panes/tabs e encerrar processos de workers órfãos
        await self.cleanup_run_panes(run_id)

        logger.info(
            "Parsed %d actionable steps for run %s from architect pane %s in workspace %s",
            len(steps),
            run_id,
            architect_pane_id,
            workspace_id,
        )

        # Inicializa pipeline de integração se isolation_mode for git_worktree (Achado #1, #3)
        isolation_mode = "git_worktree"
        if self.config and self.config.concurrency:
            isolation_mode = getattr(self.config.concurrency, "isolation_mode", "git_worktree")

        if isolation_mode == "git_worktree":
            try:
                from meister.worktree import WorktreeManager, IntegrationPipeline
                wt_mgr = WorktreeManager(repo_root=os.getcwd())
                wt_mgr.cleanup_orphans(exclude_run_id=run_id)
                pipeline = IntegrationPipeline(wt_mgr, gate=self.gate, state_manager=sm)
                pipeline.start_integration(run_id, state_manager=sm)
                self._integration_pipeline = pipeline
                crash_point("after_integration_started", run_id=run_id)
            except Exception as e:
                logger.warning("Não foi possível inicializar pipeline de integração: %s", e)

        try:
            plan_success = await self.execute_plan(steps)

            if plan_success:
                diff_summary = ""
                test_passed = False
                if self._integration_pipeline is not None:
                    # 1. Validação final determinística na branch de integração ANTES de tocar na main
                    crash_point("before_final_gate", run_id=run_id)
                    test_passed, test_output = await asyncio.to_thread(self._integration_pipeline.validate_final_integration)
                    if not test_passed:
                        logger.warning("Deterministic quality gate failed on integration branch: %s", test_output)
                        msg = "MeisterRouter: Plan executed but quality gate test verification failed on integration branch."
                        await self.client.show_notification(msg)
                        sm.transition_run(run_id, to_state=RunState.FAILED)
                        err_reason = f"Gate test verification failed on integration branch: {test_output[:200]}"
                        log_event(
                            event_type="orchestration_end",
                            run_id=run_id,
                            task_id="orchestrator",
                            exit_code=1,
                            status="failed",
                            reason=err_reason,
                            error=err_reason,
                        )
                        return False
                    diff_summary = self._integration_pipeline.get_integration_diff_summary()
                else:
                    gate = self.gate
                    if gate is None:
                        from meister.gate import DeterministicGate
                        gate = DeterministicGate()
                    test_passed, test_output = gate.run_verification()
                    if not test_passed:
                        logger.warning("Deterministic quality gate failed: test verification did not pass.")
                        msg = "MeisterRouter: Plan executed but quality gate test verification failed."
                        await self.client.show_notification(msg)
                        sm.transition_run(run_id, to_state=RunState.FAILED)
                        err_reason = f"Gate test verification failed: {test_output[:200]}"
                        log_event(
                            event_type="orchestration_end",
                            run_id=run_id,
                            task_id="orchestrator",
                            exit_code=1,
                            status="failed",
                            reason=err_reason,
                            error=err_reason,
                        )
                        return False
                    diff_summary = gate.get_diff_summary()

                # 2. Avaliação de conclusão com Jev/regras ANTES do fast-forward
                gate = self.gate
                if gate is None:
                    from meister.gate import DeterministicGate
                    gate = DeterministicGate()
                eval_result = gate.evaluate_completion(diff_summary=diff_summary, test_passed=test_passed)

                action = eval_result.get("action", "COMPLETE")
                if action != "COMPLETE":
                    logger.warning("Deterministic completion rejected with action %s: %s", action, eval_result.get("reason"))
                    msg = f"MeisterRouter: Tasks executed but completion evaluation returned {action}."
                    await self.client.show_notification(msg)
                    sm.transition_run(run_id, to_state=RunState.FAILED)
                    err_reason = f"Completion evaluation returned {action}: {eval_result.get('reason', '')[:200]}"
                    log_event(
                        event_type="orchestration_end",
                        run_id=run_id,
                        task_id="orchestrator",
                        exit_code=1,
                        status="failed",
                        reason=err_reason,
                        error=err_reason,
                    )
                    return False

                # 3. Fast-forward no repositório principal acontece APENAS após aprovação total
                if self._integration_pipeline is not None:
                    crash_point("before_fast_forward", run_id=run_id)
                    ok_ff, ff_msg = await asyncio.to_thread(self._integration_pipeline.apply_fast_forward)
                    if not ok_ff:
                        logger.error("Fast-forward na main falhou: %s", ff_msg)
                        msg = f"MeisterRouter: Plan verified but fast-forward failed: {ff_msg}"
                        await self.client.show_notification(msg)
                        sm.transition_run(run_id, to_state=RunState.FAILED)
                        err_reason = f"Fast-forward failed: {ff_msg[:200]}"
                        log_event(
                            event_type="orchestration_end",
                            run_id=run_id,
                            task_id="orchestrator",
                            exit_code=1,
                            status="failed",
                            reason=err_reason,
                            error=err_reason,
                        )
                        return False

                # 4. Sucesso confirmado: marca COMPLETED
                crash_point("after_fast_forward_before_state", run_id=run_id)
                sm.transition_run(run_id, to_state=RunState.COMPLETED)
                msg = f"MeisterRouter: Plan verified and completed successfully in workspace {workspace_id}!"
                await self.client.show_notification(msg)
                log_event(
                    event_type="orchestration_end",
                    run_id=run_id,
                    task_id="orchestrator",
                    exit_code=0,
                    status="completed",
                )
                crash_point("after_run_completed", run_id=run_id)
                return True
            else:
                if self._integration_pipeline is not None:
                    self._integration_pipeline.abort_integration()
                sm.transition_run(run_id, to_state=RunState.FAILED)
                msg = f"MeisterRouter: Plan execution failed in workspace {workspace_id}."
                await self.client.show_notification(msg)
                fail_reason = getattr(self, "last_failure_reason", None) or "Plan execution failed: one or more subtasks failed"
                log_event(
                    event_type="orchestration_end",
                    run_id=run_id,
                    task_id="orchestrator",
                    exit_code=1,
                    status="failed",
                    reason=fail_reason,
                    error=fail_reason,
                )
                return False
        finally:
            # Varredura de panes registrados no SQLite ao terminar o run (Achados P-7, R-2)
            rid = run_id or self.current_run_id
            if rid:
                await self.cleanup_run_panes(rid)
