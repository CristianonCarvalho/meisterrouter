"""Catálogo em português do Brasil para mensagens de interrupção do orchestrate."""

MESSAGES: dict[str, str] = {
    "interrupt.reason": "Orquestração interrompida antes de concluir (Ctrl+C ou sinal de encerramento)",
    "interrupt.cli_message": (
        "Interrompido. A run {run_id} foi marcada como falha e pode ser retomada com `meister orchestrate --resume`."
    ),
    "interrupt.cli_message_no_run": "Interrompido antes de a run ser iniciada.",
}
