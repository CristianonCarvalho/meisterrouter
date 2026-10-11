"""Mensagem de violação de escopo: manda desfazer e proíbe editar o meister.config.yaml."""

import re

import pytest

from meister.i18n import reset_language_cache, t

_ACCENTED = re.compile(r"[áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]")
_OLD_SUGGESTION = "add it to scope.tolerated_files in meister.config.yaml"


@pytest.mark.parametrize(
    "language, undo, forbid",
    [
        ("en", "git checkout --", "Never edit meister.config.yaml"),
        ("pt-BR", "git checkout --", "Nunca edite o meister.config.yaml"),
    ],
)
def test_scope_violation_message_tells_worker_to_undo(monkeypatch, language, undo, forbid):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    msg = t("engine.worktree.scope_violation", files="drizzle/0001.sql, src/extra.ts")
    assert "drizzle/0001.sql, src/extra.ts" in msg
    assert undo in msg
    assert forbid in msg
    assert "**Files:**" in msg
    assert "scope.tolerated_files" in msg
    assert _OLD_SUGGESTION not in msg
    assert "adicione-o a scope.tolerated_files no meister.config.yaml" not in msg
    if language == "en":
        assert not _ACCENTED.search(msg)
    reset_language_cache()
