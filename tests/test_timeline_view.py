from datetime import timezone

from meister.timeline import build_timeline
from meister.timeline_view import (
    MIN_WIDTH,
    View,
    detect_color,
    render_frame,
    render_waiting,
    slowest_task_id,
    strip_ansi,
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
    assert "Base" in plain and "Task 1:" not in plain
    assert "12 s" in plain and "22 s" in plain
    assert "✔" in plain and "▶ worker" in plain
    assert "12:00:00" in plain
    assert max(len(line) for line in plain.splitlines()) <= 120


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
