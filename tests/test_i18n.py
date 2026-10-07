"""Tests for internationalization (i18n) foundation."""


from meister.i18n import (
    get_language,
    normalize_language,
    reset_language_cache,
    set_language,
    t,
)
from meister.config import MeisterConfig, validate_config, _parse_config_dict


def test_normalize_language():
    # English variations
    assert normalize_language("en") == "en"
    assert normalize_language("EN") == "en"
    assert normalize_language("en-US") == "en"
    assert normalize_language("en_us") == "en"
    assert normalize_language("en-us") == "en"

    # Portuguese variations
    assert normalize_language("pt-BR") == "pt-BR"
    assert normalize_language("pt_BR") == "pt-BR"
    assert normalize_language("pt") == "pt-BR"
    assert normalize_language("PT-br") == "pt-BR"
    assert normalize_language("PT-BR") == "pt-BR"

    # Invalid languages
    assert normalize_language("es") is None
    assert normalize_language("fr") is None
    assert normalize_language("pt-PT") is None
    assert normalize_language("") is None
    assert normalize_language(None) is None
    assert normalize_language("123") is None


def test_get_language_precedence(tmp_path, monkeypatch, capsys):
    # (d) Default is 'en' when MEISTER_LANG is unset and no config exists
    monkeypatch.delenv("MEISTER_LANG", raising=False)
    monkeypatch.chdir(tmp_path)
    reset_language_cache()
    assert get_language() == "en"

    # (c) language key in meister.config.yaml takes effect when (a) and (b) unset
    config_file = tmp_path / "meister.config.yaml"
    config_file.write_text("language: pt-BR\n", encoding="utf-8")
    reset_language_cache()
    assert get_language() == "pt-BR"

    # (b) MEISTER_LANG takes precedence over meister.config.yaml
    monkeypatch.setenv("MEISTER_LANG", "en")
    reset_language_cache()
    assert get_language() == "en"

    # (b) Invalid MEISTER_LANG warns once on stderr and falls back to config
    monkeypatch.setenv("MEISTER_LANG", "invalid-lang")
    reset_language_cache()
    # First call: should warn
    lang1 = get_language()
    assert lang1 == "pt-BR"  # fell back to config
    stderr1 = capsys.readouterr().err
    assert "WARNING: invalid language in MEISTER_LANG" in stderr1
    assert "invalid-lang" in stderr1

    # Second call: should NOT warn again (warned once)
    lang2 = get_language()
    assert lang2 == "pt-BR"
    stderr2 = capsys.readouterr().err
    assert stderr2 == ""

    # (a) set_language explicit override takes precedence over MEISTER_LANG and config
    set_language("en")
    assert get_language() == "en"

    # set_language(None) clears explicit override
    set_language(None)
    assert get_language() == "pt-BR"  # returns to fallback (config)

    # set_language explicit pt-BR
    set_language("pt-BR")
    assert get_language() == "pt-BR"
    set_language(None)


def test_translation_selftest():
    reset_language_cache()
    set_language("en")
    assert t("i18n.selftest", name="Alice") == "Self-test message: Alice"
    assert t("i18n.selftest_plain") == "Self-test message without placeholders"

    set_language("pt-BR")
    assert t("i18n.selftest", name="Alice") == "Mensagem de auto-teste: Alice"
    assert t("i18n.selftest_plain") == "Mensagem de auto-teste sem variáveis"
    set_language(None)


def test_translation_fallback_and_missing(monkeypatch):
    from meister.i18n import CATALOGS

    monkeypatch.setitem(CATALOGS["en"], "test.english_only", "Only in English: {val}")

    # When language is pt-BR, key missing in pt-BR falls back to English
    set_language("pt-BR")
    assert t("test.english_only", val=42) == "Only in English: 42"

    # Key missing everywhere returns the key string (never raises)
    missing_key = "missing.key.nowhere.found"
    assert t(missing_key, extra="abc") == missing_key

    # Missing format placeholders returns unformatted template (never raises)
    assert t("i18n.selftest") == "Mensagem de auto-teste: {name}"

    # Invalid placeholder syntax in template does not crash
    monkeypatch.setitem(CATALOGS["en"], "test.bad_format", "Invalid {bad:syntax}")
    set_language("en")
    assert t("test.bad_format", val=1) == "Invalid {bad:syntax}"
    set_language(None)


def test_config_language_validation():
    # Valid configurations
    cfg_en = MeisterConfig(language="en")
    issues = validate_config(cfg_en)
    assert not any(i.path == "language" for i in issues)

    cfg_pt = MeisterConfig(language="pt-BR")
    issues_pt = validate_config(cfg_pt)
    assert not any(i.path == "language" for i in issues_pt)

    # Invalid language
    cfg_invalid = MeisterConfig(language="es")
    issues_inv = validate_config(cfg_invalid)
    lang_issues = [i for i in issues_inv if i.path == "language"]
    assert len(lang_issues) == 1
    assert lang_issues[0].level == "error"
    assert "en, pt-BR" in lang_issues[0].message

    # Parse from dict with invalid language
    parsed = _parse_config_dict({"language": "fr"})
    assert any(i.path == "language" for i in parsed._parse_issues)


def _config_show_language(tmp_path, monkeypatch, *, env, file_language):
    """Idioma que `meister config show --json -c <arquivo>` informa (via CLI, como o usuário)."""
    import json

    from click.testing import CliRunner

    from meister.cli import main

    cfg_file = tmp_path / "meister.config.yaml"
    cfg_file.write_text(f"language: {file_language}\n" if file_language else "router:\n  mode: first\n", encoding="utf-8")
    if env is None:
        monkeypatch.delenv("MEISTER_LANG", raising=False)
    else:
        monkeypatch.setenv("MEISTER_LANG", env)
    reset_language_cache()
    result = CliRunner().invoke(main, ["config", "show", "--json", "-c", str(cfg_file)])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["language"]


def test_cli_language_precedence_env_beats_explicit_config_file(tmp_path, monkeypatch):
    # sem a variável, vale o idioma do arquivo; sem a chave no arquivo, o padrão é en
    assert _config_show_language(tmp_path, monkeypatch, env=None, file_language="pt-BR") == "pt-BR"
    assert _config_show_language(tmp_path, monkeypatch, env=None, file_language=None) == "en"
    # a variável de ambiente vence o arquivo, inclusive quando o arquivo nem define o idioma (padrão en)
    assert _config_show_language(tmp_path, monkeypatch, env="pt-BR", file_language=None) == "pt-BR"
    assert _config_show_language(tmp_path, monkeypatch, env="en", file_language="pt-BR") == "en"
