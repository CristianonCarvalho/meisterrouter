"""Desenho ANSI da linha do tempo (Gantt): funções puras, sem I/O e sem relógio interno."""

from __future__ import annotations

import colorsys
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from meister.timeline import TaskRow, Timeline

MIN_WIDTH = 80
LABEL_W = 26
INFO_W = 28
CHROME_ROWS = 7
PROGRESS_W = 24

RGB = Tuple[int, int, int]
Piece = Tuple[str, Optional[RGB], bool]
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
JEV: RGB = (255, 105, 180)
VIA_COLORS: Tuple[RGB, ...] = (
    (70, 200, 220),
    (255, 160, 60),
    (60, 200, 170),
    (180, 140, 255),
    (240, 240, 240),
    (240, 120, 180),
)
GLYPHS = {"worker": "█", "gate": "▓", "integrate": "▒", "lock_wait": "░", "wait": "·"}
PHASE_LABEL = {
    "worker": "worker",
    "gate": "gate",
    "integrate": "integração",
    "lock_wait": "fila",
    "wait": "espera",
}


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def detect_color(env: Mapping[str, str], isatty: bool) -> str:
    if env.get("NO_COLOR") or env.get("TERM") == "dumb" or not isatty:
        return "none"
    return "truecolor" if env.get("COLORTERM", "").lower() in ("truecolor", "24bit") else "256"


@dataclass
class View:
    t_start: Optional[datetime] = None
    t_end: Optional[datetime] = None
    row_offset: int = 0
    paused: bool = False
    live: bool = True


class Painter:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def _code(self, base: int, rgb: RGB) -> str:
        if self.mode == "truecolor":
            return f"\x1b[{base};2;{rgb[0]};{rgb[1]};{rgb[2]}m"
        red, green, blue = (round(channel / 255 * 5) for channel in rgb)
        return f"\x1b[{base};5;{16 + 36 * red + 6 * green + blue}m"

    def paint(
        self, text: str, fg: Optional[RGB] = None, bg: Optional[RGB] = None, bold: bool = False
    ) -> str:
        if self.mode == "none" or not text or (fg is None and bg is None and not bold):
            return text
        prefix = (
            ("\x1b[1m" if bold else "")
            + (self._code(38, fg) if fg else "")
            + (self._code(48, bg) if bg else "")
        )
        return prefix + text + RESET

    def line(self, pieces: Sequence[Piece], width: int, bg: Optional[RGB] = None) -> str:
        clipped: List[Piece] = []
        remaining = width
        for text, fg, bold in pieces:
            if remaining <= 0:
                break
            clipped_text = text[:remaining]
            clipped.append((clipped_text, fg, bold))
            remaining -= len(clipped_text)
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
    low, high, local = (red, yellow, fraction * 2) if fraction < 0.5 else (
        yellow,
        green,
        fraction * 2 - 1,
    )
    return (
        int(low[0] + (high[0] - low[0]) * local),
        int(low[1] + (high[1] - low[1]) * local),
        int(low[2] + (high[2] - low[2]) * local),
    )


def _row_bg(hue: int, index: int) -> RGB:
    red, green, blue = colorsys.hls_to_rgb(
        hue / 360.0, 0.09 if index % 2 == 0 else 0.13, 0.38
    )
    return (int(red * 255), int(green * 255), int(blue * 255))


def slowest_task_id(timeline: Timeline) -> Optional[str]:
    done = [
        row for row in timeline.rows if row.status == "completed" and row.duration_s is not None
    ]
    return max(done, key=lambda row: row.duration_s or 0.0).task_id if done else None


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


def _ruler(
    t0: datetime,
    span: float,
    width: int,
    tz: Optional[tzinfo],
    *,
    relative: bool = False,
) -> str:
    per_second = width / span
    step = next((step for step in _STEPS if step * per_second >= 12), _STEPS[-1])
    line = [" "] * width
    if relative:
        moment = 0
        while moment < span:
            column = int(moment * per_second)
            total = int(moment)
            label = (
                f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
                if span > 3600
                else f"{total // 60}:{total % 60:02d}"
            )
            if column + len(label) <= width:
                line[column : column + len(label)] = list(label)
            moment += step
    else:
        origin = t0.timestamp()
        moment = math.ceil(origin / step) * step
        while moment < origin + span:
            column = int((moment - origin) * per_second)
            label = datetime.fromtimestamp(moment, tz).strftime(
                "%H:%M" if step >= 60 else "%H:%M:%S"
            )
            if column + len(label) <= width:
                line[column : column + len(label)] = list(label)
            moment += step
    return "".join(line)


