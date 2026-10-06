"""
meister.hooks — Gerenciador de instalação e desinstalação de hooks.

Instala:
1. Git Pre-Commit Hook (.git/hooks/pre-commit)
2. Claude Code Lifecycle Hooks (.claude/hooks/)
"""

import json
import os
import stat
from typing import Tuple

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
MANAGED_MARKER = "Managed by MeisterRouter"


def _template_content(filename: str) -> str:
    with open(os.path.join(TEMPLATES_DIR, filename), "r", encoding="utf-8") as template_file:
        return template_file.read()


def _is_managed_hook(path: str, template: str) -> bool:
    if not os.path.lexists(path):
        return True
    if os.path.islink(path):
        return False
    with open(path, "r", encoding="utf-8") as hook_file:
        content = hook_file.read()
    return MANAGED_MARKER in content or content == template


def _write_executable(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as hook_file:
        hook_file.write(content)
    mode = os.stat(path).st_mode
    os.chmod(path, mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def install_git_hook(repo_path: str = ".", force: bool = False) -> Tuple[bool, str]:
    """Instala o hook do MeisterRouter sem substituir hooks alheios por padrão."""
    git_dir = os.path.join(repo_path, ".git")
    if not os.path.isdir(git_dir):
        return False, f"O diretório '{repo_path}' não é um repositório Git (.git ausente)."

    hooks_dir = os.path.join(git_dir, "hooks")
    target_hook = os.path.join(hooks_dir, "pre-commit")
    content = _template_content("git_pre_commit.sh.template")
    if not force and not _is_managed_hook(target_hook, content):
        return False, f"Hook pre-commit existente preservado (não pertence ao MeisterRouter): {target_hook}"

    os.makedirs(hooks_dir, exist_ok=True)
    _write_executable(target_hook, content)
    return True, f"Hook Git pre-commit instalado em: {target_hook}"


def install_claude_hook(target_dir: str = ".", force: bool = False) -> Tuple[bool, str]:
    """Instala hooks do Claude Code sem substituir hooks alheios por padrão."""
    claude_dir = os.path.join(target_dir, ".claude")
    claude_hooks_dir = os.path.join(claude_dir, "hooks")
    hook_templates = (
        ("meister-prompt-hook.sh", "claude_prompt_hook.sh.template"),
        ("meister-guard-hook.sh", "claude_guard_hook.sh.template"),
        ("meister-agent-hook.sh", "claude_hook.sh.template"),
    )
    hook_contents = []
    for target_name, template_name in hook_templates:
        content = _template_content(template_name)
        hook_path = os.path.join(claude_hooks_dir, target_name)
        if not force and not _is_managed_hook(hook_path, content):
            return False, f"Hook existente preservado (não pertence ao MeisterRouter): {hook_path}"
        hook_contents.append((hook_path, content))

    settings_file = os.path.join(claude_dir, "settings.json")
    settings = {}
    if os.path.exists(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as settings_handle:
                settings = json.load(settings_handle)
        except (OSError, json.JSONDecodeError) as error:
            return False, f"Configuração existente não foi alterada ({settings_file}): {error}"
        if not isinstance(settings, dict):
            return False, f"Configuração existente inválida; hooks não instalados: {settings_file}"

    hooks_cfg = settings.setdefault("hooks", {})
    if not isinstance(hooks_cfg, dict):
        return False, f"Seção de hooks existente inválida; hooks não instalados: {settings_file}"
    for event_name in ("UserPromptSubmit", "PreToolUse"):
        if event_name in hooks_cfg and not isinstance(hooks_cfg[event_name], list):
            return False, f"Configuração existente de {event_name} inválida; hooks não instalados."

    prompt_config = {
        "type": "command",
        "command": "bash .claude/hooks/meister-prompt-hook.sh",
    }
    guard_config = {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit",
        "hooks": [
            {
                "type": "command",
                "command": "bash .claude/hooks/meister-guard-hook.sh",
            }
        ],
    }
    hooks_cfg.setdefault("UserPromptSubmit", [])
    hooks_cfg.setdefault("PreToolUse", [])
    if prompt_config not in hooks_cfg["UserPromptSubmit"]:
        hooks_cfg["UserPromptSubmit"].append(prompt_config)
    if guard_config not in hooks_cfg["PreToolUse"]:
        hooks_cfg["PreToolUse"].append(guard_config)

    os.makedirs(claude_hooks_dir, exist_ok=True)
    for hook_path, content in hook_contents:
        _write_executable(hook_path, content)
    with open(settings_file, "w", encoding="utf-8") as settings_handle:
        json.dump(settings, settings_handle, indent=2, ensure_ascii=False)
        settings_handle.write("\n")

    return (
        True,
        f"Hooks Claude Code instalados em: {claude_hooks_dir} e {settings_file}\n"
        "Guard no modo `block` (padrão). Para pedir confirmação a cada edição de código: "
        "`echo ask > .meister/guard_mode`.",
    )
