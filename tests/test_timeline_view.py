from datetime import timezone
import hashlib

from meister.timeline import build_timeline
from meister.timeline_view import (
    MIN_WIDTH,
    View,
    all_body_line_count,
    detect_color,
    render_all,
    render_frame,
    render_waiting,
    slowest_task_id,
    strip_ansi,
)
from tests.timeline_fixtures import at, ev, parallel_events, phase, worker_cli_events


def _timeline():
    return build_timeline(parallel_events(), "r1", at(30))


def _stalled_timeline():
    events = [
        ev("orchestration_start", "orchestrator", 0, run="stale123", task="stale run"),
        ev("worker_spawn", "task_stale", 1, run="stale123", tier="copilot_luna"),
        phase("task_stale", 10, "worker", 9, run="stale123"),
    ]
    return build_timeline(events, "stale123", at(5000))


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
    assert "Base" in plain and "Task 1:" not in plain
    assert "12 s" in plain and "22 s" in plain
    assert "✔" in plain and "▶ worker" in plain
    assert "12:00:00" in plain
    assert max(len(line) for line in plain.splitlines()) <= 120


def _timeline_with_jev():
    events = parallel_events() + [
        {
            "event_type": "classify",
            "run_id": "r1",
            "task_id": "task_1",
            "ts": at(3).isoformat(),
            "duration_ms": 2000,
            "cost": 0.001,
        },
        {
            "event_type": "control",
            "run_id": "r1",
            "task_id": "orchestrator",
            "ts": at(8).isoformat(),
            "duration_ms": 1000,
            "cost_usd": 0.002,
        },
    ]
    return build_timeline(events, "r1", at(30))


def test_jev_lane_appears_with_diamonds_info_color_and_bounded_dimensions():
    timeline = _timeline_with_jev()
    frame = render_frame(
        timeline, width=120, height=30, now=at(30), color="truecolor", tz=timezone.utc
    )
    plain = strip_ansi(frame)
    jev_line = next(line for line in plain.splitlines() if line.startswith("jev"))
    assert "◆" in jev_line
    assert "1 classify · 1 control" in jev_line
    assert "US$ " in jev_line  # o custo do Jev aparece inteiro, no rótulo
    colored_jev = next(line for line in frame.splitlines() if "jev" in strip_ansi(line))
    assert "\x1b[38;2;255;105;180m" in colored_jev
    assert "jev" not in strip_ansi(_frame(color="none"))
    assert max(len(line) for line in plain.splitlines()) <= 120
    assert len(frame.splitlines()) <= 30
    assert "jev" in strip_ansi(render_frame(
        timeline, width=120, height=30, now=at(30), color="none", view=View(row_offset=0)
    ))
    scrolled = strip_ansi(render_frame(
        timeline, width=120, height=9, now=at(30), color="none", view=View(row_offset=1)
    ))
    assert not any(line.startswith("jev") for line in scrolled.splitlines())
    assert any(line.startswith("task_1") for line in scrolled.splitlines())


def test_jev_lane_is_included_in_all_runs_blocks_and_scroll_count():
    timelines = _all_timelines()
    timelines[0] = _timeline_with_jev()
    out = render_all(timelines, width=120, height=30, now=at(30), color="truecolor")
    plain = strip_ansi(out)
    assert sum(line.startswith("jev") for line in plain.splitlines()) == 1
    assert all_body_line_count(timelines) == sum(
        2 + len(item.rows) + bool(item.jev.calls) for item in timelines
    )
    assert len(out.splitlines()) <= 30
    assert max(len(line) for line in plain.splitlines()) <= 120


def test_no_jev_frame_stays_unchanged_and_small_frames_respect_height():
    no_jev = _frame(color="none")
    assert "jev" not in no_jev
    # Golden from the pre-Jev renderer for a run without Jev calls.
    assert hashlib.sha256(no_jev.encode()).hexdigest() == (
        "9ad342ecd9895bcec209918beed017e26944639d658072bca072ae632c79e2f0"
    )
    assert len(render_frame(
        _timeline_with_jev(), width=120, height=5, now=at(30), color="none"
    ).splitlines()) <= 5
    assert "Sem sinal" not in no_jev


