"""
meister.logger — Registro de telemetria, eventos e custos de orquestração com envelope correlacionado.

Grava eventos em formato JSONL (um JSON por linha, append-only) para suportar
concorrência atômica entre múltiplos subagentes, hooks e instâncias de terminal.
Envelope unificado (Achado #32):
{run_id, task_id, attempt, tier, event, ts, duration_ms, cost, exit_code}
"""

import hashlib
import json
import os
import time
import uuid
import threading
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Callable, Dict, Any, List, Optional
from meister.models import estimate_cost

DEFAULT_LOG_DIR = os.path.expanduser("~/.meister/logs")
_event_observers: List[Callable[[Dict[str, Any]], None]] = []
_event_observers_lock = threading.RLock()


def add_event_observer(callback: Callable[[Dict[str, Any]], None]) -> None:
    """Registra um observador de eventos em memória."""
    with _event_observers_lock:
        if callback not in _event_observers:
            _event_observers.append(callback)


def remove_event_observer(callback: Callable[[Dict[str, Any]], None]) -> None:
    """Remove um observador previamente registrado."""
    with _event_observers_lock:
        try:
            _event_observers.remove(callback)
        except ValueError:
            pass


def find_project_root() -> Optional[str]:
    """Localiza a raiz do projeto MeisterRouter buscando por .meister ou raiz git de forma determinística."""
    curr = os.path.abspath(os.getcwd())
    while curr != os.path.dirname(curr):
        if os.path.exists(os.path.join(curr, ".meister")):
            return curr
        if os.path.exists(os.path.join(curr, ".git")):
            return curr
        curr = os.path.dirname(curr)
    return None


def get_log_dir() -> str:
    """Retorna o diretório de logs configurado ou padrão determinístico (Achado #32)."""
    log_dir = os.environ.get("MEISTER_LOG_DIR")
    if not log_dir:
        root = find_project_root()
        if root:
            meister_dir = os.path.join(root, ".meister")
            os.makedirs(meister_dir, exist_ok=True)
            gi = os.path.join(meister_dir, ".gitignore")
            if not os.path.exists(gi):
                try:
                    with open(gi, "w", encoding="utf-8") as f:
                        f.write("*\n")
                except Exception:
                    pass
            log_dir = os.path.join(meister_dir, "logs")
        else:
            log_dir = DEFAULT_LOG_DIR
    os.makedirs(log_dir, exist_ok=True)
    return os.path.abspath(log_dir)


def get_log_file() -> str:
    """Retorna o caminho do arquivo orchestration_log.jsonl."""
    return os.path.join(get_log_dir(), "orchestration_log.jsonl")


