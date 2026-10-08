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
}
