"""Catálogo em português do Brasil para o comando `meister wait`."""

MESSAGES: dict[str, str] = {
    "wait.help": "Espera uma run terminar e sai com um código que diz como ela terminou.",
    "wait.run_id_help": "Id completo da run ou prefixo único (padrão: a run mais recente do log).",
    "wait.timeout_help": "Segundos de espera antes de desistir com código 124 (0 ou ausente: sem limite).",
    "wait.interval_help": "Segundos entre as leituras do log (padrão 1, mínimo 0,05).",
    "wait.format_help": "Formato da saída: text ou json.",
    "wait.status.completed": "concluída",
    "wait.status.failed": "falhou",
    "wait.status.interrupted": "interrompida",
    "wait.status.running": "em andamento",
    "wait.summary": "Run {run_id}: {status} — {completed} concluídas, {failed} falharam, {total} total, {duration}",
    "wait.summary_reason": "Run {run_id}: {status} — {completed} concluídas, {failed} falharam, {total} total, {duration} — motivo: {reason}",
    "wait.duration": "{seconds}s",
    "wait.log_not_found": "Log não encontrado: {path}",
    "wait.no_runs": "Nenhuma run encontrada em {path}",
    "wait.error": "Erro: {error}",
    "wait.interrupted": "Interrompido durante a espera. A run não foi alterada.",
}
