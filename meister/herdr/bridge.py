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
import hashlib
import json
import logging
import math
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from meister.config import MeisterConfig, WorkerTier, effective_worker_timeouts, load_config
from meister.gate import summarize_gate_failure
from meister.i18n import CATALOGS, t
from meister.herdr.client import HerdrSocketClient, HerdrRPCError, HerdrConnectionError
from meister.herdr.events import parse_pane_event, PANE_GONE_TYPES
from meister.herdr.dag import SubtaskNode, build_subtask_dag
from meister.herdr.workers import (
    WorkerSpawner,
    detect_quota_or_rate_limit,
)
from meister.state import (
    StateManager,
    RunState,
    SubtaskState,
    compute_run_id,
    compute_subtask_id,
    task_fingerprint,
)
from meister.faults import crash_point
from meister.logger import log_event
from meister.plan import PlanError, load_plan
from meister.jev_context import build_jev_context
from meister.worker import (
    write_atomic_json,
    read_atomic_json,
    reap_harness,
    WorkerInfrastructureError,
    ensure_meister_dir,
)
from meister.jev import classify_task

logger = logging.getLogger(__name__)
_UNBOUNDED_WORKER_TIMEOUT = 1_000_000_000.0


def _worker_git_activity(path: str) -> tuple[bytes, str]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    return status, head


def _worker_has_salvageable_changes(path: str, base_commit: Optional[str]) -> bool:
    status, head = _worker_git_activity(path)
    return bool(status) or bool(base_commit and head != base_commit.strip())


class ResumeRequestError(ValueError):
    """Erro de validação de uma origem explicitamente solicitada para retomada."""


def is_infrastructure_error(int_err: Any) -> bool:
    """Detects whether an integration error represents an infrastructure failure."""
    from meister.worktree import GATE_INFRASTRUCTURE_PREFIX

    err_code = getattr(int_err, "code", None)
    prefixes = {
        GATE_INFRASTRUCTURE_PREFIX,
        CATALOGS["en"]["engine.worktree.infrastructure_prefix"],
        CATALOGS["pt-BR"]["engine.worktree.infrastructure_prefix"],
    }
    return (err_code == "gate_infrastructure") or (
        err_code is None
        and isinstance(int_err, str)
        and any(int_err.startswith(prefix) for prefix in prefixes)
    )


def extract_rejection_reason(int_err: Any) -> str:
    """Extracts semantic rejection reason ('no_changes', 'gate', 'scope', 'merge', 'integration')."""
    err_code = getattr(int_err, "code", None)
    if err_code in {"no_changes", "gate", "scope", "merge", "integration"}:
        return err_code
    elif err_code == "gate_infrastructure":
        return "gate"

    err_lower = str(int_err).lower()
    if "sem alterações" in err_lower:
        return "no_changes"
    elif "portão" in err_lower or "portao" in err_lower or "gate" in err_lower:
        return "gate"
    elif "escopo" in err_lower or "scope violation" in err_lower:
        return "scope"
    elif "merge" in err_lower or "conflito" in err_lower:
        return "merge"
    return "integration"



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


def _step_id(step: Union[Dict[str, Any], SubtaskNode]) -> str:
    if isinstance(step, dict):
        return str(step.get("id") or step.get("step_id") or "")
    return str(getattr(step, "id", ""))


