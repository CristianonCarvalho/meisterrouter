"""Catálogo de mensagens de modelos de init e hooks em português do Brasil."""

MESSAGES: dict[str, str] = {
    "templates.hooks.git_not_repo": "O diretório '{repo_path}' não é um repositório Git (.git ausente).",
    "templates.hooks.git_foreign": "Hook pre-commit existente preservado (não pertence ao MeisterRouter): {target_hook}",
    "templates.hooks.git_installed": "Hook Git pre-commit instalado em: {target_hook}",
    "templates.hooks.claude_foreign": "Hook existente preservado (não pertence ao MeisterRouter): {hook_path}",
    "templates.hooks.settings_unchanged": "Configuração existente não foi alterada ({settings_file}): {error}",
    "templates.hooks.invalid_settings": "Configuração existente inválida; hooks não instalados: {settings_file}",
    "templates.hooks.invalid_section": "Seção de hooks existente inválida; hooks não instalados: {settings_file}",
    "templates.hooks.invalid_event": "Configuração existente de {event_name} inválida; hooks não instalados.",
    "templates.hooks.claude_installed": (
        "Hooks Claude Code instalados em: {claude_hooks_dir} e {settings_file}\n"
        "Guard no modo `block` (padrão). Para pedir confirmação a cada edição de código: "
        "`echo ask > .meister/guard_mode`."
    ),
}
