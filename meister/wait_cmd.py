"""Lógica do `meister wait`: espera uma run terminar e devolve o código que diz como ela terminou.

O comando só lê o `orchestration_log.jsonl`; nunca grava nele nem cria arquivos.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from meister.dashboard.metrics import iter_events, list_runs
from meister.i18n import t
from meister.timeline_cli import pick_run
from meister.timeline_json import timeline_json_for_log

EXIT_DONE = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_TIMEOUT = 124
EXIT_INTERRUPTED = 130

_EXIT_BY_STATUS = {
    "completed": EXIT_DONE,
    "failed": EXIT_FAILED,
    "interrupted": EXIT_INTERRUPTED,
}


class WaitUsageError(Exception):
    """Erro de uso: log inexistente, sem runs ou id de run não resolvido."""


def _sleep(seconds: float) -> None:
    """Costura para o intervalo de espera: testes trocam esta função, nunca `time.sleep` global."""
    time.sleep(seconds)


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _run_state(events: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    """Estado da run a partir dos eventos brutos, na ordem do arquivo (o log só cresce).

    Uma run retomada (orchestration_start depois do último orchestration_end) está em andamento.
    """
    last_end: Optional[int] = None
    last_start: Optional[int] = None
    first_start: Optional[dict[str, Any]] = None
    first_event: Optional[dict[str, Any]] = None
    for index, event in enumerate(events):
        if str(event.get("run_id") or "") != run_id:
            continue
        if first_event is None:
            first_event = event
        kind = event.get("event_type") or event.get("event")
        if kind == "orchestration_start":
            last_start = index
            if first_start is None:
                first_start = event
        elif kind == "orchestration_end":
            last_end = index

    state: dict[str, Any] = {
        "status": "running",
        "exit_code": None,
        "reason": None,
        "started_at": _parse_ts((first_start or first_event or {}).get("ts")),
        "ended_at": None,
    }
    if last_end is None or (last_start is not None and last_start > last_end):
        return state

    end = events[last_end]
    raw_status = str(end.get("status") or "completed")
    status = raw_status if raw_status in _EXIT_BY_STATUS else "failed"
    reason = end.get("reason") or end.get("error") or end.get("message")
    state.update(
        status=status,
        exit_code=_EXIT_BY_STATUS[status],
        reason=str(reason).replace("\n", " ").strip()[:180] if reason else None,
        ended_at=_parse_ts(end.get("ts")),
    )
    return state


def _resolve_run(log_file: str, run_id: Optional[str]) -> str:
    if not os.path.isfile(log_file):
        raise WaitUsageError(t("wait.log_not_found", path=log_file))
    runs = list_runs(iter_events(log_file))
    if not runs:
        raise WaitUsageError(t("wait.no_runs", path=log_file))
    try:
        return pick_run(runs, run_id)
    except ValueError as error:
        raise WaitUsageError(t("wait.error", error=error)) from error


def _report(log_file: str, run_id: str, state: dict[str, Any], now: datetime) -> dict[str, Any]:
    summary = timeline_json_for_log(log_file, run_id=run_id, now=now)["summary"]
    started_at = state["started_at"]
    end_at = state["ended_at"] or now
    duration = max((end_at - started_at).total_seconds(), 0.0) if started_at else 0.0
    return {
        "run_id": run_id,
        "status": state["status"],
        "exit_code": state["exit_code"] if state["exit_code"] is not None else EXIT_TIMEOUT,
        "tasks_total": summary["total"],
        "tasks_completed": summary["completed"],
        "tasks_failed": summary["failed"],
        "duration_seconds": round(duration, 1),
        "reason": state["reason"],
    }


def wait_for_run(
    log_file: str,
    run_id: Optional[str],
    *,
    timeout: Optional[float],
    interval: float,
) -> dict[str, Any]:
    """Bloqueia até a run terminar (ou estourar o timeout) e devolve o relatório com `exit_code`."""
    resolved = _resolve_run(log_file, run_id)
    deadline = time.monotonic() + timeout if timeout else None
    while True:
        state = _run_state(list(iter_events(log_file)), resolved)
        if state["status"] != "running":
            return _report(log_file, resolved, state, datetime.now(timezone.utc))
        if deadline is not None and time.monotonic() >= deadline:
            return _report(log_file, resolved, state, datetime.now(timezone.utc))
        _sleep(interval)


def format_text(report: dict[str, Any]) -> str:
    """Resumo de uma linha no idioma ativo."""
    status = t(f"wait.status.{report['status']}")
    duration = t("wait.duration", seconds=f"{report['duration_seconds']:.1f}")
    fields = {
        "run_id": str(report["run_id"])[:8],
        "status": status,
        "completed": report["tasks_completed"],
        "failed": report["tasks_failed"],
        "total": report["tasks_total"],
        "duration": duration,
    }
    if report["reason"]:
        return t("wait.summary_reason", reason=report["reason"], **fields)
    return t("wait.summary", **fields)
