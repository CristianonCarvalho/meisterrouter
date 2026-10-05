# Tela TUI de linha do tempo (Gantt) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execução neste projeto:** a implementação é delegada a um worker Copilot (`MEISTER_IN_PANE=1`) numa tab do Herdr, em worktree/branch próprios; o worker **não commita nem faz push** (o orquestrador verifica e só commita com OK do usuário). Onde o template diz "Commit", este plano usa **Checkpoint**.

**Goal:** `meister timeline`: tela de terminal colorida com Gantt das tarefas de um run (ao vivo e replay), incluindo paralelismo, fase corrente e progresso geral.

**Architecture:** Quatro unidades independentes: `log_tail.py` (leitura incremental, só leitura), `timeline.py` (modelo puro: eventos → `Timeline`), `timeline_view.py` (desenho ANSI puro) e `timeline_cli.py`/`timeline_app.py` (comando, laço de teclas e atualização). Duas entregas: PR 1 (tarefas 1 a 4) = modelo, desenho e `--once`; PR 2 (tarefas 5 a 7) = interativo, tecla `t` no overlay e manual.

**Tech Stack:** Python ≥ 3.10 (CI roda 3.10 a 3.13), ANSI puro, `click`, `pytest`. Nenhuma dependência nova.

**Spec:** `docs/superpowers/specs/2026-10-05-timeline-tui-design.md`

## Global Constraints

- Sem dependência nova (nada de `rich`/`textual`); só biblioteca padrão e o que o projeto já usa.
- Compatível com Python 3.10: `from __future__ import annotations` nos módulos novos; sem `match`, sem `datetime.UTC`, sem `X | Y` em tempo de execução fora de anotações.
- O comando **só lê** o log: nunca escreve, nunca cria diretório (`get_log_dir()` cria `.meister/`; **não** use `get_log_dir()`/`get_log_file()` nos módulos novos: use `resolve_log_file` da Tarefa 1).
- **Sem nomes de modelo no código**: `tests/test_no_hardcoded_models.py` varre `meister/cli.py` e `meister/herdr/tui.py` e proíbe `sonnet`, `opus`, `haiku`, `gemini`, `gpt-6`, `luna` etc. A cor da via vem da posição dela no catálogo, nunca de um nome.
- Largura mínima 80 colunas; terminal sem cor (`NO_COLOR`, `TERM=dumb`, não-TTY) → saída sem nenhuma sequência ANSI.
- Todas as strings de interface em português.
- Suíte, `ruff check .` e `mypy meister` limpos; testes sem rede e sem tocar `~/.meister`.
- O worker não commita, não faz push, não toca em nenhum outro projeto (CRM_Base, UFG_TODO) e não chama CLI de IA.

## Review Focus

1. **Linha parcial no fim do log** (o Meister está no meio de uma escrita): o leitor não pode perder nem duplicar o evento; ele só vira evento quando a linha completa. (Tarefa 1)
2. **Log encolhido/trocado** (`meister clean`, novo run limpando o arquivo): o leitor relê do início e avisa `reset`, em vez de travar no deslocamento antigo. (Tarefa 1)
3. **Run sem `orchestration_end`** (processo morto, Ctrl+C): a tela continua "AO VIVO" sem inventar abandono; run **encerrado** com tarefa sem conclusão não pode pulsar para sempre. (Tarefa 2)
4. **Log antigo sem `plan_parsed` e sem `worker_phase`**: tarefas aparecem na ordem natural, com barra única, sem erro. (Tarefa 2)
5. **Terminal estreito, sem cor ou sem TTY**: aviso curto em vez de quebrar o layout; `--once` em pipe sai sem ANSI. (Tarefas 3 e 4)

---

## Mapa de arquivos

| Arquivo | Ação | Responsabilidade |
|---|---|---|
| `meister/log_tail.py` | criar | `LogTail` (leitura incremental) e `resolve_log_file` (caminho sem efeito colateral) |
| `meister/timeline.py` | criar | Modelo puro: `Segment`, `TaskRow`, `Summary`, `Timeline`, `build_timeline` |
| `meister/timeline_view.py` | criar | Desenho puro: `View`, `Painter`, `detect_color`, `strip_ansi`, `render_frame`, `render_waiting` |
| `meister/timeline_cli.py` | criar | `project_name`, `via_index_from_config`, `pick_run`, `once_frame`, `open_timeline` |
| `meister/timeline_app.py` | criar (PR 2) | `AppState`, `handle_key`, `read_key`, `run_interactive` |
| `meister/cli.py` | modificar | comando `timeline` (fino) |
| `meister/herdr/tui.py` | modificar (PR 2) | tecla `t` abre a tela |
| `docs/MANUAL_DE_EXECUCAO.md` | modificar (PR 2) | seção da tela |
| `tests/timeline_fixtures.py` | criar | Log sintético compartilhado pelos testes |
| `tests/test_log_tail.py`, `tests/test_timeline.py`, `tests/test_timeline_view.py`, `tests/test_timeline_cli.py`, `tests/test_timeline_app.py` | criar | testes |

---

## PR 1

### Task 1: Leitor incremental do log (`log_tail.py`)

**Files:**
- Create: `meister/log_tail.py`
- Test: `tests/test_log_tail.py`

**Interfaces:**
- Consumes: `meister.logger.DEFAULT_LOG_DIR`, `meister.logger.find_project_root`.
- Produces:
  - `class LogTail(path: str)` com `poll() -> list[dict]` e atributo `reset: bool` (verdadeiro na chamada em que o arquivo foi relido do início).
  - `resolve_log_file(log_dir: Optional[str] = None) -> str` (devolve o caminho de `orchestration_log.jsonl` **sem criar nada**).

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_log_tail.py`:

```python
import json

from meister.log_tail import LogTail, resolve_log_file


def _write(path, *events, partial=""):
    with open(path, "ab") as handle:
        for event in events:
            handle.write((json.dumps(event) + "\n").encode())
        if partial:
            handle.write(partial.encode())


def test_poll_returns_only_new_events(tmp_path):
    log = tmp_path / "orchestration_log.jsonl"
    tail = LogTail(str(log))
    assert tail.poll() == []  # arquivo inexistente
    _write(log, {"n": 1}, {"n": 2})
    assert [e["n"] for e in tail.poll()] == [1, 2]
    assert tail.poll() == []
    _write(log, {"n": 3})
    assert [e["n"] for e in tail.poll()] == [3]


def test_partial_line_is_kept_until_complete(tmp_path):
    log = tmp_path / "orchestration_log.jsonl"
    tail = LogTail(str(log))
    _write(log, {"n": 1}, partial='{"n": 2, "x"')
    assert [e["n"] for e in tail.poll()] == [1]
    _write(log, partial=': "ok"}\n')
    events = tail.poll()
    assert events == [{"n": 2, "x": "ok"}]
    assert tail.poll() == []


def test_invalid_and_non_object_lines_are_ignored(tmp_path):
    log = tmp_path / "orchestration_log.jsonl"
    log.write_bytes(b'not json\n[1, 2]\n{"n": 1}\n\n')
    assert [e["n"] for e in LogTail(str(log)).poll()] == [1]


def test_truncated_file_is_reread_and_flags_reset(tmp_path):
    log = tmp_path / "orchestration_log.jsonl"
    tail = LogTail(str(log))
    _write(log, {"n": 1}, {"n": 2}, {"n": 3})
    assert len(tail.poll()) == 3
    assert tail.reset is False
    log.write_bytes(b"")
    _write(log, {"n": 9})
    events = tail.poll()
    assert [e["n"] for e in events] == [9]
    assert tail.reset is True
    tail.poll()
    assert tail.reset is False


def test_resolve_log_file_never_creates_directories(tmp_path, monkeypatch):
    monkeypatch.delenv("MEISTER_LOG_DIR", raising=False)
    explicit = tmp_path / "custom"
    assert resolve_log_file(str(explicit)) == str(explicit / "orchestration_log.jsonl")
    assert not explicit.exists()

    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "from_env"))
    assert resolve_log_file() == str(tmp_path / "from_env" / "orchestration_log.jsonl")
    assert not (tmp_path / "from_env").exists()

    monkeypatch.delenv("MEISTER_LOG_DIR")
    project = tmp_path / "proj"
    (project / ".meister").mkdir(parents=True)
    monkeypatch.chdir(project)
    assert resolve_log_file() == str(project / ".meister" / "logs" / "orchestration_log.jsonl")
    assert not (project / ".meister" / "logs").exists()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_log_tail.py`
Expected: FAIL (`ModuleNotFoundError: meister.log_tail`).

- [ ] **Step 3: Implementar**

`meister/log_tail.py`:

```python
"""Leitura incremental e somente leitura do log de orquestração."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from meister.logger import DEFAULT_LOG_DIR, find_project_root

LOG_NAME = "orchestration_log.jsonl"


def resolve_log_file(log_dir: Optional[str] = None) -> str:
    """Caminho do log, com a mesma ordem de `get_log_dir`, mas sem criar nenhum diretório."""
    if log_dir:
        return os.path.join(os.path.abspath(log_dir), LOG_NAME)
    env_dir = os.environ.get("MEISTER_LOG_DIR")
    if env_dir:
        return os.path.join(os.path.abspath(env_dir), LOG_NAME)
    root = find_project_root()
    if root:
        return os.path.join(root, ".meister", "logs", LOG_NAME)
    return os.path.join(os.path.abspath(DEFAULT_LOG_DIR), LOG_NAME)


class LogTail:
    """Devolve só os eventos novos desde a última chamada; nunca escreve no arquivo."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.reset = False
        self._offset = 0
        self._partial = b""

    def poll(self) -> List[Dict[str, Any]]:
        self.reset = False
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self._offset:
            self._offset = 0
            self._partial = b""
            self.reset = True
        if size == self._offset:
            return []
        with open(self.path, "rb") as handle:
            handle.seek(self._offset)
            chunk = handle.read(size - self._offset)
        self._offset += len(chunk)
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()  # sem "\n" no fim: linha ainda incompleta (ou b"" se completa)
        events: List[Dict[str, Any]] = []
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(event, dict):
                events.append(event)
        return events
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_log_tail.py`
Expected: PASS (5 testes).

- [ ] **Step 5: Mutação (obrigatória)** — em cópia do arquivo, troque `self._partial = lines.pop()` por `lines.pop()` (descarta a linha parcial). `test_partial_line_is_kept_until_complete` deve FALHAR. Restaure e confira com `cmp`.

- [ ] **Step 6: Checkpoint** — `git diff --stat` (não commitar).

---

### Task 2: Modelo puro da linha do tempo (`timeline.py`)

**Files:**
- Create: `meister/timeline.py`, `tests/timeline_fixtures.py`
- Test: `tests/test_timeline.py`

**Interfaces:**
- Consumes: de `meister.dashboard.metrics`: `FAILURE_EVENTS`, `LIFECYCLE_EVENTS`, `_natural_key`, `_timestamp`, `_event_time`, `clip_title`, `event_type`; `meister.report.compute_run_report`.
- Produces:
  - `Segment(phase: str, start: datetime, end: Optional[datetime], inferred: bool = False)` (frozen). `phase` ∈ `worker|gate|lock_wait|integrate|wait`.
  - `TaskRow(task_id, title, tier, attempts, status, depends_on, segments, start, end, duration_s, failure)`; `status` ∈ `waiting|running|completed|failed|reused`.
  - `Summary(total, completed, failed, running, peak_parallel, avg_parallel, cost_usd)` (`completed` inclui `reused`).
  - `Timeline(run_id, title, status, started_at, ended_at, rows, summary)`; `status` ∈ `running|completed|failed`.
  - `build_timeline(events, run_id: str, now: datetime, tier_prices=None, credit_prices=None) -> Timeline`.

- [ ] **Step 1: Criar o log sintético compartilhado**

`tests/timeline_fixtures.py`:

```python
"""Log sintético compartilhado pelos testes da linha do tempo."""

