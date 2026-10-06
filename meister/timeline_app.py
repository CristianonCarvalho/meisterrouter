"""Modo interativo da linha do tempo: estado das teclas (puro) e laço de atualização."""

from __future__ import annotations

import os
import select
import shutil
import sys
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, TextIO, Tuple

from meister.dashboard.metrics import list_runs
from meister.log_tail import LogTail
from meister.timeline import Timeline, build_timeline
from meister.timeline_cli import (
    pick_run,
    price_tables_from_config,
    project_name,
    stale_after_from_config,
    via_index_from_config,
)
from meister.timeline_view import (
    CHROME_ROWS,
    MIN_WIDTH,
    View,
    all_body_line_count,
    render_all,
    render_frame,
    render_waiting,
)

HELP_LINES = (
    " [ / ]   run mais antigo / mais novo        l   voltar ao ao vivo (run mais novo)",
    " a       alternar entre um run e todos os runs",
    " p       pausar a tela                      + / -   zoom no eixo de tempo",
    " ← / →   andar no tempo (com zoom)          ↑ / ↓   rolar as linhas",
    " ?       esta ajuda                         q       sair",
)
_ARROWS = {b"[A": "up", b"[B": "down", b"[C": "right", b"[D": "left"}


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
    all_runs: bool = False


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
        return replace(
            state, all_runs=False, live=True, run_index=0, zoom=1, pan_s=0.0, row_offset=0
        )
    if key == "a":
        return replace(state, all_runs=not state.all_runs, row_offset=0)
    if key in ("[", "]"):
        index = state.run_index + 1 if key == "[" else state.run_index - 1
        index = max(0, min(index, max(0, n_runs - 1)))
        return replace(
            state,
            all_runs=False,
            run_index=index,
            live=False,
            zoom=1,
            pan_s=0.0,
            row_offset=0,
        )
    if state.all_runs and key in ("+", "-", "left", "right"):
        return state
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
    base_end = timeline.stalled_since or timeline.ended_at or now
    span = max((base_end - timeline.started_at).total_seconds(), 1.0)
    start = timeline.started_at + timedelta(seconds=state.pan_s)
    return replace(view, t_start=start, t_end=start + timedelta(seconds=span / state.zoom))


def parse_keys(data: bytes) -> List[str]:
    """Separa uma rajada de bytes do terminal em teclas.

    Setas viram "up"/"down"/"left"/"right"; um Esc sozinho é "\x1b"; sequências desconhecidas
    (PageUp, F-keys, alt+tecla) são descartadas em vez de virarem Esc (que fecharia a tela).
    """
    keys: List[str] = []
    index = 0
    while index < len(data):
        byte = data[index]
        if byte != 0x1B:
            width = 1 if byte < 0x80 else 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
            try:
                keys.append(data[index:index + width].decode("utf-8"))
            except UnicodeDecodeError:
                pass
            index += width
        elif index + 1 >= len(data):
            keys.append("\x1b")  # Esc sozinho
            index += 1
        elif data[index + 1:index + 3] in _ARROWS:
            keys.append(_ARROWS[data[index + 1:index + 3]])
            index += 3
        elif data[index + 1:index + 2] in (b"[", b"O"):
            end = index + 2  # sequência CSI/SS3 desconhecida: pula até o byte final (0x40-0x7E)
            while end < len(data) and not 0x40 <= data[end] <= 0x7E:
                end += 1
            index = end + 1
        else:
            index += 2  # alt+tecla
    return keys


_PENDING: Deque[str] = deque()


def read_key(fd: int, timeout: float) -> Optional[str]:
    """Devolve uma tecla por chamada; rajadas (tecla segurada, colagem) ficam numa fila."""
    if _PENDING:
        return _PENDING.popleft()
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return None
    _PENDING.extend(parse_keys(os.read(fd, 256)))
    return _PENDING.popleft() if _PENDING else None


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
    all_runs: bool = False,
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
    stale_after = stale_after_from_config()
    via_index = via_index_from_config()
    project = project_name(log_file)
    state = AppState(all_runs=all_runs)
    pinned = run_id
    if pinned:
        state = replace(state, live=False)
    tick = frames = 0
    paused_drawn = False
    last_poll = -1e9
    has_polled = False
    timeline_cache: Dict[str, Timeline] = {}
    dirty_runs: set[str] = set()
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
            if not has_polled or (
                (state.live or state.all_runs) and monotonic - last_poll >= poll_interval
            ):
                new = tail.poll()
                if tail.reset:
                    events = []
                    timeline_cache.clear()
                    dirty_runs.clear()
                events.extend(new)
                dirty_runs.update(
                    str(event.get("run_id") or "") for event in new if event.get("run_id")
                )
                last_poll = monotonic
                has_polled = True
            runs = list_runs(events)
            width, height = size_fn()
            if width > MIN_WIDTH:
                width -= 1  # nunca escreve na última coluna: o `\x1b[K` apagaria o último caractere
            timeline: Optional[Timeline] = None
            timelines: List[Timeline] = []

            def cached_timeline(run_identifier: str) -> Timeline:
                cached = timeline_cache.get(run_identifier)
                if (
                    cached is None
                    or cached.status == "running"
                    or run_identifier in dirty_runs
                ):
                    cached = build_timeline(
                        events,
                        run_identifier,
                        now,
                        tier_prices=tier_prices,
                        credit_prices=credit_prices,
                        stale_after=stale_after,
                    )
                    timeline_cache[run_identifier] = cached
                    dirty_runs.discard(run_identifier)
                return cached

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
                if state.all_runs:
                    timelines = [cached_timeline(identifier) for identifier in ids]
                    timeline = timelines[state.run_index]
                else:
                    timeline = cached_timeline(ids[state.run_index])
                frame = (
                    "\n".join(HELP_LINES)
                    if state.help
                    else (
                        render_all(
                            timelines,
                            width=width,
                            height=height,
                            now=now,
                            tick=tick,
                            view=View(row_offset=state.row_offset, paused=state.paused),
                            color=color,
                            project=project,
                            via_index=via_index,
                        )
                        if state.all_runs
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
                n_rows = all_body_line_count(timelines) if state.all_runs else 0
                if timeline is not None:
                    if not state.all_runs:
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
                    visible_rows=max(1, height - (4 if state.all_runs else CHROME_ROWS)),
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
