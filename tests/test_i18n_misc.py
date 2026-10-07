"""Mensagens fora das cinco áreas principais: aviso do importador de planos, texto da timeline e log de custo."""

import re

import pytest

from meister.i18n import reset_language_cache, t

_ACCENTED = re.compile(r"[áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]")


@pytest.mark.parametrize("language, expected", [("en", "no conclusion"), ("pt-BR", "sem conclusão")])
def test_timeline_no_conclusion_text(monkeypatch, language, expected):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    assert t("misc.timeline.no_conclusion") == expected


def test_plan_adapter_generated_file_warning_in_both_languages(monkeypatch, capsys):
    from meister.plan_adapters.superpowers import _warn_generated_files

    body = "Run npm install to add the dependency."
    outputs = {}
    for language in ("en", "pt-BR"):
        monkeypatch.setenv("MEISTER_LANG", language)
        reset_language_cache()
        _warn_generated_files("task_1", body, ["src/a.py"], [])
        outputs[language] = capsys.readouterr().err
    assert "may generate 'package-lock.json'" in outputs["en"]
    assert not _ACCENTED.search(outputs["en"])
    assert "pode gerar 'package-lock.json'" in outputs["pt-BR"]
    assert "não coberto por Files: ou scope.tolerated_files" in outputs["pt-BR"]


def test_models_log_message_exists_in_both_languages(monkeypatch):
    for language, expected in (("en", "No lane key to estimate the cost"), ("pt-BR", "Não há chave de via para estimar o custo")):
        monkeypatch.setenv("MEISTER_LANG", language)
        reset_language_cache()
        assert t("misc.models.no_lane_key") == expected
