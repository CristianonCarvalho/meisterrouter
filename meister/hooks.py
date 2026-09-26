"""
meister.hooks — Gerenciador de instalação e desinstalação de hooks.

Instala:
1. Git Pre-Commit Hook (.git/hooks/pre-commit)
2. Claude Code Lifecycle Hooks (.claude/hooks/ ou ~/.claude/hooks/)
"""

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
    """Instala hooks de ciclo de vida para o Claude Code."""
    claude_hooks_dir = os.path.join(target_dir, ".claude", "hooks")
    os.makedirs(claude_hooks_dir, exist_ok=True)
    target_hook = os.path.join(claude_hooks_dir, "meister-agent-hook.sh")

    template_path = os.path.join(TEMPLATES_DIR, "claude_hook.sh.template")
    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()

    with open(target_hook, "w", encoding="utf-8") as f:
        f.write(content)

    st = os.stat(target_hook)
    os.chmod(target_hook, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return True, f"Hook Claude Code instalado com sucesso em: {target_hook}"
