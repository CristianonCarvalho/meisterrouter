"""Mensagens em português do Brasil para configuração das vias."""

MESSAGES: dict[str, str] = {
    "tiers.invalid_effort": (
        "A via '{lane}' usa o harness '{harness}'; effort '{effort}' é inválido. "
        "Valores aceitos: {values}"
    ),
    "tiers.unsupported_effort": (
        "A via '{lane}' usa o harness '{harness}', que não aceita effort "
        "(valores aceitos: nenhum); remova a configuração de effort."
    ),
    "tiers.unknown_enabled_lane": "workers.enabled referencia a via desconhecida '{lane}'; a entrada será ignorada.",
    "tiers.enabled_name_string": "Os nomes das vias em workers.enabled devem ser strings.",
    "tiers.models.enable_help": "Ativa uma via configurada (pode ser repetida).",
    "tiers.models.disable_help": "Desativa uma via configurada (pode ser repetida).",
    "tiers.models.effort": "ESFORÇO",
    "tiers.last_enabled_lane": (
        "Não é possível desativar a última via ativa; pelo menos uma via deve permanecer ativa."
    ),
    "tiers.unknown_lane": "Via(s) desconhecida(s): {lanes}. Nomes válidos: {valid}",
    "tiers.conflicting_toggle": "Não é possível ativar e desativar a mesma via: {lanes}",
    "tiers.toggle_error": "Não foi possível atualizar as vias ativas: {error}",
    "tiers.invalid_configuration": "Não é possível atualizar as vias porque a configuração é inválida:",
    "tiers.configuration_issue": "{path}: {message}",
}