import json
from datetime import datetime, timedelta, timezone

BASE = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


def ev(kind, task, t, run="r1", **fields):
    return {"event_type": kind, "run_id": run, "task_id": task, "ts": at(t).isoformat(), **fields}


def phase(task, end_t, name, duration_s, run="r1"):
    return ev("worker_phase", task, end_t, run=run, phase=name, duration_ms=duration_s * 1000.0)


PLAN = [
    {"id": "task_1", "depends_on": []},
    {"id": "task_2", "depends_on": []},
    {"id": "task_3", "depends_on": ["task_1", "task_2"]},
]


def parallel_events(run="r1"):
    """task_1 e task_2 em paralelo (concluídas); task_3 depende das duas e está rodando em t=30."""
    return [
        ev("orchestration_start", "orchestrator", 0, run=run, task=json.dumps(PLAN)),
        ev("plan_parsed", "orchestrator", 0.1, run=run, total=3, batches=2,
           task_ids=["task_1", "task_2", "task_3"],
           task_titles={"task_1": "Task 1: Base", "task_2": "Task 2: API", "task_3": "Task 3: Docs"}),
        ev("worker_spawn", "task_1", 1, run=run, tier="copilot_luna"),
        ev("worker_spawn", "task_2", 1, run=run, tier="copilot_luna"),
        phase("task_1", 11, "worker", 10, run=run),
        phase("task_1", 12, "gate", 1, run=run),
        phase("task_1", 12, "lock_wait", 0, run=run),
        phase("task_1", 13, "integrate", 1, run=run),
        ev("subtask_completed", "task_1", 13, run=run, tier="copilot_luna",
           cost=0.5, cost_source="reported"),
        phase("task_2", 21, "worker", 20, run=run),
        phase("task_2", 22, "gate", 1, run=run),
        phase("task_2", 23, "integrate", 1, run=run),
        ev("subtask_completed", "task_2", 23, run=run, tier="agy_gemini_flash",
           cost=0.25, cost_source="reported"),
        ev("worker_spawn", "task_3", 23.5, run=run, tier="copilot_luna"),
    ]
```

- [ ] **Step 2: Escrever os testes que falham**

`tests/test_timeline.py`:

```python
from meister.timeline import build_timeline
from tests.timeline_fixtures import at, ev, parallel_events, phase


def _row(timeline, task_id):
    return next(row for row in timeline.rows if row.task_id == task_id)


def _spans(row):
    return [(s.phase, s.start, s.end) for s in row.segments]


def test_phase_start_is_end_minus_duration_and_integrate_excludes_gates():
    timeline = build_timeline(parallel_events(), "r1", at(30))
    assert _spans(_row(timeline, "task_1")) == [
        ("worker", at(1), at(11)),
        ("gate", at(11), at(12)),
        ("lock_wait", at(12), at(12)),
        ("integrate", at(12), at(13)),
    ]
    assert _row(timeline, "task_1").status == "completed"
    assert _row(timeline, "task_1").duration_s == 12.0


def test_parallel_tasks_overlap_and_summary_counts():
    timeline = build_timeline(parallel_events(), "r1", at(30))
    assert [r.task_id for r in timeline.rows] == ["task_1", "task_2", "task_3"]
    assert [r.title for r in timeline.rows] == ["Task 1: Base", "Task 2: API", "Task 3: Docs"]
    summary = timeline.summary
    assert (summary.total, summary.completed, summary.failed, summary.running) == (3, 2, 0, 1)
    assert summary.peak_parallel == 2
    assert round(summary.avg_parallel, 2) == 1.42  # 40.5 s de tarefa / 28.5 s com algo rodando
    assert summary.cost_usd == 0.75


def test_running_task_has_inferred_open_segment_and_wait_for_dependencies():
    timeline = build_timeline(parallel_events(), "r1", at(30))
    row = _row(timeline, "task_3")
    assert row.status == "running" and row.depends_on == ["task_1", "task_2"]
    assert _spans(row) == [("wait", at(0), at(23.5)), ("worker", at(23.5), None)]
    assert row.segments[-1].inferred is True
    assert timeline.status == "running" and timeline.ended_at is None


def test_open_phase_is_inferred_from_last_recorded_phase():
    base = [ev("worker_spawn", "t", 1, tier="x")]
    after_worker = build_timeline(base + [phase("t", 11, "worker", 10)], "r1", at(20))
    assert _spans(_row(after_worker, "t"))[-1] == ("gate", at(11), None)
    after_gate = build_timeline(
        base + [phase("t", 11, "worker", 10), phase("t", 12, "gate", 1)], "r1", at(20))
    assert _spans(_row(after_gate, "t"))[-1] == ("integrate", at(12), None)
    nothing = build_timeline(base, "r1", at(20))
    assert _spans(_row(nothing, "t")) == [("worker", at(1), None)]


def test_retry_adds_attempt_wait_segment_and_status_follows_last_attempt():
    events = [
        ev("worker_spawn", "t", 1, tier="x"),
        phase("t", 5, "worker", 4),
        ev("subtask_rejected", "t", 6, reason="gate", exit_code=1),
        ev("worker_spawn", "t", 8, tier="x"),
    ]
    row = _row(build_timeline(events, "r1", at(10)), "t")
    assert row.attempts == 2 and row.status == "running"
    assert ("wait", at(6), at(8)) in _spans(row)

    failed = build_timeline(events[:3], "r1", at(10))
    row = _row(failed, "t")
    assert row.status == "failed" and row.failure == "gate"
    assert failed.summary.failed == 1


def test_ended_run_closes_unfinished_task_as_failed():
    events = [
        ev("orchestration_start", "orchestrator", 0, task="x"),
        ev("worker_spawn", "t", 1, tier="x"),
        ev("orchestration_end", "orchestrator", 9, status="failed"),
    ]
    timeline = build_timeline(events, "r1", at(100))
    row = _row(timeline, "t")
    assert timeline.status == "failed" and timeline.ended_at == at(9)
    assert row.status == "failed" and row.failure == "sem conclusão"
    assert row.segments[-1].end == at(9)


def test_run_without_end_stays_running():
    timeline = build_timeline([ev("worker_spawn", "t", 1, tier="x")], "r1", at(5000))
    assert timeline.status == "running"
    assert _row(timeline, "t").status == "running"


def test_old_log_without_plan_or_phases_uses_natural_order_and_single_bar():
    events = [
        ev("worker_spawn", "task_10", 1, tier="x"),
        ev("subtask_completed", "task_10", 5, tier="x"),
        ev("worker_spawn", "task_2", 1, tier="x"),
        ev("subtask_completed", "task_2", 4, tier="x"),
        ev("subtask_completed", "task_9", 6, tier="x"),  # sem worker_spawn (log antigo)
    ]
    timeline = build_timeline(events, "r1", at(10))
    assert [r.task_id for r in timeline.rows] == ["task_2", "task_9", "task_10"]
    legacy = _row(timeline, "task_9")
    assert legacy.status == "completed" and len(legacy.segments) == 1


def test_events_out_of_order_are_sorted_and_other_runs_ignored():
    events = list(reversed(parallel_events())) + parallel_events(run="other")
    timeline = build_timeline(events, "r1", at(30))
    assert _spans(_row(timeline, "task_1"))[0] == ("worker", at(1), at(11))
    assert len(timeline.rows) == 3


def test_run_without_events_is_empty_not_an_error():
    timeline = build_timeline([], "r1", at(0))
    assert timeline.rows == [] and timeline.summary.total == 0
    assert timeline.summary.cost_usd is None


