import os
import tempfile
import subprocess

BIN_MEISTER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin", "meister"))

def test_cli_models():
    res = subprocess.run([BIN_MEISTER, "models"], stdout=subprocess.PIPE, text=True)
    assert res.returncode == 0
    assert "GPT-6 Luna" in res.stdout
    assert "Gemini 3.8 Flash" in res.stdout

def test_cli_init():
    with tempfile.TemporaryDirectory() as tmpdir:
        res = subprocess.run(
            [BIN_MEISTER, "init", "--target", tmpdir, "--no-hooks"],
            stdout=subprocess.PIPE,
            text=True
        )
        assert res.returncode == 0
        assert os.path.exists(os.path.join(tmpdir, "CLAUDE.md"))
        assert os.path.exists(os.path.join(tmpdir, "CODEX.md"))
        assert os.path.exists(os.path.join(tmpdir, "AGENTS.md"))
        assert os.path.exists(os.path.join(tmpdir, ".meister", "logs"))


def test_cli_control_auto_close_worker(tmp_path):
    from unittest.mock import patch, AsyncMock, MagicMock
    from click.testing import CliRunner
    from meister.cli import main

    runner = CliRunner()
    meister_dir = tmp_path / ".meister"
    meister_dir.mkdir()
    pane_file = meister_dir / "active_worker_pane.txt"
    pane_file.write_text("w7:p9")

    mock_client = MagicMock()
    mock_client.close_pane = AsyncMock()

    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("meister.cli.control_cycle", return_value={"action": "COMPLETE", "action_confidence": 0.99}), \
         patch("meister.cli.get_herdr_client", return_value=mock_client):
        res = runner.invoke(main, ["control", "-d", "diff", "-r", "pass"])
        assert res.exit_code == 0
        assert "fechado automaticamente após aprovação" in res.output
        mock_client.close_pane.assert_called_once_with("w7:p9")
        assert not pane_file.exists()


def test_cli_worker_worktree_isolation_and_integration(tmp_path, monkeypatch):
    """E2E-3: meister worker executa em worktree isolado e aplica pipeline (gate -> merge -> ff)."""
    from unittest.mock import patch
    from click.testing import CliRunner
    from meister.cli import main

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    cwd = str(repo_dir)

    subprocess.run(["git", "init", "-b", "main"], cwd=cwd, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@meisterrouter.local"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.name", "Meister CI"], cwd=cwd, check=True)

    calc_file = repo_dir / "calc.py"
    calc_file.write_text("def add(a, b):\n    return a + b\n")

    tests_dir = repo_dir / "tests"
    tests_dir.mkdir()
    test_calc = tests_dir / "test_calc.py"
    test_calc.write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")

    pyproject = repo_dir / "pyproject.toml"
    pyproject.write_text("[tool.pytest.ini_options]\npythonpath = [\".\"]\n")

    subprocess.run(["git", "add", "."], cwd=cwd, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=cwd, check=True)

    captured_worktree_cwds = []

    def fake_execute_worker_task(model, task, target_files, cwd, config_path=None):
        captured_worktree_cwds.append(cwd)
        # Escreve alterações no diretório passado para o worker (worktree isolado)
        with open(os.path.join(cwd, "calc.py"), "a", encoding="utf-8") as f:
            f.write("\ndef mul(a, b):\n    return a * b\n")
        with open(os.path.join(cwd, "tests", "test_calc.py"), "a", encoding="utf-8") as f:
            f.write("from calc import mul\n\ndef test_mul():\n    assert mul(2, 3) == 6\n")
        return {
            "status": "done",
            "modified_files": ["calc.py", "tests/test_calc.py"],
        }

    runner = CliRunner()
    with patch("meister.worker.is_herdr_available", return_value=False), \
         patch("meister.worker.execute_worker_task", side_effect=fake_execute_worker_task):
        res = runner.invoke(
            main,
            [
                "worker",
                "--model", "codex_luna",
                "--task", "Adicione a função mul e teste",
                "--files", "calc.py,tests/test_calc.py",
                "--cwd", cwd,
                "--no-pane",
            ],
        )
        assert res.exit_code == 0, f"Command failed: {res.output}"
        assert "Worker task finished" in res.output

    # Verifica que o worker executou estritamente em worktree isolado (não no repo principal)
    assert len(captured_worktree_cwds) == 1
    assert captured_worktree_cwds[0] != cwd
    assert "worker_" in captured_worktree_cwds[0]

    # Verifica que no repositório principal a branch main recebeu o commit e fast-forward
    log_res = subprocess.run(["git", "log", "--oneline", "--all"], cwd=cwd, capture_output=True, text=True)
    assert "Merge subtask" in log_res.stdout or "worker(codex_luna): Adicione a função mul e teste" in log_res.stdout

    # Verifica que o repositório principal está limpo (sem modificações unstaged)
    status_res = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True)
    assert status_res.stdout.strip() == ""

    # Verifica que o código e o teste foram integrados no repo principal
    assert "def mul" in (repo_dir / "calc.py").read_text()
    assert "def test_mul" in (repo_dir / "tests" / "test_calc.py").read_text()

    # Verifica que não ficaram worktrees órfãos
    wt_res = subprocess.run(["git", "worktree", "list"], cwd=cwd, capture_output=True, text=True)
    assert len(wt_res.stdout.strip().splitlines()) == 1


