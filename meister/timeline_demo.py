"""Cenário sintético da demonstração da linha do tempo: eventos sem modelo, sem custo real.

Tempos são segundos simulados desde o início do run. Os eventos seguem o formato de
`orchestration_log.jsonl`, sem `ts`; quem consumir o cenário decide o relógio.
`main()` é o único ponto com I/O: grava um log temporário e sobe a timeline web local.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from meister.i18n import t

RUN_ID = "demo_timeline"

_PLAN: list[dict[str, Any]] = [
    {"id": "task_01", "depends_on": []},
    {"id": "task_02", "depends_on": []},
    {"id": "task_03", "depends_on": ["task_01", "task_02"]},
    {"id": "task_04", "depends_on": ["task_03"]},
    {"id": "task_05", "depends_on": ["task_03"]},
    {"id": "task_06", "depends_on": ["task_03"]},
    {"id": "task_07", "depends_on": ["task_02"]},
    {"id": "task_08", "depends_on": []},
    {"id": "task_09", "depends_on": []},
    {"id": "task_10", "depends_on": ["task_09"]},
    {"id": "task_11", "depends_on": ["task_01"]},
    {"id": "task_12", "depends_on": ["task_11"]},
]

_TITLES: dict[str, str] = {
    "task_01": "Modelo de dados",
    "task_02": "Cliente HTTP",
    "task_03": "Servico central",
    "task_04": "Rota de listagem",
    "task_05": "Rota de detalhes",
    "task_06": "Validacao de schema",
    "task_07": "Migracao legada",
    "task_08": "Cache de sessao",
    "task_09": "Painel legado",
    "task_10": "Ajustes de CSS",
    "task_11": "Documentacao",
    "task_12": "Notas de versao",
}


@dataclass(frozen=True)
class ScenarioEvent:
    offset_s: float
    event: dict[str, Any]


def _event(event_name: str, task_id: str, offset_s: float, /, **fields: Any) -> ScenarioEvent:
    return ScenarioEvent(
        offset_s,
        {"event_type": event_name, "run_id": RUN_ID, "task_id": task_id, **fields},
    )


def _spawn(task_id: str, at: float, tier: str, attempt: int = 1) -> ScenarioEvent:
    return _event("worker_spawn", task_id, at, tier=tier, attempt=attempt, pane_id=f"demo-{task_id}-{attempt}")


def _phase(task_id: str, end: float, name: str, duration_s: float, tier: str, attempt: int = 1) -> ScenarioEvent:
    return _event(
        "worker_phase",
        task_id,
        end,
        phase=name,
        duration_ms=duration_s * 1000.0,
        tier=tier,
        attempt=attempt,
    )


def _attempt(
    task_id: str,
    spawn: float,
    tier: str,
    worker_s: float,
    gate_s: float,
    integrate_s: float,
    attempt: int = 1,
) -> list[ScenarioEvent]:
    """Tentativa que passa pelo gate e integra: spawn, fases e a fase final `worker`/`gate`/`integrate`."""
    worker_end = spawn + worker_s
    gate_end = worker_end + gate_s
    integrate_end = gate_end + integrate_s
    return [
        _spawn(task_id, spawn, tier, attempt),
        _phase(task_id, worker_end, "worker", worker_s, tier, attempt),
        _phase(task_id, gate_end, "gate", gate_s, tier, attempt),
        _phase(task_id, integrate_end, "integrate", integrate_s, tier, attempt),
    ]


def _completed(task_id: str, at: float, tier: str, cost: float, attempt: int = 1) -> ScenarioEvent:
    return _event(
        "subtask_completed",
        task_id,
        at,
        tier=tier,
        attempt=attempt,
        cost=cost,
        cost_source="reported",
    )


def _done(
    task_id: str,
    spawn: float,
    tier: str,
    worker_s: float,
    gate_s: float,
    integrate_s: float,
    cost: float,
    attempt: int = 1,
) -> list[ScenarioEvent]:
    """Tentativa bem-sucedida completa, com `subtask_completed` no fim da integração."""
    events = _attempt(task_id, spawn, tier, worker_s, gate_s, integrate_s, attempt)
    done_at = spawn + worker_s + gate_s + integrate_s
    events.append(_completed(task_id, done_at, tier, cost, attempt))
    return events


def build_scenario() -> list[ScenarioEvent]:
    """Log sintético de ~38 min com os casos obrigatórios da demonstração, ordenado por `offset_s`."""
    events: list[ScenarioEvent] = [
        _event("orchestration_start", "orchestrator", 0.0, task=json.dumps(_PLAN)),
        _event(
            "plan_parsed",
            "orchestrator",
            0.1,
            total=len(_PLAN),
            batches=4,
            task_ids=[item["id"] for item in _PLAN],
            task_titles=_TITLES,
        ),
    ]

    # Paralelo sem dependência: task_01 e task_02 rodam juntas na tier_1.
    events += _done("task_01", 2.0, "tier_1", 240.0, 30.0, 10.0, 0.12)
    events += _done("task_02", 3.0, "tier_1", 300.0, 40.0, 15.0, 0.18)

    # Fan-in: task_03 só começa depois das duas.
    events += _done("task_03", 360.0, "tier_1b", 420.0, 60.0, 20.0, 0.31)

    # Fan-out: task_04, task_05 e task_06 dependem de task_03.
    # task_04: pane perdido na primeira tentativa, retentativa conclui.
    events.append(_spawn("task_04", 862.0, "tier_1"))
    events.append(_event("worker_retry", "task_04", 900.0, tier="tier_1", attempt=1, reason="pane_lost", retry=1))
    events += _done("task_04", 915.0, "tier_1", 300.0, 45.0, 15.0, 0.22, attempt=2)

    # task_05: gate reprova, reparo com testes falhos, depois conclui.
    events += [
        _spawn("task_05", 862.0, "tier_1b"),
        _phase("task_05", 1262.0, "worker", 400.0, "tier_1b"),
        _phase("task_05", 1312.0, "gate", 50.0, "tier_1b"),
    ]
    events.append(
        _event(
            "gate_repair",
            "task_05",
            1312.0,
            tier="tier_1b",
            attempt=1,
            repair_attempt=1,
            reason="gate",
            failed_tests=["tests/test_detail.py::test_404", "tests/test_detail.py::test_etag"],
            failure_summary="2 testes falharam",
        )
    )
    events.append(_event("worker_retry", "task_05", 1313.0, tier="tier_1b", attempt=1, reason="gate_repair", retry=1))
    events += _done("task_05", 1315.0, "tier_1b", 150.0, 30.0, 15.0, 0.27, attempt=2)

    # task_06: escalonamento tier_1 -> tier_2 -> tier_3, com timeouts de inatividade no caminho.
    events.append(_spawn("task_06", 863.0, "tier_1"))
    events.append(_event("worker_timeout", "task_06", 1000.0, tier="tier_1", attempt=1, kind="idle", seconds=120))
    events.append(_spawn("task_06", 1002.0, "tier_2", attempt=2))
    events.append(_event("worker_timeout", "task_06", 1130.0, tier="tier_2", attempt=2, kind="idle", seconds=120))
    events += _done("task_06", 1132.0, "tier_3", 1000.0, 90.0, 50.0, 1.10, attempt=3)

    # task_07: falha no gate com testes falhos (sem conclusão).
    events += [
        _spawn("task_07", 362.0, "tier_2"),
        _phase("task_07", 862.0, "worker", 500.0, "tier_2"),
        _phase("task_07", 952.0, "gate", 90.0, "tier_2"),
    ]
    events.append(
        _event(
            "subtask_rejected",
            "task_07",
            952.0,
            tier="tier_2",
            attempt=1,
            reason="gate",
            failed_tests=["tests/test_legacy.py::test_import", "tests/test_legacy.py::test_export"],
        )
    )

    # task_08: tentativa interrompida (sem evento terminal), retomada no mesmo run e conclusão.
    events.append(_spawn("task_08", 5.0, "tier_1b"))
    events.append(_event("resume_same_run", "orchestrator", 700.0, state="interrupted"))
    events += _done("task_08", 702.0, "tier_1b", 300.0, 40.0, 10.0, 0.19, attempt=2)

    # task_09: via fora do catálogo padrão (legacy_lane), custo desconhecido.
    events += _done("task_09", 10.0, "legacy_lane", 200.0, 20.0, 5.0, 0.0)

    # task_10: ainda em execução no fim do cenário (depende de task_09, sem terminal).
    events.append(_spawn("task_10", 237.0, "tier_1"))

    # Tarefas finais que dependem de task_01 e encadeiam a documentação.
    events += _done("task_11", 284.0, "tier_1", 120.0, 15.0, 5.0, 0.06)
    events += _done("task_12", 426.0, "tier_1b", 180.0, 25.0, 10.0, 0.09)

    return sorted(events, key=lambda item: item.offset_s)


_STATIC_GRACE = timedelta(seconds=5)
_MODES = ("static", "live")


def materialize(
    scenario: list[ScenarioEvent],
    mode: str,
    now: datetime,
    speed: float = 1.0,
) -> list[dict[str, Any]]:
    """Dá `ts` ISO-8601 UTC a cada evento do cenário, sem alterar o cenário.

    `static`: histórico que termina alguns segundos antes de `now`; durações não mudam.
    `live`: começa em `now`; offsets e `duration_ms` são divididos por `speed`.
    """
    if mode not in _MODES:
        raise ValueError(f"modo desconhecido: {mode!r}")
    if speed <= 0:
        raise ValueError("speed deve ser positivo")
    moment = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    if mode == "static":
        last = max((item.offset_s for item in scenario), default=0.0)
        origin = moment - timedelta(seconds=last) - _STATIC_GRACE
        scale = 1.0
    else:
        origin = moment
        scale = speed
    out: list[dict[str, Any]] = []
    for item in scenario:
        event = dict(item.event)
        event["ts"] = (origin + timedelta(seconds=item.offset_s / scale)).isoformat()
        if mode == "live" and "duration_ms" in event:
            event["duration_ms"] = float(event["duration_ms"]) / speed
        out.append(event)
    return out


_CHECKS = (
    "timeline_demo.check_axis",
    "timeline_demo.check_arrows",
    "timeline_demo.check_tooltip",
    "timeline_demo.check_lane_header",
    "timeline_demo.check_sound",
    "timeline_demo.check_ghost",
)


def _positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(t("timeline_demo.speed_invalid", value=text))
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m meister.timeline_demo",
        description=t("timeline_demo.description"),
    )
    parser.add_argument("--mode", choices=_MODES, default="live", help=t("timeline_demo.mode_help"))
    parser.add_argument("--speed", type=_positive_float, default=30.0, help=t("timeline_demo.speed_help"))
    parser.add_argument("--port", type=int, default=5052, help=t("timeline_demo.port_help"))
    parser.add_argument("--host", default="127.0.0.1", help=t("timeline_demo.host_help"))
    return parser


def _append_live(
    path: str,
    scenario: list[ScenarioEvent],
    start: datetime,
    speed: float,
    stop: threading.Event,
) -> None:
    """Acrescenta cada evento ao log no instante proporcional ao seu offset, dividido por `speed`."""
    base = time.monotonic()
    events = materialize(scenario, "live", start, speed)
    for item, event in zip(scenario, events):
        if stop.wait(max(base + item.offset_s / speed - time.monotonic(), 0.0)):
            return
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Sobe a timeline web sobre o cenário sintético; Ctrl+C encerra e remove o log temporário."""
    from werkzeug.serving import make_server

    from meister.dashboard.server import app

    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exit_request:
        return int(exit_request.code or 0)

    log_dir = tempfile.mkdtemp(prefix="meister_timeline_demo_")
    log_path = os.path.join(log_dir, "orchestration_log.jsonl")
    previous_log_dir = os.environ.get("MEISTER_LOG_DIR")
    os.environ["MEISTER_LOG_DIR"] = log_dir
    stop = threading.Event()
    writer: threading.Thread | None = None
    try:
        start = datetime.now(timezone.utc)
        scenario = build_scenario()
        server = make_server(args.host, args.port, app)
        if args.mode == "static":
            with open(log_path, "w", encoding="utf-8") as handle:
                for event in materialize(scenario, "static", start):
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        else:
            open(log_path, "w", encoding="utf-8").close()
            writer = threading.Thread(
                target=_append_live,
                args=(log_path, scenario, start, args.speed, stop),
                daemon=True,
            )
        print(t("timeline_demo.header", mode=args.mode, speed=args.speed))
        print(t("timeline_demo.url", url=f"http://{args.host}:{args.port}/timeline"))
        print(t("timeline_demo.checklist_heading"))
        for key in _CHECKS:
            print(f"  - {t(key)}")
        print(t("timeline_demo.stop_hint"))
        if writer is not None:
            writer.start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        print(t("timeline_demo.stopped"))
        return 0
    finally:
        stop.set()
        if writer is not None:
            writer.join(timeout=1)
        if previous_log_dir is None:
            os.environ.pop("MEISTER_LOG_DIR", None)
        else:
            os.environ["MEISTER_LOG_DIR"] = previous_log_dir
        shutil.rmtree(log_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