def test_dependencies_ignore_non_json_task_text():
    events = [ev("orchestration_start", "orchestrator", 0, task="faça isto"),
              ev("worker_spawn", "t", 1, tier="x")]
    assert _row(build_timeline(events, "r1", at(2)), "t").depends_on == []
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_timeline.py`
Expected: FAIL (`ModuleNotFoundError: meister.timeline`).

- [ ] **Step 4: Implementar**

`meister/timeline.py`:

```python
"""Modelo puro da linha do tempo (Gantt) de um run: eventos do log -> `Timeline`. Sem I/O."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from meister.dashboard.metrics import (
    FAILURE_EVENTS,
    LIFECYCLE_EVENTS,
    _event_time,
    _natural_key,
    _timestamp,
    clip_title,
    event_type,
)

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
_TERMINAL = FAILURE_EVENTS | {"subtask_completed", "subtask_reused"}
# Fase seguinte à última gravada (o log só registra a fase ao TERMINAR): é a fase corrente inferida.
_NEXT_PHASE = {
    None: "worker",
    "worker": "gate",
    "gate": "integrate",
    "lock_wait": "integrate",
    "integrate": "integrate",
}


@dataclass(frozen=True)
class Segment:
    phase: str  # worker | gate | lock_wait | integrate | wait
    start: datetime
    end: Optional[datetime]  # None = em andamento
    inferred: bool = False


@dataclass
class TaskRow:
    task_id: str
    title: str
    tier: Optional[str]
    attempts: int
    status: str  # waiting | running | completed | failed | reused
    depends_on: List[str]
    segments: List[Segment]
    start: Optional[datetime]
    end: Optional[datetime]
    duration_s: Optional[float]
    failure: Optional[str]


@dataclass
class Summary:
    total: int
    completed: int
    failed: int
    running: int
    peak_parallel: int
    avg_parallel: float
    cost_usd: Optional[float]


@dataclass
class Timeline:
    run_id: str
    title: str
    status: str  # running | completed | failed
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    rows: List[TaskRow]
    summary: Summary


def _ts(event: Dict[str, Any]) -> Optional[datetime]:
    return _timestamp(_event_time(event))


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _plan_dependencies(run_events: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    for event in run_events:
        if event_type(event) != "orchestration_start":
            continue
        raw = event.get("task")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return {}
        if isinstance(raw, list):
            return {
                str(item["id"]): [str(dep) for dep in item.get("depends_on") or []]
                for item in raw
                if isinstance(item, dict) and item.get("id")
            }
        return {}
    return {}


def _integrate_remainder(
    start: datetime, end: datetime, phases: List[Tuple[str, datetime, datetime]]
) -> List[Segment]:
    """Janela final menos gate e lock_wait: o que sobra é a integração (merge)."""
    covered = sorted(
        (max(s, start), min(e, end)) for name, s, e in phases if name in ("gate", "lock_wait")
    )
    out: List[Segment] = []
    cursor = start
    for s, e in covered:
        if s > cursor:
            out.append(Segment("integrate", cursor, s))
        cursor = max(cursor, e)
    if end > cursor:
        out.append(Segment("integrate", cursor, end))
    return out


def _build_row(
    task_id: str,
    events: List[Dict[str, Any]],
    title: str,
    depends_on: List[str],
    run_start: Optional[datetime],
    run_end: Optional[datetime],
) -> TaskRow:
    attempts: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    tier: Optional[str] = None
    for event in events:
        kind = event_type(event)
        ts = _ts(event)
        if event.get("tier") and event.get("tier") != "unknown":
            tier = str(event["tier"])
        if ts is None:
            continue
        if kind == "worker_spawn":
            current = {
                "spawn": ts, "worker_end": None, "phases": [], "last_phase": None,
                "last_end": ts, "terminal": None, "kind": None, "reason": None,
            }
            attempts.append(current)
            continue
        if current is None and kind in _TERMINAL:
            # log antigo: conclusão sem worker_spawn -> barra única do início do run ao fim
            current = {
                "spawn": run_start or ts, "worker_end": None, "phases": [], "last_phase": None,
                "last_end": ts, "terminal": None, "kind": None, "reason": None,
            }
            attempts.append(current)
        if current is None:
            continue
        if kind == "worker_phase":
            name = str(event.get("phase") or "")
            duration = timedelta(milliseconds=_num(event.get("duration_ms")))
            if name == "worker":
                current["worker_end"] = ts
                current["phases"].append(("worker", current["spawn"], ts))
            elif name in ("gate", "lock_wait"):
                current["phases"].append((name, ts - duration, ts))
            if name in ("worker", "gate", "lock_wait", "integrate"):
                current["last_phase"] = name
                current["last_end"] = ts
        elif kind in _TERMINAL:
            current["terminal"] = ts
            current["kind"] = kind
            if kind in FAILURE_EVENTS:
                current["reason"] = str(event.get("reason") or event.get("error") or kind)

    segments: List[Segment] = []
    previous_end: Optional[datetime] = None
    for attempt in attempts:
        if previous_end is not None and attempt["spawn"] > previous_end:
            segments.append(Segment("wait", previous_end, attempt["spawn"]))
        elif previous_end is None and depends_on and run_start and attempt["spawn"] > run_start:
            segments.append(Segment("wait", run_start, attempt["spawn"]))
        segments.extend(Segment(name, s, e) for name, s, e in attempt["phases"])
        if attempt["terminal"] is not None:
            if attempt["worker_end"] is None:
                segments.append(Segment("worker", attempt["spawn"], attempt["terminal"]))
            else:
                segments.extend(
                    _integrate_remainder(attempt["worker_end"], attempt["terminal"], attempt["phases"])
                )
            previous_end = attempt["terminal"]
        else:
            open_start = attempt["last_end"]
            segments.append(
                Segment(_NEXT_PHASE[attempt["last_phase"]], open_start, run_end, inferred=True)
            )
            previous_end = None

    if not attempts:
        if run_start is not None:
            segments.append(Segment("wait", run_start, run_end, inferred=run_end is None))
        return TaskRow(task_id, title, tier, 0, "waiting", depends_on, segments, None, None, None, None)

    last = attempts[-1]
    failure: Optional[str] = None
    if last["terminal"] is not None:
        if last["kind"] == "subtask_completed":
            status = "completed"
        elif last["kind"] == "subtask_reused":
            status = "reused"
        else:
            status, failure = "failed", last["reason"]
        end: Optional[datetime] = last["terminal"]
    elif run_end is not None:
        status, failure, end = "failed", "sem conclusão", run_end
    else:
        status, end = "running", None
    start = attempts[0]["spawn"]
    duration = max(0.0, (end - start).total_seconds()) if end is not None else None
    return TaskRow(
        task_id, title, tier, len(attempts), status, depends_on, segments, start, end, duration, failure
    )


def _parallelism(rows: List[TaskRow], now_eff: datetime) -> Tuple[int, float]:
    marks: List[Tuple[datetime, int]] = []
    for row in rows:
        for seg in row.segments:
            if seg.phase == "wait":
                continue
            marks.append((seg.start, 1))
            marks.append((seg.end or now_eff, -1))
    if not marks:
        return 0, 0.0
    marks.sort(key=lambda m: (m[0], m[1]))  # fim (-1) antes de início (+1) no mesmo instante
    active = peak = 0
    union = total = 0.0
    previous = marks[0][0]
    for moment, delta in marks:
        span = (moment - previous).total_seconds()
        if active > 0:
            union += span
            total += span * active
        active += delta
        peak = max(peak, active)
        previous = moment
    return peak, (total / union if union > 0 else 0.0)


def build_timeline(
    events: Iterable[Dict[str, Any]],
    run_id: str,
    now: datetime,
    tier_prices: Optional[Dict[str, float]] = None,
    credit_prices: Optional[Dict[str, float]] = None,
) -> Timeline:
    all_events = list(events)
    indexed = [(i, e) for i, e in enumerate(all_events) if str(e.get("run_id") or "") == run_id]
    indexed.sort(key=lambda pair: (_ts(pair[1]) or _EPOCH, pair[0]))
    run_events = [event for _, event in indexed]

    start_event = next((e for e in run_events if event_type(e) == "orchestration_start"), None)
    end_event = next((e for e in reversed(run_events) if event_type(e) == "orchestration_end"), None)
    started_at = _ts(start_event) if start_event else (_ts(run_events[0]) if run_events else None)
    ended_at = _ts(end_event) if end_event else None
    title = clip_title(start_event.get("task")) if start_event else ""
    if end_event is None:
        status = "running"
    else:
        status = "completed" if str(end_event.get("status") or "completed") == "completed" else "failed"

    plan_event = next((e for e in reversed(run_events) if event_type(e) == "plan_parsed"), None)
    plan_ids = [str(i) for i in (plan_event.get("task_ids") or [])] if plan_event else []
    raw_titles = plan_event.get("task_titles") if plan_event else None
    titles = {str(k): str(v) for k, v in raw_titles.items()} if isinstance(raw_titles, dict) else {}
    dependencies = _plan_dependencies(run_events)

    by_task: Dict[str, List[Dict[str, Any]]] = {}
    for event in run_events:
        task_id = str(event.get("task_id") or "")
        if task_id and task_id != "orchestrator":
            by_task.setdefault(task_id, []).append(event)
    extras = sorted(
        (tid for tid, evs in by_task.items()
         if tid not in plan_ids and any(event_type(x) in LIFECYCLE_EVENTS for x in evs)),
        key=_natural_key,
    )
    rows = [
        _build_row(
            task_id, by_task.get(task_id, []), titles.get(task_id, ""),
            dependencies.get(task_id, []), started_at, ended_at,
        )
        for task_id in plan_ids + extras
    ]

    peak, average = _parallelism(rows, ended_at or now)
    cost: Optional[float] = None
    if run_events:
        from meister.report import compute_run_report

        report = compute_run_report(
            all_events, run_id, tier_prices=tier_prices, credit_prices=credit_prices
        )
        known = [
            row["cost_known_usd"] for row in report["by_tier"].values()
            if row.get("cost_known_usd") is not None
        ]
        cost = round(sum(known), 6) if known else None
    summary = Summary(
        total=len(rows),
        completed=sum(1 for r in rows if r.status in ("completed", "reused")),
        failed=sum(1 for r in rows if r.status == "failed"),
        running=sum(1 for r in rows if r.status == "running"),
        peak_parallel=peak,
        avg_parallel=average,
        cost_usd=cost,
    )
    return Timeline(run_id, title, status, started_at, ended_at, rows, summary)
```

- [ ] **Step 5: Rodar e ver passar**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_timeline.py`
Expected: PASS. Se um teste falhar por detalhe de ordenação de instantes iguais (`lock_wait` de duração 0), ajuste a **implementação**, não o teste, mantendo os valores esperados do Step 2.

- [ ] **Step 6: Mutações (obrigatórias)** — em cópia, uma de cada vez; cada uma deve FALHAR em pelo menos um teste: (a) `ts - duration` → `ts` na fase `gate`/`lock_wait`; (b) `_NEXT_PHASE` devolvendo sempre `"worker"`; (c) tratar run encerrado como `running` (remover o ramo `elif run_end is not None`). Restaure e confira com `cmp`.

- [ ] **Step 7: Checkpoint** — `git diff --stat` (não commitar).

---

### Task 3: Desenho ANSI (`timeline_view.py`)

**Files:**
- Create: `meister/timeline_view.py`
- Test: `tests/test_timeline_view.py`

**Interfaces:**
- Consumes: `meister.timeline.{Segment, TaskRow, Timeline}`; `tests/timeline_fixtures.py`.
- Produces:
  - `MIN_WIDTH = 80`, `CHROME_ROWS = 7`. Nenhuma linha do quadro passa de `width` (o `Painter.line` corta e completa).
  - `strip_ansi(text: str) -> str`.
  - `detect_color(env: Mapping[str, str], isatty: bool) -> str` (`"none" | "256" | "truecolor"`).
  - `@dataclass View(t_start: Optional[datetime] = None, t_end: Optional[datetime] = None, row_offset: int = 0, paused: bool = False, live: bool = True)`.
  - `render_frame(timeline, *, width, height, now, tick=0, view=None, color="none", project="", via_index=None, hue=210, tz=None, interactive=True) -> str` (linhas separadas por `"\n"`, sem `\n` final).
  - `render_waiting(message: str, *, width: int, color: str = "none") -> str`.
  - `slowest_task_id(timeline) -> Optional[str]`.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_timeline_view.py`:

```python
from datetime import timezone

from meister.timeline import build_timeline
from meister.timeline_view import (
    MIN_WIDTH, View, detect_color, render_frame, render_waiting, slowest_task_id, strip_ansi,
)
from tests.timeline_fixtures import at, parallel_events


def _timeline():
    return build_timeline(parallel_events(), "r1", at(30))


def _frame(**kwargs):
    options = dict(width=120, height=30, now=at(30), tz=timezone.utc, project="DEMO")
    options.update(kwargs)
    return render_frame(_timeline(), **options)


def test_frame_shows_header_progress_rows_and_states():
    plain = strip_ansi(_frame(color="truecolor"))
    assert "DEMO" in plain and "run r1" in plain
    assert "AO VIVO" in plain
    assert "Concluídas" in plain and "2/3" in plain
    assert "Rodando 1" in plain and "pico 2" in plain and "média 1.4" in plain
    assert "US$ 0.7500" in plain
    for task in ("task_1", "task_2", "task_3"):
        assert task in plain
    assert "Base" in plain and "Task 1:" not in plain  # prefixo "Task N:" removido do rótulo
    assert "✔" in plain and "▶ worker" in plain
    assert "12:00:00" in plain  # régua em UTC
    assert max(len(line) for line in plain.splitlines()) <= 120


def test_color_none_has_no_escape_sequences_and_truecolor_has_them():
    assert "\x1b" not in _frame(color="none")
    assert "\x1b[38;2;" in _frame(color="truecolor")
    assert "\x1b[38;5;" in _frame(color="256")


def test_pulse_changes_only_the_running_tip():
    even = _frame(color="none", tick=0).splitlines()
    odd = _frame(color="none", tick=1).splitlines()
    diffs = [(a, b) for a, b in zip("".join(even), "".join(odd)) if a != b]
    assert len(diffs) == 1 and {diffs[0][0], diffs[0][1]} == {"●", "○"}


def test_no_line_exceeds_the_width_even_at_the_minimum():
    for width in (MIN_WIDTH, 100, 200):
        plain = strip_ansi(_frame(width=width, color="truecolor"))
        assert max(len(line) for line in plain.splitlines()) <= width


def test_narrow_terminal_returns_short_warning():
    out = _frame(width=MIN_WIDTH - 1)
    assert "80 colunas" in out and "\n" not in out


def test_row_scroll_hides_rows_and_footer_says_so():
    plain = strip_ansi(_frame(height=9, view=View(row_offset=1)))  # 9 - 7 = 2 linhas visíveis
    assert "task_1" not in plain and "task_2" in plain and "task_3" in plain
    plain = strip_ansi(_frame(height=8))  # 1 linha visível
    assert "task_1" in plain and "task_2" not in plain
    assert "mais" in plain


def test_badges_follow_state():
    assert "AO VIVO" in strip_ansi(_frame())
    assert "PAUSADO" in strip_ansi(_frame(view=View(paused=True)))
    ended = build_timeline(
        parallel_events() + [
            {"event_type": "orchestration_end", "run_id": "r1", "task_id": "orchestrator",
             "ts": at(40).isoformat(), "status": "failed"}],
        "r1", at(50))
    out = strip_ansi(render_frame(ended, width=120, height=30, now=at(50), tz=timezone.utc))
    assert "FALHOU" in out
    out = strip_ansi(render_frame(ended, width=120, height=30, now=at(50), tz=timezone.utc,
                                  view=View(live=False)))
    assert "REPLAY" in out


def test_via_gets_a_color_from_catalog_position_without_model_names():
    colored = _frame(color="truecolor", via_index={"copilot_luna": 0, "agy_gemini_flash": 1})
    other = _frame(color="truecolor", via_index={"copilot_luna": 1, "agy_gemini_flash": 0})
    assert colored != other  # a cor depende só da posição informada


def test_zoom_window_changes_the_axis():
    full = strip_ansi(_frame())
    zoomed = strip_ansi(_frame(view=View(t_start=at(0), t_end=at(10))))
    assert full != zoomed


def test_detect_color():
    assert detect_color({}, isatty=False) == "none"
    assert detect_color({"NO_COLOR": "1"}, isatty=True) == "none"
    assert detect_color({"TERM": "dumb"}, isatty=True) == "none"
    assert detect_color({"COLORTERM": "truecolor"}, isatty=True) == "truecolor"
    assert detect_color({"TERM": "xterm-256color"}, isatty=True) == "256"


def test_slowest_task_and_waiting_message():
    assert slowest_task_id(_timeline()) == "task_2"
    assert "aguardando" in render_waiting("aguardando o primeiro run", width=100)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_timeline_view.py`
Expected: FAIL (`ModuleNotFoundError: meister.timeline_view`).

- [ ] **Step 3: Implementar**

`meister/timeline_view.py`:

```python
"""Desenho ANSI da linha do tempo (Gantt): funções puras, sem I/O e sem relógio interno."""

from __future__ import annotations

import colorsys
import math
import re
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from meister.timeline import Segment, TaskRow, Timeline

MIN_WIDTH = 80
LABEL_W = 26
INFO_W = 28
CHROME_ROWS = 7  # cabeçalho(2) + separador + régua + separador + rodapé(2)
PROGRESS_W = 24

RGB = Tuple[int, int, int]
Piece = Tuple[str, Optional[RGB], bool]  # (texto, cor, negrito)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
RESET = "\x1b[0m"

PALETTE: Dict[str, RGB] = {
    "worker": (64, 134, 255),
    "gate": (240, 200, 60),
    "integrate": (200, 90, 220),
    "lock_wait": (90, 96, 110),
    "wait": (90, 96, 110),
    "done": (80, 200, 120),
    "fail": (235, 70, 70),
    "retry": (255, 150, 50),
    "dim": (120, 126, 140),
    "text": (220, 224, 235),
    "live": (80, 220, 120),
    "replay": (90, 150, 255),
    "paused": (240, 200, 60),
}
# Uma cor por posição da via no catálogo (sem nomes de modelo no código).
VIA_COLORS: Tuple[RGB, ...] = (
    (70, 200, 220), (255, 160, 60), (60, 200, 170), (180, 140, 255), (240, 240, 240), (240, 120, 180),
)
GLYPHS = {"worker": "█", "gate": "▓", "integrate": "▒", "lock_wait": "░", "wait": "·"}
PHASE_LABEL = {
    "worker": "worker", "gate": "gate", "integrate": "integração", "lock_wait": "fila", "wait": "espera",
}


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def detect_color(env: Mapping[str, str], isatty: bool) -> str:
    if env.get("NO_COLOR") or env.get("TERM") == "dumb" or not isatty:
        return "none"
    return "truecolor" if env.get("COLORTERM", "").lower() in ("truecolor", "24bit") else "256"


@dataclass
class View:
    t_start: Optional[datetime] = None  # None = início do run
    t_end: Optional[datetime] = None  # None = agora (ou fim do run)
    row_offset: int = 0
    paused: bool = False
    live: bool = True


class Painter:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def _code(self, base: int, rgb: RGB) -> str:
        if self.mode == "truecolor":
            return f"\x1b[{base};2;{rgb[0]};{rgb[1]};{rgb[2]}m"
        r, g, b = (round(c / 255 * 5) for c in rgb)
        return f"\x1b[{base};5;{16 + 36 * r + 6 * g + b}m"

    def paint(self, text: str, fg: Optional[RGB] = None, bg: Optional[RGB] = None, bold: bool = False) -> str:
        if self.mode == "none" or not text or (fg is None and bg is None and not bold):
            return text
        prefix = ("\x1b[1m" if bold else "") + (self._code(38, fg) if fg else "") + (self._code(48, bg) if bg else "")
        return prefix + text + RESET

    def line(self, pieces: Sequence[Piece], width: int, bg: Optional[RGB] = None) -> str:
        """Junta os pedaços, corta em `width` (nenhuma linha passa da largura) e completa com espaços."""
        clipped: List[Piece] = []
        remaining = width
        for text, fg, bold in pieces:
            if remaining <= 0:
                break
            clipped.append((text[:remaining], fg, bold))
            remaining -= len(text[:remaining])
        if remaining > 0:
            clipped.append((" " * remaining, None, False))
        return "".join(self.paint(text, fg, bg, bold) for text, fg, bold in clipped)


def _fit(text: str, size: int) -> str:
    if len(text) > size:
        return text[: max(0, size - 1)] + "…"
    return text + " " * (size - len(text))


def _lighten(rgb: RGB) -> RGB:
    return (min(255, rgb[0] + 80), min(255, rgb[1] + 60), min(255, rgb[2] + 40))


def _gradient(fraction: float) -> RGB:
    red, yellow, green = (235, 70, 70), (240, 200, 60), (80, 200, 120)
    low, high, local = (red, yellow, fraction * 2) if fraction < 0.5 else (yellow, green, fraction * 2 - 1)
    return (
        int(low[0] + (high[0] - low[0]) * local),
        int(low[1] + (high[1] - low[1]) * local),
        int(low[2] + (high[2] - low[2]) * local),
    )


def _row_bg(hue: int, index: int) -> RGB:
    red, green, blue = colorsys.hls_to_rgb(hue / 360.0, 0.09 if index % 2 == 0 else 0.13, 0.38)
    return (int(red * 255), int(green * 255), int(blue * 255))


def slowest_task_id(timeline: Timeline) -> Optional[str]:
    done = [r for r in timeline.rows if r.status == "completed" and r.duration_s is not None]
    return max(done, key=lambda r: r.duration_s or 0.0).task_id if done else None


def _duration_text(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    total = int(round(seconds))
    if total >= 3600:
        return f"{total // 3600} h {total % 3600 // 60:02d} min"
    if total >= 60:
        return f"{total // 60} min {total % 60:02d} s"
    return f"{total} s"


def _label(row: TaskRow) -> str:
    title = re.sub(r"^Task\s+\d+\s*:\s*", "", row.title).strip()
    return _fit(f"{row.task_id} {title}".rstrip(), LABEL_W)


_STEPS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 43200, 86400)


def _ruler(t0: datetime, span: float, width: int, tz: Optional[tzinfo]) -> str:
    per_second = width / span
    step = next((s for s in _STEPS if s * per_second >= 12), _STEPS[-1])
    line = [" "] * width
    origin = t0.timestamp()
    moment = math.ceil(origin / step) * step
    while moment < origin + span:
        column = int((moment - origin) * per_second)
        label = datetime.fromtimestamp(moment, tz).strftime("%H:%M" if step >= 60 else "%H:%M:%S")
        if column + len(label) <= width:
            line[column:column + len(label)] = list(label)
        moment += step
    return "".join(line)


def _bar_pieces(
    row: TaskRow, t0: datetime, span: float, width: int, now_eff: datetime, tick: int
) -> List[Piece]:
    cells: List[Tuple[str, Optional[str]]] = [(" ", None)] * width

    def column(moment: datetime) -> int:
        return max(0, min(width, int((moment - t0).total_seconds() / span * width)))

    for seg in row.segments:
        end = seg.end or now_eff
        first, last = column(seg.start), column(end)
        if end > seg.start and last <= first:
            last = min(width, first + 1)
        for index in range(first, last):
            cells[index] = (GLYPHS[seg.phase], seg.phase)
    filled = [i for i, (glyph, _) in enumerate(cells) if glyph != " "]
    if row.status == "running" and filled and row.segments and row.segments[-1].end is None:
        tip = filled[-1]
        key = row.segments[-1].phase
        cells[tip] = ("●", key + "+") if tick % 2 == 0 else ("○", key)
    elif filled and filled[-1] + 1 < width and row.status in ("completed", "failed"):
        cells[filled[-1] + 1] = ("✔", "done") if row.status == "completed" else ("✖", "fail")
    pieces: List[Piece] = []
    for glyph, key in cells:
        if key is None:
            color: Optional[RGB] = None
        elif key.endswith("+"):
            color = _lighten(PALETTE[key[:-1]])
        else:
            color = PALETTE[key]
        if pieces and pieces[-1][1] == color:
            pieces[-1] = (pieces[-1][0] + glyph, color, False)
        else:
            pieces.append((glyph, color, False))
    return pieces


def _info_pieces(
    row: TaskRow, timeline: Timeline, slowest: Optional[str], via_color: Optional[RGB]
) -> List[Piece]:
    tier = row.tier or ""
    retry = f" ↻{row.attempts}" if row.attempts > 1 else ""
    pieces: List[Piece]
    if row.status == "completed":
        pieces = [("✔ ", PALETTE["done"], False),
                  (_duration_text(row.duration_s), PALETTE["fail"] if row.task_id == slowest else PALETTE["text"],
                   row.task_id == slowest)]
    elif row.status == "reused":
        pieces = [("↺ reaproveitada", PALETTE["dim"], False)]
    elif row.status == "failed":
        pieces = [(f"✖ {row.failure or 'falhou'}", PALETTE["fail"], True)]
    elif row.status == "running":
        phase = row.segments[-1].phase if row.segments else "worker"
        pieces = [("▶ ", PALETTE["live"], False), (PHASE_LABEL.get(phase, phase), PALETTE.get(phase, PALETTE["text"]), False)]
    else:
        done = {r.task_id for r in timeline.rows if r.status in ("completed", "reused")}
        waiting_on = [d for d in row.depends_on if d not in done]
        pieces = [("… aguardando " + ", ".join(waiting_on) if waiting_on else "… na fila", PALETTE["dim"], False)]
    if retry:
        pieces.append((retry, PALETTE["retry"], True))
    if tier:
        pieces.append((" " + tier, via_color or PALETTE["dim"], False))
    used = 0
    clipped: List[Piece] = []
    for text, color, bold in pieces:
        room = INFO_W - used
        if room <= 0:
            break
        clipped.append((text[:room], color, bold))
        used += len(text[:room])
    return clipped


def _badge(timeline: Timeline, view: View, tick: int) -> Tuple[str, RGB, bool]:
    if view.paused:
        return "⏸ PAUSADO", PALETTE["paused"], True
    if timeline.status == "running":
        return "▶ AO VIVO", PALETTE["live"], tick % 2 == 0
    if not view.live:
        return "⏸ REPLAY", PALETTE["replay"], True
    if timeline.status == "completed":
        return "✔ CONCLUÍDO", PALETTE["done"], True
    return "✖ FALHOU", PALETTE["fail"], True


def _clock(moment: Optional[datetime], tz: Optional[tzinfo]) -> str:
    return moment.astimezone(tz).strftime("%H:%M:%S") if moment else "agora"


def render_waiting(message: str, *, width: int, color: str = "none") -> str:
    painter = Painter(color)
    return painter.line([(f"  MEISTER  {message}", PALETTE["dim"], False)], max(width, len(message) + 12))


def render_frame(
    timeline: Timeline,
    *,
    width: int,
    height: int,
    now: datetime,
    tick: int = 0,
    view: Optional[View] = None,
    color: str = "none",
    project: str = "",
    via_index: Optional[Mapping[str, int]] = None,
    hue: int = 210,
    tz: Optional[tzinfo] = None,
    interactive: bool = True,
) -> str:
    if width < MIN_WIDTH:
        return f"Terminal estreito: use pelo menos {MIN_WIDTH} colunas (atual: {width})."
    view = view or View()
    painter = Painter(color)
    via_index = via_index or {}
    now_eff = timeline.ended_at or now
    t0 = view.t_start or timeline.started_at or now_eff
    t1 = view.t_end or now_eff
    span = max((t1 - t0).total_seconds(), 1.0)
    bar_w = width - LABEL_W - INFO_W - 2
    summary = timeline.summary
    dim, text_color = PALETTE["dim"], PALETTE["text"]
    lines: List[str] = []

    badge, badge_color, badge_bold = _badge(timeline, view, tick)
    right = f"{badge}  {_clock(timeline.started_at, tz)} → {_clock(timeline.ended_at, tz)}"
    left = f"  MEISTER  {project}   run {timeline.run_id[:8]}" + (f" · {timeline.title}" if timeline.title else "")
    left = _fit(left, width - len(right) - 1).rstrip()
    lines.append(painter.line(
        [(left, text_color, True), (" " * (width - len(left) - len(right)), None, False),
         (badge, badge_color, badge_bold), (right[len(badge):], dim, False)], width))

    done_cells = round(summary.completed / summary.total * PROGRESS_W) if summary.total else 0
    bar = [("█", _gradient(i / (PROGRESS_W - 1)), False) for i in range(done_cells)]
    bar.append(("░" * (PROGRESS_W - done_cells), dim, False))
    cost = f"US$ {summary.cost_usd:.4f}" if summary.cost_usd is not None else "US$ ?"
    lines.append(painter.line(
        [(" Concluídas ", dim, False), *bar,
         (f" {summary.completed}/{summary.total}", text_color, True),
         (f"   Rodando {summary.running} (pico {summary.peak_parallel} · média {summary.avg_parallel:.1f})", text_color, False),
         (f"   Falhas {summary.failed}", PALETTE["fail"] if summary.failed else dim, False),
         (f"   {cost}", text_color, False)], width))
    lines.append(painter.paint("─" * width, dim))
    lines.append(" " * (LABEL_W + 1) + painter.paint(_ruler(t0, span, bar_w, tz), dim))

    visible = max(1, height - CHROME_ROWS)
    rows = timeline.rows[view.row_offset: view.row_offset + visible]
    slowest = slowest_task_id(timeline)
    for index, row in enumerate(rows):
        position = via_index.get(row.tier or "")
        via_color = VIA_COLORS[position % len(VIA_COLORS)] if position is not None else None
        bg = _row_bg(hue, index) if color != "none" else None
        pieces: List[Piece] = [(_label(row), text_color, False), (" ", None, False)]
        pieces += _bar_pieces(row, t0, span, bar_w, now_eff, tick)
        pieces.append((" ", None, False))
        pieces += _info_pieces(row, timeline, slowest, via_color)
        lines.append(painter.line(pieces, width, bg))
    hidden = max(0, len(timeline.rows) - (view.row_offset + len(rows)))

    lines.append(painter.paint("─" * width, dim))
    legend: List[Piece] = []
    for phase in ("worker", "gate", "integrate", "lock_wait", "wait"):
        legend += [(GLYPHS[phase], PALETTE[phase], False), (f" {PHASE_LABEL[phase]}  ", dim, False)]
    if hidden:
        legend.append((f"  ↓ {hidden} mais (↑/↓)", PALETTE["retry"], False))
    lines.append(painter.line([(" ", None, False), *legend], width))
    keys = (
        " [ ] run   l ao vivo   p pausa   +/- zoom   ←/→ tempo   ↑/↓ linhas   ? ajuda   q sair"
        if interactive else f" Gerado às {_clock(now, tz)} · use `meister timeline` para o modo interativo"
    )
    lines.append(painter.line([(keys, dim, False)], width))
    return "\n".join(lines)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_timeline_view.py`
Expected: PASS. Ajuste a **implementação** se algum detalhe de largura/pulso divergir; os testes do Step 1 são o contrato.

- [ ] **Step 5: Mutações (obrigatórias)** — em cópia: (a) o pulso deixa de alternar (`tick % 2 == 0` → `True`): `test_pulse_changes_only_the_running_tip` deve falhar; (b) `bar_w` ignora `INFO_W` (linha passa de `width`): o teste de largura máxima deve falhar; (c) remover a checagem `NO_COLOR` em `detect_color`: `test_detect_color` deve falhar. Restaure e confira com `cmp`.

- [ ] **Step 6: Checkpoint** — `git diff --stat` (não commitar).

---

### Task 4: Ajudantes de CLI e `meister timeline --once` (`timeline_cli.py`, `cli.py`)

**Files:**
- Create: `meister/timeline_cli.py`
- Modify: `meister/cli.py` (um comando novo, perto do `report`)
- Test: `tests/test_timeline_cli.py`

**Interfaces:**
- Consumes: `LogTail`, `resolve_log_file` (Tarefa 1), `build_timeline` (2), `render_frame`, `render_waiting`, `detect_color` (3); `meister.dashboard.metrics.{iter_events, list_runs}`; `meister.config.load_config`.
- Produces:
  - `project_name(log_file: str) -> str`
  - `via_index_from_config() -> Dict[str, int]` (posição da via em `[*tier_order, *disabled]`; `{}` se a config não carregar)
  - `price_tables_from_config() -> Tuple[Dict[str, float], Dict[str, float]]` (`tier_prices`, `credit_prices`; `({}, {})` se a config falhar)
  - `pick_run(runs: list[dict], run_id: Optional[str]) -> str` (prefixo ≥ 6 caracteres; `ValueError` com a lista quando inexistente/ambíguo/curto; sem `run_id` devolve o mais novo)
  - `once_frame(log_file, run_id, *, width, now, color) -> str`
  - Comando `meister timeline [--run-id ID] [--log-dir DIR] [--once] [--no-color]`; sem `--once` neste PR: `click.UsageError("o modo interativo ainda não está disponível: use --once")`.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_timeline_cli.py`:

```python
import json
from datetime import timezone

from click.testing import CliRunner

from meister.cli import main
from meister.timeline_cli import once_frame, pick_run, project_name
from meister.timeline_view import strip_ansi
from tests.timeline_fixtures import at, parallel_events


def _write(tmp_path, events):
    log = tmp_path / "orchestration_log.jsonl"
    log.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return str(log)


def test_pick_run_prefix_default_and_errors():
    runs = [{"run_id": "bbbbbbbb2222"}, {"run_id": "aaaaaaaa1111"}]  # mais novo primeiro
    assert pick_run(runs, None) == "bbbbbbbb2222"
    assert pick_run(runs, "aaaaaa") == "aaaaaaaa1111"
    for bad in ("aaa", "zzzzzz"):
        try:
            pick_run(runs, bad)
        except ValueError as error:
            assert "bbbbbbbb2222" in str(error) or "pelo menos 6" in str(error)
        else:
            raise AssertionError("deveria falhar")


def test_project_name_from_standard_layout_and_fallback(tmp_path):
    assert project_name("/x/MeuProjeto/.meister/logs/orchestration_log.jsonl") == "MeuProjeto"
    assert project_name("/x/logs_soltos/orchestration_log.jsonl") == "logs_soltos"


def test_once_frame_renders_the_latest_run(tmp_path):
    log = _write(tmp_path, parallel_events())
    out = once_frame(log, None, width=120, now=at(30), color="none")
    assert "task_3" in out and "2/3" in out and "\x1b" not in out


def test_once_frame_without_log_says_waiting(tmp_path):
    out = once_frame(str(tmp_path / "nao_existe.jsonl"), None, width=100, now=at(0), color="none")
    assert "aguardando" in out


def test_cli_once_prints_one_frame_and_leaves_log_untouched(tmp_path):
    log = _write(tmp_path, parallel_events())
    before = (tmp_path / "orchestration_log.jsonl").stat()
    result = CliRunner().invoke(main, ["timeline", "--once", "--log-dir", str(tmp_path)])
    after = (tmp_path / "orchestration_log.jsonl").stat()
    assert result.exit_code == 0, result.output
    assert "task_1" in strip_ansi(result.output) and "\x1b" not in result.output  # não é TTY: sem cor
    assert (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)
    assert log


def test_cli_unknown_run_exits_2_and_without_once_is_a_usage_error(tmp_path):
    _write(tmp_path, parallel_events())
    bad = CliRunner().invoke(main, ["timeline", "--once", "--log-dir", str(tmp_path), "--run-id", "zzzzzz"])
    assert bad.exit_code == 2 and "inexistente" in bad.output
    interactive = CliRunner().invoke(main, ["timeline", "--log-dir", str(tmp_path)])
    assert interactive.exit_code != 0 and "--once" in interactive.output
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_timeline_cli.py`
Expected: FAIL (`ModuleNotFoundError: meister.timeline_cli`).

- [ ] **Step 3: Implementar `timeline_cli.py`**

```python
"""Ajudantes do comando `meister timeline`: resolução de run, config e quadro único."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from meister.dashboard.metrics import iter_events, list_runs
from meister.timeline import build_timeline
from meister.timeline_view import render_frame, render_waiting


