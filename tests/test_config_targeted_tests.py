from __future__ import annotations

import pathlib

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.config import MeisterConfig, _parse_config_dict, load_config, validate_config
from meister.config_editor import apply_lanes, load_effective_lanes
from meister.i18n import CATALOGS, t
from meister.locales.en_cli import MESSAGES as EN_MESSAGES
from meister.locales.pt_br_cli import MESSAGES as PT_MESSAGES

PATH = "workers.targeted_tests"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = str(REPO_ROOT / "meister.config.example.yaml")


def _issues(config: MeisterConfig):
    return [issue for issue in validate_config(config) if issue.path == PATH]


def test_targeted_tests_defaults_to_true():
    assert MeisterConfig().workers.targeted_tests is True
    config = _parse_config_dict({})
    assert config.workers.targeted_tests is True
    assert not _issues(config)
    assert load_config().workers.targeted_tests is True


def test_targeted_tests_false_is_read(tmp_path):
    config_path = tmp_path / "meister.config.yaml"
    config_path.write_text("workers:\n  targeted_tests: false\n", encoding="utf-8")

    config = load_config(str(config_path))

    assert config.workers.targeted_tests is False
    assert not _issues(config)


@pytest.mark.parametrize("value", ["yes", 1, 0, None, [True], "true", 123, {"a": 1}])
def test_targeted_tests_rejects_non_boolean(value):
    config = _parse_config_dict({"workers": {"targeted_tests": value}})

    issues = _issues(config)
    assert len(issues) == 1
    assert issues[0].level == "error"
    assert issues[0].message == t("reports.config.targeted_tests_boolean", value=repr(value))
    assert config.workers.targeted_tests is True


def test_targeted_tests_validation_catches_directly_built_config():
    config = MeisterConfig()
    config.workers.targeted_tests = "true"  # type: ignore[assignment]

    issues = _issues(config)
    assert len(issues) == 1
    assert issues[0].level == "error"
    assert issues[0].message == t("reports.config.targeted_tests_boolean", value=repr("true"))


def test_targeted_tests_message_in_both_catalogs():
    key = "reports.config.targeted_tests_boolean"
    assert "{value}" in EN_MESSAGES[key]
    assert "{value}" in PT_MESSAGES[key]
    assert EN_MESSAGES[key].isascii()
    assert "{value}" in CATALOGS["en"][key]
    assert "{value}" in CATALOGS["pt-BR"][key]


def test_meister_config_validate_accepts_example():
    runner = CliRunner()
    result = runner.invoke(main, ["config", "validate", "--config-path", EXAMPLE_CONFIG])
    assert result.exit_code == 0, result.output
    assert "error" not in result.output.lower() or "0 errors" in result.output.lower()

    cfg = load_config(EXAMPLE_CONFIG)
    error_issues = [issue for issue in validate_config(cfg) if issue.level == "error"]
    assert not error_issues
    assert cfg.workers.targeted_tests is True


def test_config_editor_preserves_targeted_tests(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_text(
        "workers:\n"
        "  targeted_tests: false\n"
        "  tier_order:\n"
        "    - name: tier_1\n"
        "      harness: copilot\n"
        "      model: claude-haiku-5.5\n"
        "      cost_per_m_tokens: 0.20\n",
        encoding="utf-8",
    )

    doc = load_effective_lanes(str(path))
    lanes = [dict(lane) for lane in doc.lanes]
    lanes[0]["model"] = "gpt-6-luna"

    apply_lanes(doc, lanes, doc.stamp)

    reloaded = load_config(str(path))
    assert reloaded.workers.targeted_tests is False
    raw_content = path.read_text(encoding="utf-8")
    assert "targeted_tests: false" in raw_content
