"""Live progress and end-of-run summaries for orchestration."""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TextIO


def format_duration(seconds: float) -> str:
    """Format elapsed seconds for concise, stable CLI output."""
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds_remainder = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {seconds_remainder:02d} s"
    return f"{seconds_remainder} s"


def _event_name(record: Dict[str, Any]) -> str:
    return str(record.get("event_type") or record.get("event") or "")


def _short_reason(record: Dict[str, Any]) -> str:
    reason = str(record.get("error") or record.get("reason") or record.get("output") or "erro desconhecido")
    reason = " ".join(reason.split())
    return reason if len(reason) <= 120 else reason[:117] + "..."


def format_event_line(
    record: Dict[str, Any],
    task_number: Optional[int] = None,
    total: Optional[int] = None,
) -> Optional[str]:
    """Return the stable display line for a task event, or None if it is not displayable."""
    event = _event_name(record)
    task_id = str(record.get("task_id") or "")
    if event == "plan_parsed":
        run_id = str(record.get("run_id") or "")
        return (
            f"Plano: {int(record.get('total') or 0)} tarefas em "
            f"{int(record.get('batches') or 0)} lotes (run {run_id[:8]})"
        )
    if task_number is None or total is None:
        return None

    prefix = f"[{task_number}/{total}] {task_id}"
    tier = str(record.get("tier") or "-")
    if event == "worker_spawn":
        return f"{prefix} iniciada em {tier}"
    if event == "subtask_completed":
        return f"{prefix} concluida em {tier} ({record.get('duration') or '-'})"
    if event == "subtask_reused":
        source_run_id = str(record.get("source_run_id") or "")
        return f"{prefix} reaproveitada do run {source_run_id[:8]}"
    if event == "worker_retry":
        retry = record.get("retry", record.get("attempt", 1))
        maximum = record.get("max_retries", record.get("max_attempts", retry))
        return f"{prefix} pane perdido; retentativa {retry}/{maximum} em {tier}"
    if event == "quota_error":
        next_tier = record.get("next_tier") or record.get("next_via")
        suffix = f"; tentando {next_tier}" if next_tier else ""
        return f"{prefix} cota esgotada em {tier}{suffix}"
    if event in {"subtask_rejected", "worker_error", "gate_infrastructure_error"}:
        return f"{prefix} FALHOU: {_short_reason(record)}"
    return None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def _is_reused(row: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    result = row.get("result_json") or {}
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            return False, None
    if not isinstance(result, dict):
        return False, None
    resumed_from = result.get("resumed_from")
    if not isinstance(resumed_from, dict):
        return False, None
    return True, str(resumed_from.get("run_id") or "")


def render_summary(
    state_manager: Any,
    run_id: str,
    durations: Dict[str, float],
    elapsed_total: float,
) -> str:
    """Build a stable summary using persisted subtask state and measured task durations."""
    subtasks = state_manager.get_subtasks(run_id)
    total = len(subtasks)
    reused_rows = {str(row.get("step_id") or ""): _is_reused(row) for row in subtasks}
    completed = sum(
        1
        for row in subtasks
        if row.get("status") == "COMPLETED" and not reused_rows[str(row.get("step_id") or "")][0]
    )
    failed = sum(1 for row in subtasks if row.get("status") == "FAILED")
    reused = sum(1 for was_reused, _ in reused_rows.values() if was_reused)
    lines = [
        f"Resumo do run {run_id[:8]}: {total} tarefas | {completed} concluidas | "
        f"{failed} falhou | {reused} reaproveitadas | tempo total {format_duration(elapsed_total)}"
    ]

    for row in subtasks:
        step_id = str(row.get("step_id") or row.get("subtask_id") or "-")
        status = str(row.get("status") or "PENDING")
        was_reused, source_run_id = reused_rows[step_id]
        if was_reused:
            display_status = "reaproveitada"
            duration = "-"
        elif status == "COMPLETED":
            display_status = "concluida"
            duration = format_duration(durations[step_id]) if step_id in durations else "-"
        elif status == "FAILED":
            display_status = "FALHOU"
            duration = format_duration(durations[step_id]) if step_id in durations else "-"
        else:
            display_status = status.lower()
            duration = format_duration(durations[step_id]) if step_id in durations else "-"
        tier = str(row.get("assigned_tier") or "-")
        detail = ""
        if was_reused and source_run_id:
            detail = f"  run {source_run_id[:8]}"
        elif status == "FAILED":
            error = " ".join(str(row.get("error_message") or "erro desconhecido").split())
            detail = f"  {error[:120]}"
        lines.append(f"  {step_id:<12} {display_status:<13} {tier:<14} {duration:>8}{detail}")

    run_record = state_manager.get_run(run_id)
    run_completed = bool(run_record and run_record.get("state") == "COMPLETED")
    if not run_record:
        run_completed = total > 0 and completed + reused == total and failed == 0
    if run_completed:
        lines.append("Concluido: a main foi atualizada.")
    else:
        lines.append("Proximo passo: corrija a causa e rode o mesmo comando (ou --resume se editou o plano).")
    return "\n".join(lines)


class ProgressReporter:
    """Thread-safe observer that prints plan/task progress to stderr."""

    def __init__(
        self,
        total_hint: Optional[int] = None,
        stream: Optional[TextIO] = None,
        quiet: bool = False,
        retry_max: int = 1,
    ):
        self.total_hint = total_hint
        self.stream = stream if stream is not None else sys.stderr
        self.quiet = quiet
        self.retry_max = retry_max
        self.run_id: Optional[str] = None
        self.task_order: Dict[str, int] = {}
        self.total: Optional[int] = total_hint
        self.durations: Dict[str, float] = {}
        self._started_at: Dict[str, datetime] = {}
        self._lock = threading.Lock()

    def on_event(self, record: Dict[str, Any]) -> None:
        if self.quiet:
            return
        event = _event_name(record)
        if event == "orchestration_start":
            self.run_id = str(record.get("run_id") or "")
            return
        event_run_id = record.get("run_id")
        if self.run_id and event_run_id and event_run_id != self.run_id:
            return
        if event == "plan_parsed":
            ids = record.get("task_ids") or []
            if isinstance(ids, list):
                self.task_order = {str(task_id): index for index, task_id in enumerate(ids, 1)}
            self.total = int(record.get("total") or self.total_hint or 0)
            line = format_event_line(record)
            if line:
                self._write(line)
            return

        task_id = str(record.get("task_id") or "")
        if task_id not in self.task_order:
            return
        if event == "worker_retry":
            record = dict(record)
            record.setdefault("max_retries", self.retry_max)
        if event == "worker_spawn":
            timestamp = _parse_timestamp(record.get("ts") or record.get("timestamp"))
            if timestamp is not None:
                self._started_at.setdefault(task_id, timestamp)
        elif event in {
            "subtask_completed",
            "subtask_rejected",
            "worker_error",
            "gate_infrastructure_error",
        }:
            timestamp = _parse_timestamp(record.get("ts") or record.get("timestamp"))
            started = self._started_at.get(task_id)
            if timestamp is not None and started is not None:
                self.durations[task_id] = max(0.0, (timestamp - started).total_seconds())
            if event == "subtask_completed" and task_id in self.durations:
                record = dict(record)
                record["duration"] = format_duration(self.durations[task_id])
        line = format_event_line(record, self.task_order[task_id], self.total)
        if line:
            self._write(line)

    def _write(self, line: str) -> None:
        with self._lock:
            self.stream.write(line + "\n")
            self.stream.flush()
