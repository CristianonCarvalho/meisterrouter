from __future__ import annotations

from pathlib import Path

import pytest

from meister.dashboard.server import app


@pytest.fixture
def timeline_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".meister").mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    resp = app.test_client().get("/timeline")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


@pytest.mark.parametrize(
    "needle",
    [
        'addEventListener("pointerdown"',
        '"Escape"',
        "tooltipNextAction(",
        "tooltipAutoHideMs(",
        "lastPointerType",
        "tooltipSource",
    ],
)
def test_page_wires_touch_tooltip(timeline_html: str, needle: str) -> None:
    assert needle in timeline_html


def test_page_hides_tooltip_on_scroll_and_resize(timeline_html: str) -> None:
    assert 'addEventListener("scroll", hideTaskTooltip, { passive: true })' in timeline_html
    assert 'addEventListener("resize", hideTaskTooltip)' in timeline_html


def test_helpers_block_still_appears_once(timeline_html: str) -> None:
    assert timeline_html.count("// helpers:begin") == 1
    assert timeline_html.count("// helpers:end") == 1


def test_show_task_tooltip_uses_placement_tooltip_row(timeline_html: str) -> None:
    assert "function showTaskTooltip(event, row, via, phase, seg)" in timeline_html
    show_task_body = timeline_html.split(
        "function showTaskTooltip(event, row, via, phase, seg)", 1
    )[1].split("function showLaneTooltip", 1)[0]
    assert "placementTooltipRow(row, via)" in show_task_body
    assert timeline_html.count("// helpers:begin") == 1
    assert timeline_html.count("// helpers:end") == 1

