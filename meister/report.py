"""Read-only run reports built from orchestration JSONL events."""

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Tuple

from meister.dashboard.metrics import clip_title, compute_summary, event_type, list_runs
from meister.i18n import t


def _timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _event_time(event: Dict[str, Any]) -> Optional[datetime]:
    return _timestamp(event.get("ts") or event.get("timestamp"))


def _worker_intervals(events: List[Dict[str, Any]]) -> List[Tuple[float, float]]:
    intervals = []
    for event in events:
        if event_type(event) != "worker_phase" or event.get("phase") != "worker":
            continue
        end = _event_time(event)
        duration = _number(event.get("duration_ms"))
        if end is None or duration is None or duration < 0:
            continue
        end_seconds = end.timestamp()
        intervals.append((end_seconds - duration / 1000.0, end_seconds))
    return intervals


def _interval_metrics(intervals: List[Tuple[float, float]]) -> Tuple[float, int]:
    if not intervals:
        return 0.0, 0
    ordered = sorted((point, delta) for start, end in intervals for point, delta in ((start, 1), (end, -1)))
    active = peak = 0
    for _, delta in ordered:
        active += delta
        peak = max(peak, active)
    ranges = sorted(intervals)
    union = 0.0
    start, end = ranges[0]
    for next_start, next_end in ranges[1:]:
        if next_start > end:
            union += end - start
            start, end = next_start, next_end
        else:
            end = max(end, next_end)
    return union + end - start, peak


def _task_count(summary: Dict[str, Any], key: str) -> int:
    return int(summary["totals"].get(key, 0))