def test_color_none_has_no_escape_sequences_and_truecolor_has_them():
    assert "\x1b" not in _frame(color="none")
    assert "\x1b[38;2;" in _frame(color="truecolor")
    assert "\x1b[38;5;" in _frame(color="256")


def test_pulse_changes_only_the_running_tip():
    even = _frame(color="none", tick=0).splitlines()
    odd = _frame(color="none", tick=1).splitlines()
    diffs = [(left, right) for left, right in zip("".join(even), "".join(odd)) if left != right]
    assert len(diffs) == 1 and {diffs[0][0], diffs[0][1]} == {"●", "○"}


def test_no_line_exceeds_the_width_even_at_the_minimum():
    for width in (MIN_WIDTH, 100, 200):
        plain = strip_ansi(_frame(width=width, color="truecolor"))
        assert max(len(line) for line in plain.splitlines()) <= width


def test_narrow_terminal_returns_short_warning():
    out = _frame(width=MIN_WIDTH - 1)
    assert "80 colunas" in out and "\n" not in out


def test_row_scroll_hides_rows_and_footer_says_so():
    plain = strip_ansi(_frame(height=9, view=View(row_offset=1)))
    assert "task_1" not in plain and "task_2" in plain and "task_3" in plain
    plain = strip_ansi(_frame(height=8))
    assert "task_1" in plain and "task_2" not in plain
    assert "mais" in plain


def test_badges_follow_state():
    assert "AO VIVO" in strip_ansi(_frame())
    assert "PAUSADO" in strip_ansi(_frame(view=View(paused=True)))
    ended = build_timeline(
        parallel_events()
        + [
            {
                "event_type": "orchestration_end",
                "run_id": "r1",
                "task_id": "orchestrator",
                "ts": at(40).isoformat(),
                "status": "failed",
            }
        ],
        "r1",
        at(50),
    )
    out = strip_ansi(render_frame(ended, width=120, height=30, now=at(50), tz=timezone.utc))
    assert "FALHOU" in out
    out = strip_ansi(
        render_frame(ended, width=120, height=30, now=at(50), tz=timezone.utc, view=View(live=False))
    )
    assert "REPLAY" in out


def test_via_gets_a_color_from_catalog_position_without_model_names():
    colored = _frame(color="truecolor", via_index={"copilot_luna": 0, "agy_gemini_flash": 1})
    other = _frame(color="truecolor", via_index={"copilot_luna": 1, "agy_gemini_flash": 0})
    assert colored != other


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


def _all_timelines():
    older_events = parallel_events("older123") + [
        {
            "event_type": "orchestration_end",
            "run_id": "older123",
            "task_id": "orchestrator",
            "ts": at(30).isoformat(),
            "status": "completed",
        }
    ]
    return [
        build_timeline(parallel_events("newer456"), "newer456", at(30)),
        build_timeline(older_events, "older123", at(30)),
    ]


def _all_frame(**kwargs):
    options = dict(
        width=120,
        height=20,
        now=at(30),
        tz=timezone.utc,
        project="DEMO",
        interactive=True,
    )
    options.update(kwargs)
    return render_all(_all_timelines(), **options)


def test_all_runs_render_in_order_with_relative_rulers_and_block_costs():
    plain = strip_ansi(_all_frame(color="none"))
    lines = plain.splitlines()
    assert "todos os runs (2)" in lines[0]
    headers = [line for line in lines if "newer456" in line or "older123" in line]
    assert "newer456" in headers[0] and "older123" in headers[1]
    assert all("US$ 0.7500" in line for line in headers)
    rulers = [line for line in lines if line[27:].lstrip().startswith("0:00")]
    assert len(rulers) == 2
    assert "AO VIVO" in headers[0] and "CONCLUÍDO" in headers[1]


