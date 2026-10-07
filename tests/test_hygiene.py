import os
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]
from pathlib import Path
from click.testing import CliRunner
from meister.cli import main


def test_pyproject_toml_configuration():
    repo_root = Path(__file__).resolve().parent.parent
    pyproject_path = repo_root / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml must exist in repo root"

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    # Project metadata
    assert data["project"]["name"] == "meisterrouter"
    assert "version" in data["project"]["dynamic"]
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "meister.__version__"}
    assert data["project"]["scripts"]["meister"] == "meister.cli:main"

    deps = data["project"]["dependencies"]
    assert any("pydantic" in d for d in deps)
    assert any("click" in d for d in deps)
    assert any("PyYAML" in d for d in deps)

    dev_deps = data["project"]["optional-dependencies"]["dev"]
    assert any("pytest" in d for d in dev_deps)
    assert any("pytest-asyncio" in d for d in dev_deps)
    assert any("ruff" in d for d in dev_deps)
    assert any("mypy" in d for d in dev_deps)

    # Tool configurations
    assert "pytest" in data["tool"]
    assert "ruff" in data["tool"]
    assert "mypy" in data["tool"]


def test_requirements_and_setup_alignment():
    repo_root = Path(__file__).resolve().parent.parent
    req_path = repo_root / "requirements.txt"
    setup_path = repo_root / "setup.py"

    assert req_path.exists()
    assert setup_path.exists()

    req_content = req_path.read_text(encoding="utf-8")
    setup_content = setup_path.read_text(encoding="utf-8")

    for dep in ["pydantic", "pytest", "pytest-asyncio", "pytest-xdist", "ruff", "mypy", "PyYAML", "click"]:
        assert dep in req_content, f"Missing {dep} in requirements.txt"
        assert dep in setup_content, f"Missing {dep} in setup.py"


def test_documentation_pricing_and_catalog_alignment():
    repo_root = Path(__file__).resolve().parent.parent
    claude_md = (repo_root / "CLAUDE.md").read_text(encoding="utf-8")
    claude_template = (repo_root / "meister" / "templates" / "CLAUDE.md.template").read_text(encoding="utf-8")
    codex_md = (repo_root / "CODEX.md").read_text(encoding="utf-8")
    agents_md = (repo_root / "AGENTS.md").read_text(encoding="utf-8")

    # Repository guidance retains its current catalog; distributable templates stay generic.
    assert "$4.00" in claude_md
    assert "$1.54" not in claude_md
    assert "meister config show" in claude_template
    assert "meister models" in claude_template
    assert "meister.config.yaml" in claude_template
    assert "$" not in claude_template

    # Copilot harness presence in documentation
    assert "copilot" in claude_md
    assert "copilot" in codex_md
    assert "Copilot Harness" in agents_md or "copilot" in agents_md

    # Luna pricing consistency ($0.20, preço combinado 3:1 de 0.10 entrada / 0.50 saída)
    assert "$0.20" in claude_md
    assert "$0.20" in codex_md
    assert "$0.20" in agents_md


def test_daemon_pid_locking_race_prevention(tmp_path):
    runner = CliRunner()
    pid_file = tmp_path / "race_daemon.pid"

    # Start first daemon session with mock client
    from unittest.mock import AsyncMock

    mock_client = AsyncMock()
    mock_client.connect.return_value = None
    mock_client.subscribe_events.return_value = None

    # Simulate an active process holding a lock or holding the PID
    pid_file.write_text(f"{os.getpid()}\n")

    # Second invocation attempting to start with the same PID file must fail gracefully
    res = runner.invoke(main, ["daemon", "--start", "--pid-file", str(pid_file)])
    assert res.exit_code == 0
    assert "already running" in res.output


def test_mypy_floor_and_optional_narrowing():
    repo_root = Path(__file__).resolve().parent.parent
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    reqs = (repo_root / "requirements.txt").read_text(encoding="utf-8")
    setup = (repo_root / "setup.py").read_text(encoding="utf-8")

    # Mypy floor must be at least 1.10.0 to prevent Optional narrowing incompatibilities across versions
    assert "mypy>=1.10.0" in pyproject
    assert "mypy>=1.10.0" in reqs
    assert "mypy>=1.10.0" in setup
    assert "mypy>=1.8.0" not in pyproject

    # Verify logger and bridge narrow Optional variables to avoid mypy 1.10 errors
    logger_src = (repo_root / "meister" / "logger.py").read_text(encoding="utf-8")
    bridge_src = (repo_root / "meister" / "herdr" / "bridge.py").read_text(encoding="utf-8")

    assert 'resolved_model: str = str(model or "")' in logger_src
    assert 'run_id: str = str(run_record["run_id"])' in bridge_src
    assert "sm.transition_run(run_id, to_state=RunState.RUNNING)" in bridge_src


