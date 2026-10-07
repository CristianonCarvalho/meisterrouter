import re

from meister import env_setup, setup_cmd
from meister.clean import BranchPlan, CleanupPlan, render_result
from meister.config import MeisterConfig
from meister.i18n import reset_language_cache
from meister.plan_analysis import analyze_plan, format_analysis_table, serial_plan_warning
from meister.progress import format_event_line
from meister.timeline_cli import pick_run


_PORTUGUESE_ACCENTS = re.compile(r"[áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]")


def _outputs(language, monkeypatch, tmp_path):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()

    monkeypatch.setattr(setup_cmd.shutil, "which", lambda _name: None)

    class PluginProcess:
        stdout = "dev.meisterrouter.orchestrator v1"
        stderr = ""
        returncode = 0

    plugin_status = setup_cmd.ensure_herdr_plugin(runner=lambda _command: PluginProcess())[1]
    setup_messages = [
        setup_cmd.get_desired_bindings("/bin/meister")[0].description,
        setup_cmd.write_herdr_config(
            str(tmp_path / "config.toml"),
            setup_cmd.MergeResult("", {}, changed=False),
        )[1],
        setup_cmd.check_herdr().message,
        plugin_status,
        setup_cmd.generate_config_yaml_content(),
    ]

    clean_output = render_result(
        CleanupPlan(
            repo=str(tmp_path),
            base="main",
            apply=False,
            branches=[
                BranchPlan(
                    "meister/integration/example",
                    0,
                    "merged",
                    "apagar",
                    situation_code="merged",
                    action_code="delete",
                )
            ],
            archive_refs_preserved=0,
        )
    )

    tasks = [
        {"id": "one", "target_files": ["one.py"], "depends_on": []},
        {"id": "two", "target_files": ["two.py"], "depends_on": ["one"]},
        {"id": "three", "target_files": ["three.py"], "depends_on": ["two"]},
    ]
    analysis = analyze_plan(tasks, max_workers=4)
    analysis_output = format_analysis_table(analysis, max_workers=4)

    invalid_package = tmp_path / f"invalid-{language}"
    invalid_package.mkdir()
    (invalid_package / "package.json").write_text("{not json", encoding="utf-8")
    env_message = env_setup.prepare_environment(str(invalid_package), MeisterConfig())[1]

    try:
        pick_run([], None)
    except ValueError as error:
        no_runs_message = str(error)
    else:
        raise AssertionError("pick_run should reject an empty run list")

    try:
        pick_run([{"run_id": "abcdef123"}], "zzzzzz")
    except ValueError as error:
        missing_run_message = str(error)
    else:
        raise AssertionError("pick_run should reject an unknown run")

    progress_messages = [
        format_event_line(
            {"event_type": "plan_parsed", "run_id": "123456789", "total": 2, "batches": 1}
        ),
        format_event_line(
            {"event_type": "worker_spawn", "task_id": "task", "tier": "copilot"},
            1,
            3,
        ),
        format_event_line(
            {
                "event_type": "worker_retry",
                "task_id": "task",
                "tier": "copilot",
                "retry": 2,
                "max_retries": 3,
            },
            1,
            3,
        ),
        format_event_line(
            {
                "event_type": "worker_timeout",
                "task_id": "task",
                "tier": "copilot",
                "kind": "idle",
                "seconds": 600,
                "action": "retrying on the same lane",
            },
            1,
            3,
        ),
        format_event_line(
            {"event_type": "worker_error", "task_id": "task", "error": "failure details"},
            1,
            3,
        ),
    ]
    return setup_messages + [
        clean_output,
        serial_plan_warning(tasks),
        analysis_output,
        env_message,
        no_runs_message,
        missing_run_message,
        *progress_messages,
    ]


def test_command_messages_are_localized_and_portuguese_is_preserved(monkeypatch, tmp_path):
    english = _outputs("en", monkeypatch, tmp_path)
    portuguese = _outputs("pt-BR", monkeypatch, tmp_path)
    reset_language_cache()

    assert len(english) >= 10
    assert all(message is not None for message in english + portuguese)
    assert not any(_PORTUGUESE_ACCENTS.search(message) for message in english)

    assert english[0] == "MeisterRouter: orchestrate autonomous cycle"
    assert portuguese[0] == "MeisterRouter: orquestrar ciclo autônomo"
    assert english[1] == "no changes"
    assert portuguese[1] == "sem mudanças"
    assert english[2].startswith("Herdr not found on PATH.")
    assert portuguese[2].startswith("Herdr não encontrado no PATH.")
    assert english[3] == "Plugin dev.meisterrouter.orchestrator is already linked in Herdr"
    assert portuguese[3] == "Plugin dev.meisterrouter.orchestrator já vinculado no Herdr"
    assert "Local MeisterRouter configuration" in english[4]
    assert "Configuração local do MeisterRouter" in portuguese[4]
    assert "Nothing was changed. To apply:" in english[5]
    assert "Nada foi alterado. Para aplicar:" in portuguese[5]
    assert english[6] == "fully serial plan (3 tasks in 3 batches)"
    assert portuguese[6] == "plano totalmente serial (3 tarefas em 3 lotes)"
    assert "Plan analysis" in english[7]
    assert "Análise do plano" in portuguese[7]
    assert english[8] == "Could not determine the Node installation command"
    assert portuguese[8] == "Não foi possível determinar o comando de instalação Node"
    assert english[9] == "no runs available"
    assert portuguese[9] == "nenhum run disponível"
    assert "not found" in english[10]
    assert "inexistente" in portuguese[10]
    assert english[11] == "Plan: 2 tasks in 1 batches (run 12345678)"
    assert portuguese[11] == "Plano: 2 tarefas em 1 lotes (run 12345678)"
    assert english[12] == "[1/3] task started on copilot"
    assert portuguese[12] == "[1/3] task iniciada em copilot"
    assert english[13] == "[1/3] task pane lost; retry 2/3 on copilot"
    assert portuguese[13] == "[1/3] task pane perdido; retentativa 2/3 em copilot"
    assert english[14] == "[1/3] task idle timeout (600 s) on copilot; retrying on the same lane"
    assert portuguese[14] == "[1/3] task timeout por inatividade (600 s) em copilot; retrying on the same lane"
    assert english[15] == "[1/3] task FAILED: failure details"
    assert portuguese[15] == "[1/3] task FALHOU: failure details"
