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
