import os
from unittest.mock import patch, MagicMock

from meister.worker import (
    HARNESS_COPILOT,
    resolve_worker_harness_and_model,
    find_cli_binary,
    build_harness_command,
    smoke_test_tier,
    build_safe_worker_env,
    HarnessWorker,
)
from meister.config import WorkerTier, MeisterConfig
from meister.herdr.workers import WorkerSpawner


def test_copilot_harness_resolution():
    """Verifica que aliases do Copilot resolvem para HARNESS_COPILOT e modelo configurado."""
    harness, model = resolve_worker_harness_and_model("copilot")
    assert harness == HARNESS_COPILOT
    assert model == "auto"

    harness2, _ = resolve_worker_harness_and_model("github-copilot")
    assert harness2 == HARNESS_COPILOT

    harness3, _ = resolve_worker_harness_and_model("gh-copilot")
    assert harness3 == HARNESS_COPILOT


def test_copilot_configured_model(monkeypatch):
    """Verifica que variável de ambiente MEISTER_COPILOT_MODEL é respeitada."""
    monkeypatch.setenv("MEISTER_COPILOT_MODEL", "gpt-5.4")
    harness, model = resolve_worker_harness_and_model("copilot")
    assert harness == HARNESS_COPILOT
    assert model == "gpt-5.4"


def test_copilot_find_cli_binary():
    """Verifica localização do executável copilot no PATH do sistema."""
    bin_path = find_cli_binary(HARNESS_COPILOT)
    assert bin_path is not None
    assert "copilot" in bin_path
    assert os.path.exists(bin_path)


def test_copilot_smoke_test():
    """Verifica o smoke test do tier copilot."""
    smoke = smoke_test_tier("copilot")
    assert smoke["tier"] == "copilot"
    assert smoke["harness"] == HARNESS_COPILOT
    assert smoke["available"] is True
    assert smoke["cli_binary"] is not None


def test_copilot_build_harness_command_default():
    """Verifica construção do comando CLI sem model explícito (usa defaults seguros e não interativos)."""
    cmd = build_harness_command(
        harness=HARNESS_COPILOT,
        cli_bin="/usr/local/bin/copilot",
        model="auto",
        prompt="Fix the memory leak in auth module",
        cwd="/workspace/project",
    )
    assert cmd == [
        "/usr/local/bin/copilot",
        "-p",
        "Fix the memory leak in auth module",
        "--allow-all",
        "--no-ask-user",
    ]


def test_copilot_build_harness_command_custom_model():
    """Verifica construção do comando CLI com modelo explícito fornecido."""
    cmd = build_harness_command(
        harness=HARNESS_COPILOT,
        cli_bin="/usr/local/bin/copilot",
        model="claude-3.7-sonnet",
        prompt="Refactor database queries",
        cwd="/workspace/project",
    )
    assert cmd == [
        "/usr/local/bin/copilot",
        "-p",
        "Refactor database queries",
        "--allow-all",
        "--no-ask-user",
        "--model",
        "claude-3.7-sonnet",
    ]


def test_copilot_spawner_resolve_command():
    """Verifica que o WorkerSpawner constrói o comando correto para Herdr pane/tab com o harness copilot."""
    spawner = WorkerSpawner(config=MeisterConfig())
    tier = WorkerTier(name="copilot", harness="copilot", model="gpt-5.4")

    cmd = spawner.resolve_command(
        tier=tier,
        task_context={"description": "Implement authentication endpoints"},
    )
    assert cmd == [
        "copilot",
        "-p",
        "Implement authentication endpoints",
        "--allow-all",
        "--no-ask-user",
        "--model",
        "gpt-5.4",
    ]


def test_copilot_safe_env_vars(monkeypatch):
    """Verifica que credenciais GitHub/Copilot são propagadas e OpenRouter estritamente isolado (Achado #29)."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret123")
    monkeypatch.setenv("GH_TOKEN", "ghp_secret456")
    monkeypatch.setenv("COPILOT_API_KEY", "copilot_key_789")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-must-be-stripped")

    safe_env = build_safe_worker_env()
    assert safe_env.get("GITHUB_TOKEN") == "ghp_secret123"
    assert safe_env.get("GH_TOKEN") == "ghp_secret456"
    assert safe_env.get("COPILOT_API_KEY") == "copilot_key_789"
    assert "OPENROUTER_API_KEY" not in safe_env


def test_copilot_harness_worker_run_task(tmp_path):
    """Verifica execução de tarefa no HarnessWorker com o harness copilot mockado."""
    worker = HarnessWorker(model="copilot", cwd=str(tmp_path))
    assert worker.harness == HARNESS_COPILOT

    with patch("subprocess.Popen") as mock_popen, patch(
        "meister.worker.get_git_status_files", side_effect=[set(), {"auth.py"}]
    ):
        process_mock = MagicMock()
        process_mock.stdout = ["Implementing changes with Copilot CLI...\n", "Done.\n"]
        process_mock.wait.return_value = 0
        mock_popen.return_value = process_mock

        result = worker.run_task(task="Fix auth bug", target_files=["auth.py"])

        assert result["status"] == "done"
        assert result["exit_code"] == 0
        assert result["harness"] == HARNESS_COPILOT
        assert "auth.py" in result["modified_files"]
        assert mock_popen.called
        call_args = mock_popen.call_args[0][0]
        assert "-p" in call_args
        assert "--allow-all" in call_args
        assert "--no-ask-user" in call_args


def test_copilot_excluded_from_default_tiers_and_opt_in(monkeypatch):
    from meister.config import load_config
    from pathlib import Path

    # Default configuration must NOT include copilot in tier order because CLI flags are unverified
    monkeypatch.delenv("MEISTER_ENABLE_COPILOT", raising=False)
    default_config = load_config()
    tier_names = [t.name for t in default_config.workers.tier_order]
    assert "copilot" not in tier_names, "Copilot must be excluded from default worker tiers until verified"

    # Explicit opt-in via MEISTER_ENABLE_COPILOT=true includes copilot
    monkeypatch.setenv("MEISTER_ENABLE_COPILOT", "true")
    opt_in_config = load_config()
    opt_in_tier_names = [t.name for t in opt_in_config.workers.tier_order]
    assert "copilot" in opt_in_tier_names, "Copilot must be included when MEISTER_ENABLE_COPILOT is set"

    # Verify documentation and source warnings regarding unverified flags
    repo_root = Path(__file__).resolve().parent.parent
    readme_content = (repo_root / "README.md").read_text(encoding="utf-8")
    assert "NÃO VERIFICADO" in readme_content
    assert "--allow-all" in readme_content

    worker_src = (repo_root / "meister" / "worker.py").read_text(encoding="utf-8")
    assert "NÃO VERIFICADAS" in worker_src