def _bar_pieces(
    row: TaskRow, t0: datetime, span: float, width: int, now_eff: datetime, tick: int
) -> List[Piece]:
    cells: List[Tuple[str, Optional[str]]] = [(" ", None)] * width

    def column(moment: datetime) -> int:
        return max(0, min(width, int((moment - t0).total_seconds() / span * width)))

    for segment in row.segments:
        end = segment.end or now_eff
        first, last = column(segment.start), column(end)
        if end > segment.start and last <= first:
            last = min(width, first + 1)
        for index in range(first, last):
            cells[index] = (GLYPHS[segment.phase], segment.phase)
    filled = [index for index, (glyph, _) in enumerate(cells) if glyph != " "]
    if row.status == "running" and filled and row.segments and row.segments[-1].end is None:
        tip = filled[-1]
        phase_name = row.segments[-1].phase
        cells[tip] = ("●", phase_name + "+") if tick % 2 == 0 else ("○", phase_name)
    elif filled and filled[-1] + 1 < width and row.status in ("completed", "failed"):
        cells[filled[-1] + 1] = ("✔", "done") if row.status == "completed" else ("✖", "fail")
    pieces: List[Piece] = []
    for glyph, cell_phase in cells:
        if cell_phase is None:
            color: Optional[RGB] = None
        elif cell_phase.endswith("+"):
            color = _lighten(PALETTE[cell_phase[:-1]])
        else:
            color = PALETTE[cell_phase]
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
    if row.status == "completed":
        pieces = [
            ("✔ ", PALETTE["done"], False),
            (
                _duration_text(row.duration_s),
                PALETTE["fail"] if row.task_id == slowest else PALETTE["text"],
                row.task_id == slowest,
            ),
        ]
    elif row.status == "reused":
        pieces = [("↺ reaproveitada", PALETTE["dim"], False)]
    elif row.status == "failed":
        pieces = [(f"✖ {row.failure or 'falhou'}", PALETTE["fail"], True)]
    elif row.status == "running":
        phase = row.segments[-1].phase if row.segments else "worker"
        pieces = [
            ("▶ ", PALETTE["live"], False),
            (PHASE_LABEL.get(phase, phase), PALETTE.get(phase, PALETTE["text"]), False),
        ]
    else:
        done = {item.task_id for item in timeline.rows if item.status in ("completed", "reused")}
        waiting_on = [dependency for dependency in row.depends_on if dependency not in done]
        text = "… aguardando " + ", ".join(waiting_on) if waiting_on else "… na fila"
        pieces = [(text, PALETTE["dim"], False)]
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
    return painter.line([("  MEISTER  " + message, PALETTE["dim"], False)], max(width, len(message) + 12))


def _render_ruler_line(
    painter: Painter,
    *,
    t0: datetime,
    span: float,
    bar_width: int,
    tz: Optional[tzinfo],
    relative: bool = False,
) -> str:
    return " " * (LABEL_W + 1) + painter.paint(
        _ruler(t0, span, bar_width, tz, relative=relative), PALETTE["dim"]
    )


