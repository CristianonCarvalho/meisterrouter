"""English catalog for orchestration interruption messages."""

MESSAGES: dict[str, str] = {
    "interrupt.reason": "Orchestration interrupted before completion (Ctrl+C or termination signal)",
    "interrupt.cli_message": (
        "Interrupted. Run {run_id} is marked as failed and can be resumed with `meister orchestrate --resume`."
    ),
    "interrupt.cli_message_no_run": "Interrupted before a run was started.",
}
