import json
import os
import stat
import subprocess
import tempfile

from meister.hooks import install_git_hook, install_claude_hook


def test_install_claude_hook_creates_files_and_config(monkeypatch):
    monkeypatch.delenv("MEISTER_IN_PANE", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        ok, msg = install_claude_hook(tmpdir)
        assert ok is True

        claude_dir = os.path.join(tmpdir, ".claude")
        hooks_dir = os.path.join(claude_dir, "hooks")
        prompt_hook = os.path.join(hooks_dir, "meister-prompt-hook.sh")
        guard_hook = os.path.join(hooks_dir, "meister-guard-hook.sh")
        settings_file = os.path.join(claude_dir, "settings.json")

        assert os.path.exists(prompt_hook)
        assert os.path.exists(guard_hook)
        assert os.path.exists(settings_file)

        # Ensure executable permissions
        assert os.stat(prompt_hook).st_mode & stat.S_IEXEC
        assert os.stat(guard_hook).st_mode & stat.S_IEXEC

        # Check settings.json schema
        with open(settings_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        assert "hooks" in cfg
        assert "UserPromptSubmit" in cfg["hooks"]
        assert "PreToolUse" in cfg["hooks"]

        # Run guard hook in subprocess to verify Exit Code 2
        res = subprocess.run(["bash", guard_hook], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert res.returncode == 2
        assert "Edição direta de código bloqueada" in res.stderr

        # Run prompt hook in subprocess to verify stdout injection
        res_p = subprocess.run(["bash", prompt_hook], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert res_p.returncode == 0
        assert "[MEISTERROUTER MANDATORY DIRECTIVE]" in res_p.stdout


def test_guard_hook_allow_orchestrator_override(monkeypatch):
    monkeypatch.delenv("MEISTER_IN_PANE", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        install_claude_hook(tmpdir)
        guard_hook = os.path.join(tmpdir, ".claude", "hooks", "meister-guard-hook.sh")

        # With MEISTER_ALLOW_ORCHESTRATOR_EDIT=1
        env = dict(os.environ, MEISTER_ALLOW_ORCHESTRATOR_EDIT="1")
        res = subprocess.run(["bash", guard_hook], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert res.returncode == 0


def test_install_git_hook_fail_closed_without_llm():
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = os.path.join(tmpdir, ".git")
        os.makedirs(git_dir)

        ok, msg = install_git_hook(tmpdir)
        assert ok is True

        hook_file = os.path.join(git_dir, "hooks", "pre-commit")
        assert os.path.exists(hook_file)
        assert os.stat(hook_file).st_mode & stat.S_IEXEC

        with open(hook_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Must NOT delegate to Jev or have fail-open bypass
        assert '{"action": "COMPLETE"}' not in content
        assert "meister control" not in content
        assert "fail-closed" in content
