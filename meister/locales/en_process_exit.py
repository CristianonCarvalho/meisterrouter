MESSAGES = {
    "process_exit.error": "Worker process in pane {pane_id} exited with code {code} without producing a result (infrastructure error). Last output: {tail}",
    "process_exit.progress_retry": "{prefix} worker process exited with code {code}; retry {retry}/{maximum} on {tier}",
    "process_exit.progress_gate_repair": "{prefix} gate repair {retry}/{maximum} on {tier}",
}