def _render_task_lines(
    painter: Painter,
    timeline: Timeline,
    *,
    t0: datetime,
    span: float,
    bar_width: int,
    width: int,
    now_eff: datetime,
    tick: int,
    via_index: Mapping[str, int],
    color: str,
    hue: int,
    row_offset: int,
    row_count: int,
    row_index_offset: int = 0,
) -> Tuple[List[str], int]:
    rows = timeline.rows[row_offset : row_offset + row_count]
    slowest = slowest_task_id(timeline)
    lines: List[str] = []
    for index, row in enumerate(rows):
        position = via_index.get(row.tier or "")
        via_color = VIA_COLORS[position % len(VIA_COLORS)] if position is not None else None
        background = _row_bg(hue, row_index_offset + index) if color != "none" else None
        pieces: List[Piece] = [(_label(row), PALETTE["text"], False), (" ", None, False)]
        pieces += _bar_pieces(row, t0, span, bar_width, now_eff, tick)
        pieces.append((" ", None, False))
        pieces += _info_pieces(row, timeline, slowest, via_color)
        lines.append(painter.line(pieces, width, background))
    hidden = max(0, len(timeline.rows) - (row_offset + len(rows)))
    return lines, hidden


def _render_jev_line(
    painter: Painter,
    timeline: Timeline,
    *,
    t0: datetime,
    span: float,
    bar_width: int,
    width: int,
    color: str,
    hue: int,
) -> str:
    cells = [" "] * bar_width

    def column(moment: datetime) -> int:
        return max(0, min(bar_width - 1, int((moment - t0).total_seconds() / span * bar_width)))

    for call in timeline.jev.calls:
        if call.end < t0 or call.start > t0 + timedelta(seconds=span):
            continue
        first, last = column(call.start), column(call.end)
        if first > last:
            continue
        for index in range(first, last):
            if cells[index] == " ":
                cells[index] = "━"
        cells[last] = "◆"

    # o custo vai no rótulo (a coluna de info é estreita e cortaria o valor)
    label = _fit(f"jev · US$ {timeline.jev.cost_usd:.4f}", LABEL_W)
    info = f"{timeline.jev.classify_count} classify · {timeline.jev.control_count} control"
    pieces: List[Piece] = [(label, JEV, False), (" ", None, False)]
    pieces.append(("".join(cells), JEV, False))
    pieces.append((" ", None, False))
    pieces.append((info[:INFO_W], JEV, False))
    background = _row_bg(hue, 0) if color != "none" else None
    return painter.line(pieces, width, background)


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
    bar_width = width - LABEL_W - INFO_W - 2
    summary = timeline.summary
    dim, text_color = PALETTE["dim"], PALETTE["text"]
    lines: List[str] = []

    badge, badge_color, badge_bold = _badge(timeline, view, tick)
    right = f"{badge}  {_clock(timeline.started_at, tz)} → {_clock(timeline.ended_at, tz)}"
    left = f"  MEISTER  {project}   run {timeline.run_id[:8]}" + (
        f" · {timeline.title}" if timeline.title else ""
    )
    left = _fit(left, width - len(right) - 1).rstrip()
    lines.append(
        painter.line(
            [
                (left, text_color, True),
                (" " * (width - len(left) - len(right)), None, False),
                (badge, badge_color, badge_bold),
                (right[len(badge) :], dim, False),
            ],
            width,
        )
    )

    done_cells = round(summary.completed / summary.total * PROGRESS_W) if summary.total else 0
    progress = [("█", _gradient(index / (PROGRESS_W - 1)), False) for index in range(done_cells)]
    progress.append(("░" * (PROGRESS_W - done_cells), dim, False))
    cost = f"US$ {summary.cost_usd:.4f}" if summary.cost_usd is not None else "US$ ?"
    lines.append(
        painter.line(
            [
                (" Concluídas ", dim, False),
                *progress,
                (f" {summary.completed}/{summary.total}", text_color, True),
                (
                    f"   Rodando {summary.running} (pico {summary.peak_parallel} · "
                    f"média {summary.avg_parallel:.1f})",
                    text_color,
                    False,
                ),
                (f"   Falhas {summary.failed}", PALETTE["fail"] if summary.failed else dim, False),
                (f"   {cost}", text_color, False),
            ],
            width,
        )
    )
    lines.append(painter.paint("─" * width, dim))
    lines.append(
        _render_ruler_line(
            painter,
            t0=t0,
            span=span,
            bar_width=bar_width,
            tz=tz,
        )
    )

    lane_count = int(bool(timeline.jev.calls))
    total_rows = lane_count + len(timeline.rows)
    visible = max(0, height - CHROME_ROWS)
    offset = min(max(0, view.row_offset), max(0, total_rows - visible))
    lane_visible = lane_count and offset == 0 and visible > 0
    if lane_visible:
        lines.append(
            _render_jev_line(
                painter,
                timeline,
                t0=t0,
                span=span,
                bar_width=bar_width,
                width=width,
                color=color,
                hue=hue,
            )
        )
    task_offset = max(0, offset - lane_count)
    task_capacity = max(0, visible - int(bool(lane_visible)))
    rows, _ = _render_task_lines(
        painter,
        timeline,
        t0=t0,
        span=span,
        bar_width=bar_width,
        width=width,
        now_eff=now_eff,
        tick=tick,
        via_index=via_index,
        color=color,
        hue=hue,
        row_offset=task_offset,
        row_count=task_capacity,
        row_index_offset=lane_count + task_offset if lane_count else 0,
    )
    lines.extend(rows)
    hidden = max(0, total_rows - offset - visible)

    lines.append(painter.paint("─" * width, dim))
    legend: List[Piece] = []
    for phase in ("worker", "gate", "integrate", "lock_wait", "wait"):
        legend += [
            (GLYPHS[phase], PALETTE[phase], False),
            (f" {PHASE_LABEL[phase]}  ", dim, False),
        ]
    if hidden:
        legend.append((f"  ↓ {hidden} mais (↑/↓)", PALETTE["retry"], False))
    lines.append(painter.line([(" ", None, False), *legend], width))
    keys = (
        " [ ] run   l ao vivo   p pausa   +/- zoom   ←/→ tempo   ↑/↓ linhas   ? ajuda   q sair"
        if interactive
        else f" Gerado às {_clock(now, tz)} · use `meister timeline` para o modo interativo"
    )
    lines.append(painter.line([(keys, dim, False)], width))
    return "\n".join(lines[: max(0, height)])


