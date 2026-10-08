"""English command message catalog."""

MESSAGES: dict[str, str] = {
    "commands.setup.binding_orchestrate": "MeisterRouter: orchestrate autonomous cycle",
    "commands.setup.binding_dashboard": "MeisterRouter: TUI dashboard",
    "commands.setup.binding_timeline": "MeisterRouter: timeline (Gantt)",
    "commands.setup.binding_orchestrate_direct": "MeisterRouter: orchestrate autonomous cycle (direct)",
    "commands.setup.binding_dashboard_direct": "MeisterRouter: TUI dashboard (direct)",
    "commands.setup.binding_timeline_direct": "MeisterRouter: timeline (Gantt) (direct)",
    "commands.setup.no_changes": "no changes",
    "commands.setup.atomic_write_failed": "Atomic file write failed: {error}",
    "commands.setup.config_check_failed": "Validation 'herdr config check' failed ({message}). Original content restored.",
    "commands.setup.config_check_default": "herdr config check failed",
    "commands.setup.reload_warning": "Configuration applied, but the Herdr server could not be reloaded ({message})",
    "commands.setup.config_reloaded": "Configuration updated and successfully reloaded in Herdr",
    "commands.setup.plugin_linked": "Plugin dev.meisterrouter.orchestrator is already linked in Herdr",
    "commands.setup.plugin_would_link": "[dry run] Would link plugin from {path}",
    "commands.setup.plugin_manifest_missing": "herdr-plugin.toml not found at the repository root",
    "commands.setup.plugin_manifest_missing_clone": "herdr-plugin.toml not found at the root; install from the cloned repository",
    "commands.setup.plugin_link_success": "Plugin dev.meisterrouter.orchestrator successfully linked in Herdr",
    "commands.setup.plugin_link_failed": "Failed to link plugin in Herdr: {error}",
    "commands.setup.python_incompatible": "Python {version} is incompatible (requires >= 3.10)",
    "commands.setup.python_compatible": "Python {version} (>= 3.10)",
    "commands.setup.herdr_missing": "Herdr not found on PATH. Install with: curl -fsSL https://herdr.dev/install.sh | sh",
    "commands.setup.herdr_found": "Herdr found at {path}",
    "commands.setup.daemon_inactive": "Daemon is not running (starts with Herdr)",
    "commands.setup.daemon_active": "Daemon active (PID: {pid})",
    "commands.setup.worker_found": "Harness CLI '{harness}' ({tier}) found: {path}",
    "commands.setup.worker_missing": "Harness CLI '{harness}' ({tier}) not found on PATH",
    "commands.setup.openrouter_configured": "OPENROUTER_API_KEY is configured",
    "commands.setup.openrouter_not_needed": "OPENROUTER_API_KEY is missing, but router.mode is 'first' (Jev is not used)",
    "commands.setup.openrouter_missing": "OPENROUTER_API_KEY is missing (Jev will not work; use router.mode: first)",
    "commands.setup.config_error": "Configuration ({source}) [error {path}]: {message}",
    "commands.setup.config_warning": "Configuration ({source}) [warning {path}]: {message}",
    "commands.setup.config_valid": "Active configuration is valid ({source})",
    "commands.setup.config_load_failed": "Error loading configuration: {error}",
    "commands.setup.config_template": """# meister.config.yaml — Local MeisterRouter configuration for this project.
#
# The workers.tier_order list below REPLACES the entire default list.
# To check active lanes and costs: meister models
# To validate this file: meister config validate

router:
  # router.mode: jev selects the initial lane via the Decisions API (OpenRouter);
  # router.mode: first skips Jev and uses the first active lane below, without network calls.
  mode: jev
  timeout_seconds: 10
  max_attempts: 2
  unavailable_cooldown_seconds: 300
  context_max_chars: 4000

workers:
  # The order defines the fallback chain (always forward).
  # enabled: false disables a lane in automatic routing.
  # eligible_classes restricts the classes (SMALL, MEDIUM, HIGH, ESCALATE) Jev can assign to the lane.
  tier_order:
    # tier_1 uses the <=100k token band; above 100k the combined price is USD 1.00.
    - name: tier_1
      harness: copilot
      model: claude-haiku-5.5
      cost_per_m_tokens: 0.20
      credit_usd: 0.01
      max_retries: 2
      best_for:
        - small_edits
        - single_file
        - css_fixes
        - unit_test_additions

    - name: tier_1b
      harness: copilot
      model: gpt-6-luna
      cost_per_m_tokens: 0.20
      credit_usd: 0.01
      max_retries: 2
      best_for:
        - small_edits
        - single_file
        - css_fixes
        - unit_test_additions

    - name: tier_1c
      harness: codex
      model: gpt-6-luna
      enabled: false
      cost_per_m_tokens: 0.20
      max_retries: 2
      best_for:
        - small_edits
        - single_file
        - css_fixes
        - unit_test_additions

    - name: tier_2
      harness: agy
      model: gemini-3.8-flash-high
      cost_per_m_tokens: 1.50
      max_retries: 2
      best_for:
        - deep_reasoning
        - complex_algorithms
        - hard_bugs

    - name: tier_3
      harness: claude
      model: sonnet
      cost_per_m_tokens: 4.00
      max_retries: 1
      best_for:
        - architectural_recovery
        - systemic_regressions
      eligible_classes:
        - ESCALATE

    - name: tier_3b
      harness: claude
      model: opus
      enabled: false
      cost_per_m_tokens: 8.00
      max_retries: 1
      best_for:
        - architectural_recovery
        - systemic_regressions
      eligible_classes:
        - ESCALATE
""",
    "commands.setup.read_warning": "Warning reading {path}: {error}",
    "commands.setup.dry_run_shortcuts": "🔍 [Dry run] Checking shortcuts in {path}:",
    "commands.setup.shortcut_already": "  ⏭️ Shortcut '{shortcut}': already configured ({command})",
    "commands.setup.shortcut_conflict": "  ⚠️ Shortcut conflict for '{shortcut}': bound to another command ({command})",
    "commands.setup.shortcut_would_add": "  ➕ Shortcut '{shortcut}': would be added to the managed block",
    "commands.setup.block_would_write": "\nBlock that would be written to the Herdr config.toml:",
    "commands.setup.no_shortcut_changes": "\nNo changes needed in the Herdr config.toml.",
    "commands.setup.shortcut_conflicts": "Herdr shortcuts have unresolved conflicts: {keys}",
    "commands.setup.shortcuts_updated": "Herdr shortcuts successfully updated ({keys})",
    "commands.setup.dry_run_project": "🔍 [Dry run] Would initialize project at '{path}' with hooks (--project)",
    "commands.setup.shortcuts_configured": "Herdr shortcuts configured ({keys})",
    "commands.setup.report_title": "MeisterRouter Setup Report",
    "commands.setup.next_steps": "Next steps:",
    "commands.setup.open_herdr": "  1. Open Herdr (herdr)",
    "commands.setup.run_plan": "  2. meister orchestrate --plan-file plan.json",
    "commands.setup.shortcuts_summary": "  3. Shortcuts: {keys}",
    "commands.setup.project_tip": "\nTip: to equip a project: meister setup --project",
    "commands.clean.git_failed": "git {args} failed: {detail}",
    "commands.clean.not_git_repo": "Not a Git repository: {path}",
    "commands.clean.base_missing": "Base branch does not exist: {branch}",
    "commands.clean.base_missing_default": "Base branch does not exist: neither 'main' nor 'master' is available. Use --base BRANCH.",
    "commands.clean.process_inspection_failed": "Could not inspect processes: {error}",
    "commands.clean.archive_ref_failed": "Failed to create archive ref {ref}: {error}",
    "commands.clean.simulation_apply_error": "The plan is a dry run; generate it with apply=True to apply.",
    "commands.clean.busy": "A MeisterRouter process is running; use --force-busy to ignore it.",
    "commands.clean.branch_changed": "Branch {branch} changed after planning; no deletion was performed.",
    "commands.clean.branch_delete_failed": "Failed to delete branch {branch}: {error}",
    "commands.clean.branch": "BRANCH",
    "commands.clean.ahead": "AHEAD",
    "commands.clean.situation": "SITUATION",
    "commands.clean.action": "ACTION",
    "commands.clean.situation_merged": "merged",
    "commands.clean.situation_equivalent": "equivalent",
    "commands.clean.situation_archived": "archived",
    "commands.clean.situation_unmerged": "UNMERGED",
    "commands.clean.action_in_use": "kept (branch in use)",
    "commands.clean.action_keep": "kept (--keep)",
    "commands.clean.action_unmerged": "kept (unmerged)",
    "commands.clean.action_archive_delete": "archive and delete",
    "commands.clean.action_delete": "delete",
    "commands.clean.action_in_use_or_keep": "kept (branch in use or --keep)",
    "commands.clean.action_changed": "kept (branch changed)",
    "commands.clean.action_archive_failed": "kept (archive failed)",
    "commands.clean.action_delete_failed": "failed to delete",
    "commands.clean.action_deleted": "deleted",
    "commands.clean.summary": "Summary: {evaluated} evaluated, {deleted_label}, {kept} kept, {runs} runs closed, {refs} archive refs preserved.",
    "commands.clean.would_delete": "{count} would be deleted",
    "commands.clean.deleted": "{count} deleted",
    "commands.clean.error": "Error: {error}",
    "commands.clean.simulation_message": "Nothing was changed. To apply: meister clean --repo {repo} --apply",
    "commands.plan.serial_warning": "fully serial plan ({tasks} tasks in {batches} batches)",
    "commands.plan.file_estimate_warning": "; the file-based analysis would take {rounds} steps",
    "commands.plan.unscoped_warning": "tasks without target_files run in isolation: {tasks}",
    "commands.plan.hot_file_warning": "hot file {file} declared by {count} tasks",
    "commands.plan.title": "Plan analysis",
    "commands.plan.metrics": "Tasks: {tasks} | Batches: {batches} | Maximum width: {width}",
    "commands.plan.rounds": "Sequential steps (up to {workers} workers): {rounds}",
    "commands.plan.batches": "Batches:",
    "commands.plan.batch": "  Batch {index}: {tasks}",
    "commands.plan.none": "  (none)",
    "commands.plan.none_feminine": "  (none)",
    "commands.plan.critical_path": "Critical path ({length} tasks): {tasks}",
    "commands.plan.why_not_parallel": "Why not parallel:",
    "commands.plan.file_conflict": "{kind}: {tasks} (files: {files})",
    "commands.plan.reason": "{kind}: {tasks}",
    "commands.plan.hot_files": "Hot files:",
    "commands.plan.unscoped_tasks": "Unscoped tasks:",
    "commands.plan.file_estimate": "File-based estimate (ignores semantic dependencies): {batches} batches; {rounds} steps",
    "commands.plan.warnings": "Warnings:",
    "commands.env.node_command_unknown": "Could not determine the Node installation command",
    "commands.env.prepare_failed": "Failed to prepare environment with {command}: {error}",
    "commands.env.install_failed": "Installation of {command} failed (rc={code}): {output}",
    "commands.timeline.no_runs": "no runs available",
    "commands.timeline.id_min_length": "ID must contain at least 6 characters: {id}",
    "commands.timeline.id_missing": "not found",
    "commands.timeline.id_ambiguous": "ambiguous",
    "commands.timeline.runs_available": "ID {status}: {id}. Available runs: {runs}",
    "commands.timeline.waiting_missing_log": "waiting for the first run (log does not exist yet)",
    "commands.timeline.waiting_first_run": "waiting for the first run",
    "commands.progress.plan": "Plan: {tasks} tasks in {batches} batches (run {run_id})",
    "commands.progress.started": "{prefix} started on {tier}",
    "commands.progress.completed": "{prefix} completed on {tier} ({duration})",
    "commands.progress.reused": "{prefix} reused from run {run_id}",
    "commands.progress.retry_timeout": "{prefix} timeout; retry {retry}/{maximum} on {tier}",
    "commands.progress.retry_lost": "{prefix} pane lost; retry {retry}/{maximum} on {tier}",
    "commands.progress.timeout_idle": "idle timeout ({seconds} s)",
    "commands.progress.timeout_max": "exceeded the {seconds} s limit",
    "commands.progress.timeout": "{prefix} {timeout} on {tier}; {action}",
    "commands.progress.quota": "{prefix} quota exhausted on {tier}{suffix}",
    "commands.progress.failed": "{prefix} FAILED: {reason}",
    "commands.progress.unknown_error": "unknown error",
    "commands.progress.summary": "Run {run_id} summary: {total} tasks | {completed} completed | {failed} failed | {reused} reused | total time {duration}",
    "commands.progress.status_reused": "reused",
    "commands.progress.status_completed": "completed",
    "commands.progress.status_failed": "FAILED",
    "commands.progress.completed_main": "Completed: main was updated.",
    "commands.progress.next_step": "Next step: fix the cause and rerun the same command (or use --resume if you edited the plan).",
}
