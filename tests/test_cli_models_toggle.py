from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
import pytest
import yaml

from meister.cli import main


def _invoke(args: list[str]):
    return CliRunner().invoke(
        main,
        args,
        env={"MEISTER_LANG": "en", "MEISTER_CONFIG_PATH": ""},
    )


def _write_config(path: Path, enabled: dict[str, bool] | None = None) -> None:
    lanes = ["alpha"] if enabled is not None and list(enabled) == ["alpha"] else ["alpha", "beta"]
    config = {
        "custom": {"keep": "value"},
        "workers": {
            "tier_order": [
                {
                    "name": name,
                    "harness": "test",
                    "model": f"model-{name}",
                    "cost_per_m_tokens": 0.1,
                    "enabled": True,
                }
                for name in lanes
            ],
            "enabled": enabled or {name: True for name in lanes},
        },
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_models_disable_writes_config_and_displays_new_state(tmp_path: Path):
    config_path = tmp_path / "meister.config.yaml"
    _write_config(config_path)

    result = _invoke(["models", "--disable", "alpha", "-c", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "alpha" in result.output
    assert "disabled" in result.output
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["workers"]["enabled"] == {"alpha": False, "beta": True}
    assert saved["custom"] == {"keep": "value"}
    assert Path(f"{config_path}.bak").exists()


def test_models_enable_is_idempotent(tmp_path: Path):
    config_path = tmp_path / "meister.config.yaml"
    _write_config(config_path, {"alpha": False, "beta": True})

    first = _invoke(["models", "--enable", "alpha", "-c", str(config_path)])
    assert first.exit_code == 0, first.output
    first_content = config_path.read_bytes()
    second = _invoke(["models", "--enable", "alpha", "-c", str(config_path)])

    assert second.exit_code == 0, second.output
    assert config_path.read_bytes() == first_content
    assert yaml.safe_load(first_content)["workers"]["enabled"] == {
        "alpha": True,
        "beta": True,
    }


def test_models_cannot_disable_last_enabled_lane(tmp_path: Path):
    config_path = tmp_path / "meister.config.yaml"
    _write_config(config_path, {"alpha": True})
    original = config_path.read_bytes()

    result = _invoke(["models", "--disable", "alpha", "-c", str(config_path)])

    assert result.exit_code != 0
    assert "last enabled lane" in result.output
    assert config_path.read_bytes() == original
    assert not Path(f"{config_path}.bak").exists()


@pytest.mark.parametrize("option", ["--enable", "--disable"])
def test_models_unknown_lane_lists_valid_lanes_without_writing(
    tmp_path: Path, option: str
):
    config_path = tmp_path / "meister.config.yaml"
    _write_config(config_path)
    original = config_path.read_bytes()

    result = _invoke(["models", option, "missing", "-c", str(config_path)])

    assert result.exit_code != 0
    assert "missing" in result.output
    assert "alpha" in result.output
    assert "beta" in result.output
    assert config_path.read_bytes() == original
    assert not Path(f"{config_path}.bak").exists()


def test_models_lists_effort_and_uses_dash_when_absent(tmp_path: Path):
    config_path = tmp_path / "meister.config.yaml"
    _write_config(config_path)

    result = _invoke(["models", "-c", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "EFFORT" in result.output
    assert "model-alpha" in result.output
    assert "-          $0.100  enabled" in result.output


def test_models_toggle_creates_missing_config_with_only_enabled_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from meister.config import load_config

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEISTER_CONFIG_PATH", raising=False)
    lane_name = load_config().workers.tier_order[0].name
    config_path = tmp_path / "new-config.yaml"

    result = _invoke(
        ["models", "--disable", lane_name, "-c", str(config_path)]
    )

    assert result.exit_code == 0, result.output
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert set(saved) == {"workers"}
    assert set(saved["workers"]) == {"enabled"}
    assert saved["workers"]["enabled"][lane_name] is False
