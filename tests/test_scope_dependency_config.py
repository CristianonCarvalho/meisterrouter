from __future__ import annotations

import pytest

from meister.config import MeisterConfig, _parse_config_dict, load_config, validate_config
from meister.i18n import t
from meister.locales.en_reports import MESSAGES as EN_MESSAGES
from meister.locales.pt_br_reports import MESSAGES as PT_MESSAGES

PATH = "scope.allow_dependency_files"


def _issues(config):
    return [issue for issue in validate_config(config) if issue.path == PATH]


def test_allow_dependency_files_defaults_to_false():
    assert MeisterConfig().scope.allow_dependency_files is False
    config = _parse_config_dict({})
    assert config.scope.allow_dependency_files is False
    assert not _issues(config)


def test_allow_dependency_files_true_is_read(tmp_path):
    config_path = tmp_path / "meister.config.yaml"
    config_path.write_text("scope:\n  allow_dependency_files: true\n", encoding="utf-8")

    config = load_config(str(config_path))

    assert config.scope.allow_dependency_files is True
    assert config.scope.tolerated_files == MeisterConfig().scope.tolerated_files
    assert not _issues(config)


@pytest.mark.parametrize("value", ["yes", 1, 0, None, [True]])
def test_allow_dependency_files_rejects_non_boolean(value):
    config = _parse_config_dict({"scope": {"allow_dependency_files": value}})

    issues = _issues(config)
    assert len(issues) == 1
    assert issues[0].level == "error"
    assert issues[0].message == t("reports.config.allow_dependency_files_boolean", value=repr(value))
    assert config.scope.allow_dependency_files is False


def test_allow_dependency_files_validation_catches_directly_built_config():
    config = MeisterConfig()
    config.scope.allow_dependency_files = "true"  # type: ignore[assignment]

    issues = _issues(config)
    assert len(issues) == 1
    assert issues[0].level == "error"


def test_allow_dependency_files_message_in_both_catalogs():
    key = "reports.config.allow_dependency_files_boolean"
    assert "{value}" in EN_MESSAGES[key]
    assert "{value}" in PT_MESSAGES[key]
    assert EN_MESSAGES[key].isascii()