def compute_run_report(
    events: Iterable[Dict[str, Any]],
    run_id: str,
    tier_prices: Optional[Dict[str, float]] = None,
    credit_prices: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Compute comparable run metrics from already-loaded events, without side effects."""
    all_events = list(events)
    selected = [event for event in all_events if str(event.get("run_id") or "") == run_id]
    run = next((item for item in list_runs(selected) if item["run_id"] == run_id), {})
    summary = compute_summary(selected, run_id)
    phases = [event for event in selected if event_type(event) == "worker_phase"]
    phase_seconds: Dict[str, Optional[float]] = {
        phase: None for phase in ("worker", "gate", "integrate", "lock_wait", "merge", "setup")
    }
    worker_seconds_sum: Optional[float] = None
    worker_seconds_union: Optional[float] = None
    peak_parallel_workers: Optional[int] = None
    overhead_ratio: Optional[float] = None
    notes: List[str] = []
    if phases:
        totals: Dict[str, float] = {
            "worker": 0.0, "gate": 0.0, "integrate": 0.0, "lock_wait": 0.0,
        }
        for event in phases:
            phase = event.get("phase")
            duration = _number(event.get("duration_ms"))
            if phase in {"worker", "gate", "integrate", "lock_wait"} and duration is not None and duration >= 0:
                totals[phase] += duration
        for phase in ("worker", "gate", "integrate", "lock_wait"):
            phase_seconds[phase] = round(totals[phase] / 1000.0, 3)
        phase_seconds["merge"] = round(
            max(0.0, totals["integrate"] - totals["gate"]) / 1000.0, 3
        )
        intervals = _worker_intervals(selected)
        worker_seconds_sum = round(totals["worker"] / 1000.0, 3)
        worker_event_count = sum(
            event_type(event) == "worker_phase" and event.get("phase") == "worker"
            for event in selected
        )
        if len(intervals) == worker_event_count:
            interval_union, peak_parallel_workers = _interval_metrics(intervals)
            worker_seconds_union = round(interval_union, 3)
        else:
            notes.append(t("reports.note_worker_timestamp_missing"))
    else:
        notes.append(t("reports.note_legacy_run_phases_missing"))

    setup_durations = [
        _number(event.get("duration_ms"))
        for event in selected
        if event_type(event) == "worktree_setup_ok"
        and str(event.get("run_id") or "") == run_id
    ]
    setup_values = [value for value in setup_durations if value is not None and value >= 0]
    if setup_values:
        phase_seconds["setup"] = round(sum(setup_values) / 1000.0, 3)
    if any(
        event_type(event) == "worktree_setup_ok" and not event.get("run_id")
        for event in all_events
    ):
        notes.append(t("reports.note_setup_without_run"))

    started_at = run.get("started_at")
    ended_at = run.get("ended_at")
    ended = ended_at is not None
    start_time = _timestamp(started_at)
    if ended:
        finish_time = _timestamp(ended_at)
    else:
        event_times = [instant for instant in (_event_time(event) for event in selected) if instant]
        finish_time = max(event_times) if event_times else None
        notes.append(t("reports.note_run_end_missing"))
    wall_seconds = (
        round(max(0.0, (finish_time - start_time).total_seconds()), 3)
        if start_time and finish_time else None
    )
    if not phases or worker_seconds_union is None:
        overhead_ratio = None
    elif wall_seconds is not None and wall_seconds > 0 and worker_seconds_union is not None:
        overhead_ratio = round(
            min(1.0, max(0.0, 1.0 - worker_seconds_union / wall_seconds)), 4
        )

    spawns = [event for event in selected if event_type(event) == "worker_spawn"]
    spawn_tiers: Dict[str, List[Optional[str]]] = defaultdict(list)
    for event in spawns:
        task_id = str(event.get("task_id") or "")
        if task_id:
            tier = event.get("tier")
            spawn_tiers[task_id].append(str(tier) if tier else None)
    escalations = sum(
        1 for tiers in spawn_tiers.values()
        if len(tiers) > 1 and len({tier for tier in tiers if tier is not None}) > 1
    )
    rejections = Counter(
        str(event.get("reason") or t("reports.not_reported"))
        for event in selected if event_type(event) == "subtask_rejected"
    )
    retries = sum(event_type(event) == "worker_retry" for event in selected)
    jev = summary["jev"]
    jev_costs = [
        _number(event.get("cost") if event.get("cost") is not None else event.get("cost_usd"))
        for event in selected if event_type(event) in {"classify", "control"}
    ]
    known_jev_costs = [value for value in jev_costs if value is not None]

    by_tier: Dict[str, Dict[str, Any]] = {}
    prices = tier_prices or {}
    credits_prices = credit_prices or {}
    credited_tiers = set()
    for event in selected:
        if event_type(event) != "subtask_completed":
            continue
        tier = str(event.get("tier") or "unknown")
        row = by_tier.setdefault(
            tier,
            {
                "completed": 0,
                "tokens_in": [],
                "tokens_out": [],
                "tokens_total": [],
                "credits": [],
                "credits_usd": [],
                "cost_reported_usd": [],
                "cost_estimated_usd": [],
                "events_unknown": 0,
                "approx": False,
            },
        )
        row["completed"] += 1
        for key in ("tokens_in", "tokens_out", "tokens_total", "credits"):
            value = _number(event.get(key))
            if value is not None:
                row[key].append(value)
        credits = _number(event.get("credits"))
        credit_price = _number(credits_prices.get(tier))
        credit_cost = (
            credits * credit_price
            if credits is not None and credit_price is not None and credit_price > 0
            else None
        )
        if credit_cost is not None:
            row["credits_usd"].append(credit_cost)
            row["cost_reported_usd"].append(credit_cost)
            credited_tiers.add(tier)
        source = event.get("cost_source")
        cost = _number(event.get("cost"))
        if cost is None:
            cost = _number(event.get("cost_usd"))
        if credit_cost is None:
            if source == "reported":
                if cost is not None:
                    row["cost_reported_usd"].append(cost)
            elif source == "estimated":
                token_total = _number(event.get("tokens_total"))
                if token_total is None:
                    token_in = _number(event.get("tokens_in"))
                    token_out = _number(event.get("tokens_out"))
                    if token_in is not None or token_out is not None:
                        token_total = (token_in or 0.0) + (token_out or 0.0)
                catalog_price = _number(prices.get(tier))
                estimated = (
                    token_total * catalog_price / 1_000_000.0
                    if token_total is not None and catalog_price is not None else cost
                )
                if estimated is not None:
                    row["cost_estimated_usd"].append(estimated)
            else:
                row["events_unknown"] += 1
        row["approx"] = row["approx"] or event.get("approx") is True

    for row in by_tier.values():
        for key in ("tokens_in", "tokens_out", "tokens_total", "credits"):
            values = row[key]
            row[key] = round(sum(values), 3) if values else None
        for key in ("cost_reported_usd", "cost_estimated_usd"):
            values = row[key]
            row[key] = round(sum(values), 6) if values else None
        credits_usd = row["credits_usd"]
        row["credits_usd"] = round(sum(credits_usd), 6) if credits_usd else None
        known_costs = [
            value for value in (row["cost_reported_usd"], row["cost_estimated_usd"])
            if value is not None
        ]
        row["cost_known_usd"] = round(sum(known_costs), 6) if known_costs else None
    for tier in sorted(credited_tiers):
        notes.append(
            t("reports.note_credit_cost", tier=tier, price=f"{credits_prices[tier]:g}")
        )

    return {
        "run_id": run_id,
        "title": clip_title(run.get("title")) or run.get("title") or "",
        "started_at": started_at,
        "ended_at": ended_at,
        "status": run.get("status"),
        "ended": ended,
        "tasks": {
            "total": _task_count(summary, "tasks_total"),
            "completed": _task_count(summary, "completed"),
            "failed": _task_count(summary, "failed"),
            "reused": _task_count(summary, "reused"),
        },
        "wall_seconds": wall_seconds,
        "phase_seconds": phase_seconds,
        "worker_seconds_sum": worker_seconds_sum,
        "worker_seconds_union": worker_seconds_union,
        "peak_parallel_workers": peak_parallel_workers,
        "overhead_ratio": overhead_ratio,
        "attempts": {
            "spawns": len(spawns),
            "retries": retries,
            "escalations": escalations,
            "rejections_by_reason": dict(sorted(rejections.items())),
            "timeouts": sum(event_type(event) == "worker_timeout" for event in selected),
            "quota_errors": sum(event_type(event) == "quota_error" for event in selected),
            "worker_errors": sum(event_type(event) == "worker_error" for event in selected),
        },
        "jev": {
            "classify_calls": jev["classify_calls"],
            "control_calls": jev["control_calls"],
            "cost_usd": round(sum(known_jev_costs), 6) if known_jev_costs else None,
            "avg_latency_ms": jev["avg_latency_ms"],
        },
        "by_tier": dict(sorted(by_tier.items())),
        "notes": notes,
    }


def _numeric_metrics(report: Dict[str, Any], prefix: str = "") -> Dict[str, float]:
    found: Dict[str, float] = {}
    for key, value in report.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            found.update(_numeric_metrics(value, name))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            found[name] = float(value)
    return found


def compute_group_report(name: str, reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize all numeric metrics while excluding unmeasured (null) values."""
    samples = [_numeric_metrics(report) for report in reports]
    all_metrics = {key for sample in samples for key in sample}
    # Contagens por motivo/via só aparecem no run em que ocorreram: ausência é 0, não "não medido".
    for metric in all_metrics:
        if metric.startswith("attempts.rejections_by_reason.") or (
            metric.startswith("by_tier.") and metric.endswith(".completed")
        ):
            for sample in samples:
                sample.setdefault(metric, 0.0)
    metrics: Dict[str, Dict[str, Any]] = {}
    for metric in sorted(all_metrics):
        values = [sample[metric] for sample in samples if metric in sample]
        metrics[metric] = {
            "median": round(float(median(values)), 6),
            "min": round(min(values), 6),
            "max": round(max(values), 6),
            "n_runs": len(reports),
            "n_measured": len(values),
        }
    warnings = [t("reports.single_run_median_warning")] if len(reports) == 1 else []
    return {"name": name, "n_runs": len(reports), "metrics": metrics, "notes": warnings}


def render_report(data: Dict[str, Any], output_format: str) -> str:
    """Render a report as stable JSON, Markdown, or aligned text."""
    if output_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)

    groups = data["groups"]
    reports = data["runs"]
    if output_format == "markdown":
        lines = []
        if groups:
            lines.extend([f"## {t('reports.groups')}", "", f"| {t('reports.group')} | {t('reports.metric')} | {t('reports.median')} | {t('reports.minimum')} | {t('reports.maximum')} | {t('reports.measured_runs')} |",
                          "|---|---|---:|---:|---:|---:|"])
            for group in groups:
                for metric, values in group["metrics"].items():
                    lines.append(
                        f"| {group['name']} | {metric} | {values['median']} | {values['min']} | "
                        f"{values['max']} | {values['n_measured']} / {values['n_runs']} |"
                    )
                for note in group["notes"]:
                    lines.append(f"| {group['name']} | {t('reports.warning')} | {note} |  |  |  |")
            lines.append("")
        if reports:
            headers = [t("reports.metric")] + [str(report["run_id"]) for report in reports]
            lines.extend([f"## {t('reports.runs')}", "", "| " + " | ".join(headers) + " |",
                          "|" + "|".join(["---"] * len(headers)) + "|"])
            for label, values in _run_rows(reports):
                lines.append("| " + " | ".join([label, *values]) + " |")
        return "\n".join(lines)

    lines = []
    if groups:
        lines.extend([f"{t('reports.groups')}:", f"{t('reports.group')} | {t('reports.metric')} | {t('reports.median')} | {t('reports.minimum')} | {t('reports.maximum')} | {t('reports.measured_runs_compact')}"])
        for group in groups:
            for metric, values in group["metrics"].items():
                lines.append(
                    f"{group['name']} | {metric} | {values['median']} | {values['min']} | "
                    f"{values['max']} | {values['n_measured']}/{values['n_runs']}"
                )
            lines.extend(f"{group['name']} | {t('reports.warning')}: {note}" for note in group["notes"])
    if reports:
        rows = _run_rows(reports)
        headers = [t("reports.metric"), *[str(report["run_id"]) for report in reports]]
        matrix = [headers] + [[label, *values] for label, values in rows]
        widths = [max(len(row[index]) for row in matrix) for index in range(len(headers))]
        lines.extend([f"{t('reports.runs')}:", "  ".join(value.ljust(widths[i]) for i, value in enumerate(headers))])
        lines.extend("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)) for row in matrix[1:])
    return "\n".join(lines)


def _money(value: Optional[float], unknown: int = 0) -> str:
    if value is None:
        return "?" if unknown else t("reports.not_measured")
    amount = f"US$ {value:.4f}"
    return f"{amount} + ?" if unknown else amount


def _run_rows(reports: List[Dict[str, Any]]) -> List[Tuple[str, List[str]]]:
    rows: List[Tuple[str, List[str]]] = []

    def add(label: str, values: List[str]) -> None:
        rows.append((label, values))

    add(t("reports.title"), [_clip(report["title"]) or t("reports.not_measured") for report in reports])
    add(t("reports.status"), [str(report["status"] or (t("reports.in_progress") if not report["ended"] else t("reports.not_reported")))
                   for report in reports])
    add(t("reports.tasks_total_completed_failed_reused"), [
        "{total}/{completed}/{failed}/{reused}".format(**report["tasks"]) for report in reports
    ])
    add(t("reports.wall_clock_seconds"), [_display(report["wall_seconds"]) for report in reports])
    add(t("reports.worker_sum_seconds"), [_display(report["worker_seconds_sum"]) for report in reports])
    add(t("reports.worker_union_seconds"), [_display(report["worker_seconds_union"]) for report in reports])
    for phase in ("worker", "gate", "integrate", "lock_wait", "merge", "setup"):
        add(t("reports.phase_seconds", phase=phase), [_display(report["phase_seconds"][phase]) for report in reports])
    add(t("reports.overhead"), [_display_percent(report["overhead_ratio"]) for report in reports])
    add(t("reports.peak_workers"), [_display(report["peak_parallel_workers"]) for report in reports])
    for key, label in (
        ("spawns", t("reports.spawns")), ("retries", t("reports.retries")), ("escalations", t("reports.escalations")),
        ("timeouts", t("reports.timeouts")), ("quota_errors", t("reports.quota_errors")), ("worker_errors", t("reports.worker_errors")),
    ):
        add(label, [str(report["attempts"][key]) for report in reports])
    add(t("reports.rejections_by_reason"), [
        ", ".join(f"{reason}: {count}" for reason, count in report["attempts"]["rejections_by_reason"].items())
        or t("reports.none") for report in reports
    ])
    add("Jev (chamadas classify/control)", [
        f"{report['jev']['classify_calls']}/{report['jev']['control_calls']}" for report in reports
    ])
    add(t("reports.jev_cost"), [_money(report["jev"]["cost_usd"]) for report in reports])
    add(t("reports.jev_average_latency_ms"), [_display(report["jev"]["avg_latency_ms"]) for report in reports])
    tiers = sorted({tier for report in reports for tier in report["by_tier"]})
    for tier in tiers:
        add(t("reports.tier_completed_tasks", tier=tier), [
            str(report["by_tier"].get(tier, {}).get("completed", 0)) for report in reports
        ])
        for metric, label in (
            ("tokens_in", "tokens in"), ("tokens_out", "tokens out"),
            ("tokens_total", "tokens"), ("credits", t("reports.credits")),
        ):
            add(f"{tier} {label}", [
                _display(report["by_tier"].get(tier, {}).get(metric)) for report in reports
            ])
        add(t("reports.tier_credits_usd", tier=tier), [
            _money(report["by_tier"].get(tier, {}).get("credits_usd"))
            for report in reports
        ])
        add(f"{tier} US$", [
            _money(
                report["by_tier"][tier]["cost_known_usd"],
                report["by_tier"][tier]["events_unknown"],
            )
            for report in reports if tier in report["by_tier"]
        ] if all(tier in report["by_tier"] for report in reports) else [
            _tier_cost_cell(report, tier) for report in reports
        ])
        add(t("reports.tier_unknown_cost_events", tier=tier), [
            str(report["by_tier"].get(tier, {}).get("events_unknown", 0)) for report in reports
        ])
        add(t("reports.tier_approximate", tier=tier), [
            "~" if report["by_tier"].get(tier, {}).get("approx") else "" for report in reports
        ])
    return rows


def _tier_cost_cell(report: Dict[str, Any], tier: str) -> str:
    row = report["by_tier"].get(tier)
    if row is None:
        return t("reports.not_measured")
    return _money(row["cost_known_usd"], row["events_unknown"])


def _clip(text: Any, size: int = 48) -> str:
    """Short table title (JSON keeps the full title)."""
    text = str(text or "")
    return text if len(text) <= size else text[: size - 1] + "…"


def _display(value: Any) -> str:
    return t("reports.not_measured") if value is None else str(value)


def _display_percent(value: Optional[float]) -> str:
    return t("reports.not_measured") if value is None else f"{value:.1%}"