def test_test_environment_isolation_guard():
    """Guarda: a suíte de testes nunca deve apontar para o banco ou logs do repo ou de ~/.meister."""
    from meister.state import StateManager

    repo_root = str(Path(__file__).resolve().parent.parent)
    home_meister = os.path.expanduser("~/.meister")

    # Verifica StateManager padrão sob o ambiente de teste
    sm = StateManager()
    db_path = os.path.abspath(sm.db_path)
    assert not db_path.startswith(repo_root), f"DB path {db_path} deve estar isolado fora do repo {repo_root}"
    assert not db_path.startswith(home_meister), f"DB path {db_path} deve estar isolado fora de ~/.meister"

    # Verifica logger padrão sob o ambiente de teste
    log_dir = os.environ.get("MEISTER_LOG_DIR")
    assert log_dir is not None, "MEISTER_LOG_DIR deve estar definido pela fixture de teste"
    abs_log_dir = os.path.abspath(log_dir)
    assert not abs_log_dir.startswith(repo_root), f"Log dir {abs_log_dir} deve estar isolado fora do repo {repo_root}"
    assert not abs_log_dir.startswith(home_meister), f"Log dir {abs_log_dir} deve estar isolado fora de ~/.meister"

    # Verifica worktrees dir sob o ambiente de teste
    wt_dir = os.environ.get("MEISTER_WORKTREES_DIR")
    assert wt_dir is not None, "MEISTER_WORKTREES_DIR deve estar definido pela fixture de teste"
    abs_wt_dir = os.path.abspath(wt_dir)
    assert not abs_wt_dir.startswith(repo_root), f"WT dir {abs_wt_dir} deve estar isolado fora do repo {repo_root}"
    assert not abs_wt_dir.startswith(home_meister), f"WT dir {abs_wt_dir} deve estar isolado fora de ~/.meister"


def test_model_table_matches_catalog():
    """A tabela de estudo (docs/modelos_e_custos.csv) precisa bater com o catálogo de vias."""
    import csv

    import yaml

    repo_root = Path(__file__).resolve().parent.parent
    catalog = yaml.safe_load((repo_root / "meister" / "default_config.yaml").read_text(encoding="utf-8"))
    rows = list(csv.DictReader((repo_root / "docs" / "modelos_e_custos.csv").open(encoding="utf-8")))
    assert (repo_root / "docs" / "models-and-costs.md").is_file()
    assert (repo_root / "docs" / "pt-BR" / "MODELOS_E_CUSTOS.md").is_file()

    for tier in catalog["workers"]["tier_order"]:
        matching = [
            row for row in rows
            if tier["name"] in [via.strip() for via in row["vias_meister"].split(";")]
        ]
        assert matching, f"via {tier['name']} sem linha na tabela de modelos"
        for row in matching:
            assert float(row["combinado_3_1_usd_1m"]) == tier["cost_per_m_tokens"], (
                f"{tier['name']}: catálogo {tier['cost_per_m_tokens']} != tabela {row['combinado_3_1_usd_1m']}"
            )
            # o combinado precisa ser (3 * entrada + saída) / 4
            entrada, saida = float(row["entrada_usd_1m"]), float(row["saida_usd_1m"])
            assert round((3 * entrada + saida) / 4, 4) == float(row["combinado_3_1_usd_1m"])


def test_modules_that_use_cwd_task_files_are_isolated_for_parallel_runs():
    """Quem grava/lê arquivos de tarefa em <cwd>/.meister/runs precisa de cwd próprio: senão, em paralelo
    (pytest-xdist), um teste responde às tarefas dos outros e trava até o watchdog."""
    from tests.conftest import CWD_ISOLATED_MODULES

    tests_dir = Path(__file__).resolve().parent
    markers = ("auto_write_result", ".meister/runs", '".meister" / "runs"', "os.getcwd()")
    users = {
        path.stem
        for path in tests_dir.glob("test_*.py")
        if path.stem != "test_hygiene"
        and any(marker in path.read_text(encoding="utf-8") for marker in markers)
    }
    missing = users - CWD_ISOLATED_MODULES
    assert not missing, (
        f"adicione a CWD_ISOLATED_MODULES (tests/conftest.py) os módulos {sorted(missing)}: "
        "eles usam arquivos de tarefa no cwd compartilhado"
    )