def _step_title(step: Union[Dict[str, Any], SubtaskNode], size: int = 120) -> str:
    """Primeira linha não vazia da descrição, para identificar a tarefa no dashboard."""
    raw = step.get("description") if isinstance(step, dict) else getattr(step, "description", "")
    for line in str(raw or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= size else line[: size - 1] + "…"
    return ""


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
        self._tier_active: Dict[str, int] = {}
        self._tier_cond = asyncio.Condition()
        self._held_slots: Dict[str, str] = {}
        self._jev_unavailable_until: float = 0.0

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

    def _reap_worker_harness(
        self,
        task_context: Dict[str, Any],
        task_id: str,
        attempt: int,
    ) -> str:
        task_file = task_context.get("task_file")
        if not isinstance(task_file, str) or not task_file:
            return "no_file"
        pid_file = f"{os.path.splitext(task_file)[0]}.harness.json"
        status = reap_harness(pid_file)
        if status == "killed":
            logger.info(t("orphan.reaped", task_id=task_id, attempt=attempt))
            log_event(
                event_type="harness_reaped",
                task_id=task_id,
                attempt=attempt,
                status=status,
            )
        return status

    def get_state_manager(self) -> StateManager:
        """Retorna o gerenciador de estado SQLite instanciado ou inicializa um novo."""
        if self.state_manager is None:
            self.state_manager = StateManager()
        return self.state_manager

    def _report_scope_tolerated(
        self,
        run_id: Optional[str],
        task_id: str,
        subtask_id: Optional[str],
        files: List[str],
    ) -> None:
        if not files:
            return
        log_event(
            event_type="scope_tolerated",
            run_id=run_id,
            task_id=task_id,
            subtask_id=subtask_id,
            files=files,
            count=len(files),
        )

        rows = self.state_manager.get_subtasks(run_id) if self.state_manager is not None and run_id else []
        task_ids = [str(row.get("step_id") or "") for row in rows]
        task_number = task_ids.index(task_id) + 1 if task_id in task_ids else 1
        total = len(task_ids) or 1
        prefix = f"[{task_number}/{total}] {task_id}"
        visible_files = ", ".join(files[:5])
        extra = f" (+{len(files) - 5})" if len(files) > 5 else ""
        logger.info(
            t(
                "scope_report.progress",
                prefix=prefix,
                files=visible_files,
                extra=extra,
            )
        )

    def _reuse_completed_subtasks(
        self,
        state_manager: StateManager,
        source_run_id: str,
        run_id: str,
        steps: List[Dict[str, Any]],
        repo_root: str,
    ) -> None:
        """Adota subtasks concluídas da origem somente após validar conteúdo, commit e escopo."""
        from meister.worktree import scope_violations, tolerated_touched

        current_by_id = {str(step.get("id") or step.get("step_id")): step for step in steps}
        current_subtasks = {str(row["step_id"]): row for row in state_manager.get_subtasks(run_id)}
        source_rows = state_manager.get_subtasks(source_run_id)
        source_by_id = {str(row["step_id"]): row for row in source_rows}
        source_steps: Dict[str, Dict[str, Any]] = {}
        for row in source_rows:
            try:
                dependencies = json.loads(row.get("depends_on_json") or "[]")
            except (TypeError, json.JSONDecodeError):
                dependencies = None
            source_steps[str(row["step_id"])] = {
                "id": str(row["step_id"]),
                "description": row.get("description") or "",
                "depends_on": dependencies,
            }

        tolerated_files = (
            self.config.scope.tolerated_files
            if self.config and self.config.scope
            else load_config(cwd=repo_root).scope.tolerated_files
        )
        decisions: Dict[str, bool] = {}
        evaluated: set[str] = set()
        evaluating: set[str] = set()

        def log_not_reused(step_id: str, reason: str, source: Optional[Dict[str, Any]]) -> None:
            log_event(
                event_type="subtask_not_reused",
                run_id=run_id,
                task_id=step_id,
                source_run_id=source_run_id,
                source_subtask_id=source.get("subtask_id") if source else None,
                reason=reason,
            )

        def attempt(step_id: str) -> bool:
            if step_id in evaluated:
                return decisions.get(step_id, False)
            if step_id in evaluating:
                return False
            step = current_by_id.get(step_id)
            new_row = current_subtasks.get(step_id)
            if step is None or new_row is None:
                evaluated.add(step_id)
                decisions[step_id] = False
                return False

            if new_row["status"] == SubtaskState.COMPLETED.value:
                evaluated.add(step_id)
                decisions[step_id] = True
                return True
            if new_row["status"] != SubtaskState.PENDING.value:
                evaluated.add(step_id)
                decisions[step_id] = False
                return False

            dependencies = step.get("depends_on") or []
            if not isinstance(dependencies, list):
                dependencies = []
            evaluating.add(step_id)
            for dependency in dependencies:
                if not isinstance(dependency, str) or not attempt(dependency):
                    evaluating.remove(step_id)
                    source = source_by_id.get(step_id)
                    log_not_reused(step_id, "dependency_not_reused", source)
                    evaluated.add(step_id)
                    decisions[step_id] = False
                    return False
            evaluating.remove(step_id)

            source = source_by_id.get(step_id)
            reason: Optional[str] = None
            sha: Optional[str] = None
            if source is None or source.get("status") != SubtaskState.COMPLETED.value or not source.get("integrated_sha"):
                reason = "changed"
            else:
                try:
                    current_fingerprint = task_fingerprint(step, current_by_id)
                    source_fingerprint = task_fingerprint(source_steps[step_id], source_steps)
                    if current_fingerprint != source_fingerprint:
                        reason = "changed"
                except (KeyError, TypeError, ValueError):
                    reason = "changed"

            if reason is None and source is not None:
                sha = str(source["integrated_sha"])
                commit_exists = subprocess.run(
                    ["git", "-C", repo_root, "cat-file", "-e", f"{sha}^{{commit}}"],
                    capture_output=True,
                    text=True,
                )
                if commit_exists.returncode != 0:
                    reason = "missing_commit"

            if reason is None and sha is not None:
                touched = subprocess.run(
                    [
                        "git", "-C", repo_root, "diff-tree", "--root", "--no-commit-id",
                        "--name-only", "--no-renames", "-r", sha,
                    ],
                    capture_output=True,
                    text=True,
                )
                if touched.returncode != 0:
                    reason = "scope"
                else:
                    touched_files = [path for path in touched.stdout.splitlines() if path]
                    ignored_result = subprocess.run(
                        ["git", "-C", repo_root, "check-ignore", "--no-index", "-z", "--stdin"],
                        input="".join(f"{path}\0" for path in touched_files),
                        capture_output=True,
                        text=True,
                    )
                    ignored = {path for path in ignored_result.stdout.split("\0") if path}
                    out_of_scope = scope_violations(
                        touched_files,
                        step.get("target_files") or [],
                        tolerated_files,
                        ignored,
                    )
                    if out_of_scope:
                        reason = "scope"
                    else:
                        self._report_scope_tolerated(
                            run_id,
                            step_id,
                            str(new_row["subtask_id"]),
                            tolerated_touched(
                                touched_files,
                                step.get("target_files") or [],
                                tolerated_files,
                                ignored,
                            ),
                        )

            if reason is not None or source is None or sha is None:
                log_not_reused(step_id, reason or "changed", source)
                evaluated.add(step_id)
                decisions[step_id] = False
                return False

            ref = f"refs/meister/resume/{run_id}/{step_id}"
            pin = subprocess.run(
                ["git", "-C", repo_root, "update-ref", ref, sha],
                capture_output=True,
                text=True,
            )
            if pin.returncode != 0:
                raise RuntimeError(
                    t(
                        "engine.bridge.pin_resume_failed",
                        sha=sha,
                        error=pin.stderr.strip(),
                    )
                )

            state_manager.adopt_subtask(
                new_row["subtask_id"],
                source_run_id,
                source["subtask_id"],
                sha,
                source.get("assigned_tier") or "",
            )
            log_event(
                event_type="subtask_reused",
                run_id=run_id,
                task_id=step_id,
                source_run_id=source_run_id,
                source_subtask_id=source["subtask_id"],
                integrated_sha=sha,
            )
            evaluated.add(step_id)
            decisions[step_id] = True
            return True

        for step_id in current_by_id:
            attempt(step_id)

    async def handle_herdr_event(self, event: Dict[str, Any]) -> None:
        """Process incoming Herdr socket notifications (e.g. quota errors, outputs)."""
        if not isinstance(event, dict):
            return

        norm_type, norm_pane_id = parse_pane_event(event)
        params = event.get("params", event)
        pane_id = norm_pane_id or params.get("pane_id") or params.get("pane")

        # Tratamento reativo de evento pane_exited e pane_closed (Achado #10 / E2E-2)
        if norm_type in PANE_GONE_TYPES and norm_pane_id:
            if norm_pane_id in self._exit_events:
                self._exit_events[norm_pane_id].set()

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
        """Execute a subtask; per-tier limits count the tier where it starts.

        A quota fallback later in execution does not transfer its reserved slot.
        """
        if not any(
            tier.max_parallel is not None
            for tier in (self.config.workers.tier_order + self.config.workers.disabled)
        ):
            return await self._execute_subtask_core(subtask, initial_tier, run_id)

        task_dict = subtask.to_dict() if isinstance(subtask, SubtaskNode) else dict(subtask)
        task_id = str(task_dict.get("id", "task"))
        description = str(task_dict.get("description", task_id))
        active_run_id = run_id or self.current_run_id
        key = compute_subtask_id(active_run_id or "default", task_id, description)
        try:
            return await self._execute_subtask_core(subtask, initial_tier, run_id)
        finally:
            async with self._tier_cond:
                tier_name = self._held_slots.pop(key, None)
                if tier_name is not None:
                    self._tier_active[tier_name] = max(0, self._tier_active.get(tier_name, 0) - 1)
                self._tier_cond.notify_all()

    async def _acquire_tier_slot(
        self,
        preferred: str,
        key: str,
        run_id: Optional[str] = None,
    ) -> str:
        tiers = self.config.workers.tier_order
        preferred_index = next(
            (index for index, tier in enumerate(tiers) if tier.name == preferred),
            None,
        )
        if preferred_index is None:
            return preferred

        async with self._tier_cond:
            while True:
                available_tiers = []
                for tier in tiers[preferred_index:]:
                    if self.state_manager is not None:
                        is_available = getattr(self.spawner, "_is_tier_available", None)
                        if is_available is not None and not is_available(tier, self.state_manager):
                            continue
                    available_tiers.append(tier)

                if not available_tiers:
                    return preferred

                for tier in available_tiers:
                    limit = tier.max_parallel
                    if limit is None or self._tier_active.get(tier.name, 0) < limit:
                        self._tier_active[tier.name] = self._tier_active.get(tier.name, 0) + 1
                        self._held_slots[key] = tier.name
                        if tier.name != preferred:
                            log_event(
                                event_type="tier_overflow",
                                run_id=run_id,
                                task_id=key,
                                tier=tier.name,
                                skipped_tier=preferred,
                            )
                        return tier.name

                await self._tier_cond.wait()

    async def _execute_subtask_core(
        self,
        subtask: Union[Dict[str, Any], SubtaskNode],
        initial_tier: Optional[str],
        run_id: Optional[str],
    ) -> bool:
        """Execute a single subtask with automated quota failover and tier escalation.

        Jev routing only escalates forward in tier_order; selecting a later tier
        does not retain an earlier, cheaper fallback.

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
        existing_subtask = None

        # Retomada idempotente: se a subtask já estiver COMPLETED no SQLite, pula reexecução (Achados #7, #28)
        if active_run_id:
            existing_subtask = sm.get_subtask(subtask_id)
            if existing_subtask and existing_subtask.get("status") == SubtaskState.COMPLETED.value:
                logger.info(
                    t(
                        "engine.bridge.subtask_already_completed",
                        task_id=task_id,
                        subtask_id=subtask_id,
                        run_id=active_run_id,
                    )
                )
                return True

        # Determine starting tier
        current_tier = initial_tier
        if not current_tier:
            if self.config.workers and self.config.workers.tier_order:
                tier_order = self.config.workers.tier_order
                current_tier = tier_order[0].name
            else:
                logger.error(t("engine.bridge.no_lanes"))
                return False

            if getattr(getattr(self.config, "router", None), "mode", "first") == "jev" and len(tier_order) > 1:
                tier_names = {tier.name for tier in tier_order}
                if (
                    existing_subtask
                    and existing_subtask.get("status") == SubtaskState.RUNNING.value
                    and existing_subtask.get("assigned_tier") in tier_names
                ):
                    current_tier = existing_subtask["assigned_tier"]
                    log_event(
                        event_type="route_decision",
                        run_id=active_run_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        tier=current_tier,
                        classification=None,
                        confidence=None,
                        fallback_rule_applied=False,
                        status="resumed",
                    )
                else:
                    if time.monotonic() < self._jev_unavailable_until:
                        current_tier = tier_order[0].name
                        log_event(
                            event_type="route_decision",
                            run_id=active_run_id,
                            task_id=task_id,
                            subtask_id=subtask_id,
                            tier=current_tier,
                            classification=None,
                            confidence=None,
                            fallback_rule_applied=True,
                            status="skipped_unavailable",
                        )
                    else:
                        context = build_jev_context(
                            task_dict,
                            self.config.router.context_max_chars,
                        )
                        try:
                            timeout_seconds = self.config.router.timeout_seconds
                            max_attempts = self.config.router.max_attempts
                            result = await asyncio.wait_for(
                                asyncio.to_thread(
                                    classify_task,
                                    context=context,
                                    model=self.config.master.model,
                                    task_id=task_id,
                                    run_id=active_run_id,
                                    implementers=tier_order,
                                    timeout=timeout_seconds,
                                    max_attempts=max_attempts,
                                ),
                                timeout=timeout_seconds * max_attempts + 5,
                            )
                            recommended = result.get("recommended_implementer")
                            if result.get("api_unavailable", False):
                                error = t("engine.bridge.jev_unavailable")
                                self._jev_unavailable_until = (
                                    time.monotonic() + self.config.router.unavailable_cooldown_seconds
                                )
                                logger.warning(
                                    "Jev routing failed for subtask %s; using first tier: %s",
                                    task_id,
                                    error,
                                )
                                current_tier = tier_order[0].name
                                log_event(
                                    event_type="route_decision",
                                    run_id=active_run_id,
                                    task_id=task_id,
                                    subtask_id=subtask_id,
                                    tier=current_tier,
                                    classification=result.get("classification"),
                                    confidence=result.get("classification_confidence"),
                                    fallback_rule_applied=True,
                                    status="fallback",
                                    error=error,
                                )
                            else:
                                classification = result.get("classification")
                                jev_recommended = recommended
                                ineligible_replaced = False
                                if recommended in tier_names:
                                    current_tier = recommended
                                    classification_name = (
                                        classification.upper()
                                        if isinstance(classification, str)
                                        else ""
                                    )
                                    recommended_index = next(
                                        index
                                        for index, tier in enumerate(tier_order)
                                        if tier.name == recommended
                                    )
                                    recommended_tier = tier_order[recommended_index]
                                    if (
                                        recommended_tier.eligible_classes
                                        and classification_name not in recommended_tier.eligible_classes
                                    ):
                                        def is_eligible(tier: WorkerTier) -> bool:
                                            return (
                                                not tier.eligible_classes
                                                or classification_name in tier.eligible_classes
                                            )

                                        replacement = next(
                                            (
                                                tier
                                                for tier in reversed(tier_order[:recommended_index])
                                                if is_eligible(tier)
                                            ),
                                            None,
                                        )
                                        if replacement is None:
                                            replacement = next(
                                                (
                                                    tier
                                                    for tier in tier_order[recommended_index + 1 :]
                                                    if is_eligible(tier)
                                                ),
                                                None,
                                            )
                                        if replacement is not None:
                                            current_tier = replacement.name
                                            ineligible_replaced = True
                                            log_event(
                                                event_type="route_decision",
                                                run_id=active_run_id,
                                                task_id=task_id,
                                                subtask_id=subtask_id,
                                                tier=current_tier,
                                                classification=classification,
                                                confidence=result.get("classification_confidence"),
                                                fallback_rule_applied=True,
                                                jev_recommended=jev_recommended,
                                                status="ineligible_replaced",
                                            )
                                if not ineligible_replaced:
                                    log_event(
                                        event_type="route_decision",
                                        run_id=active_run_id,
                                        task_id=task_id,
                                        subtask_id=subtask_id,
                                        tier=current_tier,
                                        classification=classification,
                                        confidence=result.get("classification_confidence"),
                                        fallback_rule_applied=bool(result.get("fallback_rule_applied", False))
                                        or recommended not in tier_names,
                                    )
                        except Exception as e:
                            logger.warning("Jev routing failed for subtask %s; using first tier: %s", task_id, e)
                            self._jev_unavailable_until = (
                                time.monotonic() + self.config.router.unavailable_cooldown_seconds
                            )
                            current_tier = tier_order[0].name
                            log_event(
                                event_type="route_decision",
                                run_id=active_run_id,
                                task_id=task_id,
                                subtask_id=subtask_id,
                                tier=current_tier,
                                classification=None,
                                confidence=None,
                                fallback_rule_applied=True,
                                status="fallback",
                                error=str(e)[:200] or "Jev routing timed out",
                            )

        if current_tier is None:
            logger.error(t("engine.bridge.no_lane_selected", task_id=task_id))
            return False

        if sm and hasattr(self.spawner, "get_first_available_tier"):
            tier_obj = self.spawner.get_tier(current_tier) if hasattr(self.spawner, "get_tier") else None
            if tier_obj is not None:
                first_tier = self.spawner.get_first_available_tier(current_tier, state_manager=sm)
                first_tier_name = getattr(first_tier, "name", None)
                if isinstance(first_tier_name, str) and first_tier_name != current_tier:
                    logger.info(
                        t(
                            "engine.bridge.initial_tier_cooldown",
                            current_tier=current_tier,
                            next_tier=first_tier_name,
                        )
                    )
                    log_event(
                        event_type="tier_skipped_breaker",
                        run_id=active_run_id,
                        task_id=task_id,
                        tier=first_tier_name,
                        skipped_tier=current_tier,
                    )
                    current_tier = first_tier_name

        if initial_tier is None and any(
            tier.max_parallel is not None for tier in self.config.workers.tier_order
        ):
            current_tier = await self._acquire_tier_slot(current_tier, subtask_id, active_run_id)

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
                    err_msg = t("engine.bridge.worktree_create_failed", task_id=task_id, error=e)
                    logger.warning(err_msg)
                    log_event(
                        event_type="subtask_rejected",
                        run_id=active_run_id,
                        task_id=task_id,
                        reason="worktree_create_failed",
                        error=str(e)[:200],
                    )
                    self.last_failure_reason = err_msg
                    if active_run_id:
                        try:
                            sm.transition_subtask(subtask_id, to_state=SubtaskState.FAILED, error=err_msg)
                        except Exception:
                            pass
                    return False

            attempt_count = 0
            pane_lost_retries_used = 0
            repair_used = 0
            original_description = description

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

                effective_timeouts = effective_worker_timeouts(self.config, current_tier, task_dict)
                timeout_val = effective_timeouts["max_runtime_seconds"]
                worker_timeout = (
                    timeout_val + 60.0 if timeout_val > 0 else _UNBOUNDED_WORKER_TIMEOUT
                )
                task_payload = {
                    "run_id": active_run_id,
                    "task_id": task_id,
                    "model": current_tier,
                    "task": description,
                    "target_files": target_files or [],
                    "cwd": resolved_cwd,
                    "config_path": resolved_config_path,
                    "timeout": worker_timeout,
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
                tab_closed = False
                pane_closed = False
                spawn_cwd = subtask_wt.worktree_path if subtask_wt else (task_dict.get("cwd") or task_dict.get("worktree"))
                initial_worktree_fingerprint: Optional[tuple[bytes, str]] = None
                if spawn_cwd:
                    try:
                        initial_worktree_fingerprint = await asyncio.to_thread(
                            _worker_git_activity, spawn_cwd
                        )
                    except (OSError, subprocess.SubprocessError) as e:
                        logger.debug("Could not inspect worker worktree before spawn at %s: %s", spawn_cwd, e)
                worker_phase_start = time.monotonic()
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
                        "attempt": attempt_count,
                        "current_tier": current_tier,
                        "status": "running",
                        "subtask": task_dict,
                    }

                    prompt_result: Optional[Dict[str, Any]] = None
                    infra_error: Optional[str] = None
                    timeout_kind: Optional[str] = None
                    timeout_seconds = 0.0
                    start_wait = time.monotonic()
                    poll_interval = 0.2

                    try:
                        liveness_interval = float(os.environ.get("MEISTER_PANE_LIVENESS_INTERVAL", "5.0"))
                    except (ValueError, TypeError):
                        liveness_interval = 5.0
                    if not math.isfinite(liveness_interval) or liveness_interval < 0:
                        liveness_interval = 5.0
                    last_liveness_check = start_wait
                    last_activity = start_wait
                    worktree_fingerprint = initial_worktree_fingerprint
                    pane_fingerprint: Optional[str] = None
                    try:
                        pane_content = await self.client.read_pane(pane_id)
                        pane_fingerprint = hashlib.sha256(str(pane_content).encode("utf-8")).hexdigest()
                    except Exception:
                        pass

                    while True:
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
                            infra_error = t("engine.bridge.worker_pane_exited", pane_id=pane_id)
                            self._reap_worker_harness(task_dict, task_id, attempt_count)
                            break

                        if liveness_interval > 0 and time.monotonic() - last_liveness_check >= liveness_interval:
                            last_liveness_check = time.monotonic()
                            if spawn_cwd:
                                try:
                                    current_worktree_fingerprint = await asyncio.to_thread(
                                        _worker_git_activity, spawn_cwd
                                    )
                                    if (
                                        worktree_fingerprint is not None
                                        and current_worktree_fingerprint != worktree_fingerprint
                                    ):
                                        last_activity = time.monotonic()
                                    worktree_fingerprint = current_worktree_fingerprint
                                except (OSError, subprocess.SubprocessError) as e:
                                    logger.debug("Could not inspect worker worktree activity at %s: %s", spawn_cwd, e)
                            try:
                                pane_content = await self.client.read_pane(pane_id)
                                current_pane_fingerprint = hashlib.sha256(
                                    str(pane_content).encode("utf-8")
                                ).hexdigest()
                                if (
                                    pane_fingerprint is not None
                                    and current_pane_fingerprint != pane_fingerprint
                                ):
                                    last_activity = time.monotonic()
                                pane_fingerprint = current_pane_fingerprint
                            except Exception:
                                pass
                            if self.client is not None and hasattr(self.client, "pane_exists"):
                                try:
                                    exists = await self.client.pane_exists(pane_id)
                                    if exists is False:
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
                                        infra_error = t("engine.bridge.worker_pane_disappeared", pane_id=pane_id)
                                        self._reap_worker_harness(task_dict, task_id, attempt_count)
                                        break
                                except Exception:
                                    pass

                        now = time.monotonic()
                        if (
                            effective_timeouts["max_runtime_seconds"] > 0
                            and now - start_wait >= effective_timeouts["max_runtime_seconds"]
                        ):
                            timeout_kind = "max_runtime"
                            timeout_seconds = effective_timeouts["max_runtime_seconds"]
                        elif (
                            effective_timeouts["idle_timeout_seconds"] > 0
                            and now - last_activity >= effective_timeouts["idle_timeout_seconds"]
                        ):
                            timeout_kind = "idle"
                            timeout_seconds = effective_timeouts["idle_timeout_seconds"]
                        if timeout_kind:
                            break

                        await asyncio.sleep(poll_interval)

                    log_event(
                        event_type="worker_phase",
                        run_id=active_run_id,
                        task_id=task_id,
                        attempt=attempt_count,
                        tier=current_tier,
                        phase="worker",
                        duration_ms=(time.monotonic() - worker_phase_start) * 1000.0,
                    )
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

                    timeout_message = ""
                    if timeout_kind:
                        reap_status = self._reap_worker_harness(task_dict, task_id, attempt_count)
                        seconds_label = f"{timeout_seconds:g}"
                        if timeout_kind == "idle":
                            timeout_message = t(
                                "engine.bridge.idle_timeout",
                                tier=current_tier,
                                seconds=seconds_label,
                            )
                        else:
                            timeout_message = t(
                                "engine.bridge.max_runtime_timeout",
                                tier=current_tier,
                                seconds=seconds_label,
                            )
                        try:
                            await self.client.send_interrupt(pane_id)
                        except Exception as e:
                            logger.debug("Failed interrupting timed-out worker pane %s: %s", pane_id, e)
                        await asyncio.sleep(1.0)

                        if os.path.exists(result_file):
                            prompt_result = read_atomic_json(result_file)
                            if prompt_result is not None:
                                try:
                                    os.remove(result_file)
                                    if os.path.exists(task_file):
                                        os.remove(task_file)
                                except OSError:
                                    pass
                            if (
                                prompt_result is not None
                                and reap_status == "killed"
                                and prompt_result.get("status") == "error"
                            ):
                                # O erro foi gravado pelo run-task por causa da nossa própria morte do harness:
                                # o timeout manda (retry, escalonamento ou aproveitamento do trabalho).
                                prompt_result = None

                        timeout_salvaged = False
                        if prompt_result is None and subtask_wt is not None and self._integration_pipeline is not None:
                            try:
                                has_changes = await asyncio.to_thread(
                                    _worker_has_salvageable_changes,
                                    subtask_wt.worktree_path,
                                    subtask_wt.base_commit,
                                )
                            except (OSError, subprocess.SubprocessError) as e:
                                logger.warning(
                                    "Could not determine whether timed-out worktree %s has salvageable work: %s",
                                    subtask_wt.worktree_path,
                                    e,
                                )
                                has_changes = False
                            if has_changes:
                                timeout_salvaged = True
                                prompt_result = {
                                    "status": "done",
                                    "modified_files": [],
                                    "timeout_salvaged": True,
                                }

                        retry_timeout = False
                        timeout_next_tier = None
                        if prompt_result is None:
                            retry_timeout = (
                                pane_lost_retries_used < self.config.retry.pane_lost_attempts
                            )
                            if retry_timeout:
                                pane_lost_retries_used += 1
                            else:
                                timeout_next_tier = self.spawner.get_next_available_tier(
                                    current_tier, state_manager=sm
                                )
                            action = (
                                t("engine.bridge.timeout_retry_same_lane")
                                if retry_timeout
                                else (
                                    t("engine.bridge.timeout_escalating", tier=timeout_next_tier.name)
                                    if timeout_next_tier is not None
                                    else t("engine.bridge.timeout_failed_no_salvage")
                                )
                            )
                        elif timeout_salvaged:
                            action = t("engine.bridge.timeout_integrating_salvaged")
                        else:
                            action = t("engine.bridge.timeout_integrating_result")

                        log_event(
                            event_type="worker_timeout",
                            run_id=active_run_id,
                            task_id=task_id,
                            tier=current_tier,
                            attempt=attempt_count,
                            kind=timeout_kind,
                            seconds=timeout_seconds,
                            pane_id=pane_id,
                            message=timeout_message,
                            action=action,
                        )
                        if timeout_salvaged:
                            log_event(
                                event_type="worker_timeout_salvaged",
                                run_id=active_run_id,
                                task_id=task_id,
                                tier=current_tier,
                                attempt=attempt_count,
                                kind=timeout_kind,
                                seconds=timeout_seconds,
                                pane_id=pane_id,
                            )

                        if tab_id and hasattr(self.client, "close_tab"):
                            try:
                                await self.client.close_tab(tab_id)
                                tab_closed = True
                            except Exception as e:
                                logger.debug("Failed closing timed-out worker tab %s: %s", tab_id, e)
                        elif pane_id and hasattr(self.client, "close_pane"):
                            try:
                                await self.client.close_pane(pane_id)
                                pane_closed = True
                            except Exception as e:
                                logger.debug("Failed closing timed-out worker pane %s: %s", pane_id, e)
                        if pane_id in self.active_workers:
                            self.active_workers[pane_id]["status"] = "timed_out"

                        if prompt_result is None:
                            if active_run_id:
                                sm.unregister_pane(pane_id)
                                try:
                                    sm.transition_subtask(
                                        subtask_id,
                                        to_state=SubtaskState.RETRYING,
                                        error=timeout_message,
                                    )
                                except Exception:
                                    pass
                            self._quota_events.pop(pane_id, None)
                            self._exit_events.pop(pane_id, None)
                            self.active_workers.pop(pane_id, None)

                            if retry_timeout:
                                base_backoff = self.config.retry.pane_lost_backoff_seconds
                                backoff_exponent = pane_lost_retries_used - 1
                                backoff_cap_exponent = max(
                                    0,
                                    math.ceil(math.log2(60.0) - math.log2(base_backoff))
                                    if base_backoff > 0
                                    else backoff_exponent,
                                )
                                backoff_seconds = (
                                    0.0
                                    if base_backoff == 0
                                    else min(
                                        60.0,
                                        math.ldexp(
                                            base_backoff,
                                            min(backoff_exponent, backoff_cap_exponent),
                                        ),
                                    )
                                )
                                log_event(
                                    event_type="worker_retry",
                                    run_id=active_run_id,
                                    task_id=task_id,
                                    tier=current_tier,
                                    attempt=attempt_count,
                                    reason="timeout",
                                    retry=pane_lost_retries_used,
                                    max_retries=self.config.retry.pane_lost_attempts,
                                    backoff_seconds=backoff_seconds,
                                )
                                await asyncio.sleep(backoff_seconds)
                                continue

                            if timeout_next_tier is not None:
                                current_tier = timeout_next_tier.name
                                continue

                            self.last_failure_reason = timeout_message
                            log_event(
                                event_type="worker_error",
                                run_id=active_run_id,
                                task_id=task_id,
                                attempt=attempt_count,
                                tier=current_tier,
                                status="timeout",
                                error=timeout_message,
                            )
                            if active_run_id:
                                try:
                                    sm.transition_subtask(
                                        subtask_id,
                                        to_state=SubtaskState.FAILED,
                                        error=timeout_message,
                                    )
                                except Exception:
                                    pass
                            return False

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
                            if tab_id and not tab_closed and hasattr(self.client, "close_tab"):
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

                        # Pane perdido/encerrado: retenta na mesma via antes de falhar sem escalar.
                        err_msg = infra_error or f"Worker no pane {pane_id} terminou a espera sem fornecer resultado"
                        if (
                            infra_error is not None
                            and pane_lost_retries_used < self.config.retry.pane_lost_attempts
                        ):
                            self._reap_worker_harness(task_dict, task_id, attempt_count)
                            pane_lost_retries_used += 1
                            base_backoff = self.config.retry.pane_lost_backoff_seconds
                            backoff_exponent = pane_lost_retries_used - 1
                            backoff_cap_exponent = max(
                                0,
                                math.ceil(math.log2(60.0) - math.log2(base_backoff))
                                if base_backoff > 0
                                else backoff_exponent,
                            )
                            backoff_seconds = (
                                0.0
                                if base_backoff == 0
                                else min(
                                    60.0,
                                    math.ldexp(
                                        base_backoff,
                                        min(backoff_exponent, backoff_cap_exponent),
                                    ),
                                )
                            )
                            logger.warning(
                                "Retrying worker pane for subtask %s on tier %s after pane loss "
                                "(retry %s/%s)",
                                task_id,
                                current_tier,
                                pane_lost_retries_used,
                                self.config.retry.pane_lost_attempts,
                            )
                            if active_run_id:
                                sm.unregister_pane(pane_id)
                            log_event(
                                event_type="worker_retry",
                                run_id=active_run_id,
                                task_id=task_id,
                                tier=current_tier,
                                attempt=attempt_count,
                                reason="pane_lost",
                                retry=pane_lost_retries_used,
                                backoff_seconds=backoff_seconds,
                            )
                            if tab_id and hasattr(self.client, "close_tab"):
                                try:
                                    await self.client.close_tab(tab_id)
                                except Exception as e:
                                    logger.debug("Failed closing worker tab %s: %s", tab_id, e)
                            elif pane_id and hasattr(self.client, "close_pane"):
                                try:
                                    await self.client.close_pane(pane_id)
                                except Exception as e:
                                    logger.debug("Failed closing worker pane %s: %s", pane_id, e)
                            self._quota_events.pop(pane_id, None)
                            self._exit_events.pop(pane_id, None)
                            self.active_workers.pop(pane_id, None)
                            tab_id = None
                            pane_id = None
                            await asyncio.sleep(backoff_seconds)
                            continue

                        if pane_lost_retries_used:
                            err_msg = (
                                f"{err_msg} (retentativas esgotadas: {pane_lost_retries_used})"
                            )
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

                    integrated_sha_for_task = None
                    if subtask_wt is not None and self._integration_pipeline is not None:
                        crash_point("after_worker_result", task_id=task_id, run_id=active_run_id)
                        prepare_duration = 0.0
                        merge_duration = 0.0
                        try:
                            prepare_start = time.monotonic()
                            try:
                                prepared = await asyncio.to_thread(
                                    self._integration_pipeline.prepare_subtask,
                                    subtask_wt=subtask_wt,
                                    target_files=target_files,
                                    commit_message=f"subtask({task_id}): {original_description}",
                                    task_id=task_id,
                                    attempt=attempt_count,
                                    tier=current_tier,
                                )
                                self._report_scope_tolerated(
                                    active_run_id,
                                    task_id,
                                    subtask_id,
                                    getattr(prepared, "tolerated_touched", []),
                                )
                            finally:
                                prepare_duration = time.monotonic() - prepare_start
                            lock_wait_start = time.monotonic()
                            async with self._merge_lock:
                                log_event(
                                    event_type="worker_phase",
                                    run_id=active_run_id,
                                    task_id=task_id,
                                    attempt=attempt_count,
                                    tier=current_tier,
                                    phase="lock_wait",
                                    duration_ms=(time.monotonic() - lock_wait_start) * 1000.0,
                                )
                                merge_start = time.monotonic()
                                try:
                                    ok_int, int_err = await asyncio.to_thread(
                                        self._integration_pipeline.merge_prepared,
                                        prepared,
                                    )
                                    integrated_sha_for_task = self._integration_pipeline.last_integrated_sha
                                finally:
                                    merge_duration = time.monotonic() - merge_start
                        finally:
                            log_event(
                                event_type="worker_phase",
                                run_id=active_run_id,
                                task_id=task_id,
                                attempt=attempt_count,
                                tier=current_tier,
                                phase="integrate",
                                duration_ms=(prepare_duration + merge_duration) * 1000.0,
                            )
                        is_infra_error = is_infrastructure_error(int_err)

                        if not ok_int and is_infra_error:
                            logger.error(
                                "Infrastructure error in deterministic gate for subtask %s: %s",
                                task_id,
                                int_err,
                            )
                            log_event(
                                event_type="worker_error",
                                run_id=active_run_id,
                                task_id=task_id,
                                attempt=attempt_count,
                                tier=current_tier,
                                exit_code=2,
                                status="infrastructure_error",
                                error=int_err,
                            )
                            self.last_failure_reason = int_err
                            if subtask_wt is not None:
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
                            return False
                        if not ok_int:
                            err_code = getattr(int_err, "code", None)
                            reason = extract_rejection_reason(int_err)
                            gate_summary = summarize_gate_failure(str(int_err))
                            failed_tests = gate_summary.get("failed_tests", [])
                            failure_summary = gate_summary.get("excerpt", "")

                            max_repair_attempts = 1
                            if self.config and hasattr(self.config, "gate"):
                                max_repair_attempts = getattr(self.config.gate, "repair_attempts", 1)

                            can_repair = (
                                subtask_wt is not None
                                and not is_infra_error
                                and (err_code in ("gate", "scope") or (err_code is None and reason in ("gate", "scope")))
                                and (repair_used < max_repair_attempts)
                            )

                            if can_repair:
                                repair_used += 1
                                logger.warning(
                                    "Deterministic gate rejected subtask %s (%s). Attempting repair %d/%d...",
                                    task_id,
                                    reason,
                                    repair_used,
                                    max_repair_attempts,
                                )
                                log_event(
                                    event_type="gate_repair",
                                    run_id=active_run_id,
                                    task_id=task_id,
                                    attempt=attempt_count,
                                    repair_attempt=repair_used,
                                    reason=reason,
                                    tier=current_tier,
                                    failed_tests=failed_tests,
                                    failure_summary=failure_summary,
                                )
                                log_event(
                                    event_type="worker_retry",
                                    run_id=active_run_id,
                                    task_id=task_id,
                                    tier=current_tier,
                                    attempt=attempt_count,
                                    reason="gate_repair",
                                    retry=repair_used,
                                )
                                repair_parts = [
                                    "\n\n## REPAIR",
                                    f"Reason: {reason}",
                                ]
                                if failed_tests:
                                    repair_parts.append(f"Failed tests: {', '.join(failed_tests)}")
                                    for ft in failed_tests:
                                        repair_parts.append(f"- {ft}")
                                else:
                                    repair_parts.append("Failed tests: []")
                                repair_parts.append(f"Failure summary:\n{failure_summary}")
                                repair_parts.append(
                                    "Instructions: Your previous attempt was rejected by the deterministic gate. "
                                    "The files from your previous attempt are already in the worktree. "
                                    "Fix ONLY what is needed so the gate passes; do not undo unrelated changes. "
                                    "Before finishing, run the whole test suite and make it pass."
                                )
                                description = f"{original_description}\n" + "\n".join(repair_parts)
                                task_dict["description"] = description
                                if active_run_id:
                                    try:
                                        sm.transition_subtask(
                                            subtask_id,
                                            to_state=SubtaskState.RETRYING,
                                            error=f"Gate rejected ({reason}). Repair attempt {repair_used}/{max_repair_attempts}",
                                        )
                                    except Exception:
                                        pass
                                continue

                            await asyncio.to_thread(
                                self._integration_pipeline.wt_mgr.cleanup_worktree,
                                subtask_wt.task_id,
                                delete_branch=True,
                                force=True,
                                archive_unmerged=True,
                            )
                            subtask_wt = None

                            logger.warning(
                                t(
                                    "engine.bridge.integration_failed",
                                    task_id=task_id,
                                    error=int_err,
                                )
                            )
                            short_err = int_err.strip()[:200]
                            self.last_failure_reason = f"Subtask {task_id} rejected: {reason} ({short_err})"
                            event_kwargs: Dict[str, Any] = {
                                "event_type": "subtask_rejected",
                                "run_id": active_run_id,
                                "task_id": task_id,
                                "attempt": attempt_count,
                                "tier": current_tier,
                                "reason": reason,
                                "output": short_err,
                                "error": short_err,
                                "exit_code": 1,
                            }
                            if reason in ("gate", "scope"):
                                event_kwargs["failed_tests"] = failed_tests
                                event_kwargs["failure_summary"] = failure_summary
                            log_event(**event_kwargs)
                            if active_run_id:
                                sm.unregister_pane(pane_id)
                                try:
                                    sm.transition_subtask(subtask_id, to_state=SubtaskState.FAILED, error=int_err)
                                except Exception:
                                    pass
                            return False

                        await asyncio.to_thread(
                            self._integration_pipeline.wt_mgr.cleanup_worktree,
                            subtask_wt.task_id,
                            delete_branch=True,
                            force=True,
                            archive_unmerged=True,
                        )
                        subtask_wt = None

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
                    usage_data = prompt_result.get("usage", {})
                    usage_data = usage_data if isinstance(usage_data, dict) else {}
                    event_cost = usage_data.get("cost")
                    event_cost = float(event_cost) if event_cost is not None else 0.0
                    usage_event_fields = {
                        key: usage_data[key]
                        for key in ("tokens_in", "tokens_out", "tokens_total", "credits", "approx")
                        if usage_data.get(key) is not None
                    }
                    log_event(
                        event_type="subtask_completed",
                        run_id=active_run_id,
                        task_id=task_id,
                        attempt=attempt_count,
                        tier=current_tier,
                        cost=event_cost,
                        cost_source=usage_data.get("cost_source", "unknown"),
                        **usage_event_fields,
                        exit_code=0,
                    )
                    if active_run_id:
                        crash_point("after_merge_before_state", task_id=task_id, run_id=active_run_id)
                        try:
                            sm.transition_subtask(
                                subtask_id,
                                to_state=SubtaskState.COMPLETED,
                                result=prompt_result,
                                pane_id=pane_id,
                                integrated_sha=integrated_sha_for_task,
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
                        if hasattr(self.client, "close_pane") and not tab_id and not pane_closed:
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
                    logger.debug(
                        t(
                            "engine.bridge.cleanup_failed",
                            task_id=subtask_wt.task_id,
                            error=e,
                        )
                    )
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
        dag = build_subtask_dag(steps)
        batches = dag.get_execution_batches()
        log_event(
            event_type="plan_parsed",
            run_id=self.current_run_id,
            task_id="orchestrator",
            total=len(steps),
            batches=len(batches),
            task_ids=[_step_id(step) for step in steps],
            task_titles={_step_id(step): _step_title(step) for step in steps},
        )

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

            worker_info = self.active_workers.get(pane_id, {}) if pane_id else {}
            worker_context = worker_info.get("subtask", {})
            self._reap_worker_harness(
                worker_context if isinstance(worker_context, dict) else {},
                str(worker_info.get("task_id") or ""),
                int(worker_info.get("attempt") or 0),
            )

            # 1. Encerra o grupo de processos do worker órfão
            if self.client is not None and self.client.is_connected and pane_id:
                try:
                    await self.client.send_interrupt(pane_id)
                except Exception as e:
                    logger.debug(t("engine.bridge.interrupt_failed", pane_id=pane_id, error=e))

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
                    logger.debug(t("engine.bridge.process_info_failed", pane_id=pane_id, error=e))

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
                        logger.debug(t("engine.bridge.close_tab_failed", tab_id=tab_id, error=e))

                if pane_id and hasattr(self.client, "close_pane"):
                    try:
                        await self.client.close_pane(pane_id)
                    except Exception as e:
                        logger.debug(t("engine.bridge.close_pane_failed", pane_id=pane_id, error=e))

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
        allow_freeform: bool = True,
        resume_run_id: Optional[str] = None,
        resume_hint_callback: Optional[Callable[[str], None]] = None,
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
            t("engine.bridge.orchestration_started", workspace_id=workspace_id),
            title="MeisterRouter",
        )

        # Obtain plan from direct task argument or read from architect pane
        if task and task.strip():
            raw_plan = task.strip()
        else:
            raw_plan = await self.client.read_pane(architect_pane_id)

        try:
            steps = load_plan(raw_plan, allow_freeform=allow_freeform)
        except PlanError as e:
            if allow_freeform:
                steps = parse_architect_plan(raw_plan)
            else:
                err_msg = t("engine.bridge.plan_rejected", error=e)
                logger.error(err_msg)
                log_event(
                    event_type="plan_rejected",
                    task_id="orchestrator",
                    exit_code=1,
                    status="rejected",
                    reason="plan_invalid",
                    error=str(e)[:200],
                )
                await self.client.show_notification(f"MeisterRouter: {err_msg[:200]}", title="MeisterRouter")
                return False

        sm = self.get_state_manager()
        cwd = os.path.abspath(os.getcwd())
        new_run_id = compute_run_id(raw_plan, cwd)
        source_run: Optional[Dict[str, Any]] = None
        same_run = sm.get_run(new_run_id)
        resumable_candidate = sm.find_resumable_run(cwd, exclude_run_id=new_run_id)

        def automatic_source_hint() -> str:
            if resumable_candidate is None:
                return ""
            return f". Use --resume sem id para usar o run {resumable_candidate['run_id'][:8]}"

        async def resume_same_run(run: Dict[str, Any]) -> None:
            message = (
                t(
                    "engine.bridge.resume_same_run",
                    run_id=run["run_id"][:8],
                    state=run["state"],
                )
            )
            logger.info(message)
            log_event(
                event_type="resume_same_run",
                run_id=run["run_id"],
                task_id="orchestrator",
                state=run["state"],
            )
            if resume_hint_callback is not None:
                resume_hint_callback(message)
            if self.client is not None:
                await self.client.show_notification(message, title="MeisterRouter")

        if resume_run_id is None and resume_hint_callback is not None:
            candidate = resumable_candidate
            if candidate is not None:
                completed_count = sum(
                    1
                    for subtask in sm.get_subtasks(candidate["run_id"])
                    if subtask["status"] == SubtaskState.COMPLETED.value and subtask.get("integrated_sha")
                )
                resume_hint_callback(
                    t(
                        "engine.bridge.resume_hint",
                        run_id=candidate["run_id"],
                        state=candidate["state"],
                        count=completed_count,
                    )
                )
        if resume_run_id == "auto":
            source_run = resumable_candidate
            if source_run is None:
                if (
                    same_run is not None
                    and same_run["state"] in {RunState.FAILED.value, RunState.RUNNING.value}
                    and os.path.abspath(same_run["cwd"]) == cwd
                ):
                    await resume_same_run(same_run)
                else:
                    warning = t("engine.bridge.resume_no_run")
                    logger.warning(warning)
                    await self.client.show_notification(warning, title="MeisterRouter")
        elif resume_run_id is not None:
            source_run = sm.get_run(resume_run_id)
            if source_run is None:
                raise ResumeRequestError(
                    t(
                        "engine.bridge.resume_missing_run",
                        run_id=resume_run_id,
                        hint=automatic_source_hint(),
                    )
                )
            if source_run["state"] not in {RunState.FAILED.value, RunState.RUNNING.value}:
                raise ResumeRequestError(
                    t(
                        "engine.bridge.resume_invalid_state",
                        run_id=resume_run_id,
                        state=source_run["state"],
                        hint=automatic_source_hint(),
                    )
                )
            if os.path.abspath(source_run["cwd"]) != cwd:
                raise ResumeRequestError(
                    t(
                        "engine.bridge.resume_other_cwd",
                        run_id=resume_run_id,
                        hint=automatic_source_hint(),
                    )
                )
            if resume_run_id == new_run_id:
                same_run = source_run
                source_run = None
                await resume_same_run(same_run)

        run_record = sm.create_or_get_run(task_prompt=raw_plan, cwd=cwd)
        run_id: str = str(run_record["run_id"])
        self.current_run_id = run_id
        run_metadata = {"resumed_from": source_run["run_id"]} if source_run is not None else None
        if run_metadata is not None:
            sm.transition_run(run_id, to_state=RunState.RUNNING, metadata=run_metadata)
        else:
            sm.transition_run(run_id, to_state=RunState.RUNNING)
        if source_run is not None:
            sm.mark_superseded(source_run["run_id"], run_id)
        log_event(
            event_type="orchestration_start",
            run_id=run_id,
            task_id="orchestrator",
            task=task or raw_plan[:200],
        )
        sm.add_subtasks(run_id, steps)
        if source_run is not None:
            self._reuse_completed_subtasks(sm, source_run["run_id"], run_id, steps, cwd)
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
                wt_mgr = WorktreeManager(repo_root=cwd)
                wt_mgr.cleanup_orphans(exclude_run_id=run_id)
                pipeline = IntegrationPipeline(wt_mgr, gate=self.gate, state_manager=sm)
                pipeline.start_integration(
                    run_id,
                    state_manager=sm,
                    strict_replay=source_run is not None,
                )
                self._integration_pipeline = pipeline
                crash_point("after_integration_started", run_id=run_id)
            except Exception as e:
                if source_run is not None and getattr(e, "code", None) == "replay_failed":
                    failure = f"{e}; rode sem --resume"
                    logger.error(failure)
                    sm.transition_run(run_id, to_state=RunState.FAILED)
                    await self.client.show_notification(f"MeisterRouter: {failure}", title="MeisterRouter")
                    log_event(
                        event_type="orchestration_end",
                        run_id=run_id,
                        task_id="orchestrator",
                        exit_code=1,
                        status="failed",
                        reason=failure,
                        error=failure,
                    )
                    return False
                logger.warning(t("engine.bridge.integration_init_failed", error=e))

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
                        logger.error(t("engine.bridge.fast_forward_main_failed", error=ff_msg))
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
