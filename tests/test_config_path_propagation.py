import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from meister.config import MeisterConfig, load_config
from meister.engine.bridge import HerdrEventBridge
from meister.worker import write_atomic_json

CONFIG_YAML = "router:\n  mode: first\n"


def _write(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_YAML, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _no_env_config(monkeypatch):
    monkeypatch.delenv("MEISTER_CONFIG_PATH", raising=False)


def test_explicit_config_path_is_absolute(tmp_path):
    cfg_file = _write(tmp_path / "explicit" / "meister.config.yaml")

    cfg = load_config(config_path=str(cfg_file))

    assert cfg.config_path == str(cfg_file)


def test_env_config_path_is_absolute(tmp_path, monkeypatch):
    cfg_file = _write(tmp_path / "from_env" / "custom.yaml")
    monkeypatch.setenv("MEISTER_CONFIG_PATH", str(cfg_file))

    cfg = load_config()

    assert cfg.config_path == str(cfg_file)


def test_cwd_config_path_is_absolute(tmp_path):
    cwd = tmp_path / "project"
    cfg_file = _write(cwd / "meister.config.yaml")

    cfg = load_config(cwd=str(cwd))

    assert cfg.config_path == str(cfg_file)


def test_dot_config_path_is_absolute_when_loaded_from_current_dir(tmp_path, monkeypatch):
    _write(tmp_path / "meister.config.yaml")
    monkeypatch.chdir(tmp_path)

    cfg = load_config()

    assert cfg.config_path == str(Path.cwd() / "meister.config.yaml")


def test_relative_config_path_becomes_absolute(tmp_path, monkeypatch):
    _write(tmp_path / "rel" / "meister.config.yaml")
    monkeypatch.chdir(tmp_path)

    cfg = load_config(config_path="rel/meister.config.yaml")

    assert cfg.config_path is not None
    assert Path(cfg.config_path).is_absolute()
    assert cfg.config_path == str(Path.cwd() / "rel" / "meister.config.yaml")


def test_no_file_leaves_config_path_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    cfg = load_config(cwd=str(tmp_path))

    assert cfg.config_path is None
    assert MeisterConfig().config_path is None


@pytest.mark.asyncio
async def test_bridge_writes_config_path_into_task_file(tmp_path, monkeypatch):
    cwd = tmp_path / "work"
    cwd.mkdir()
    cfg_file = _write(tmp_path / "cfg" / "meister.config.yaml")
    monkeypatch.chdir(tmp_path)

    captured = {}

    async def fake_split(*args, **kwargs):
        runs_dir = cwd / ".meister" / "runs"
        task_files = list(runs_dir.glob("*_task.json"))
        assert len(task_files) == 1
        data = json.loads(task_files[0].read_text(encoding="utf-8"))
        captured["config_path"] = data["config_path"]
        write_atomic_json(data["result_file"], {"status": "done", "modified_files": []})
        return "w1:p1"

    client = AsyncMock()
    client.is_connected = True
    client.split_pane.side_effect = fake_split
    client.prompt_agent = MagicMock(side_effect=AssertionError("prompt_agent must not be called"))

    cfg = load_config(config_path=str(cfg_file))
    cfg.concurrency.layout_strategy = "tiled"
    cfg.concurrency.isolation_mode = "none"
    bridge = HerdrEventBridge(config=cfg, client=client)

    subtask = {"id": "t1", "description": "Noop", "target_files": [], "cwd": str(cwd)}
    assert await bridge.execute_subtask(subtask) is True

    assert captured["config_path"] == str(cfg_file)
    assert cfg.config_path == str(cfg_file)
