from __future__ import annotations

import re

import pytest

from tests.timeline_js import TIMELINE_TEMPLATE, run_js


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            '{segments:[{phase:"wait",start:1},{phase:"worker",start:5}]}',
            "50",
        ),
        ('{segments:[{phase:"wait",start:1}]}', "99"),
        ("{segments:[]}", "99"),
        ("{}", "99"),
    ],
)
def test_first_work_start_x(row: str, expected: str) -> None:
    expression = f"firstWorkStartX({row}, (t) => t * 10, 99)"
    assert run_js(expression) == int(expected)


def _path(sx: float, sy: float, dx: float, dy: float, lane: int) -> str:
    return run_js(f"edgePathD({sx}, {sy}, {dx}, {dy}, {lane})")


def test_edge_path_starts_and_ends_at_endpoints() -> None:
    d = _path(100, 50, 300, 90, 0)
    assert d.startswith("M 100 50 ")
    assert d.endswith(" 300 90")


def test_edge_trunks_are_distinct_per_lane() -> None:
    trunks = []
    for lane in (0, 1, 2):
        d = _path(100, 50, 400, 90, lane)
        match = re.search(r"V 90", d)
        assert match is not None, d
        trunk = re.search(r"H (\S+) V 90", d)
        assert trunk is not None, d
        trunks.append(float(trunk.group(1)))
    assert len(set(trunks)) == 3
    assert trunks == sorted(trunks)


def test_edge_stub_and_tail_have_minimum_lengths() -> None:
    sx, dx = 100.0, 300.0
    d = _path(sx, 50, dx, 90, 0)
    trunk = float(re.search(r"H (\S+) V", d).group(1))  # type: ignore[union-attr]
    assert trunk - sx >= 12
    assert dx - trunk >= 16


def test_edge_trunk_is_limited_before_destination() -> None:
    sx, dx = 100.0, 140.0
    d = _path(sx, 50, dx, 90, 5)
    trunk = float(re.search(r"H (\S+) V", d).group(1))  # type: ignore[union-attr]
    assert dx - trunk >= 16


def test_narrow_edge_falls_back_to_straight_line() -> None:
    assert _path(100, 50, 110, 90, 0) == "M 100 50 L 110 90"


def test_same_row_edge_is_straight() -> None:
    assert _path(100, 50, 300, 50, 2) == "M 100 50 L 300 50"


def test_dependency_block_uses_new_geometry_helpers() -> None:
    source = TIMELINE_TEMPLATE.read_text(encoding="utf-8")
    assert "firstWorkStartX(dstRow" in source
    assert "edgePathD(sx, sy, dx, dy, laneIndex)" in source
    assert "innerHTML" not in source
