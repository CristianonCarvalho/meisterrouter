import os
import tempfile
import subprocess
import pytest

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
