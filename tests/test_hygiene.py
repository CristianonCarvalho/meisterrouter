import os
import tomllib
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
    assert data["project"]["version"] == "1.0.0"
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

    for dep in ["pydantic", "pytest", "pytest-asyncio", "ruff", "mypy", "PyYAML", "click"]:
        assert dep in req_content, f"Missing {dep} in requirements.txt"
        assert dep in setup_content, f"Missing {dep} in setup.py"


def test_documentation_pricing_and_catalog_alignment():
    repo_root = Path(__file__).resolve().parent.parent
    claude_md = (repo_root / "CLAUDE.md").read_text(encoding="utf-8")
    claude_template = (repo_root / "meister" / "templates" / "CLAUDE.md.template").read_text(encoding="utf-8")
    codex_md = (repo_root / "CODEX.md").read_text(encoding="utf-8")
    agents_md = (repo_root / "AGENTS.md").read_text(encoding="utf-8")

    # Sonnet pricing must be aligned to $3.00, not stale $1.54
    assert "$3.00" in claude_md
    assert "$1.54" not in claude_md
    assert "$3.00" in claude_template
    assert "$1.54" not in claude_template

    # Copilot harness presence in documentation
    assert "copilot" in claude_md
    assert "copilot" in codex_md
    assert "Copilot Harness" in agents_md or "copilot" in agents_md

    # Luna pricing consistency ($0.077)
    assert "$0.077" in claude_md
    assert "$0.077" in codex_md
    assert "$0.077" in agents_md


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