def project_name(log_file: str) -> str:
    """Nome do projeto: a pasta acima de `.meister/logs`, ou a pasta do log."""
    folder = os.path.dirname(os.path.abspath(log_file))
    if os.path.basename(folder) == "logs" and os.path.basename(os.path.dirname(folder)) == ".meister":
        return os.path.basename(os.path.dirname(os.path.dirname(folder)))
    return os.path.basename(folder)


def _tiers() -> List[Any]:
    from meister.config import load_config

    config = load_config()
    return [*config.workers.tier_order, *config.workers.disabled]


def via_index_from_config() -> Dict[str, int]:
    try:
        return {tier.name: index for index, tier in enumerate(_tiers())}
    except Exception:
        return {}


def price_tables_from_config() -> Tuple[Dict[str, float], Dict[str, float]]:
    try:
        tiers = _tiers()
    except Exception:
        return {}, {}
    return (
        {tier.name: tier.cost_per_m_tokens for tier in tiers},
        {tier.name: tier.credit_usd for tier in tiers if tier.credit_usd is not None},
    )


def pick_run(runs: List[Dict[str, Any]], run_id: Optional[str]) -> str:
    ids = [str(run["run_id"]) for run in runs]
    if run_id is None:
        if not ids:
            raise ValueError("nenhum run disponível")
        return ids[0]
    if len(run_id) < 6:
        raise ValueError(f"ID deve ter pelo menos 6 caracteres: {run_id}")
    matches = [rid for rid in ids if rid.startswith(run_id)]
    if len(matches) == 1:
        return matches[0]
    detail = "ambíguo" if matches else "inexistente"
    listing = ", ".join(ids) if ids else "nenhum run disponível"
    raise ValueError(f"ID {detail}: {run_id}. Runs disponíveis: {listing}")


