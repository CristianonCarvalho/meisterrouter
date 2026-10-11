from __future__ import annotations

import json

import pytest

from tests.timeline_js import run_js


@pytest.mark.parametrize(
    ("pointer_type", "expected"),
    [("touch", 8000), ("pen", 8000), ("mouse", 0), ("", 0), (None, 0), ("unknown", 0)],
)
def test_tooltip_auto_hide_ms(pointer_type: str | None, expected: int) -> None:
    assert run_js(f"tooltipAutoHideMs({json.dumps(pointer_type)})") == expected


def test_tooltip_auto_hide_ms_without_argument() -> None:
    assert run_js("tooltipAutoHideMs()") == 0


@pytest.mark.parametrize(
    ("pointer_type", "expected"),
    [("touch", True), ("pen", True), ("mouse", False), ("", False), (None, False)],
)
def test_is_touch_like_pointer(pointer_type: str | None, expected: bool) -> None:
    assert run_js(f"isTouchLikePointer({json.dumps(pointer_type)})") is expected


def _action(**state: object) -> object:
    base = {
        "visible": False,
        "sameSource": False,
        "pointerType": "touch",
        "insideTooltip": False,
        "onTarget": False,
    }
    base.update(state)
    return run_js(f"tooltipNextAction({json.dumps(base)})")


@pytest.mark.parametrize("pointer_type", ["touch", "pen"])
def test_tap_on_same_target_while_visible_toggles_off(pointer_type: str) -> None:
    assert _action(pointerType=pointer_type, onTarget=True, visible=True, sameSource=True) == "hide"


def test_tap_on_other_target_while_visible_shows() -> None:
    assert _action(onTarget=True, visible=True, sameSource=False) == "show"


def test_tap_on_target_while_hidden_shows() -> None:
    assert _action(onTarget=True, visible=False) == "show"


def test_tap_inside_tooltip_hides() -> None:
    assert _action(insideTooltip=True, visible=True) == "hide"


def test_tap_on_background_while_visible_hides() -> None:
    assert _action(visible=True) == "hide"


def test_tap_on_background_while_hidden_keeps() -> None:
    assert _action(visible=False) == "keep"


@pytest.mark.parametrize("visible", [True, False])
@pytest.mark.parametrize("on_target", [True, False])
@pytest.mark.parametrize("inside", [True, False])
@pytest.mark.parametrize("same", [True, False])
def test_mouse_always_keeps(visible: bool, on_target: bool, inside: bool, same: bool) -> None:
    assert (
        _action(pointerType="mouse", visible=visible, onTarget=on_target, insideTooltip=inside, sameSource=same)
        == "keep"
    )
