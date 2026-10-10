import os
import sys
import pytest
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
    """Verifica que a via configurada do Copilot resolve para o adaptador e modelo declarados."""
    harness, model = resolve_worker_harness_and_model("tier_1b")
    assert harness == HARNESS_COPILOT
    assert model == "gpt-6-luna"
    with pytest.raises(ValueError, match="tier_1b"):
        resolve_worker_harness_and_model("copilot")


def test_copilot_model_environment_override_is_ignored(monkeypatch):
    monkeypatch.setenv("MEISTER_COPILOT_MODEL", "gpt-5.4")
    harness, model = resolve_worker_harness_and_model("tier_1b")
    assert harness == HARNESS_COPILOT
    assert model == "gpt-6-luna"


@pytest.fixture
def fake_copilot_cli(tmp_path, monkeypatch):
    """Cria executável falso que imita contrato mínimo de copilot (-p, --allow-all, --no-ask-user, exit 0)."""
    fake_bin_dir = tmp_path / "fake_bin"
    fake_bin_dir.mkdir(parents=True, exist_ok=True)
    fake_copilot = fake_bin_dir / "copilot"
    fake_copilot.write_text(
        "#!/bin/sh\n"
        "# Fake GitHub Copilot CLI\n"
        "exit 0\n"
    )
    fake_copilot.chmod(0o755)
    if sys.platform == "win32":
        # shutil.which só acha executáveis com extensão listada em PATHEXT.
        fake_copilot = fake_bin_dir / "copilot.cmd"
        fake_copilot.write_text("@echo off\r\nrem Fake GitHub Copilot CLI\r\nexit /b 0\r\n")

    orig_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{fake_bin_dir}{os.pathsep}{orig_path}")
    return str(fake_copilot)


def test_copilot_find_cli_binary(fake_copilot_cli):
    """Verifica localização do executável copilot no PATH do sistema."""
    bin_path = find_cli_binary(HARNESS_COPILOT)
    assert bin_path is not None
    assert "copilot" in bin_path
    assert os.path.exists(bin_path)
    assert bin_path == fake_copilot_cli


def test_copilot_find_cli_binary_not_found(monkeypatch):
    """Verifica comportamento determinístico quando copilot não está no sistema."""
    monkeypatch.setattr("shutil.which", lambda *args, **kwargs: None)
    orig_exists = os.path.exists
    monkeypatch.setattr(
        "os.path.exists",
        lambda p: False if any(n in str(p) for n in ("copilot", "github-copilot-cli")) else orig_exists(p),
    )
    assert find_cli_binary(HARNESS_COPILOT) is None


def test_copilot_smoke_test(fake_copilot_cli):
    """Verifica o smoke test do tier copilot quando o binário está disponível."""
    smoke = smoke_test_tier("tier_1b")
    assert smoke["tier"] == "tier_1b"
    assert smoke["harness"] == HARNESS_COPILOT
    assert smoke["available"] is True
    assert smoke["cli_binary"] == fake_copilot_cli


def test_copilot_smoke_test_when_not_available(monkeypatch):
    """Verifica o smoke test do tier copilot quando o binário não está disponível."""
    monkeypatch.setattr("shutil.which", lambda *args, **kwargs: None)
    orig_exists = os.path.exists
    monkeypatch.setattr(
        "os.path.exists",
        lambda p: False if any(n in str(p) for n in ("copilot", "github-copilot-cli")) else orig_exists(p),
    )
    smoke = smoke_test_tier("tier_1b")
    assert smoke["tier"] == "tier_1b"
    assert smoke["harness"] == HARNESS_COPILOT
    assert smoke["available"] is False
    assert smoke["cli_binary"] is None


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


def test_copilot_harness_worker_run_task(tmp_path, fake_copilot_cli):
    """Verifica execução de tarefa no HarnessWorker com o harness copilot mockado."""
    worker = HarnessWorker(model="tier_1b", cwd=str(tmp_path))
    assert worker.harness == HARNESS_COPILOT
    assert worker.cli_binary == fake_copilot_cli

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


def test_copilot_harness_worker_fails_when_cli_not_found(tmp_path, monkeypatch):
    """Verifica que o HarnessWorker levanta RuntimeError quando a CLI não é encontrada."""
    monkeypatch.setattr("shutil.which", lambda *args, **kwargs: None)
    orig_exists = os.path.exists
    monkeypatch.setattr(
        "os.path.exists",
        lambda p: False if any(n in str(p) for n in ("copilot", "github-copilot-cli")) else orig_exists(p),
    )
    worker = HarnessWorker(model="tier_1b", cwd=str(tmp_path))
    with pytest.raises(RuntimeError, match="não encontrado"):
        worker.run_task(task="Fix auth bug", target_files=["auth.py"])



def test_copilot_is_first_default_route_and_environment_cannot_change_it(monkeypatch):
    from meister.config import load_config
    from pathlib import Path

    monkeypatch.delenv("MEISTER_ENABLE_COPILOT", raising=False)
    default_config = load_config()
    tier_names = [t.name for t in default_config.workers.tier_order]
    assert tier_names[0] == "tier_1"

    monkeypatch.setenv("MEISTER_ENABLE_COPILOT", "true")
    opt_in_config = load_config()
    opt_in_tier_names = [t.name for t in opt_in_config.workers.tier_order]
    assert opt_in_tier_names == tier_names

    # Verify documentation and source confirm verified CLI status and flags
    repo_root = Path(__file__).resolve().parent.parent
    docs_content = (repo_root / "docs" / "advanced.md").read_text(encoding="utf-8")
    assert "STATUS: VERIFIED" in docs_content
    assert "--allow-all" in docs_content
    docs_pt = (repo_root / "docs" / "pt-BR" / "INSTALACAO_E_COMANDOS_AVANCADOS.md").read_text(encoding="utf-8")
    assert "STATUS: VERIFICADO" in docs_pt
    assert "--allow-all" in docs_pt

    worker_src = (repo_root / "meister" / "worker.py").read_text(encoding="utf-8")
    assert "verificado contra GitHub Copilot CLI" in worker_src
