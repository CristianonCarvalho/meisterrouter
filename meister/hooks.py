"""
meister.hooks — Gerenciador de instalação e desinstalação de hooks.

Instala:
1. Git Pre-Commit Hook (.git/hooks/pre-commit)
2. Claude Code Lifecycle Hooks (.claude/hooks/)
"""

import json
import os
import stat
import sys
from typing import Tuple

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
MANAGED_MARKER = "Managed by MeisterRouter"


def _language_templates_dir() -> str:
    from meister.i18n import get_language

    language = get_language()
    directory = "pt-BR" if language == "pt-BR" else "en"
    return os.path.join(TEMPLATES_DIR, directory)


def _template_content(filename: str) -> str:
    with open(os.path.join(_language_templates_dir(), filename), "r", encoding="utf-8") as template_file:
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
    # newline="\n" evita CRLF no Windows, que quebraria o bash ao executar o hook.
    with open(path, "w", encoding="utf-8", newline="\n") as hook_file:
        hook_file.write(content)
    if sys.platform != "win32":
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def install_git_hook(repo_path: str = ".", force: bool = False) -> Tuple[bool, str]:
    """Install the active-language hook without replacing unrelated hooks by default.

    Hook messages are fixed in the installed script; reinstall after changing language.
    """
    from meister.i18n import t

    git_dir = os.path.join(repo_path, ".git")
    if not os.path.isdir(git_dir):
        return False, t("templates.hooks.git_not_repo", repo_path=repo_path)

    hooks_dir = os.path.join(git_dir, "hooks")
    target_hook = os.path.join(hooks_dir, "pre-commit")
    content = _template_content("git_pre_commit.sh.template")
    if not force and not _is_managed_hook(target_hook, content):
        return False, t("templates.hooks.git_foreign", target_hook=target_hook)

    os.makedirs(hooks_dir, exist_ok=True)
    _write_executable(target_hook, content)
    return True, t("templates.hooks.git_installed", target_hook=target_hook)


def install_claude_hook(target_dir: str = ".", force: bool = False) -> Tuple[bool, str]:
    """Install active-language Claude hooks without replacing unrelated hooks by default.

    Hook messages are fixed in the installed scripts; reinstall after changing language.
    """
    from meister.i18n import t

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
            return False, t("templates.hooks.claude_foreign", hook_path=hook_path)
        hook_contents.append((hook_path, content))

    settings_file = os.path.join(claude_dir, "settings.json")
    settings = {}
    if os.path.exists(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as settings_handle:
                settings = json.load(settings_handle)
        except (OSError, json.JSONDecodeError) as error:
            return False, t(
                "templates.hooks.settings_unchanged",
                settings_file=settings_file,
                error=error,
            )
        if not isinstance(settings, dict):
            return False, t("templates.hooks.invalid_settings", settings_file=settings_file)

    hooks_cfg = settings.setdefault("hooks", {})
    if not isinstance(hooks_cfg, dict):
        return False, t("templates.hooks.invalid_section", settings_file=settings_file)
    for event_name in ("UserPromptSubmit", "PreToolUse"):
        if event_name in hooks_cfg and not isinstance(hooks_cfg[event_name], list):
            return False, t("templates.hooks.invalid_event", event_name=event_name)

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

    return True, t(
        "templates.hooks.claude_installed",
        claude_hooks_dir=claude_hooks_dir,
        settings_file=settings_file,
    )
