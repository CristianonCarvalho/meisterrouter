"""
meister.dashboard.server — Servidor web local de telemetria do MeisterRouter.

Exibe em tempo real:
- Decisões de roteamento do Jev (classify e control)
- Subagentes alocados (GPT-6 Luna, Gemini 3.8 Flash, Haiku, Sonnet)
- Custos acumulados em USD e economia gerada em relação a arquitetura single-model
- Duração das tarefas e taxa de aprovação
"""

import os
from collections import defaultdict
from flask import Flask, jsonify, render_template, request
from meister.logger import read_events, get_log_file

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates")
)


def compute_metrics(events: list) -> dict:
    """Calcula agregados de telemetria para o dashboard."""
    total_cost = 0.0
    tasks_count = 0
    by_model_cost: defaultdict[str, float] = defaultdict(float)
    by_model_count: defaultdict[str, int] = defaultdict(int)
    by_classification: defaultdict[str, int] = defaultdict(int)
    by_action: defaultdict[str, int] = defaultdict(int)
    total_duration = 0.0
    completed_tasks = 0

    # Custo de referência se todas as tarefas fossem executadas no Sonnet ($1.00 por tarefa)
    baseline_cost = 0.0

    for ev in events:
        etype = ev.get("event_type")
        cost = ev.get("cost_usd", 0.0)
        total_cost += cost

        if etype == "classify":
            cls = ev.get("classification", "UNKNOWN")
            by_classification[cls] += 1
            tasks_count += 1
            baseline_cost += 0.85  # Estimativa conservadora de tarefa tradicional

        elif etype == "control":
            act = ev.get("action", "UNKNOWN")
            by_action[act] += 1

        elif etype == "task_end":
            model = ev.get("subagent", "unknown")
            by_model_cost[model] += cost
            by_model_count[model] += 1
            dur = ev.get("duration_seconds", 0.0)
            total_duration += dur
            completed_tasks += 1

    savings = max(0.0, baseline_cost - total_cost)
    savings_pct = (savings / baseline_cost * 100) if baseline_cost > 0 else 0.0

    avg_duration = round(total_duration / completed_tasks, 2) if completed_tasks > 0 else 0.0

    return {
        "total_cost": round(total_cost, 4),
        "tasks_count": tasks_count,
        "completed_tasks": completed_tasks,
        "avg_duration_sec": avg_duration,
        "savings_usd": round(savings, 2),
        "savings_pct": round(savings_pct, 1),
        "by_model_cost": {k: round(v, 4) for k, v in by_model_cost.items()},
        "by_model_count": dict(by_model_count),
        "by_classification": dict(by_classification),
        "by_action": dict(by_action),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/events")
def api_events():
    limit = request.args.get("limit", 200, type=int)
    events = read_events(limit=limit)
    return jsonify({"events": list(reversed(events))})


@app.route("/api/summary")
def api_summary():
    events = read_events(limit=1000)
    summary = compute_metrics(events)
    return jsonify(summary)


def start_server(host: str = "127.0.0.1", port: int = 5050):
    """Inicia o servidor Flask do dashboard."""
    print(f"🚀 [MeisterRouter] Dashboard iniciado em http://{host}:{port}")
    print(f"📁 Lendo eventos de: {get_log_file()}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    start_server()
