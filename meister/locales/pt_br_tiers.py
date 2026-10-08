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
}