def test_e2e_correlation_and_telemetry_flow(tmp_path, monkeypatch):
    """E2E-6: Fluxo classify -> worker -> control propaga run_id e task_id, mede duração e emite worker_start/worker_end."""
    from unittest.mock import patch
    import json
    from click.testing import CliRunner
    from meister.cli import main
    from meister.logger import read_events

    log_dir = str(tmp_path / "logs")
    monkeypatch.setenv("MEISTER_LOG_DIR", log_dir)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    cwd = str(repo_dir)

    subprocess.run(["git", "init", "-b", "main"], cwd=cwd, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@meisterrouter.local"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.name", "Meister CI"], cwd=cwd, check=True)

    calc_file = repo_dir / "calc.py"
    calc_file.write_text("def add(a, b):\n    return a + b\n")
    tests_dir = repo_dir / "tests"
    tests_dir.mkdir()
    test_calc = tests_dir / "test_calc.py"
    test_calc.write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    pyproject = repo_dir / "pyproject.toml"
    pyproject.write_text("[tool.pytest.ini_options]\npythonpath = [\".\"]\n")

    subprocess.run(["git", "add", "."], cwd=cwd, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=cwd, check=True)

    runner = CliRunner()

    # 1. Classify
    res_cls = runner.invoke(main, ["classify", "--context", "Adicione a função mul"])
    assert res_cls.exit_code == 0
    cls_data = json.loads(res_cls.output)
    expected_run_id = cls_data["run_id"]
    expected_task_id = cls_data["task_id"]
    assert expected_run_id != "global"
    assert expected_task_id != ""

    # 2. Worker
    def fake_execute_worker_task(model, task, target_files, cwd, config_path=None):
        with open(os.path.join(cwd, "calc.py"), "a", encoding="utf-8") as f:
            f.write("\ndef mul(a, b):\n    return a * b\n")
        return {
            "status": "done",
            "modified_files": ["calc.py"],
            "exit_code": 0,
            "cost": 0.00005,
        }

    with patch("meister.worker.is_herdr_available", return_value=False), \
         patch("meister.worker.execute_worker_task", side_effect=fake_execute_worker_task):
        res_wrk = runner.invoke(
            main,
            [
                "worker",
                "--model", "codex_luna",
                "--task", "Adicione a função mul",
                "--files", "calc.py",
                "--cwd", cwd,
                "--no-pane",
            ],
        )
        assert res_wrk.exit_code == 0

    # 3. Control
    res_ctl = runner.invoke(
        main,
        [
            "control",
            "--diff-summary", "1 file changed",
            "--test-result", "pass",
            "--no-close-worker",
        ],
    )
    assert res_ctl.exit_code == 0

    # 4. Verificar eventos de telemetria
    events = read_events()
    assert len(events) == 4, f"Esperava 4 eventos, obteve {len(events)}: {[e['event'] for e in events]}"

    event_types = [e["event"] for e in events]
    assert event_types == ["classify", "worker_start", "worker_end", "control"]

    # Todos os 4 eventos devem compartilhar o mesmo run_id e mesmo task_id
    for ev in events:
        assert ev["run_id"] == expected_run_id
        assert ev["task_id"] == expected_task_id
        assert ev["duration_ms"] >= 0.0

    # Worker_start e worker_end
    start_ev = events[1]
    end_ev = events[2]
    assert start_ev["tier"] == "codex_luna"
    assert end_ev["tier"] == "codex_luna"
    assert end_ev["exit_code"] == 0
    assert end_ev["cost"] == 0.00005
    assert end_ev["duration_ms"] >= 0.0


