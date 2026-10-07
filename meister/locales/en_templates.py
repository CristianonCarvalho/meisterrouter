"""English init and hook template messages."""

MESSAGES: dict[str, str] = {
    "templates.hooks.git_not_repo": "Directory '{repo_path}' is not a Git repository (.git is missing).",
    "templates.hooks.git_foreign": "Existing pre-commit hook preserved (not managed by MeisterRouter): {target_hook}",
    "templates.hooks.git_installed": "Git pre-commit hook installed at: {target_hook}",
    "templates.hooks.claude_foreign": "Existing hook preserved (not managed by MeisterRouter): {hook_path}",
    "templates.hooks.settings_unchanged": "Existing configuration was not changed ({settings_file}): {error}",
    "templates.hooks.invalid_settings": "Existing configuration is invalid; hooks not installed: {settings_file}",
    "templates.hooks.invalid_section": "Existing hooks section is invalid; hooks not installed: {settings_file}",
    "templates.hooks.invalid_event": "Existing {event_name} configuration is invalid; hooks not installed.",
    "templates.hooks.claude_installed": (
        "Claude Code hooks installed at: {claude_hooks_dir} and {settings_file}\n"
        "Guard is in `block` mode (default). To request confirmation for each code edit: "
        "`echo ask > .meister/guard_mode`."
    ),
}
