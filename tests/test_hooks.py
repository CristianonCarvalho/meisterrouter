import json
import os
import stat
import subprocess
import tempfile

import pytest

from meister.hooks import install_git_hook, install_claude_hook


def _run_guard(project, event, mode=None, env_overrides=None):
    install_claude_hook(str(project))
    guard_hook = project / ".claude" / "hooks" / "meister-guard-hook.sh"
    if mode is not None:
        meister_dir = project / ".meister"
        meister_dir.mkdir(exist_ok=True)
        (meister_dir / "guard_mode").write_text(mode, encoding="utf-8")

    env = dict(os.environ)
    env.pop("MEISTER_IN_PANE", None)
    env.pop("MEISTER_ALLOW_ORCHESTRATOR_EDIT", None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(guard_hook)],
        cwd=project,
        env=env,
        input=event,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
    )


def _tool_event(path, tool_name="Edit", path_key="file_path"):
    return json.dumps({"tool_name": tool_name, "tool_input": {path_key: str(path)}})


def test_install_claude_hook_creates_files_and_config(monkeypatch):
    monkeypatch.delenv("MEISTER_IN_PANE", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        ok, msg = install_claude_hook(tmpdir)
        assert ok is True
        assert "Guard no modo `block` (padrão)" in msg
        assert "echo ask > .meister/guard_mode" in msg

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
        # cwd no projeto temporário: o hook lê .meister/guard_mode do diretório atual
        res = subprocess.run(["bash", guard_hook], cwd=tmpdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert res.returncode == 2
        assert "Edição direta de código bloqueada" in res.stderr

        # Run prompt hook in subprocess to verify stdout injection
        res_p = subprocess.run(["bash", prompt_hook], cwd=tmpdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert res_p.returncode == 0
        assert "[MEISTERROUTER MANDATORY DIRECTIVE]" in res_p.stdout


@pytest.mark.parametrize("mode", [None, "", "block", "talvez"])
def test_guard_blocks_code_for_default_explicit_and_unknown_modes(tmp_path, mode):
    result = _run_guard(tmp_path, _tool_event("src/app.py"), mode=mode)

    assert result.returncode == 2
    assert "Edição direta de código bloqueada" in result.stderr
    assert "meister orchestrate" in result.stderr
    assert result.stdout == ""


def test_guard_ask_returns_claude_permission_json(tmp_path):
    result = _run_guard(tmp_path, _tool_event("src/app.py"), mode="ASK please")

    assert result.returncode == 0
    response = json.loads(result.stdout)
    output = response["hookSpecificOutput"]
    assert output["permissionDecision"] == "ask"
    assert "meister orchestrate" in output["permissionDecisionReason"]
    assert result.stderr == ""


def test_guard_off_allows_code_silently(tmp_path):
    result = _run_guard(tmp_path, _tool_event("src/app.py"), mode="off")

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    "path",
    ["docs/guia.txt", "README.md", "pkg/NOTES.md", "docs/guia.mdx", "docs/absolute.txt"],
)
def test_guard_allows_documentation_silently_even_in_block_mode(tmp_path, path):
    doc_path = tmp_path / path
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_guard(tmp_path, _tool_event(doc_path), mode="block")

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize("path", ["config.yaml", "docs_not/x.py"])
def test_guard_does_not_allow_non_documentation_paths(tmp_path, path):
    result = _run_guard(tmp_path, _tool_event(path), mode="block")

    assert result.returncode == 2
    assert "Edição direta de código bloqueada" in result.stderr


def test_guard_treats_notebook_edit_as_code(tmp_path):
    result = _run_guard(
        tmp_path,
        _tool_event("notebooks/example.ipynb", tool_name="NotebookEdit", path_key="notebook_path"),
        mode="block",
    )

    assert result.returncode == 2
    assert "Edição direta de código bloqueada" in result.stderr


@pytest.mark.parametrize("event", ["", "{invalid json"])
def test_guard_fails_closed_for_empty_or_invalid_stdin(tmp_path, event):
    result = _run_guard(tmp_path, event, mode="block")

    assert result.returncode == 2
    assert "Edição direta de código bloqueada" in result.stderr


@pytest.mark.parametrize(
    "env_overrides,create_file",
    [
        ({"MEISTER_IN_PANE": "1"}, False),
        ({"MEISTER_ALLOW_ORCHESTRATOR_EDIT": "1"}, False),
        ({}, True),
    ],
)
def test_guard_bypasses_for_workers_and_explicit_override(tmp_path, env_overrides, create_file):
    if create_file:
        meister_dir = tmp_path / ".meister"
        meister_dir.mkdir()
        (meister_dir / "allow_orchestrator").touch()
    result = _run_guard(tmp_path, _tool_event("src/app.py"), mode="block", env_overrides=env_overrides)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_guard_hook_allow_orchestrator_override(monkeypatch):
    monkeypatch.delenv("MEISTER_IN_PANE", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        install_claude_hook(tmpdir)
        guard_hook = os.path.join(tmpdir, ".claude", "hooks", "meister-guard-hook.sh")

        # With MEISTER_ALLOW_ORCHESTRATOR_EDIT=1
        env = dict(os.environ, MEISTER_ALLOW_ORCHESTRATOR_EDIT="1")
        res = subprocess.run(["bash", guard_hook], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert res.returncode == 0


def test_installed_hooks_use_lf_line_endings(tmp_path):
    (tmp_path / ".git").mkdir()
    assert install_git_hook(str(tmp_path))[0] is True
    assert install_claude_hook(str(tmp_path))[0] is True

    written = [
        tmp_path / ".git" / "hooks" / "pre-commit",
        tmp_path / ".claude" / "hooks" / "meister-prompt-hook.sh",
        tmp_path / ".claude" / "hooks" / "meister-guard-hook.sh",
        tmp_path / ".claude" / "hooks" / "meister-agent-hook.sh",
    ]
    for hook in written:
        assert b"\r" not in hook.read_bytes(), hook


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


def _guard_exit_code(tmp_path, file_path):
    import json
    import subprocess

    from meister.hooks import TEMPLATES_DIR

    script = os.path.join(TEMPLATES_DIR, "pt-BR", "claude_guard_hook.sh.template")
    event = {"tool_name": "Edit", "tool_input": {"file_path": file_path}}
    env = dict(os.environ)
    env.pop("MEISTER_IN_PANE", None)
    env.pop("MEISTER_ALLOW_ORCHESTRATOR_EDIT", None)
    return subprocess.run(
        ["bash", script], input=json.dumps(event), text=True, cwd=tmp_path, env=env,
        capture_output=True, timeout=10,
    ).returncode


def test_guard_hook_dot_dot_components_never_count_as_documentation(tmp_path):
    """`docs/../src/app.py` casaria com `docs/*`; um `..` no caminho vira edição de código (bloqueada)."""
    for file_path in (
        "docs/../src/app.py",
        "docs/a/../../src/app.py",
        "src/../docs/x.md",
        "../fora/leia.md",
        "docs/..",
    ):
        assert _guard_exit_code(tmp_path, file_path) == 2, file_path
    for file_path in ("docs/guia.md", "./docs/ok.md", "README.md", "docs/a/b/c.txt"):
        assert _guard_exit_code(tmp_path, file_path) == 0, file_path
