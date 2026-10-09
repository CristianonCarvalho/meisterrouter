"""English catalog for the `meister wait` command."""

MESSAGES: dict[str, str] = {
    "wait.help": "Wait until a run finishes and exit with a code that says how it ended.",
    "wait.run_id_help": "Full run id or a unique prefix (default: the most recent run in the log).",
    "wait.timeout_help": "Seconds to wait before giving up with exit code 124 (0 or omitted: no limit).",
    "wait.interval_help": "Seconds between log checks (default 1, minimum 0.05).",
    "wait.format_help": "Output format: text or json.",
    "wait.status.completed": "completed",
    "wait.status.failed": "failed",
    "wait.status.interrupted": "interrupted",
    "wait.status.running": "running",
    "wait.summary": "Run {run_id}: {status} - {completed} completed, {failed} failed, {total} total, {duration}",
    "wait.summary_reason": "Run {run_id}: {status} - {completed} completed, {failed} failed, {total} total, {duration} - reason: {reason}",
    "wait.duration": "{seconds}s",
    "wait.log_not_found": "Log not found: {path}",
    "wait.no_runs": "No runs found in {path}",
    "wait.error": "Error: {error}",
    "wait.interrupted": "Interrupted while waiting. The run was not changed.",
}
