"""
meister.hooks — Gerenciador de instalação e desinstalação de hooks.

Instala:
1. Git Pre-Commit Hook (.git/hooks/pre-commit)
2. Claude Code Lifecycle Hooks (.claude/hooks/ ou ~/.claude/hooks/)
"""

import json
import os
import stat
from typing import Tuple

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def install_git_hook(repo_path: str = ".") -> Tuple[bool, str]:
    """Instala o hook de pre-commit do MeisterRouter em um repositório git."""
    git_dir = os.path.join(repo_path, ".git")
    if not os.path.isdir(git_dir):
        return False, f"O diretório '{repo_path}' não é um repositório Git (.git ausente)."

    hooks_dir = os.path.join(git_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    target_hook = os.path.join(hooks_dir, "pre-commit")

    template_path = os.path.join(TEMPLATES_DIR, "git_pre_commit.sh.template")
    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()

    with open(target_hook, "w", encoding="utf-8") as f:
        f.write(content)

    # Torna o script executável
    st = os.stat(target_hook)
    os.chmod(target_hook, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return True, f"Hook Git pre-commit instalado com sucesso em: {target_hook}"


def install_claude_hook(target_dir: str = ".") -> Tuple[bool, str]:
    """Instala hooks de ciclo de vida e guardiões determinísticos para o Claude Code."""
    claude_dir = os.path.join(target_dir, ".claude")
    claude_hooks_dir = os.path.join(claude_dir, "hooks")
    os.makedirs(claude_hooks_dir, exist_ok=True)

    # 1. Hook de Prompt (UserPromptSubmit): Injeta diretivas determinísticas no contexto
    prompt_hook_path = os.path.join(claude_hooks_dir, "meister-prompt-hook.sh")
    prompt_template = os.path.join(TEMPLATES_DIR, "claude_prompt_hook.sh.template")
    if os.path.exists(prompt_template):
        with open(prompt_template, "r", encoding="utf-8") as f:
            content = f.read()
        with open(prompt_hook_path, "w", encoding="utf-8") as f:
            f.write(content)
        st = os.stat(prompt_hook_path)
        os.chmod(prompt_hook_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # 2. Hook Guardião (PreToolUse): Bloqueia chamadas de Edit/Write com Exit Code 2
    guard_hook_path = os.path.join(claude_hooks_dir, "meister-guard-hook.sh")
    guard_template = os.path.join(TEMPLATES_DIR, "claude_guard_hook.sh.template")
    if os.path.exists(guard_template):
        with open(guard_template, "r", encoding="utf-8") as f:
            content = f.read()
        with open(guard_hook_path, "w", encoding="utf-8") as f:
            f.write(content)
        st = os.stat(guard_hook_path)
        os.chmod(guard_hook_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # 3. Hook legado de telemetria
    legacy_hook_path = os.path.join(claude_hooks_dir, "meister-agent-hook.sh")
    legacy_template = os.path.join(TEMPLATES_DIR, "claude_hook.sh.template")
    if os.path.exists(legacy_template):
        with open(legacy_template, "r", encoding="utf-8") as f:
            content = f.read()
        with open(legacy_hook_path, "w", encoding="utf-8") as f:
            f.write(content)
        st = os.stat(legacy_hook_path)
        os.chmod(legacy_hook_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # 4. Configurar .claude/settings.json
    settings_file = os.path.join(claude_dir, "settings.json")
    settings = {}
    if os.path.exists(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            settings = {}

    hooks_cfg = settings.setdefault("hooks", {})
    hooks_cfg["UserPromptSubmit"] = [
        {
            "type": "command",
            "command": "bash .claude/hooks/meister-prompt-hook.sh"
        }
    ]
    hooks_cfg["PreToolUse"] = [
        {
            "matcher": "Edit|Write|MultiEdit|NotebookEdit",
            "hooks": [
                {
                    "type": "command",
                    "command": "bash .claude/hooks/meister-guard-hook.sh"
                }
            ]
        }
    ]

    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return True, f"Hooks Claude Code (Prompt + PreToolUse Guard) instalados em: {claude_hooks_dir} e {settings_file}"
