import io
import json

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
    assert key(key(older, "["), "[").run_index == 2
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
    assert right.pan_s == 15.0
    far = right
    for _ in range(10):
        far = key(far, "right")
    assert far.pan_s == 60.0
    assert key(far, "left").pan_s == 45.0
    assert key(AppState(zoom=1), "right").pan_s == 0.0
    assert key(zoomed, "-").zoom == 1 and key(AppState(zoom=2, pan_s=9), "-").pan_s == 0.0
    assert key(AppState(), "down").row_offset == 1
    assert key(AppState(row_offset=6), "down").row_offset == 6
    assert key(AppState(), "up").row_offset == 0


def test_toggles_quit_and_unknown_keys():
    assert key(AppState(), "p").paused is True and key(key(AppState(), "p"), "p").paused is False
    assert key(AppState(), "?").help is True
    for quit_key in ("q", "\x03", "\x1b"):
        assert key(AppState(), quit_key).quit is True
    assert key(AppState(), "z") == AppState()


def test_view_for_maps_zoom_and_pan_to_a_time_window():
    timeline = build_timeline(parallel_events(), "r1", at(30))
    assert view_for(AppState(), timeline, at(30)).t_start is None
    view = view_for(AppState(zoom=2, pan_s=5.0), timeline, at(30))
    assert view.t_start == at(5.0) and view.t_end == at(20.0)


def _log(tmp_path, events):
    path = tmp_path / "orchestration_log.jsonl"
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return str(path)


def _drive(tmp_path, events, keys, frames):
    log = _log(tmp_path, events)
    out = io.StringIO()
    script = iter(keys)
    run_interactive(
        log,
        color="none",
        stdout=out,
        use_terminal=False,
        max_frames=frames,
        key_reader=lambda fd, timeout: next(script, None),
        now_fn=lambda: at(30),
        size_fn=lambda: (120, 30),
        poll_interval=0.0,
        frame_interval=0.0,
    )
    return out.getvalue()


def test_loop_draws_frames_without_clearing_the_screen_and_quits_on_q(tmp_path):
    out = _drive(tmp_path, parallel_events(), [None, "p", "p", "q"], frames=20)
    assert "\x1b[H" in out and "\x1b[2J" not in out
    assert "task_3" in strip_ansi(out)


def test_loop_shows_waiting_message_when_log_is_missing(tmp_path):
    out = io.StringIO()
    run_interactive(
        str(tmp_path / "nao_existe.jsonl"),
        color="none",
        stdout=out,
        use_terminal=False,
        max_frames=2,
        key_reader=lambda fd, timeout: None,
        now_fn=lambda: at(0),
        size_fn=lambda: (100, 24),
        poll_interval=0.0,
        frame_interval=0.0,
    )
    assert "aguardando" in strip_ansi(out.getvalue())


def test_pause_draws_one_frame_with_the_badge_then_freezes(tmp_path):
    out = _drive(tmp_path, parallel_events(), [None, "p", None, None, "q"], frames=20)
    assert "PAUSADO" in strip_ansi(out)
    assert out.count("\x1b[H") == 3  # 2 antes da pausa (o 2º já com a tecla) + 1 quadro PAUSADO; depois congela


def test_frames_never_use_the_last_terminal_column(tmp_path):
    raw = _drive(tmp_path, parallel_events(), [None, "q"], frames=5)
    frames = [part for part in raw.split("\x1b[H") if part]
    assert frames
    for frame in frames:  # cada quadro termina com "\x1b[K\x1b[J": cada linha é medida sem ANSI
        for line in strip_ansi(frame).splitlines():
            assert len(line) <= 119
