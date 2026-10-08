from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from meister.lane_toggle import LaneToggleError, apply_toggle


def test_disabling_and_enabling_lanes_writes_the_updated_map(tmp_path: Path):
    config_path = tmp_path / "meister.config.yaml"
    config_path.write_text(
        "workers:\n  enabled:\n    fast: true\n    fallback: true\n",
        encoding="utf-8",
    )

    disabled = apply_toggle(
        config_path,
        ["fast", "fallback"],
        {"fast": True, "fallback": True},
        enable=[],
        disable=["fast"],
    )

    assert disabled == {"fast": False, "fallback": True}
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == {
        "workers": {"enabled": disabled}
    }

    enabled = apply_toggle(
        config_path,
        ["fast", "fallback"],
        disabled,
        enable=["fast"],
        disable=[],
    )

    assert enabled == {"fast": True, "fallback": True}
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == {
        "workers": {"enabled": enabled}
    }


def test_repeating_a_toggle_is_idempotent(tmp_path: Path):
    config_path = tmp_path / "meister.config.yaml"
    config_path.write_text("workers:\n  enabled:\n    fast: true\n    backup: true\n")
    state = {"fast": True, "backup": True}

    first = apply_toggle(
        config_path, ["fast", "backup"], state, enable=[], disable=["fast"]
    )
    first_content = config_path.read_bytes()
    second = apply_toggle(
        config_path, ["fast", "backup"], first, enable=[], disable=["fast"]
    )

    assert second == first
    assert config_path.read_bytes() == first_content


def test_disabling_last_enabled_lane_fails_without_writing(tmp_path: Path):
    config_path = tmp_path / "meister.config.yaml"
    original = b"workers:\n  enabled:\n    only: true\n"
    config_path.write_bytes(original)

    with pytest.raises(LaneToggleError, match="at least one|last|ultima|última"):
        apply_toggle(config_path, ["only"], {"only": True}, enable=[], disable=["only"])

    assert config_path.read_bytes() == original
    assert not config_path.with_suffix(".yaml.bak").exists()


@pytest.mark.parametrize(
    ("enable", "disable"),
    [(["ghost"], []), ([], ["ghost"])],
)
def test_unknown_lane_error_lists_valid_names(
    tmp_path: Path, enable: list[str], disable: list[str]
):
    config_path = tmp_path / "meister.config.yaml"
    original = b"workers:\n  enabled:\n    fast: true\n"
    config_path.write_bytes(original)

    with pytest.raises(LaneToggleError) as exc_info:
        apply_toggle(config_path, ["fast", "backup"], {"fast": True}, enable, disable)

    message = str(exc_info.value)
    assert "ghost" in message
    assert "fast" in message
    assert "backup" in message
    assert config_path.read_bytes() == original
    assert not Path(f"{config_path}.bak").exists()


def test_existing_config_preserves_other_keys_and_creates_exact_backup(
    tmp_path: Path,
):
    config_path = tmp_path / "meister.config.yaml"
    original = (
        "# user settings\n"
        "router:\n"
        "  mode: first\n"
        "custom:\n"
        "  keep: value\n"
        "workers:\n"
        "  tier_order: []\n"
        "  enabled:\n"
        "    fast: true\n"
        "    backup: true\n"
    ).encode()
    config_path.write_bytes(original)

    apply_toggle(
        config_path,
        ["fast", "backup"],
        {"fast": True, "backup": True},
        enable=[],
        disable=["fast"],
    )

    assert Path(f"{config_path}.bak").read_bytes() == original
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["router"] == {"mode": "first"}
    assert saved["custom"] == {"keep": "value"}
    assert saved["workers"]["tier_order"] == []
    assert saved["workers"]["enabled"] == {"fast": False, "backup": True}


def test_missing_config_is_created_with_only_enabled_map(tmp_path: Path):
    config_path = tmp_path / "missing.yaml"

    result = apply_toggle(
        config_path,
        ["fast", "backup"],
        {"fast": True, "backup": True},
        enable=[],
        disable=["fast"],
    )

    assert result == {"fast": False, "backup": True}
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == {
        "workers": {"enabled": result}
    }
    assert not Path(f"{config_path}.bak").exists()


def test_replace_failure_preserves_original_and_cleans_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import meister.lane_toggle as lane_toggle

    config_path = tmp_path / "meister.config.yaml"
    original = b"workers:\n  enabled:\n    fast: true\n    backup: true\n"
    config_path.write_bytes(original)
    real_replace = lane_toggle.os.replace
    failed_once = False

    def fail_config_replace(source: str, destination: str) -> None:
        nonlocal failed_once
        if destination == str(config_path) and not failed_once:
            failed_once = True
            raise OSError("simulated replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(lane_toggle.os, "replace", fail_config_replace)

    with pytest.raises(LaneToggleError, match="simulated replace failure"):
        apply_toggle(
            config_path,
            ["fast", "backup"],
            {"fast": True, "backup": True},
            enable=[],
            disable=["fast"],
        )

    assert config_path.read_bytes() == original
    assert not Path(f"{config_path}.bak").exists()
    assert not any(path.name.startswith(".lane-toggle-") for path in tmp_path.iterdir())
