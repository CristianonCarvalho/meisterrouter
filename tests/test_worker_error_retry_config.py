import pytest

from meister.config import MeisterConfig, load_config, validate_config


def _errors(config, path="retry.worker_error_attempts"):
    return [
        issue for issue in validate_config(config)
        if issue.level == "error" and issue.path == path
    ]


def test_worker_error_attempts_defaults_to_one():
    assert MeisterConfig().retry.worker_error_attempts == 1
    assert not _errors(MeisterConfig())


def test_worker_error_attempts_default_when_file_omits_it(tmp_path):
    config_file = tmp_path / "meister.config.yaml"
    config_file.write_text("retry:\n  pane_lost_attempts: 2\n")

    config = load_config(str(config_file))

    assert config.retry.worker_error_attempts == 1
    assert config.retry.pane_lost_attempts == 2
    assert not _errors(config)


@pytest.mark.parametrize("value", [0, 3])
def test_worker_error_attempts_accepts_nonnegative_integers(tmp_path, value):
    config_file = tmp_path / "meister.config.yaml"
    config_file.write_text(f"retry:\n  worker_error_attempts: {value}\n")

    config = load_config(str(config_file))

    assert config.retry.worker_error_attempts == value
    assert not _errors(config)


@pytest.mark.parametrize("value", ["-1", "1.5", '"one"', "true"])
def test_worker_error_attempts_rejects_invalid_values(tmp_path, value):
    config_file = tmp_path / "meister.config.yaml"
    config_file.write_text(f"retry:\n  worker_error_attempts: {value}\n")

    config = load_config(str(config_file))

    assert len(_errors(config)) == 1
    assert isinstance(config.retry.worker_error_attempts, int)


def test_worker_error_attempts_negative_set_in_python_is_reported():
    config = MeisterConfig()
    config.retry.worker_error_attempts = -2

    assert len(_errors(config)) == 1


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_worker_error_attempts_messages_are_translated(tmp_path, language):
    from meister.i18n import set_language

    config_file = tmp_path / "meister.config.yaml"
    config_file.write_text("retry:\n  worker_error_attempts: -1\n")
    set_language(language)
    try:
        message = _errors(load_config(str(config_file)))[0].message
    finally:
        set_language("en")

    assert "worker_error_attempts" in message
    if language == "en":
        assert message.isascii()
