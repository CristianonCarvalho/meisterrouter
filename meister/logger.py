"""
meister.logger — Registro de telemetria, eventos e custos de orquestração.

Grava eventos em formato JSONL (um JSON por linha, append-only) para suportar
concorrência atômica entre múltiplos subagentes, hooks e instâncias de terminal.
"""

import os
import json
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Dict, Any, List, Optional
from meister.models import estimate_cost

DEFAULT_LOG_DIR = os.path.expanduser("~/.meister/logs")


def get_log_dir() -> str:
    """Retorna o diretório de logs configurado ou padrão."""
    log_dir = os.environ.get("MEISTER_LOG_DIR")
    if not log_dir:
        # Se estiver dentro de um repositório git com .meister, usa local
        if os.path.exists(".meister"):
            log_dir = os.path.abspath(".meister/logs")
        else:
            log_dir = DEFAULT_LOG_DIR
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def get_log_file() -> str:
    """Retorna o caminho do arquivo orchestration_log.jsonl."""
    return os.path.join(get_log_dir(), "orchestration_log.jsonl")


def log_event(event_type: str, **fields) -> Dict[str, Any]:
    """Grava um evento genérico no arquivo de telemetria."""
    record = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        **fields,
    }
    log_path = get_log_file()
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def log_classify(task_id: str, context: str, classification: str,
                 recommended_implementer: str, confidence: float,
                 tokens_in: int = 0, tokens_out: int = 0) -> Dict[str, Any]:
    """Registra uma decisão de classificação do Jev."""
    return log_event(
        "classify",
        task_id=task_id,
        context=context[:300],
        classification=classification,
        recommended_implementer=recommended_implementer,
        confidence=confidence,
        model="typesafe/jev-1.13",
        cost_usd=estimate_cost("typesafe/jev-1.13", tokens_in, tokens_out),
    )


def log_control(task_id: str, action: str, should_escalate: bool,
                switch_implementer: bool, confidence: float = 0.95,
                tokens_in: int = 0, tokens_out: int = 0) -> Dict[str, Any]:
    """Registra uma decisão de controle de ciclo do Jev."""
    return log_event(
        "control",
        task_id=task_id,
        action=action,
        should_escalate=should_escalate,
        switch_implementer=switch_implementer,
        confidence=confidence,
        model="typesafe/jev-1.13",
        cost_usd=estimate_cost("typesafe/jev-1.13", tokens_in, tokens_out),
    )


@contextmanager
def track_task(task_id: str, subagent: str, level: str = "", instance_label: str = ""):
    """
    Context manager para rastrear tempo e custo de execução de subagente.
    """
    start = time.monotonic()
    log_event("task_start", task_id=task_id, subagent=subagent, level=level,
              instance_label=instance_label)
    state = {"tokens_in": 0, "tokens_out": 0, "cache_read": 0, "status": "ok", "error": None}
    try:
        yield state
    except Exception as e:
        state["status"] = "error"
        state["error"] = str(e)
        raise
    finally:
        duration = round(time.monotonic() - start, 3)
        cost = estimate_cost(subagent, state["tokens_in"], state["tokens_out"], state["cache_read"])
        log_event(
            "task_end",
            task_id=task_id,
            subagent=subagent,
            level=level,
            instance_label=instance_label,
            duration_seconds=duration,
            status=state["status"],
            error=state["error"],
            tokens_in=state["tokens_in"],
            tokens_out=state["tokens_out"],
            cache_read_tokens=state["cache_read"],
            cost_usd=cost,
        )


def read_events(limit: int = 500) -> List[Dict[str, Any]]:
    """Lê os últimos N eventos gravados na telemetria."""
    log_path = get_log_file()
    if not os.path.exists(log_path):
        return []
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    events = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events
