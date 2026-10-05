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
from meister.timeline_cli import pick_run, price_tables_from_config, project_name, via_index_from_config
from meister.timeline_view import CHROME_ROWS, MIN_WIDTH, View, render_frame, render_waiting

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
    view = View(row_offset=state.row_offset, paused=state.paused, live=state.live)
    if state.zoom <= 1 or timeline.started_at is None:
        return view
    base_end = timeline.ended_at or now
    span = max((base_end - timeline.started_at).total_seconds(), 1.0)
    start = timeline.started_at + timedelta(seconds=state.pan_s)
    return replace(view, t_start=start, t_end=start + timedelta(seconds=span / state.zoom))


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

    def _size() -> Tuple[int, int]:
        size = shutil.get_terminal_size((120, 30))
        return size.columns, size.lines

    size_fn = size_fn or _size
    fd = sys.stdin.fileno() if use_terminal and sys.stdin.isatty() else -1
    saved = None
    tail = LogTail(log_file)
    events: List[Dict[str, Any]] = []
    tier_prices, credit_prices = price_tables_from_config()
    via_index = via_index_from_config()
    project = project_name(log_file)
    state = AppState()
    pinned = run_id
    if pinned:
        state = replace(state, live=False)
    tick = frames = 0
    paused_drawn = False
    last_poll = -1e9
    has_polled = False
    try:
        if fd >= 0:
            import termios
            import tty

            saved = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            out.write("\x1b[?1049h\x1b[?25l")
        while not state.quit and (max_frames is None or frames < max_frames):
            now = now_fn()
            monotonic = time.monotonic()
            if not has_polled or (state.live and monotonic - last_poll >= poll_interval):
                new = tail.poll()
                if tail.reset:
                    events = []
                events.extend(new)
                last_poll = monotonic
                has_polled = True
            runs = list_runs(events)
            width, height = size_fn()
            if width > MIN_WIDTH:
                width -= 1  # nunca escreve na última coluna: o `\x1b[K` apagaria o último caractere
            timeline: Optional[Timeline] = None
            if not runs:
                frame = render_waiting("aguardando o primeiro run", width=width, color=color)
            else:
                ids = [str(run["run_id"]) for run in runs]
                if pinned:
                    selected = pick_run(runs, pinned)
                    state = replace(state, run_index=ids.index(selected))
                    pinned = None
                if state.live:
                    state = replace(state, run_index=0)
                state = replace(state, run_index=min(state.run_index, len(ids) - 1))
                timeline = build_timeline(
                    events, ids[state.run_index], now, tier_prices=tier_prices, credit_prices=credit_prices
                )
                frame = (
                    "\n".join(HELP_LINES)
                    if state.help
                    else render_frame(
                        timeline,
                        width=width,
                        height=height,
                        now=now,
                        tick=tick,
                        view=view_for(state, timeline, now),
                        color=color,
                        project=project,
                        via_index=via_index,
                        hue=(210 + 67 * state.run_index) % 360,
                    )
                )
            if not state.paused:
                paused_drawn = False
            if not state.paused or state.help or not paused_drawn:
                paused_drawn = state.paused  # pausado: desenha um quadro (selo PAUSADO) e congela
                out.write("\x1b[H" + "\x1b[K\n".join(frame.split("\n")) + "\x1b[K\x1b[J")
                out.flush()
            key = key_reader(fd, frame_interval)
            if key:
                span = 1.0
                n_rows = 0
                if timeline is not None:
                    n_rows = len(timeline.rows)
                    if timeline.started_at:
                        span = max(
                            ((timeline.ended_at or now) - timeline.started_at).total_seconds(), 1.0
                        )
                state = handle_key(
                    state,
                    key,
                    n_runs=len(runs),
                    n_rows=n_rows,
                    visible_rows=max(1, height - CHROME_ROWS),
                    base_span_s=span,
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