def _all_badge(timelines: Sequence[Timeline], view: View, tick: int) -> Tuple[str, RGB, bool]:
    if view.paused:
        return "⏸ PAUSADO", PALETTE["paused"], True
    if any(timeline.status == "running" for timeline in timelines):
        return "▶ AO VIVO", PALETTE["live"], tick % 2 == 0
    return "≡ VISÃO GERAL", PALETTE["replay"], True


def _run_duration(timeline: Timeline, now: datetime) -> str:
    if timeline.started_at is None:
        return "—"
    end = timeline.ended_at or now
    return _duration_text(max(0.0, (end - timeline.started_at).total_seconds()))


def all_body_line_count(timelines: Sequence[Timeline]) -> int:
    return sum(2 + len(timeline.rows) + bool(timeline.jev.calls) for timeline in timelines)


def render_all(
    timelines: Sequence[Timeline],
    *,
    width: int,
    height: int,
    now: datetime,
    tick: int = 0,
    view: Optional[View] = None,
    color: str = "none",
    project: str = "",
    via_index: Optional[Mapping[str, int]] = None,
    tz: Optional[tzinfo] = None,
    interactive: bool = True,
) -> str:
    if width < MIN_WIDTH:
        return f"Terminal estreito: use pelo menos {MIN_WIDTH} colunas (atual: {width})."
    view = view or View()
    painter = Painter(color)
    via_index = via_index or {}
    dim, text_color = PALETTE["dim"], PALETTE["text"]
    summary = [timeline.summary for timeline in timelines]
    completed = sum(item.completed for item in summary)
    total = sum(item.total for item in summary)
    running = sum(item.running for item in summary)
    failed = sum(item.failed for item in summary)
    known_costs = [item.cost_usd for item in summary if item.cost_usd is not None]
    cost = f"US$ {sum(known_costs):.4f}" if known_costs else "US$ ?"
    badge, badge_color, badge_bold = _all_badge(timelines, view, tick)
    left = _fit(f"  MEISTER  {project}   todos os runs ({len(timelines)})", width - len(badge) - 1)
    lines = [
        painter.line(
            [
                (left.rstrip(), text_color, True),
                (" " * (width - len(left.rstrip()) - len(badge)), None, False),
                (badge, badge_color, badge_bold),
            ],
            width,
        ),
        painter.line(
            [
                (f" Concluídas {completed}/{total}", text_color, True),
                (f"   Rodando {running}", PALETTE["live"] if running else dim, False),
                (f"   Falhas {failed}", PALETTE["fail"] if failed else dim, False),
                (f"   {cost}", text_color, False),
            ],
            width,
        ),
    ]

    body: List[str] = []
    for run_index, timeline in enumerate(timelines):
        t0 = timeline.started_at or timeline.ended_at or now
        now_eff = timeline.ended_at or now
        span = max((now_eff - t0).total_seconds(), 1.0)
        bar_width = max(1, width - LABEL_W - INFO_W - 2)
        run_badge = (
            ("▶ AO VIVO", PALETTE["live"], tick % 2 == 0)
            if timeline.status == "running"
            else (
                ("✔ CONCLUÍDO", PALETTE["done"], True)
                if timeline.status == "completed"
                else ("✖ FALHOU", PALETTE["fail"], True)
            )
        )
        status, status_color, status_bold = run_badge
        run_cost = (
            f"US$ {timeline.summary.cost_usd:.4f}"
            if timeline.summary.cost_usd is not None
            else "US$ ?"
        )
        time_range = f"{_clock(timeline.started_at, tz)} → {_clock(timeline.ended_at, tz)}"
        details = (
            f"{status}  {timeline.summary.completed}/{timeline.summary.total}  "
            f"{run_cost}  {_run_duration(timeline, now)}  {time_range}"
        )
        label = f" {timeline.run_id[:8]} · {timeline.title}".rstrip(" ·")
        label = _fit(label, max(0, width - len(details) - 1)).rstrip()
        body.append(
            painter.line(
                [
                    (label, text_color, True),
                    (" " * max(0, width - len(label) - len(details)), None, False),
                    (status, status_color, status_bold),
                    (details[len(status) :], dim, False),
                ],
                width,
                _row_bg((210 + 67 * run_index) % 360, 0) if color != "none" else None,
            )
        )
        body.append(
            _render_ruler_line(
                painter,
                t0=t0,
                span=span,
                bar_width=bar_width,
                tz=tz,
                relative=True,
            )
        )
        if timeline.jev.calls:
            body.append(
                _render_jev_line(
                    painter,
                    timeline,
                    t0=t0,
                    span=span,
                    bar_width=bar_width,
                    width=width,
                    color=color,
                    hue=(210 + 67 * run_index) % 360,
                )
            )
        task_lines, _ = _render_task_lines(
            painter,
            timeline,
            t0=t0,
            span=span,
            bar_width=bar_width,
            width=width,
            now_eff=now_eff,
            tick=tick,
            via_index=via_index,
            color=color,
            hue=(210 + 67 * run_index) % 360,
            row_offset=0,
            row_count=len(timeline.rows),
            row_index_offset=int(bool(timeline.jev.calls)),
        )
        body.extend(task_lines)

    visible_body = max(0, height - 4)
    offset = min(view.row_offset, max(0, len(body) - visible_body))
    visible = body[offset : offset + visible_body]
    lines.extend(visible)
    hidden_above = offset
    hidden_below = max(0, len(body) - offset - len(visible))
    legend: List[Piece] = [(" ", None, False)]
    for phase in ("worker", "gate", "integrate", "lock_wait", "wait"):
        legend += [
            (GLYPHS[phase], PALETTE[phase], False),
            (f" {PHASE_LABEL[phase]}  ", dim, False),
        ]
    if hidden_above or hidden_below:
        legend.append(
            (f"  ↑ {hidden_above} acima ↓ {hidden_below} abaixo", PALETTE["retry"], False)
        )
    lines.append(painter.line(legend, width))
    keys = (
        " a run selecionado   [ / ] sair e navegar   l ao vivo   p pausa   "
        "zoom e tempo: só em um run   ↑/↓ rolar   ? ajuda   q sair"
        if interactive
        else f" Gerado às {_clock(now, tz)} · use `meister timeline` para o modo interativo"
    )
    lines.append(painter.line([(keys, dim, False)], width))
    return "\n".join(lines[:height])