def test_all_runs_summary_colors_size_and_scroll():
    timelines = _all_timelines()
    out = render_all(
        timelines,
        width=100,
        height=20,
        now=at(30),
        color="truecolor",
        project="DEMO",
        tz=timezone.utc,
    )
    plain = strip_ansi(out)
    assert "Concluídas 4/6" in plain
    assert "US$ 1.5000" in plain
    assert all(len(line) <= 100 for line in plain.splitlines())
    assert len(out.splitlines()) <= 20
    bg_codes = {
        line.split("\x1b[48;2;", 1)[1].split("m", 1)[0]
        for line in out.splitlines()
        if "\x1b[48;2;" in line
    }
    assert len(bg_codes) >= 2
    colored_headers = [
        line
        for line in out.splitlines()
        if "newer456" in line or "older123" in line
    ]
    header_backgrounds = [
        line.split("\x1b[48;2;", 1)[1].split("m", 1)[0] for line in colored_headers
    ]
    assert len(header_backgrounds) == 2 and header_backgrounds[0] != header_backgrounds[1]
    scrolled = strip_ansi(
        render_all(
            timelines,
            width=120,
            height=8,
            now=at(30),
            view=View(row_offset=1),
            tz=timezone.utc,
        )
    )
    assert "newer456" not in scrolled
    assert "0:00" in scrolled
    assert all_body_line_count(timelines) == sum(2 + len(item.rows) for item in timelines)


def test_all_runs_width_warning_and_no_color_escape_sequences():
    assert "80 colunas" in render_all(_all_timelines(), width=79, height=20, now=at(30))
    assert "\x1b" not in _all_frame(color="none")


def test_all_runs_cost_is_unknown_when_none_are_known():
    timelines = _all_timelines()
    for timeline in timelines:
        timeline.summary.cost_usd = None
    plain = strip_ansi(
        render_all(timelines, width=120, height=20, now=at(30), tz=timezone.utc)
    )
    assert "US$ ?" in plain


def test_stalled_run_frame_marks_warning_without_exceeding_width():
    timeline = _stalled_timeline()
    out = render_frame(
        timeline, width=120, height=20, now=at(5000), color="none", tz=timezone.utc
    )
    lines = out.splitlines()
    plain = strip_ansi(out)
    assert "⚠ SEM SINAL" in plain and "→ ⚠ 12:00:10" in plain
    assert "Sem sinal 1" in plain
    assert "⚠" in next(line for line in lines if "task_stale" in line)
    assert "sem sinal há 1 h 23 min" in plain
    assert "\x1b" not in out
    assert max(map(len, lines)) <= 120
    assert timeline.stalled_since == at(10)


def test_stalled_runs_in_all_view_have_warning_and_only_live_runs_set_live_badge():
    stalled = _stalled_timeline()
    overview = strip_ansi(
        render_all([stalled], width=120, height=20, now=at(5000), color="none")
    )
    assert "VISÃO GERAL" in overview and "AO VIVO" not in overview
    assert "⚠ SEM SINAL" in overview
    assert "último evento há 1 h 23 min" in overview
    assert "Sem sinal 1" in overview

    mixed = strip_ansi(
        render_all(
            [stalled, _timeline()],
            width=120,
            height=24,
            now=at(5000),
            color="none",
        )
    )
    assert "AO VIVO" in mixed and max(map(len, mixed.splitlines())) <= 120


def test_worker_cli_failure_renders_task_bar_and_closed_run_badge():
    timeline = build_timeline(worker_cli_events(), "worker_cli", at(20))
    out = render_frame(
        timeline, width=120, height=20, now=at(20), color="none", tz=timezone.utc
    )
    plain = strip_ansi(out)
    task_line = next(line for line in plain.splitlines() if line.startswith("5b936c6b"))
    assert "█" in task_line and "timeout" in task_line
    assert "Falhas 1" in plain
    assert "FALHOU" in plain and "AO VIVO" not in plain
    assert all(len(line) <= 120 for line in plain.splitlines())
