MESSAGES = {
    "process_exit.error": "Processo do worker no pane {pane_id} encerrou com código {code} sem produzir resultado (erro de infraestrutura). Última saída: {tail}",
    "process_exit.progress_retry": "{prefix} processo do worker encerrou com código {code}; retentativa {retry}/{maximum} em {tier}",
    "process_exit.progress_gate_repair": "{prefix} reparo do gate {retry}/{maximum} em {tier}",
}