def test_e2e_meister_gitignore_clean_git_status(tmp_path):
    """E2E-4: Diretório .meister possui .gitignore com '*' e não polui o git status."""
    from meister.config import ensure_meister_dir
    from meister.state import StateManager

    repo_dir = tmp_path / "repo_gi"
    repo_dir.mkdir()
    cwd = str(repo_dir)

    subprocess.run(["git", "init", "-b", "main"], cwd=cwd, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@meisterrouter.local"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.name", "Meister CI"], cwd=cwd, check=True)

    dummy_file = repo_dir / "app.py"
    dummy_file.write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=cwd, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=cwd, check=True)

    # Cria .meister e arquivos internos (db, runs, logs)
    m_dir = ensure_meister_dir(cwd)
    gi_path = os.path.join(m_dir, ".gitignore")
    assert os.path.exists(gi_path)
    with open(gi_path, "r", encoding="utf-8") as f:
        assert "*" in f.read()

    # Cria arquivos internos típicos
    (repo_dir / ".meister" / "runs").mkdir(exist_ok=True)
    (repo_dir / ".meister" / "runs" / "task1.json").write_text("{}")
    (repo_dir / ".meister" / "active_worker_pane.txt").write_text("w1:p1")

    # StateManager padrão no repo
    sm = StateManager(os.path.join(cwd, ".meister", "meister.db"))
    sm.close()

    # git status não deve apontar '?? .meister/'
    st = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True)
    assert ".meister" not in st.stdout
    assert st.stdout.strip() == ""


def test_cli_worker_defaults_to_tab_in_herdr(tmp_path):
    """E2E-9: meister worker despacha para aba dedicada no Herdr por padrão (tab.create), e split pane apenas sob demanda."""
    from unittest.mock import patch
    from click.testing import CliRunner
    from meister.cli import main

    runner = CliRunner()

    with patch("meister.worker.is_herdr_available", return_value=True), \
         patch("meister.worker.run_worker_in_herdr_tab", return_value={"status": "done", "modified_files": []}) as mock_tab, \
         patch("meister.worker.run_worker_in_herdr_pane", return_value={"status": "done", "modified_files": []}) as mock_pane:

        # 1. Padrão: sem flags de pane/split -> deve abrir TAB
        res_default = runner.invoke(main, ["worker", "--model", "codex_luna", "--task", "task 1", "--cwd", str(tmp_path)])
        assert res_default.exit_code == 0
        assert "aba dedicada no Herdr" in res_default.output
        mock_tab.assert_called_once()
        mock_pane.assert_not_called()

        mock_tab.reset_mock()
        mock_pane.reset_mock()

        # 2. Com --split ou --pane -> deve abrir PANE lateral
        res_split = runner.invoke(main, ["worker", "--model", "codex_luna", "--task", "task 2", "--cwd", str(tmp_path), "--split"])
        assert res_split.exit_code == 0
        assert "terminal lateral no Herdr" in res_split.output
        mock_pane.assert_called_once()
        mock_tab.assert_not_called()


