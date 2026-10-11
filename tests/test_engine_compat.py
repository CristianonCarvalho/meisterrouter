"""Os caminhos antigos meister.herdr.{bridge,workers,dag,events} apontam para meister.engine."""

import importlib
from unittest.mock import patch

import pytest

import meister.engine
import meister.herdr

MOVED_MODULES = ("bridge", "workers", "dag", "events")


@pytest.mark.parametrize("name", MOVED_MODULES)
def test_old_and_new_module_paths_are_the_same_object(name: str) -> None:
    old = importlib.import_module(f"meister.herdr.{name}")
    new = importlib.import_module(f"meister.engine.{name}")
    assert old is new
    assert getattr(meister.herdr, name) is new


def test_patch_on_old_path_reaches_engine_code() -> None:
    import meister.engine.bridge as engine_bridge

    original = engine_bridge.log_event
    with patch("meister.herdr.bridge.log_event") as fake:
        assert engine_bridge.log_event is fake
    assert engine_bridge.log_event is original


def test_herdr_and_engine_exports_match_for_moved_names() -> None:
    for name in meister.engine.__all__:
        assert name in meister.herdr.__all__
        assert getattr(meister.herdr, name) is getattr(meister.engine, name)
    for name in ("HerdrSocketClient", "render_tui_dashboard", "run_tui_loop"):
        assert hasattr(meister.herdr, name)
