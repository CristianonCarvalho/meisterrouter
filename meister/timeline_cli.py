"""Ajudantes do comando `meister timeline`: resolução de run, config e quadro único."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from meister.dashboard.metrics import iter_events, list_runs
from meister.i18n import t
from meister.timeline import DEFAULT_STALE_AFTER, build_timeline
from meister.timeline_view import all_body_line_count, render_all, render_frame, render_waiting


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


def stale_after_from_config() -> timedelta:
    """Silence threshold from the largest configured worker runtime, plus five minutes."""
    try:
        values = [
            float(tier.max_runtime_seconds)
            for tier in _tiers()
            if tier.max_runtime_seconds is not None
        ]
    except Exception:
        return DEFAULT_STALE_AFTER
    return timedelta(seconds=max(values) + 300) if values else DEFAULT_STALE_AFTER


def pick_run(runs: List[Dict[str, Any]], run_id: Optional[str]) -> str:
    ids = [str(run["run_id"]) for run in runs]
    if run_id is None:
        if not ids:
            raise ValueError(t("commands.timeline.no_runs"))
        return ids[0]
    if len(run_id) < 6:
        raise ValueError(t("commands.timeline.id_min_length", id=run_id))
    matches = [identifier for identifier in ids if identifier.startswith(run_id)]
    if len(matches) == 1:
        return matches[0]
    detail = t("commands.timeline.id_ambiguous") if matches else t("commands.timeline.id_missing")
    listing = ", ".join(ids) if ids else t("commands.timeline.no_runs")
    raise ValueError(
        t(
            "commands.timeline.runs_available",
            status=detail,
            id=run_id,
            runs=listing,
        )
    )


def once_frame(
    log_file: str,
    run_id: Optional[str],
    *,
    width: int,
    now: datetime,
    color: str,
    all_runs: bool = False,
) -> str:
    """Um quadro completo (sem rolagem) do run escolhido, para `--once`."""
    if not os.path.isfile(log_file):
        if run_id is not None:
            pick_run([], run_id)
        return render_waiting(
            t("commands.timeline.waiting_missing_log"), width=width, color=color
        )
    events = list(iter_events(log_file))
    runs = list_runs(events)
    if not runs and (run_id is None or all_runs):
        return render_waiting(t("commands.timeline.waiting_first_run"), width=width, color=color)
    tier_prices, credit_prices = price_tables_from_config()
    stale_after = stale_after_from_config()
    if all_runs:
        timelines = [
            build_timeline(
                events,
                str(run["run_id"]),
                now,
                tier_prices=tier_prices,
                credit_prices=credit_prices,
                stale_after=stale_after,
            )
            for run in runs
        ]
        return render_all(
            timelines,
            width=width,
            height=all_body_line_count(timelines) + 4,
            now=now,
            color=color,
            project=project_name(log_file),
            via_index=via_index_from_config(),
            interactive=False,
        )
    chosen = pick_run(runs, run_id)
    timeline = build_timeline(
        events,
        chosen,
        now,
        tier_prices=tier_prices,
        credit_prices=credit_prices,
        stale_after=stale_after,
    )
    return render_frame(
        timeline,
        width=width,
        height=len(timeline.rows) + 8,
        now=now,
        color=color,
        project=project_name(log_file),
        via_index=via_index_from_config(),
        interactive=False,
    )


def open_timeline(log_dir: Optional[str] = None) -> None:
    """Abre a tela interativa no terminal atual (usada pelo comando e pelo overlay)."""
    import sys

    from meister.log_tail import resolve_log_file
    from meister.timeline_app import run_interactive
    from meister.timeline_view import detect_color

    color = detect_color(os.environ, sys.stdout.isatty())
    run_interactive(resolve_log_file(log_dir), color=color)