def once_frame(
    log_file: str, run_id: Optional[str], *, width: int, now: datetime, color: str
) -> str:
    """Um quadro completo (sem rolagem) do run escolhido, para `--once`."""
    if not os.path.isfile(log_file):
        return render_waiting("aguardando o primeiro run (log ainda não existe)", width=width, color=color)
    events = list(iter_events(log_file))
    runs = list_runs(events)
    if not runs:
        return render_waiting("aguardando o primeiro run", width=width, color=color)
    chosen = pick_run(runs, run_id)
    tier_prices, credit_prices = price_tables_from_config()
    timeline = build_timeline(events, chosen, now, tier_prices=tier_prices, credit_prices=credit_prices)
    return render_frame(
        timeline, width=width, height=len(timeline.rows) + 8, now=now, color=color,
        project=project_name(log_file), via_index=via_index_from_config(), interactive=False,
    )
```

- [ ] **Step 4: Implementar o comando em `meister/cli.py`**

Acrescente depois do comando `report`. `os`, `click` e `main` já existem no módulo; os demais imports ficam dentro da função (padrão do `report`):

```python
@main.command("timeline")
@click.option("--run-id", default=None, help="Run a exibir (prefixo ≥ 6 caracteres); padrão: o mais novo")
@click.option("--log-dir", type=click.Path(file_okay=False), default=None, help="Diretório com orchestration_log.jsonl")
@click.option("--once", is_flag=True, default=False, help="Imprime um quadro e sai (sem modo interativo)")
@click.option("--no-color", is_flag=True, default=False, help="Desliga as cores")
def timeline_command(run_id, log_dir, once, no_color):
    """Linha do tempo (Gantt) colorida das tarefas de um run, somente leitura."""
    import shutil
    from datetime import datetime, timezone

    from meister.log_tail import resolve_log_file
    from meister.timeline_cli import once_frame
    from meister.timeline_view import detect_color

    if not once:
        raise click.UsageError("o modo interativo ainda não está disponível: use --once")
    log_file = resolve_log_file(log_dir)
    isatty = sys.stdout.isatty()
    color = "none" if no_color else detect_color(os.environ, isatty)
    width = shutil.get_terminal_size((120, 24)).columns if isatty else 120
    try:
        frame = once_frame(log_file, run_id, width=width, now=datetime.now(timezone.utc), color=color)
    except ValueError as error:
        click.echo(f"Erro: {error}", err=True)
        raise click.exceptions.Exit(2)
    click.echo(frame)
