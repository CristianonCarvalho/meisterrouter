from __future__ import annotations

from types import SimpleNamespace

import pytest

from meister.gate import DeterministicGate
from meister.herdr.bridge import is_infrastructure_error
from meister.i18n import reset_language_cache, t
from meister.state import task_fingerprint
from meister.worker import UnknownTierError, resolve_worker_harness_and_model
from meister.worktree import GATE_INFRASTRUCTURE_PREFIX


@pytest.fixture
def set_engine_language(monkeypatch):
    def set_language(language: str) -> None:
        monkeypatch.setenv("MEISTER_LANG", language)
        reset_language_cache()

    yield set_language
    reset_language_cache()


def test_engine_catalog_messages_switch_between_english_and_pt_br(set_engine_language):
    keys = {
        "engine.worktree.repo_dirty": (
            "Main repository is dirty; skipping fast-forward",
            "Repositório principal está dirty; fast-forward pulado",
        ),
        "engine.worktree.fast_forward_ok": (
            "Fast-forward applied successfully",
            "Fast-forward aplicado com sucesso",
        ),
        "engine.worktree.integration_none_active": (
            "No active integration.",
            "Nenhuma integração ativa.",
        ),
        "engine.gate.no_checks": (
            "No tests or linters detected. Configure gate.commands in meister.config.yaml (or gate.allow_unverified: true).",
            "Nenhum teste ou linter detectado. Configure gate.commands no meister.config.yaml (ou gate.allow_unverified: true).",
        ),
        "engine.state.run_missing": (
            "Run 'run-1' not found",
            "Run 'run-1' não encontrado",
        ),
        "engine.worker.no_files_changed": ("No files changed.", "Nenhum arquivo alterado."),
        "engine.workers.tier_cooldown": (
            "Lane low-cost (codex) is on circuit breaker cooldown. Skipping...",
            "Tier low-cost (codex) está em cooldown no circuit breaker. Pulando...",
        ),
        "engine.bridge.no_lanes": (
            "Cannot execute subtask: no worker lanes are configured.",
            "Não é possível executar a subtarefa: nenhuma via está configurada.",
        ),
        "engine.bridge.jev_unavailable": (
            "Jev Decisions API is unavailable",
            "Jev Decisions API indisponível",
        ),
        "engine.worktree.infrastructure_message": (
            "GATE INFRASTRUCTURE ERROR: setup failed. The worker's code was not rejected; fix the environment and rerun the same command to resume.",
            "ERRO DE INFRAESTRUTURA no portão: setup failed. O código do worker não foi reprovado; corrija o ambiente e rode o mesmo comando para retomar.",
        ),
    }

    def render_fields(key: str) -> dict[str, str]:
        if key == "engine.state.run_missing":
            return {"run_id": "run-1"}
        if key == "engine.workers.tier_cooldown":
            return {"tier": "low-cost", "harness": "codex"}
        if key == "engine.worktree.infrastructure_message":
            return {"prefix": t("engine.worktree.infrastructure_prefix"), "detail": "setup failed"}
        return {}

    set_engine_language("en")
    for key, (english, portuguese) in keys.items():
        assert t(key, **render_fields(key)) == english

    set_engine_language("pt-BR")
    for key, (english, portuguese) in keys.items():
        fields = render_fields(key)
        if key == "engine.worktree.infrastructure_message":
            fields["prefix"] = GATE_INFRASTRUCTURE_PREFIX
        assert t(key, **fields) == portuguese


def test_primary_engine_errors_use_active_language(set_engine_language, tmp_path):
    config = SimpleNamespace(gate=SimpleNamespace(python="missing-python"))
    gate = DeterministicGate(str(tmp_path), config=config)
    set_engine_language("en")
    with pytest.raises(FileNotFoundError, match="gate.python not found"):
        gate._resolve_python()
    set_engine_language("pt-BR")
    with pytest.raises(FileNotFoundError, match="gate.python não encontrado"):
        gate._resolve_python()

    set_engine_language("en")
    with pytest.raises(ValueError, match="Unknown dependency"):
        task_fingerprint({"id": "task"}, {"task": {"id": "task", "depends_on": ["missing"]}})
    set_engine_language("pt-BR")
    with pytest.raises(ValueError, match="Dependência desconhecida"):
        task_fingerprint({"id": "task"}, {"task": {"id": "task", "depends_on": ["missing"]}})

    empty_config = SimpleNamespace(
        workers=SimpleNamespace(tier_order=[], disabled=[]),
    )
    set_engine_language("en")
    with pytest.raises(UnknownTierError, match="Unknown lane"):
        resolve_worker_harness_and_model("unknown", config=empty_config)
    set_engine_language("pt-BR")
    with pytest.raises(UnknownTierError, match="Via desconhecida"):
        resolve_worker_harness_and_model("unknown", config=empty_config)


def test_infrastructure_prefix_is_recognized_in_both_languages(set_engine_language):
    for language, prefix in (
        ("en", "GATE INFRASTRUCTURE ERROR:"),
        ("pt-BR", GATE_INFRASTRUCTURE_PREFIX),
    ):
        set_engine_language(language)
        from meister.worktree import IntegrationPipeline

        message = IntegrationPipeline._gate_infrastructure_message("environment unavailable")
        assert str(message).startswith(prefix)
        assert is_infrastructure_error(message)
        assert is_infrastructure_error(f"{prefix} legacy error")