def save_current_run(run_id: str, task_id: Optional[str] = None) -> None:
    """Salva o contexto da execução atual para correlação determinística entre classify, worker e control (E2E-6)."""
    data = {
        "run_id": run_id,
        "task_id": task_id or "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    log_dir = get_log_dir()
    path = os.path.join(log_dir, "current_run.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass
    root = find_project_root()
    if root and os.path.abspath(root) != os.path.abspath(log_dir):
        meister_dir = os.path.join(root, ".meister")
        if os.path.exists(meister_dir):
            gi = os.path.join(meister_dir, ".gitignore")
            if not os.path.exists(gi):
                try:
                    with open(gi, "w", encoding="utf-8") as f:
                        f.write("*\n")
                except Exception:
                    pass
            try:
                with open(os.path.join(meister_dir, "current_run.json"), "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
            except Exception:
                pass


def get_current_run() -> Dict[str, Any]:
    """Recupera o contexto da execução atual para correlação de eventos (E2E-6)."""
    log_dir = get_log_dir()
    path = os.path.join(log_dir, "current_run.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    root = find_project_root()
    if root:
        alt_path = os.path.join(root, ".meister", "current_run.json")
        if os.path.exists(alt_path):
            try:
                with open(alt_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


def clear_current_run() -> None:
    """Limpa o contexto da execução atual."""
    for d in [get_log_dir(), find_project_root()]:
        if not d:
            continue
        for p in [os.path.join(d, "current_run.json"), os.path.join(d, ".meister", "current_run.json")]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def log_event(
    event_type: str,
    run_id: Optional[str] = None,
    task_id: Optional[str] = None,
    attempt: int = 1,
    tier: Optional[str] = None,
    duration_ms: Optional[float] = None,
    cost: Optional[float] = None,
    exit_code: Optional[int] = None,
    **fields,
) -> Dict[str, Any]:
    """Grava um evento correlacionado com envelope unificado no JSONL de telemetria (Achado #32)."""
    now_ts = datetime.now(timezone.utc).isoformat()
    current = get_current_run()
    resolved_run_id = (
        run_id
        or fields.pop("run", None)
        or os.environ.get("MEISTER_RUN_ID")
        or current.get("run_id")
        or "global"
    )
    resolved_task_id = (
        task_id
        or fields.pop("subtask_id", None)
        or current.get("task_id")
        or ""
    )
    resolved_tier = tier or fields.pop("model", None) or "unknown"
    resolved_cost = cost if cost is not None else float(fields.pop("cost_usd", 0.0))
    resolved_duration = duration_ms if duration_ms is not None else float(fields.pop("duration_seconds", 0.0) * 1000.0 if "duration_seconds" in fields else 0.0)
    resolved_exit_code = exit_code if exit_code is not None else int(fields.pop("code", 0) if "code" in fields else 0)

    record: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "run_id": resolved_run_id,
        "task_id": resolved_task_id,
        "attempt": attempt,
        "tier": resolved_tier,
        "event": event_type,
        "event_type": event_type,  # retrocompatibilidade
        "ts": now_ts,
        "timestamp": now_ts,       # retrocompatibilidade
        "duration_ms": round(float(resolved_duration), 2),
        "cost": round(float(resolved_cost), 6),
        "cost_usd": round(float(resolved_cost), 6),  # retrocompatibilidade
        "exit_code": resolved_exit_code,
        **fields,
    }

    log_path = get_log_file()
    with _event_observers_lock:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        for callback in tuple(_event_observers):
            try:
                callback(record)
            except Exception:
                continue
    return record


def log_classify(
    task_id: str,
    context: str,
    classification: str,
    recommended_implementer: str,
    confidence: Optional[float] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: Optional[float] = None,
    run_id: Optional[str] = None,
    attempt: int = 1,
    duration_ms: float = 0.0,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Registra uma decisão de classificação do Jev com envelope correlacionado e modelo configurável (Achado #32)."""
    resolved_model: str = str(model or "")
    cost_usd = cost if cost is not None else estimate_cost(resolved_model, tokens_in, tokens_out)
    return log_event(
        event_type="classify",
        run_id=run_id,
        task_id=task_id,
        attempt=attempt,
        tier="jev",
        duration_ms=duration_ms,
        cost=cost_usd,
        exit_code=0,
        context=context[:300],
        context_chars=len(context),
        context_sha256=hashlib.sha256(context.encode("utf-8")).hexdigest(),
        classification=classification,
        recommended_implementer=recommended_implementer,
        confidence=confidence,
        model=resolved_model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
    )


def log_control(
    task_id: str,
    action: str,
    should_escalate: bool,
    switch_implementer: bool,
    confidence: Optional[float] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: Optional[float] = None,
    run_id: Optional[str] = None,
    attempt: int = 1,
    duration_ms: float = 0.0,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Registra uma decisão de controle de ciclo do Jev com envelope correlacionado e modelo configurável (Achado #32)."""
    resolved_model: str = str(model or "")
    cost_usd = cost if cost is not None else estimate_cost(resolved_model, tokens_in, tokens_out)
    return log_event(
        event_type="control",
        run_id=run_id,
        task_id=task_id,
        attempt=attempt,
        tier="jev",
        duration_ms=duration_ms,
        cost=cost_usd,
        exit_code=0,
        action=action,
        should_escalate=should_escalate,
        switch_implementer=switch_implementer,
        confidence=confidence,
        model=resolved_model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
    )


@contextmanager
def track_task(
    task_id: str,
    subagent: str,
    level: str = "",
    instance_label: str = "",
    run_id: Optional[str] = None,
    attempt: int = 1,
):
    """Context manager para rastrear tempo e custo de execução de subagente com envelope correlacionado (Achado #32)."""
    start = time.monotonic()
    resolved_run_id = run_id or os.environ.get("MEISTER_RUN_ID") or "global"
    log_event(
        event_type="task_start",
        run_id=resolved_run_id,
        task_id=task_id,
        attempt=attempt,
        tier=subagent,
        level=level,
        instance_label=instance_label,
        subagent=subagent,
    )
    state: Dict[str, Any] = {"tokens_in": 0, "tokens_out": 0, "cache_read": 0, "status": "ok", "error": None, "cost": None}
    exit_code = 0
    try:
        yield state
    except Exception as e:
        state["status"] = "error"
        state["error"] = str(e)
        exit_code = 1
        raise
    finally:
        duration_sec = time.monotonic() - start
        duration_ms = round(duration_sec * 1000.0, 2)
        cost_raw = state.get("cost")
        cost: Optional[float] = float(cost_raw) if cost_raw is not None else None
        if cost is None:
            tokens_in = int(state.get("tokens_in") or 0)
            tokens_out = int(state.get("tokens_out") or 0)
            cache_read = int(state.get("cache_read") or 0)
            cost = estimate_cost(subagent, tokens_in, tokens_out, cache_read)
        log_event(
            event_type="task_end",
            run_id=resolved_run_id,
            task_id=task_id,
            attempt=attempt,
            tier=subagent,
            duration_ms=duration_ms,
            cost=cost,
            exit_code=exit_code,
            level=level,
            instance_label=instance_label,
            duration_seconds=round(duration_sec, 3),
            status=state["status"],
            error=state["error"],
            tokens_in=state["tokens_in"],
            tokens_out=state["tokens_out"],
            cache_read_tokens=state["cache_read"],
            cost_usd=cost,
            subagent=subagent,
        )


def read_events(limit: int = 500, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lê eventos gravados na telemetria, opcionalmente filtrando por run_id."""
    log_path = get_log_file()
    if not os.path.exists(log_path):
        return []
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
            if run_id is None or ev.get("run_id") == run_id:
                events.append(ev)
        except json.JSONDecodeError:
            continue
    return events[-limit:]


def get_events_by_run_id(run_id: str) -> List[Dict[str, Any]]:
    """Recupera todos os eventos correlacionados ao run_id em ordem cronológica (Achado #32)."""
    return read_events(limit=10000, run_id=run_id)