```

(`sys` já é importado em `cli.py`; confirme com `grep -n "^import sys" meister/cli.py`. Não escreva nenhum nome de modelo neste arquivo.)

- [ ] **Step 5: Rodar e ver passar**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_timeline_cli.py tests/test_no_hardcoded_models.py`
Expected: PASS.

- [ ] **Step 6: Verificação real (somente leitura)**

```bash
LG=/Users/cristianocarvalho/Documents/UFG_TODO/.meister/logs
stat -f '%Sm %z' $LG/orchestration_log.jsonl
.venv/bin/python -m meister.cli timeline --once --log-dir $LG --run-id 3a5ae6
.venv/bin/python -m meister.cli timeline --once --log-dir $LG --run-id 815dd3
stat -f '%Sm %z' $LG/orchestration_log.jsonl
```
Expected: 10 e 8 tarefas, `Concluídas 10/10` e `8/8`, custo `US$ 0.5346` e `US$ 0.0606` (iguais ao `meister report`), o `stat` do log idêntico antes e depois. Cole a saída no relatório.

- [ ] **Step 7: Suíte e qualidade do PR 1**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`, a mesma com `env -i HOME="$HOME" PATH="$PATH" GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`, `.venv/bin/ruff check .`, `.venv/bin/python -m mypy meister`.
Expected: tudo verde.

- [ ] **Step 8: Checkpoint** — `git status --short` e `git diff -w --stat` (não commitar). Fim do **PR 1**.

---

## PR 2

### Task 5: Modo interativo (`timeline_app.py`)

**Files:**
- Create: `meister/timeline_app.py`
- Modify: `meister/cli.py` (o comando `timeline` passa a rodar o modo interativo quando não há `--once`)
- Test: `tests/test_timeline_app.py`

**Interfaces:**
- Consumes: `LogTail`, `build_timeline`, `list_runs`, `render_frame`, `render_waiting`, `View`, `detect_color`, `price_tables_from_config`, `via_index_from_config`, `project_name`.
- Produces:
  - `@dataclass AppState(run_index: int = 0, live: bool = True, zoom: int = 1, pan_s: float = 0.0, row_offset: int = 0, paused: bool = False, help: bool = False, quit: bool = False)`.
  - `handle_key(state: AppState, key: str, *, n_runs: int, n_rows: int, visible_rows: int, base_span_s: float) -> AppState` (função pura; devolve um estado novo).
  - `view_for(state: AppState, timeline: Timeline, now: datetime) -> View`.
  - `read_key(fd: int, timeout: float) -> Optional[str]` (nomes: `"up"`, `"down"`, `"left"`, `"right"`, ou o próprio caractere).
  - `run_interactive(log_file, *, run_id=None, color="truecolor", key_reader=read_key, stdout=None, now_fn=None, size_fn=None, max_frames=None, use_terminal=True, poll_interval=1.0, frame_interval=0.25) -> None`.

Regras de `handle_key` (todas cobertas por teste):
- `"]"`: run **mais novo** (`run_index - 1`, mínimo 0) e `live=False`; `"["`: run **mais antigo** (`run_index + 1`, máximo `n_runs - 1`) e `live=False`; trocar de run zera `zoom`, `pan_s`, `row_offset`.
- `"l"`: `live=True`, `run_index=0`, `zoom=1`, `pan_s=0`, `row_offset=0`.
- `"p"`: alterna `paused`. `"?"`: alterna `help`. `"q"`, `"\x03"` (Ctrl+C), `"\x1b"` (Esc): `quit=True`.
- `"+"`: `zoom = min(zoom * 2, 64)`; `"-"`: `zoom = max(zoom // 2, 1)`; ao voltar a `zoom=1`, `pan_s=0`.
- `"left"`/`"right"`: `pan_s` muda `±(base_span_s / zoom) / 4`, limitado a `[0, base_span_s - base_span_s / zoom]`; só tem efeito com `zoom > 1`.
- `"up"`/`"down"`: `row_offset` ±1, limitado a `[0, max(0, n_rows - visible_rows)]`.
- Qualquer outra tecla: estado inalterado.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_timeline_app.py`:

```python
import io
import json
from datetime import timezone

from meister.timeline import build_timeline
from meister.timeline_app import AppState, handle_key, run_interactive, view_for
from meister.timeline_view import strip_ansi
from tests.timeline_fixtures import at, parallel_events

COMMON = dict(n_runs=3, n_rows=10, visible_rows=4, base_span_s=120.0)


def key(state, name, **over):
    return handle_key(state, name, **{**COMMON, **over})


def test_run_navigation_leaves_live_and_resets_view():
    state = AppState(zoom=4, pan_s=10, row_offset=2)
    older = key(state, "[")
    assert (older.run_index, older.live, older.zoom, older.pan_s, older.row_offset) == (1, False, 1, 0, 0)
    assert key(older, "[").run_index == 2
    assert key(key(older, "["), "[").run_index == 2  # limitado ao mais antigo
    newer = key(older, "]")
    assert newer.run_index == 0 and newer.live is False
    back = key(newer, "l")
    assert back.live is True and back.run_index == 0


def test_zoom_pan_and_scroll_are_clamped():
    state = key(AppState(), "+")
    assert state.zoom == 2
    for _ in range(10):
        state = key(state, "+")
    assert state.zoom == 64
    zoomed = AppState(zoom=2)
    right = key(zoomed, "right")
    assert right.pan_s == 15.0  # (120/2)/4
    far = right
    for _ in range(10):
        far = key(far, "right")
    assert far.pan_s == 60.0  # base_span - base_span/zoom
    assert key(far, "left").pan_s == 45.0
    assert key(AppState(zoom=1), "right").pan_s == 0.0  # sem zoom não anda
    assert key(zoomed, "-").zoom == 1 and key(AppState(zoom=2, pan_s=9), "-").pan_s == 0.0
    assert key(AppState(), "down").row_offset == 1
    assert key(AppState(row_offset=6), "down").row_offset == 6  # max(0, 10 - 4)
    assert key(AppState(), "up").row_offset == 0


def test_toggles_quit_and_unknown_keys():
    assert key(AppState(), "p").paused is True and key(key(AppState(), "p"), "p").paused is False
    assert key(AppState(), "?").help is True
    for quit_key in ("q", "\x03", "\x1b"):
        assert key(AppState(), quit_key).quit is True
    assert key(AppState(), "z") == AppState()


def test_view_for_maps_zoom_and_pan_to_a_time_window():
    timeline = build_timeline(parallel_events(), "r1", at(30))
    assert view_for(AppState(), timeline, at(30)).t_start is None  # zoom 1 = automático
    view = view_for(AppState(zoom=2, pan_s=5.0), timeline, at(30))
    assert view.t_start == at(5.0) and view.t_end == at(20.0)  # run: 0..30 s, janela de 15 s


def _log(tmp_path, events):
    path = tmp_path / "orchestration_log.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return str(path)


def _drive(tmp_path, events, keys, frames):
    log = _log(tmp_path, events)
    out = io.StringIO()
    script = iter(keys)
    run_interactive(
        log, color="none", stdout=out, use_terminal=False, max_frames=frames,
        key_reader=lambda fd, timeout: next(script, None),
        now_fn=lambda: at(30), size_fn=lambda: (120, 30), poll_interval=0.0, frame_interval=0.0,
    )
    return out.getvalue()


def test_loop_draws_frames_without_clearing_the_screen_and_quits_on_q(tmp_path):
    out = _drive(tmp_path, parallel_events(), [None, "p", "p", "q"], frames=20)
    assert "\x1b[H" in out and "\x1b[2J" not in out
    assert "task_3" in strip_ansi(out)


def test_loop_shows_waiting_message_when_log_is_missing(tmp_path):
    out = io.StringIO()
    run_interactive(
        str(tmp_path / "nao_existe.jsonl"), color="none", stdout=out, use_terminal=False, max_frames=2,
        key_reader=lambda fd, timeout: None, now_fn=lambda: at(0), size_fn=lambda: (100, 24),
        poll_interval=0.0, frame_interval=0.0,
    )
    assert "aguardando" in strip_ansi(out.getvalue())
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_timeline_app.py`
Expected: FAIL (`ModuleNotFoundError: meister.timeline_app`).

- [ ] **Step 3: Implementar `meister/timeline_app.py`**

```python
"""Modo interativo da linha do tempo: estado das teclas (puro) e laço de atualização."""

from __future__ import annotations

import os
import select
import shutil
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, TextIO, Tuple

from meister.dashboard.metrics import list_runs
from meister.log_tail import LogTail
from meister.timeline import Timeline, build_timeline
from meister.timeline_cli import price_tables_from_config, project_name, via_index_from_config
from meister.timeline_view import CHROME_ROWS, View, render_frame, render_waiting

HELP_LINES = (
    " [ / ]   run mais antigo / mais novo        l   voltar ao ao vivo (run mais novo)",
    " p       pausar a tela                      + / -   zoom no eixo de tempo",
    " ← / →   andar no tempo (com zoom)          ↑ / ↓   rolar as linhas",
    " ?       esta ajuda                         q       sair",
)
_ESCAPES = {b"\x1b[A": "up", b"\x1b[B": "down", b"\x1b[C": "right", b"\x1b[D": "left"}


@dataclass
class AppState:
    run_index: int = 0
    live: bool = True
    zoom: int = 1
    pan_s: float = 0.0
    row_offset: int = 0
    paused: bool = False
    help: bool = False
    quit: bool = False


def handle_key(
    state: AppState, key: str, *, n_runs: int, n_rows: int, visible_rows: int, base_span_s: float
) -> AppState:
    if key in ("q", "\x03", "\x1b"):
        return replace(state, quit=True)
    if key == "p":
        return replace(state, paused=not state.paused)
    if key == "?":
        return replace(state, help=not state.help)
    if key == "l":
        return replace(state, live=True, run_index=0, zoom=1, pan_s=0.0, row_offset=0)
    if key in ("[", "]"):
        index = state.run_index + 1 if key == "[" else state.run_index - 1
        index = max(0, min(index, max(0, n_runs - 1)))
        return replace(state, run_index=index, live=False, zoom=1, pan_s=0.0, row_offset=0)
    if key in ("+", "-"):
        zoom = min(state.zoom * 2, 64) if key == "+" else max(state.zoom // 2, 1)
        return replace(state, zoom=zoom, pan_s=0.0 if zoom == 1 else state.pan_s)
    if key in ("left", "right") and state.zoom > 1:
        window = base_span_s / state.zoom
        pan = state.pan_s + (window / 4 if key == "right" else -window / 4)
        return replace(state, pan_s=max(0.0, min(pan, base_span_s - window)))
    if key in ("up", "down"):
        offset = state.row_offset + (1 if key == "down" else -1)
        return replace(state, row_offset=max(0, min(offset, max(0, n_rows - visible_rows))))
    return state


def view_for(state: AppState, timeline: Timeline, now: datetime) -> View:
    base = View(row_offset=state.row_offset, paused=state.paused, live=state.live)
    if state.zoom <= 1 or timeline.started_at is None:
        return base
    base_end = timeline.ended_at or now
    span = max((base_end - timeline.started_at).total_seconds(), 1.0)
    start = timeline.started_at + timedelta(seconds=state.pan_s)
    return replace(base, t_start=start, t_end=start + timedelta(seconds=span / state.zoom))


def read_key(fd: int, timeout: float) -> Optional[str]:
    """Lê uma tecla de `fd` (modo cbreak); setas viram "up"/"down"/"left"/"right"."""
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return None
    data = os.read(fd, 8)
    if data in _ESCAPES:
        return _ESCAPES[data]
    if data == b"\x1b":
        return "\x1b"
    try:
        return data.decode("utf-8")[:1] or None
    except UnicodeDecodeError:
        return None


def run_interactive(
    log_file: str,
    *,
    run_id: Optional[str] = None,
    color: str = "truecolor",
    key_reader: Callable[[int, float], Optional[str]] = read_key,
    stdout: Optional[TextIO] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
    size_fn: Optional[Callable[[], Tuple[int, int]]] = None,
    max_frames: Optional[int] = None,
    use_terminal: bool = True,
    poll_interval: float = 1.0,
    frame_interval: float = 0.25,
) -> None:
    out = stdout or sys.stdout
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    size_fn = size_fn or (lambda: tuple(shutil.get_terminal_size((120, 30))))  # type: ignore[arg-type,return-value]
    fd = sys.stdin.fileno() if use_terminal and sys.stdin.isatty() else -1
    saved = None
    if fd >= 0:
        import termios
        import tty

        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        out.write("\x1b[?1049h\x1b[?25l")  # tela alternada, cursor oculto
    tail = LogTail(log_file)
    events: List[Dict[str, Any]] = []
    tier_prices, credit_prices = price_tables_from_config()
    via_index = via_index_from_config()
    project = project_name(log_file)
    state = AppState()
    pinned = run_id  # --run-id fixa o run inicial (replay)
    if pinned:
        state = replace(state, live=False)
    tick = frames = 0
    last_poll = -1e9
    try:
        while not state.quit and (max_frames is None or frames < max_frames):
            now = now_fn()
            monotonic = time.monotonic()
            if monotonic - last_poll >= poll_interval:
                new = tail.poll()
                if tail.reset:
                    events = []
                events.extend(new)
                last_poll = monotonic
            runs = list_runs(events)
            width, height = size_fn()
            if not runs:
                frame = render_waiting("aguardando o primeiro run", width=width, color=color)
            else:
                ids = [r["run_id"] for r in runs]
                if pinned:
                    match = [i for i, rid in enumerate(ids) if rid.startswith(pinned)]
                    state = replace(state, run_index=match[0] if match else 0)
                    pinned = None
                if state.live:
                    state = replace(state, run_index=0)
                state = replace(state, run_index=min(state.run_index, len(ids) - 1))
                timeline = build_timeline(
                    events, ids[state.run_index], now, tier_prices=tier_prices, credit_prices=credit_prices
                )
                frame = (
                    "\n".join(HELP_LINES) if state.help else render_frame(
                        timeline, width=width, height=height, now=now, tick=tick,
                        view=view_for(state, timeline, now), color=color, project=project,
                        via_index=via_index, hue=(210 + 67 * state.run_index) % 360,
                    )
                )
            if not state.paused or state.help:
                out.write("\x1b[H" + "\x1b[K\n".join(frame.split("\n")) + "\x1b[K\x1b[J")
                out.flush()
            key = key_reader(fd, frame_interval)
            if key:
                span = 1.0
                n_rows = 0
                if runs:
                    n_rows = len(timeline.rows)
                    if timeline.started_at:
                        span = max(((timeline.ended_at or now) - timeline.started_at).total_seconds(), 1.0)
                state = handle_key(
                    state, key, n_runs=len(runs), n_rows=n_rows,
                    visible_rows=max(1, height - CHROME_ROWS), base_span_s=span,
                )
            tick += 1
            frames += 1
    except KeyboardInterrupt:
        pass
    finally:
        if fd >= 0 and saved is not None:
            import termios

            out.write("\x1b[?25h\x1b[?1049l")
            out.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
```

- [ ] **Step 4: Ligar o comando `timeline` ao modo interativo**

Em `meister/cli.py`, no comando `timeline` (Tarefa 4), troque o `raise click.UsageError(...)` por:

```python
    if not once:
        from meister.log_tail import resolve_log_file
        from meister.timeline_app import run_interactive
        from meister.timeline_view import detect_color

        log_file = resolve_log_file(log_dir)
        color = "none" if no_color else detect_color(os.environ, sys.stdout.isatty())
        run_interactive(log_file, run_id=run_id, color=color)
        return
```
e ajuste o teste da Tarefa 4 `test_cli_unknown_run_exits_2_and_without_once_is_a_usage_error` removendo a parte do modo interativo (`interactive = ...`), que deixa de ser um erro de uso.

- [ ] **Step 5: Rodar e ver passar**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_timeline_app.py tests/test_timeline_cli.py tests/test_no_hardcoded_models.py`
Expected: PASS. Se o mypy reclamar da linha `size_fn` com `# type: ignore`, troque por uma função interna `def _size() -> Tuple[int, int]: size = shutil.get_terminal_size((120, 30)); return size.columns, size.lines`.

- [ ] **Step 6: Mutações (obrigatórias)** — em cópia: (a) `"]"` passa a aumentar `run_index` (inverte o sentido): `test_run_navigation_leaves_live_and_resets_view` falha; (b) remover o limite de `pan_s` (`min(pan, base_span_s - window)`): `test_zoom_pan_and_scroll_are_clamped` falha; (c) o laço usa `\x1b[2J` em vez de `\x1b[H`: `test_loop_draws_frames_without_clearing_the_screen_and_quits_on_q` falha. Restaure e confira com `cmp`.

- [ ] **Step 7: Verificação real (num terminal)** — o worker NÃO roda o modo interativo real (precisa de TTY). Deixe no relatório: "interativo não verificado em TTY". O orquestrador verifica numa tab do Herdr.

- [ ] **Step 8: Checkpoint** — `git diff --stat` (não commitar).

---

### Task 6: Tecla `t` no overlay (`meister dashboard --tui`)

**Files:**
- Modify: `meister/timeline_cli.py` (acrescenta `open_timeline`), `meister/herdr/tui.py`
- Test: `tests/test_tui.py` (um teste novo; não altere os existentes)

**Interfaces:**
- Consumes: `run_interactive` (Tarefa 5), `resolve_log_file` (Tarefa 1), `detect_color` (Tarefa 3).
- Produces: `open_timeline(log_dir: Optional[str] = None) -> None` em `timeline_cli.py`.

- [ ] **Step 1: Escrever o teste que falha**

Acrescente a `tests/test_tui.py`:

```python
def test_tui_key_t_opens_the_timeline_and_returns_to_the_overlay():
    keys = iter(["t", "q"])
    opened = MagicMock()
    with patch("meister.herdr.tui.get_live_metrics_and_state", return_value=({}, {})), \
         patch("meister.herdr.tui.check_key_press", side_effect=lambda timeout=0.0: next(keys, None)), \
         patch("meister.timeline_cli.open_timeline", opened), \
         patch("sys.stdout"):
        run_tui_loop(poll_interval=0.5)
    assert opened.call_count == 1
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tui.py::test_tui_key_t_opens_the_timeline_and_returns_to_the_overlay`
Expected: FAIL (a tecla `t` não faz nada; `opened.call_count == 0`).

- [ ] **Step 3: Implementar**

Em `meister/timeline_cli.py` acrescente:

```python
def open_timeline(log_dir: Optional[str] = None) -> None:
    """Abre a tela interativa no terminal atual (usada pelo comando e pela tecla `t` do overlay)."""
    import sys

    from meister.log_tail import resolve_log_file
    from meister.timeline_app import run_interactive
    from meister.timeline_view import detect_color

    color = detect_color(os.environ, sys.stdout.isatty())
    run_interactive(resolve_log_file(log_dir), color=color)
```

Em `meister/herdr/tui.py`, no laço de teclas de `run_tui_loop`, depois do ramo `elif key_lower == "o":` acrescente:

```python
                    elif key_lower == "t":
                        from meister.timeline_cli import open_timeline
                        open_timeline()
                        break  # volta a redesenhar o overlay imediatamente
```

e altere a linha dos atalhos em `render_tui_dashboard` para:

```python
    shortcuts = "[Q] to close overlay (or Esc) | [O] to open web browser dashboard | [T] timeline"
```

(Não escreva nenhum nome de modelo nesse arquivo: `tests/test_no_hardcoded_models.py` o varre. Se a linha de atalhos ficar mais larga que o quadro, confira `inner_width` e encurte o texto em vez de alargar o quadro.)

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tui.py tests/test_no_hardcoded_models.py`
Expected: PASS (inclusive os testes antigos de `test_tui.py`, que checam `[Q]` e `[O]`).

- [ ] **Step 5: Checkpoint** — `git diff --stat` (não commitar).

---

### Task 7: Manual e verificação final do PR 2

**Files:**
- Modify: `docs/MANUAL_DE_EXECUCAO.md` (seção "5. Onde olhar": um item novo, depois de "Relatório de runs")

- [ ] **Step 1: Documentar**

Acrescente, logo depois do item `**Relatório de runs:** ...`:

```markdown
- **Linha do tempo (Gantt):** `meister timeline` abre, no terminal (ex.: numa tab do Herdr), um Gantt colorido das tarefas do run mais novo: uma barra por tarefa no eixo do tempo, cor por fase (worker, gate, integração, espera), a ponta pulsando nas tarefas em andamento e o paralelismo visível (tarefas sobrepostas, pico e média no topo). Teclas: `[`/`]` trocam de run (replay), `l` volta ao ao vivo, `p` pausa, `+`/`-` zoom, `←`/`→` tempo, `↑`/`↓` linhas, `?` ajuda, `q` sai. `--once` imprime um quadro e sai (serve para pipe); `--no-color` ou `NO_COLOR=1` desligam as cores; precisa de 80 colunas. Dentro de `meister dashboard --tui`, a tecla `t` abre a mesma tela. Só lê o log; a fase corrente de uma tarefa em andamento é inferida (o log registra a fase ao terminar) e não há percentual por tarefa.
```

- [ ] **Step 2: Suíte e qualidade do PR 2**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider` (normal e com `env -i HOME="$HOME" PATH="$PATH" GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`), `.venv/bin/ruff check .`, `.venv/bin/python -m mypy meister`; `git status --short`, `git diff -w --stat`, `git for-each-ref refs/heads/meister` e `git worktree list` iguais antes e depois.
Expected: tudo verde; o teste de higiene `tests/test_hygiene.py` continua passando.

- [ ] **Step 3: Relatório final (curto)** — arquivos mudados; decisões; saída dos comandos; mutações com a saída; **e o que NÃO foi verificado** (modo interativo em TTY real, aparência em screenshot, run ao vivo com workers reais).

- [ ] **Step 4: Checkpoint** — `git diff --stat` (não commitar). Fim do **PR 2**.

---

## Verificação do orquestrador (depois de cada PR, fora do worker)

- PR 1: `--once` no log do UFG_TODO (somente leitura, `stat` antes/depois), durações e custo iguais ao `meister report`; mutações refeitas em cópia; suíte normal, `env -i` e "como o CI".
- PR 2: abrir `meister timeline` numa tab do Herdr contra um log real e conferir as teclas; ver um run ao vivo com 3 workers paralelos (gasta alguns créditos do Copilot: o usuário é avisado antes); `meister dashboard --tui` e a tecla `t`.
