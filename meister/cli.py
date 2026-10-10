"""Command-line interface for MeisterRouter."""

import ipaddress
import os
import sys
import json
import signal
import asyncio
import logging
import subprocess
import uuid
import time
import hashlib
import threading
from pathlib import Path
from typing import Any, Optional

import click

from meister import __version__, osops
from meister.dashboard.launcher import ensure_dashboard
from meister.jev import classify_task, control_cycle, call_decisions
from meister.hooks import install_git_hook, install_claude_hook
from meister.logger import (
    add_event_observer,
    get_events_by_run_id,
    get_current_run,
    get_log_file,
    log_event,
    remove_event_observer,
)
from meister.config import load_config, ensure_meister_dir
from meister.herdr.client import HerdrSocketClient
from meister.herdr.bridge import HerdrEventBridge, ResumeRequestError
from meister.hosts import HostError, select_host
from meister.i18n import get_language, t

logger = logging.getLogger(__name__)
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_DEFAULT_ENSURE_DASHBOARD = ensure_dashboard


def _start_dashboard(project_root: Path, dashboard_config: Any) -> None:
    """Launch the dashboard independently so startup and browser opening never block orchestration."""
    if "pytest" in sys.modules and ensure_dashboard is _DEFAULT_ENSURE_DASHBOARD:
        return

    def launch() -> None:
        try:
            outcome = ensure_dashboard(project_root, dashboard_config)
        except Exception as error:
            logger.warning("Could not launch the orchestration dashboard: %s", error)
            return
        if outcome.kind != "disabled" and outcome.url:
            click.echo(t("dashboard.timeline", url=outcome.url))

    try:
        threading.Thread(target=launch, daemon=True).start()
    except Exception as error:
        logger.warning("Could not start the orchestration dashboard launcher: %s", error)


def _version_string() -> str:
    package_dir = Path(__file__).resolve().parent
    if not (package_dir.parent / ".git").exists():
        return f"meister {__version__}"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=package_dir,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        commit = result.stdout.strip()
    except Exception:
        return f"meister {__version__}"
    if not commit:
        return f"meister {__version__}"
    return f"meister {__version__} (commit {commit})"


def _show_version(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    if value:
        click.echo(_version_string())
        ctx.exit()


def _load_cli_config(*args: Any, **kwargs: Any):
    """`load_config` com o idioma aplicado; repassa os argumentos exatamente como o chamador os deu."""
    cfg = load_config(*args, **kwargs)
    from meister.i18n import set_language

    # MEISTER_LANG tem precedência sobre o idioma do arquivo de configuração (inclusive de um -c explícito)
    if not os.environ.get("MEISTER_LANG"):
        set_language(cfg.language)
    return cfg


def _worker_usage_event_fields(result: Optional[dict[str, Any]]) -> dict[str, Any]:
    usage = result.get("usage") if isinstance(result, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    source = usage.get("cost_source", "unknown")
    cost = usage.get("cost")
    if source == "unknown" or cost is None:
        source = "unknown"
        cost = 0.0
    else:
        cost = float(cost)
    fields = {
        key: usage[key]
        for key in ("tokens_in", "tokens_out", "tokens_total", "credits", "approx")
        if usage.get(key) is not None
    }
    return {"cost": cost, "cost_source": source, **fields}


def is_pid_alive(pid: int) -> bool:
    """Return whether a process with the given PID is active."""
    return osops.pid_alive(pid)


def _is_loopback_host(host: str) -> bool:
    """Return whether ``host`` only accepts connections from the local machine."""
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_loopback
    except ValueError:
        return False


def get_default_pid_file() -> str:
    """Return the default daemon PID file path."""
    env_pid = os.environ.get("MEISTER_PID_FILE")
    if env_pid:
        return env_pid
    return os.path.expanduser("~/.meister/daemon.pid")


def get_herdr_client(socket_path: Optional[str] = None) -> Optional[HerdrSocketClient]:
    """Instancia HerdrSocketClient de forma tolerante a ambiente sem socket configurado."""
    try:
        return HerdrSocketClient(socket_path=socket_path)
    except (ValueError, Exception) as e:
        logger.debug("HerdrSocketClient initialization failed: %s", e)
        return None


@click.group(
    name="meister",
    help=t("cli.main.help"),
)
@click.option(
    "--version",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_show_version,
    help=t("cli.version.help"),
)
def main():
    """MeisterRouter CLI entry point."""
    pass


@main.command("init", help=t("cli.init.help"))
@click.option("--target", "-t", default=".", help=t("cli.init.target_help"))
@click.option(
    "--type",
    "type_",
    type=click.Choice(["all", "claude", "codex"]),
    default="all",
    help=t("cli.init.type_help"),
)
@click.option(
    "--hooks",
    "install_hooks",
    is_flag=True,
    default=False,
    help=(
        t("cli.init.hooks_help")
    ),
)
@click.option(
    "--no-hooks",
    is_flag=True,
    default=False,
    help=t("cli.init.no_hooks_help"),
)
@click.option("--force", is_flag=True, default=False, help=t("cli.init.force_help"))
def init(target, type_, install_hooks, no_hooks, force):
    """Initialize MeisterRouter rules in an existing project."""
    target_dir = os.path.abspath(target or ".")
    click.echo(t("cli.init.start", target=target_dir))
    os.makedirs(target_dir, exist_ok=True)
    created = []
    skipped = []

    rule_files = []
    if type_ in ["all", "claude"]:
        rule_files.append(("CLAUDE.md.template", "CLAUDE.md", t("cli.init.claude_description")))
    if type_ in ["all", "codex"]:
        rule_files.append(("CODEX.md.template", "CODEX.md", t("cli.init.codex_description")))
    rule_files.append(("AGENTS.md.template", "AGENTS.md", t("cli.init.agents_description")))
    for template_name, filename, description in rule_files:
        destination = os.path.join(target_dir, filename)
        existed = os.path.exists(destination)
        if existed and not force:
            click.echo(t("cli.init.exists_skipped", filename=filename))
            skipped.append(filename)
            continue
        template_language_dir = "pt-BR" if get_language() == "pt-BR" else "en"
        template_path = os.path.join(TEMPLATES_DIR, template_language_dir, template_name)
        with open(template_path, "r", encoding="utf-8") as template_file:
            content = template_file.read()
        with open(destination, "w", encoding="utf-8") as destination_file:
            destination_file.write(content)
        action = t("cli.init.overwritten") if existed else t("cli.init.created")
        click.echo(t("cli.init.file_created", action=action, filename=filename, description=description))
        created.append(filename)

    # 4. Cria diretório local .meister com .gitignore para logs
    ensure_meister_dir(target_dir)
    local_meister = os.path.join(target_dir, ".meister", "logs")
    logs_existed = os.path.isdir(local_meister)
    os.makedirs(local_meister, exist_ok=True)
    if not logs_existed:
        created.append(".meister/logs/")
        click.echo(t("cli.init.logs_created"))

    # 5. Instala hooks se solicitado
    if install_hooks:
        ok_git, msg_git = install_git_hook(target_dir, force=force)
        if ok_git:
            click.echo(t("cli.init.hook_created", message=msg_git))
            created.append("hook Git pre-commit")
        else:
            click.echo(t("cli.init.hook_skipped", message=msg_git))
            skipped.append("hook Git pre-commit")

        ok_claude, msg_claude = install_claude_hook(target_dir, force=force)
        if ok_claude:
            click.echo(t("cli.init.hook_created", message=msg_claude))
            created.append("hooks Claude Code")
        else:
            click.echo(t("cli.init.hook_skipped", message=msg_claude))
            skipped.append("hooks Claude Code")

    summary_created = ", ".join(created) if created else t("cli.init.no_items")
    summary_skipped = ", ".join(skipped) if skipped else t("cli.init.no_items")
    click.echo(t("cli.init.summary", created=summary_created, skipped=summary_skipped))
    if skipped:
        click.echo(t("cli.init.force_hint"))
    if not install_hooks:
        click.echo(t("cli.init.hooks_not_installed"))
    if no_hooks:
        click.echo(t("cli.init.no_hooks_compatibility"))


@main.command("setup", help=t("cli.setup.help"))
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help=t("cli.setup.dry_run_help"),
)
@click.option(
    "--direct-keys",
    is_flag=True,
    default=False,
    help=t("cli.setup.direct_keys_help"),
)
@click.option(
    "--herdr-config",
    "herdr_config_path",
    default=None,
    help=t("cli.setup.herdr_config_help"),
)
@click.option(
    "--project",
    "project_path",
    is_flag=False,
    flag_value=".",
    default=None,
    help=t("cli.setup.project_help"),
)
@click.pass_context
def setup(ctx, dry_run, direct_keys, herdr_config_path, project_path):
    """Install MeisterRouter, link the Herdr plugin, and configure shortcuts."""
    from meister.setup_cmd import run_setup

    def _init_proj(p):
        ctx.invoke(init, target=p, type_="all", install_hooks=True, no_hooks=False, force=False)

    exit_code = run_setup(
        dry_run=dry_run,
        direct_keys=direct_keys,
        herdr_config_path=herdr_config_path,
        project_path=project_path,
        echo=click.echo,
        init_project_fn=_init_proj,
    )
    if exit_code != 0:
        sys.exit(exit_code)


@main.command("classify", help=t("cli.classify.help"))
@click.option("--context", "-c", required=True, help=t("cli.classify.context_help"))
@click.option("--model", "-m", default=None, help=t("cli.common.model_help"))
@click.option("--run-id", default=None, help=t("cli.common.run_id_help"))
@click.option("--task-id", default=None, help=t("cli.common.task_id_help"))
def classify(context, model, run_id, task_id):
    """Classify task complexity."""
    try:
        cfg = load_config()
        result = classify_task(
            context=context,
            model=model or cfg.master.model,
            run_id=run_id,
            task_id=task_id,
            implementers=cfg.workers.tier_order,
        )
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        sys.stderr.write(t("cli.classify.error", error=e) + "\n")
        sys.exit(1)


@main.command("control", help=t("cli.control.help"))
@click.option("--diff-summary", "-d", required=True, help=t("cli.control.diff_summary_help"))
@click.option(
    "--test-result",
    "-r",
    required=True,
    type=click.Choice(["pass", "fail", "unknown"]),
    help=t("cli.control.test_result_help"),
)
@click.option("--attempts", "-a", type=int, default=1, help=t("cli.control.attempts_help"))
@click.option(
    "--security-sensitive",
    "-s",
    is_flag=True,
    default=False,
    help=t("cli.control.security_sensitive_help"),
)
@click.option("--model", "-m", default=None, help=t("cli.common.model_help"))
@click.option("--run-id", default=None, help=t("cli.common.run_id_help"))
@click.option("--task-id", default=None, help=t("cli.common.task_id_help"))
@click.option(
    "--close-worker/--no-close-worker",
    default=True,
    help=t("cli.control.close_worker_help"),
)
def control(diff_summary, test_result, attempts, security_sensitive, model, run_id, task_id, close_worker):
    """Run the agent loop control decision."""
    try:
        cfg = load_config()
        result = control_cycle(
            diff_summary=diff_summary,
            test_result=test_result,
            attempts=attempts,
            security_sensitive=security_sensitive,
            model=model or cfg.master.model,
            run_id=run_id,
            task_id=task_id,
        )
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))

        # Se a ação for COMPLETE e close_worker for True, fecha os terminais dos workers automaticamente (Achado #7)
        if result.get("action") == "COMPLETE" and close_worker:
            client = get_herdr_client()

            # Fecha todos os active panes registrados no SQLite
            try:
                from meister.state import StateManager
                state_mgr = StateManager()
                active_panes = state_mgr.get_active_panes()
                for p_id in active_panes:
                    if client is not None:
                        async def _close(p):
                            await client.close_pane(p)
                        asyncio.run(_close(p_id))
                        click.echo(t("cli.control.pane_closed", pane_id=p_id))
                    state_mgr.unregister_pane(p_id)
            except Exception as e:
                logger.debug("Could not auto-close registered worker panes: %s", e)

            # Mantém suporte legado para active_worker_pane.txt se existir
            active_pane_file = os.path.join(os.getcwd(), ".meister", "active_worker_pane.txt")
            if os.path.exists(active_pane_file):
                try:
                    with open(active_pane_file, "r", encoding="utf-8") as f:
                        pane_id = f.read().strip()
                    if pane_id and client is not None:
                        async def _close_legacy():
                            await client.close_pane(pane_id)
                        asyncio.run(_close_legacy())
                        click.echo(t("cli.control.pane_closed", pane_id=pane_id))
                    os.remove(active_pane_file)
                except Exception as e:
                    logger.debug("Could not auto-close legacy worker pane: %s", e)

    except Exception as e:
        sys.stderr.write(t("cli.control.error", error=e) + "\n")
        sys.exit(1)


@main.command("dashboard", help=t("cli.dashboard.help"))
@click.option("--port", "-p", type=int, default=5050, help=t("cli.dashboard.port_help"))
@click.option("--host", default="127.0.0.1", help=t("cli.dashboard.host_help"))
@click.option("--log-dir", type=click.Path(file_okay=False), default=None, help=t("cli.common.telemetry_log_dir_help"))
@click.option("--tui", is_flag=True, default=False, help=t("cli.dashboard.tui_help"))
@click.option("--allow-remote", is_flag=True, default=False, help=t("cli.dashboard.allow_remote_help"))
@click.option(
    "--idle-exit-minutes",
    type=click.IntRange(min=1),
    default=None,
    hidden=True,
    help=t("dashboard.idle_exit_minutes_help"),
)
def dashboard(port, host, log_dir, tui, allow_remote, idle_exit_minutes):
    """Start the local telemetry server or TUI overlay."""
    if tui:
        from meister.herdr.tui import run_tui_loop
        run_tui_loop()
    else:
        from meister.dashboard.server import start_server
        if not _is_loopback_host(host):
            if not allow_remote:
                click.echo(t("cli.dashboard.remote_host_refused", host=host), err=True)
                raise click.exceptions.Exit(2)
            click.echo(t("cli.dashboard.remote_host_warning", host=host), err=True)
        start_server(
            host=host,
            port=port,
            log_dir=log_dir,
            idle_exit_minutes=idle_exit_minutes,
        )


@main.command("report", help=t("cli.report.help"))
@click.option("--run-id", "run_ids", multiple=True, help=t("cli.report.run_id_help"))
@click.option("--group", "groups", multiple=True, metavar="NOME=ID,ID,...", help=t("cli.report.group_help"))
@click.option("--log-dir", type=click.Path(file_okay=False), default=None, help=t("cli.common.orchestration_log_dir_help"))
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "markdown"]),
    default="table",
    show_default=True,
)
def report(run_ids, groups, log_dir, output_format):
    """Compare cost, duration, and attempts without modifying the log."""
    from meister.dashboard.metrics import iter_events, list_runs
    from meister.report import compute_group_report, compute_run_report, render_report

    if not run_ids and not groups:
        raise click.UsageError(t("cli.report.require_run_or_group"))
    log_file = (
        os.path.join(os.path.abspath(log_dir), "orchestration_log.jsonl")
        if log_dir is not None else get_log_file()
    )
    if not os.path.isfile(log_file):
        click.echo(t("cli.common.log_not_found", path=log_file), err=True)
        raise click.exceptions.Exit(2)

    events = list(iter_events(log_file))
    available = list_runs(events)
    available_ids = [item["run_id"] for item in available]

    def resolve(identifier):
        if len(identifier) < 6:
            raise ValueError(t("cli.report.short_id", identifier=identifier))
        matches = [run for run in available_ids if run.startswith(identifier)]
        if len(matches) == 1:
            return matches[0]
        detail = t("cli.report.ambiguous") if matches else t("cli.report.missing")
        listing = ", ".join(available_ids) if available_ids else t("cli.report.no_runs")
        raise ValueError(t("cli.report.unresolved_id", detail=detail, identifier=identifier, listing=listing))

    try:
        resolved_runs = [resolve(identifier) for identifier in run_ids]
        resolved_groups = []
        for group in groups:
            if "=" not in group:
                raise ValueError(t("cli.report.invalid_group", group=group))
            name, raw_ids = group.split("=", 1)
            identifiers = [item.strip() for item in raw_ids.split(",") if item.strip()]
            if not name.strip() or not identifiers:
                raise ValueError(t("cli.report.empty_group", group=group))
            resolved_groups.append((name.strip(), [resolve(identifier) for identifier in identifiers]))
    except ValueError as error:
        click.echo(t("cli.common.error", error=error), err=True)
        raise click.exceptions.Exit(2)

    cfg = load_config()
    tier_prices = {tier.name: tier.cost_per_m_tokens for tier in cfg.workers.tier_order}
    credit_prices = {
        tier.name: tier.credit_usd
        for tier in [*cfg.workers.tier_order, *cfg.workers.disabled]
        if tier.credit_usd is not None
    }
    all_ids = list(dict.fromkeys(resolved_runs + [
        run_id for _, member_ids in resolved_groups for run_id in member_ids
    ]))
    reports = {
        run_id: compute_run_report(
            events, run_id, tier_prices=tier_prices, credit_prices=credit_prices
        )
        for run_id in all_ids
    }
    group_reports = []
    for name, member_ids in resolved_groups:
        group_report = compute_group_report(name, [reports[run_id] for run_id in member_ids])
        group_report["run_ids"] = member_ids
        group_reports.append(group_report)
    data = {
        "groups": group_reports,
        "runs": [reports[run_id] for run_id in resolved_runs],
    }
    click.echo(render_report(data, output_format))


@main.command("timeline", help=t("cli.timeline.help"))
@click.option("--run-id", default=None, help=t("cli.timeline.run_id_help"))
@click.option(
    "--log-dir",
    type=click.Path(file_okay=False),
    default=None,
    help=t("cli.common.orchestration_log_dir_help"),
)
@click.option("--once", is_flag=True, default=False, help=t("cli.timeline.once_help"))
@click.option("--all", "all_runs", is_flag=True, default=False, help=t("cli.timeline.all_help"))
@click.option("--no-color", is_flag=True, default=False, help=t("cli.timeline.no_color_help"))
@click.option("--json", "json_format", is_flag=True, default=False)
def timeline_command(run_id, log_dir, once, all_runs, no_color, json_format):
    """Read-only, colorized Gantt timeline of a run's tasks."""
    import shutil
    from datetime import datetime, timezone

    from meister.log_tail import resolve_log_file
    from meister.timeline_cli import once_frame
    from meister.timeline_view import detect_color

    if json_format:
        from meister.timeline_json import timeline_json_for_log

        try:
            data = timeline_json_for_log(
                resolve_log_file(log_dir),
                run_id=run_id,
                now=datetime.now(timezone.utc),
            )
        except ValueError as error:
            click.echo(t("cli.common.error", error=error), err=True)
            raise click.exceptions.Exit(2)
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if all_runs and run_id is not None:
        raise click.UsageError(t("cli.timeline.all_or_run_id"))
    if not once:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise click.UsageError(t("cli.timeline.tty_required"))
        from meister.timeline_app import run_interactive

        log_file = resolve_log_file(log_dir)
        color = "none" if no_color else detect_color(os.environ, sys.stdout.isatty())
        try:
            run_interactive(log_file, run_id=run_id, color=color, all_runs=all_runs)
        except ValueError as error:
            click.echo(t("cli.common.error", error=error), err=True)
            raise click.exceptions.Exit(2)
        return
    log_file = resolve_log_file(log_dir)
    isatty = sys.stdout.isatty()
    color = "none" if no_color else detect_color(os.environ, isatty)
    width = shutil.get_terminal_size((120, 24)).columns if isatty else 120
    try:
        frame = once_frame(
            log_file,
            run_id,
            width=width,
            now=datetime.now(timezone.utc),
            color=color,
            all_runs=all_runs,
        )
    except ValueError as error:
        click.echo(t("cli.common.error", error=error), err=True)
        raise click.exceptions.Exit(2)
    click.echo(frame)


@main.command("wait", help=t("wait.help"))
@click.option("--run-id", default=None, help=t("wait.run_id_help"))
@click.option("--timeout", type=click.FloatRange(min=0), default=None, help=t("wait.timeout_help"))
@click.option("--interval", type=click.FloatRange(min=0.05), default=1.0, show_default=True, help=t("wait.interval_help"))
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help=t("wait.format_help"),
)
def wait(run_id, timeout, interval, output_format):
    """Wait until a run finishes and exit with a code describing how it ended."""
    from meister.log_tail import resolve_log_file
    from meister.wait_cmd import (
        EXIT_INTERRUPTED,
        EXIT_USAGE,
        WaitUsageError,
        format_text,
        wait_for_run,
    )

    try:
        report = wait_for_run(
            resolve_log_file(None),
            run_id,
            timeout=timeout,
            interval=interval,
        )
    except WaitUsageError as error:
        click.echo(str(error), err=True)
        raise click.exceptions.Exit(EXIT_USAGE)
    except KeyboardInterrupt:
        click.echo(t("wait.interrupted"), err=True)
        raise click.exceptions.Exit(EXIT_INTERRUPTED)

    if output_format == "json":
        click.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        click.echo(format_text(report))
    if report["exit_code"]:
        raise click.exceptions.Exit(report["exit_code"])


@main.command("install-hooks", help=t("cli.hooks.help"))
@click.option("--target", "-t", default=".", help=t("cli.common.repository_dir_help"))
@click.option("--git", is_flag=True, default=False, help=t("cli.hooks.git_help"))
@click.option("--claude", is_flag=True, default=False, help=t("cli.hooks.claude_help"))
def install_hooks(target, git, claude):
    """Install hooks for Git or Claude Code."""
    target_dir = os.path.abspath(target or ".")
    if git or not claude:
        ok, msg = install_git_hook(target_dir)
        click.echo(f"[{'OK' if ok else t('cli.config.level.error')}] {msg}")
    if claude:
        ok, msg = install_claude_hook(target_dir)
        click.echo(f"[{'OK' if ok else t('cli.config.level.error')}] {msg}")


@main.command("models", help=t("cli.models.help"))
@click.option("--config", "-c", "config_path", default=None, help=t("cli.common.config_file_help"))
@click.option("--enable", "enable_lanes", multiple=True, help=t("tiers.models.enable_help"))
@click.option("--disable", "disable_lanes", multiple=True, help=t("tiers.models.disable_help"))
def models(config_path, enable_lanes, disable_lanes):
    """Print configured lanes and their costs."""
    from meister.lane_toggle import LaneToggleError, apply_toggle

    try:
        if (
            (enable_lanes or disable_lanes)
            and config_path is not None
            and not Path(config_path).exists()
        ):
            cfg = _load_cli_config()
        else:
            cfg = _load_cli_config(config_path=config_path)
    except Exception as error:
        if not enable_lanes and not disable_lanes:
            raise
        click.echo(t("tiers.toggle_error", error=error), err=True)
        raise click.exceptions.Exit(1)

    tiers = [
        *((tier, True) for tier in cfg.workers.tier_order),
        *((tier, False) for tier in cfg.workers.disabled),
    ]
    config_source = cfg.config_source
    if enable_lanes or disable_lanes:
        from meister.config import validate_config

        errors = [
            issue for issue in validate_config(cfg) if issue.level == "error"
        ]
        if errors:
            click.echo(t("tiers.invalid_configuration"), err=True)
            for issue in errors:
                click.echo(
                    t(
                        "tiers.configuration_issue",
                        path=issue.path,
                        message=issue.message,
                    ),
                    err=True,
                )
            raise click.exceptions.Exit(1)

        catalog_names = [tier.name for tier, _ in tiers]
        enabled_now = {tier.name: enabled for tier, enabled in tiers}
        if config_path is not None:
            target_path = config_path
        else:
            source_path = Path(cfg.config_source)
            target_path = str(
                source_path
                if source_path.is_file()
                else Path.cwd() / "meister.config.yaml"
            )
        try:
            final_state = apply_toggle(
                target_path,
                catalog_names,
                enabled_now,
                enable_lanes,
                disable_lanes,
            )
        except LaneToggleError as error:
            message = str(error)
            if "at least one enabled lane" in message:
                output = t("tiers.last_enabled_lane")
            elif message.startswith("Unknown lane name(s): "):
                unknown, valid = message.removeprefix("Unknown lane name(s): ").split(
                    ". Valid lane names: ", 1
                )
                output = t("tiers.unknown_lane", lanes=unknown, valid=valid)
            elif message.startswith("Cannot enable and disable the same lane: "):
                output = t(
                    "tiers.conflicting_toggle",
                    lanes=message.removeprefix("Cannot enable and disable the same lane: "),
                )
            else:
                output = t("tiers.toggle_error", error=error)
            click.echo(output, err=True)
            raise click.exceptions.Exit(1)
        tiers = [(tier, final_state[tier.name]) for tier, _ in tiers]
        config_source = target_path

    click.echo(t("cli.models.config_source", source=config_source))
    click.echo(
        f"{'#':>3}  {t('cli.models.name'):<24} {t('cli.models.harness'):<16} "
        f"{t('cli.models.model'):<28} {t('tiers.models.effort'):<10} "
        f"{t('cli.models.cost_per_m') :>10}  {t('cli.models.status')}"
    )
    for position, (tier, enabled) in enumerate(tiers, 1):
        status = t("cli.models.enabled") if enabled else t("cli.models.disabled")
        effort = "-" if tier.effort is None else tier.effort
        click.echo(
            f"{position:>3}  {tier.name:<24} {tier.harness:<16} {tier.model:<28} "
            f"{effort:<10} "
            f"${tier.cost_per_m_tokens:.3f}  {status}"
        )


@main.command("test", help=t("cli.test.help"))
def test():
    """Test connectivity to the OpenRouter Decisions API."""
    click.echo(t("cli.test.start"))
    try:
        res = call_decisions(
            state={"test_key": "conectar_meisterrouter"},
            questions={
                "connection": {
                    "type": "choice",
                    "instructions": "O serviço está operacional?",
                    "criteria": {
                        "ok": "Serviço operacional.",
                        "fail": "Falha no serviço.",
                    },
                }
            }
        )
        click.echo(t("cli.test.success"))
        click.echo(t("cli.test.answer", answer=json.dumps(res.get('answers'), ensure_ascii=False, indent=2)))
    except Exception as e:
        click.echo(t("cli.test.failure", error=e))
        sys.exit(1)


@main.command("worker", help=t("cli.worker.help"))
@click.option("--model", "-m", default=None, help=t("cli.worker.model_help"))
@click.option("--task", "-t", default=None, help=t("cli.worker.task_help"))
@click.option("--files", "-f", default=None, help=t("cli.worker.files_help"))
@click.option("--cwd", default=None, help=t("cli.common.cwd_help"))
@click.option("--pane/--no-pane", "pane", default=None, help=t("cli.worker.pane_help"))
@click.option("--tab/--no-tab", "tab", default=True, help=t("cli.worker.tab_help"))
@click.option("--split", is_flag=True, default=False, help=t("cli.worker.split_help"))
@click.option("--config", "-c", "config_path", default=None, help=t("cli.common.config_file_help"))
@click.option("--run-id", default=None, help=t("cli.common.run_id_help"))
@click.option("--task-id", default=None, help=t("cli.common.task_id_help"))
def worker(model, task, files, cwd, pane, tab, split, config_path, run_id, task_id):
    """Start a native MeisterRouter worker."""
    from meister.worker import (
        execute_worker_task,
        run_worker_interactive_loop,
        is_herdr_available,
        run_worker_in_herdr_pane,
        run_worker_in_herdr_tab,
        write_atomic_json,
        WorkerInfrastructureError,
    )

    target_files = [f.strip() for f in files.split(",")] if files else None
    resolved_cwd = os.path.abspath(cwd or os.getcwd())
    cfg = _load_cli_config(config_path=config_path, cwd=resolved_cwd)
    valid_tiers = [tier.name for tier in cfg.workers.tier_order]
    if model is None:
        if not valid_tiers:
            raise click.UsageError(t("cli.worker.no_lanes"))
        model = valid_tiers[0]
    elif model.casefold() not in {name.casefold() for name in [*valid_tiers, *(t.name for t in cfg.workers.disabled)]}:
        listing = ", ".join(valid_tiers)
        if cfg.workers.disabled:
            listing += t("cli.worker.disabled_lanes", lanes=", ".join(t.name for t in cfg.workers.disabled))
        raise click.UsageError(t("cli.worker.unknown_lane", model=model, listing=listing))
    ensure_meister_dir(resolved_cwd)

    if task:
        # Se estivermos no Herdr e não estivermos já dentro de um pane de worker, abre uma tab ou terminal visível (E2E-9)
        in_pane = os.environ.get("MEISTER_IN_PANE") == "1"
        herdr_disabled = (pane is False) or (tab is False)
        should_spawn_in_herdr = not herdr_disabled and not in_pane and is_herdr_available()
        use_split_pane = split or (pane is True)

        current = get_current_run()
        resolved_run_id = (
            run_id
            or os.environ.get("MEISTER_RUN_ID")
            or current.get("run_id")
            or f"run_{uuid.uuid4().hex[:8]}"
        )
        resolved_task_id = (
            task_id
            or current.get("task_id")
            or hashlib.sha256(task.encode("utf-8")).hexdigest()[:16]
        )

        worker_start_time = time.monotonic()
        if not in_pane:
            log_event(
                event_type="worker_start",
                run_id=resolved_run_id,
                task_id=resolved_task_id,
                tier=model,
                model=model,
            )

        # Isolamento com WorktreeManager + IntegrationPipeline (E2E-3)
        is_git = False
        if not in_pane:
            try:
                chk = subprocess.run(
                    ["git", "rev-parse", "--is-inside-work-tree"],
                    cwd=resolved_cwd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                is_git = chk.returncode == 0 and chk.stdout.strip() == "true"
            except Exception:
                is_git = False

        wt_mgr = None
        pipeline = None
        subtask_wt = None
        worker_cwd = resolved_cwd

        if is_git and not in_pane:
            from meister.worktree import WorktreeManager, IntegrationPipeline
            from meister.gate import DeterministicGate

            wt_mgr = WorktreeManager(repo_root=resolved_cwd)
            wt_mgr.cleanup_orphans()
            gate = DeterministicGate(repo_path=resolved_cwd)
            pipeline = IntegrationPipeline(wt_mgr, gate=gate)

            pipeline.start_integration(resolved_run_id)

            subtask_wt = wt_mgr.create_worktree(
                task_id=f"worker_{resolved_run_id}",
                base_ref=pipeline.integration_info.branch_name,
            )
            worker_cwd = subtask_wt.worktree_path

        try:
            worker_phase_start = time.monotonic()
            try:
                if should_spawn_in_herdr:
                    if use_split_pane:
                        click.echo(t("cli.worker.dispatch_pane", model=model))
                        res = run_worker_in_herdr_pane(
                            model=model,
                            task=task,
                            target_files=target_files,
                            cwd=worker_cwd,
                            config_path=config_path,
                            run_id=resolved_run_id,
                            task_id=resolved_task_id,
                        )
                    else:
                        click.echo(t("cli.worker.dispatch_tab", model=model))
                        res = run_worker_in_herdr_tab(
                            model=model,
                            task=task,
                            target_files=target_files,
                            cwd=worker_cwd,
                            config_path=config_path,
                            run_id=resolved_run_id,
                            task_id=resolved_task_id,
                            label=resolved_task_id,
                        )
                else:
                    # Execução direta (dentro do pane recém-aberto ou se o Herdr não estiver rodando ou --no-pane)
                    click.echo(t("cli.worker.starting_task", model=model))
                    res = execute_worker_task(model=model, task=task, target_files=target_files, cwd=worker_cwd, config_path=config_path)
            finally:
                if not in_pane:
                    log_event(
                        event_type="worker_phase",
                        run_id=resolved_run_id,
                        task_id=resolved_task_id,
                        attempt=1,
                        tier=model,
                        phase="worker",
                        duration_ms=(time.monotonic() - worker_phase_start) * 1000.0,
                    )

            status = res.get("status", "done")
            worker_duration_ms = round((time.monotonic() - worker_start_time) * 1000.0, 2)
            usage_fields = _worker_usage_event_fields(res)

            if status != "done":
                if subtask_wt is not None and wt_mgr is not None:
                    wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
                if pipeline is not None:
                    pipeline.abort_integration()
                if not in_pane:
                    log_event(
                        event_type="worker_end",
                        run_id=resolved_run_id,
                        task_id=resolved_task_id,
                        tier=model,
                        duration_ms=worker_duration_ms,
                        exit_code=res.get("exit_code", 1),
                        **usage_fields,
                        status=status,
                    )
                click.echo(t("cli.worker.ended_with_status", status=status), err=True)
                sys.exit(1)

            if not in_pane:
                log_event(
                    event_type="worker_end",
                    run_id=resolved_run_id,
                    task_id=resolved_task_id,
                    tier=model,
                    duration_ms=worker_duration_ms,
                    exit_code=res.get("exit_code", 0),
                    **usage_fields,
                    status=status,
                    modified_files=res.get("modified_files", []),
                )

            # Se worktree/pipeline estavam ativos, integra determinísticamente (gate -> commit -> merge -> gate -> ff) (E2E-3)
            if pipeline is not None and subtask_wt is not None and wt_mgr is not None:
                integration_start = time.monotonic()
                try:
                    ok_int, int_err = pipeline.integrate_subtask(
                        subtask_wt=subtask_wt,
                        target_files=target_files,
                        commit_message=f"worker({model}): {task}",
                        task_id=resolved_task_id,
                        attempt=1,
                        tier=model,
                    )
                finally:
                    log_event(
                        event_type="worker_phase",
                        run_id=resolved_run_id,
                        task_id=resolved_task_id,
                        attempt=1,
                        tier=model,
                        phase="integrate",
                        duration_ms=(time.monotonic() - integration_start) * 1000.0,
                    )
                wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
                subtask_wt = None

                if not ok_int:
                    pipeline.abort_integration()
                    click.echo(t("cli.worker.integration_failed", error=int_err), err=True)
                    sys.exit(1)

                passed, out = pipeline.validate_final_integration()
                if not passed:
                    pipeline.abort_integration()
                    click.echo(t("cli.worker.quality_gate_failed", output=out), err=True)
                    sys.exit(1)

                ok_ff, ff_msg = pipeline.apply_fast_forward()
                if not ok_ff:
                    pipeline.abort_integration()
                    click.echo(t("cli.worker.fast_forward_failed", message=ff_msg), err=True)
                    sys.exit(1)

            where = "Herdr pane" if (should_spawn_in_herdr and use_split_pane) else "Herdr tab" if should_spawn_in_herdr else "worker"
            click.echo(t("cli.worker.task_finished", where=where, status=status))
            if res.get("modified_files"):
                click.echo(t("cli.worker.modified_files", files=res["modified_files"]))

            # Se estiver rodando dentro de um pane criado pelo Herdr, grava o arquivo de resultado para o pai
            if run_id and in_pane:
                runs_dir = os.path.join(resolved_cwd, ".meister", "runs")
                result_file = os.path.join(runs_dir, f"{run_id}.json")
                write_atomic_json(result_file, res)
                click.echo(t("cli.worker.generated_successfully"))
                click.echo(t("cli.worker.awaiting_approval"))

            return

        except WorkerInfrastructureError as e:
            if subtask_wt is not None and wt_mgr is not None:
                wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
            if pipeline is not None:
                pipeline.abort_integration()
            worker_duration_ms = round((time.monotonic() - worker_start_time) * 1000.0, 2)
            if not in_pane:
                log_event(
                    event_type="worker_end",
                    run_id=resolved_run_id,
                    task_id=resolved_task_id,
                    tier=model,
                    duration_ms=worker_duration_ms,
                    exit_code=2,
                    **_worker_usage_event_fields(None),
                    status="infrastructure_error",
                    error=str(e),
                )
            click.echo(t("cli.worker.infrastructure_error", error=e), err=True)
            sys.exit(2)

        except TimeoutError as e:
            if subtask_wt is not None and wt_mgr is not None:
                wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
            if pipeline is not None:
                pipeline.abort_integration()
            worker_duration_ms = round((time.monotonic() - worker_start_time) * 1000.0, 2)
            if not in_pane:
                log_event(
                    event_type="worker_end",
                    run_id=resolved_run_id,
                    task_id=resolved_task_id,
                    tier=model,
                    duration_ms=worker_duration_ms,
                    exit_code=1,
                    **_worker_usage_event_fields(None),
                    status="timeout",
                    error=str(e),
                )
            click.echo(t("cli.worker.timeout", error=e), err=True)
            sys.exit(1)
        except Exception as e:
            if subtask_wt is not None and wt_mgr is not None:
                wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
            if pipeline is not None:
                pipeline.abort_integration()
            worker_duration_ms = round((time.monotonic() - worker_start_time) * 1000.0, 2)
            if not in_pane:
                log_event(
                    event_type="worker_end",
                    run_id=resolved_run_id,
                    task_id=resolved_task_id,
                    tier=model,
                    duration_ms=worker_duration_ms,
                    exit_code=1,
                    **_worker_usage_event_fields(None),
                    status="error",
                    error=str(e),
                )
            click.echo(t("cli.worker.dispatch_failed", error=e), err=True)
            if run_id and in_pane:
                runs_dir = os.path.join(resolved_cwd, ".meister", "runs")
                result_file = os.path.join(runs_dir, f"{run_id}.json")
                write_atomic_json(result_file, {"status": "error", "error": str(e)})
                click.echo(t("cli.worker.task_failed_terminal_kept"))
            sys.exit(1)
    else:
        click.echo(t("cli.worker.starting", model=model))
        run_worker_interactive_loop(model=model, cwd=cwd)


@main.command("run-task", help=t("cli.run_task.help"))
@click.argument("task_file", type=click.Path(exists=True))
@click.option("--result-file", default=None, help=t("cli.run_task.result_file_help"))
def run_task(task_file, result_file):
    """Safely and atomically run a task described in a task.json file."""
    from meister.worker import execute_task_file, install_termination_handlers
    with install_termination_handlers():
        try:
            res = execute_task_file(task_file, result_file=result_file)
            status = res.get("status", "done")
            click.echo(t("cli.run_task.finished", status=status))
            if res.get("modified_files"):
                click.echo(t("cli.worker.modified_files", files=res["modified_files"]))
        except Exception as e:
            click.echo(t("cli.run_task.failed", error=e), err=True)
            sys.exit(1)


@main.command("daemon", help=t("cli.daemon.help"))
@click.option("--start", is_flag=True, default=False, help=t("cli.daemon.start_help"))
@click.option("--stop", is_flag=True, default=False, help=t("cli.daemon.stop_help"))
@click.option("--status", is_flag=True, default=False, help=t("cli.daemon.status_help"))
@click.option("--config", "-c", "config_path", default=None, help=t("cli.common.config_file_help"))
@click.option("--socket-path", default=None, help=t("cli.daemon.socket_path_help"))
@click.option("--pid-file", default=None, help=t("cli.daemon.pid_file_help"))
def daemon(start, stop, status, config_path, socket_path, pid_file):
    """Manage the MeisterRouter daemon lifecycle."""
    resolved_pid = pid_file or get_default_pid_file()

    if status:
        if os.path.exists(resolved_pid):
            try:
                with open(resolved_pid, "r") as f:
                    pid = int(f.read().strip())
                if is_pid_alive(pid):
                    click.echo(f"Meister daemon is running (PID: {pid}).")
                    return
            except Exception:
                pass
        click.echo("Meister daemon is not running.")
        return

    if stop:
        if not os.path.exists(resolved_pid):
            click.echo("Meister daemon is not running.")
            return

        try:
            with open(resolved_pid, "r") as f:
                pid = int(f.read().strip())

            if is_pid_alive(pid):
                os.kill(pid, signal.SIGTERM)
                click.echo(f"Sent SIGTERM to Meister daemon (PID: {pid}).")
            else:
                click.echo("Meister daemon was not running (stale PID file cleaned).")

            if os.path.exists(resolved_pid):
                os.remove(resolved_pid)
        except Exception as e:
            click.echo(f"Error stopping daemon: {e}")
        return

    if start:
        pid_dir = os.path.dirname(os.path.abspath(resolved_pid))
        os.makedirs(pid_dir, exist_ok=True)

        try:
            pid_fd = os.open(resolved_pid, os.O_CREAT | os.O_RDWR, 0o644)
        except OSError as e:
            click.echo(f"Error opening PID file: {e}")
            return

        import fcntl
        try:
            fcntl.flock(pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            try:
                with open(resolved_pid, "r") as f:
                    pid = int(f.read().strip())
            except Exception:
                pid = "unknown"
            click.echo(f"Meister daemon is already running (PID: {pid}).")
            os.close(pid_fd)
            return

        # Check if existing PID inside file belongs to an active process
        content = os.read(pid_fd, 64).decode("utf-8").strip()
        if content:
            try:
                existing_pid = int(content)
                if is_pid_alive(existing_pid):
                    click.echo(f"Meister daemon is already running (PID: {existing_pid}).")
                    try:
                        fcntl.flock(pid_fd, fcntl.LOCK_UN)
                    except Exception:
                        pass
                    os.close(pid_fd)
                    return
            except ValueError:
                pass

        # Write current PID atomically
        os.ftruncate(pid_fd, 0)
        os.lseek(pid_fd, 0, os.SEEK_SET)
        os.write(pid_fd, f"{os.getpid()}\n".encode("utf-8"))
        os.fsync(pid_fd)

        click.echo(f"Starting MeisterRouter daemon (PID: {os.getpid()})...")

        stop_event = asyncio.Event()

        def _signal_handler(sig, frame):
            logger.info("Signal %s received, shutting down daemon...", sig)
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _signal_handler)
            except Exception:
                pass

        async def _run_loop():
            cfg = _load_cli_config(config_path)
            client = get_herdr_client(socket_path=socket_path)
            bridge = HerdrEventBridge(config=cfg, client=client)

            if client is not None:
                try:
                    await client.connect()
                    await client.subscribe_events(bridge.handle_herdr_event)
                    click.echo("MeisterRouter daemon connected to Herdr socket and listening for events.")
                except Exception as e:
                    click.echo(f"Notice: Herdr socket unavailable ({e}). Daemon standing by in background.")
            else:
                click.echo("Notice: Herdr socket not configured. Daemon standing by in background.")

            try:
                while not stop_event.is_set():
                    await asyncio.sleep(0.2)
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
            finally:
                if client is not None:
                    try:
                        await client.close()
                    except Exception:
                        pass
                click.echo("MeisterRouter daemon stopped.")

        try:
            asyncio.run(_run_loop())
        finally:
            try:
                fcntl.flock(pid_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                os.close(pid_fd)
            except Exception:
                pass
            if os.path.exists(resolved_pid):
                try:
                    with open(resolved_pid, "r") as f:
                        if f.read().strip() == str(os.getpid()):
                            os.remove(resolved_pid)
                except Exception:
                    pass
        return

    click.echo("Specify --start, --stop, or --status. Use meister daemon --help for more information.")


@main.command("herdr-action", help=t("cli.herdr_action.help"))
@click.argument("action_id")
@click.option("--workspace-id", default=None, help=t("cli.herdr_action.workspace_id_help"))
@click.option("--pane-id", default=None, help=t("cli.herdr_action.pane_id_help"))
@click.option("--task", "-t", default=None, help=t("cli.common.direct_task_help"))
@click.option("--socket-path", default=None, help=t("cli.daemon.socket_path_help"))
@click.option("--config", "-c", "config_path", default=None, help=t("cli.common.config_file_help"))
def herdr_action(action_id, workspace_id, pane_id, task, socket_path, config_path):
    """Run actions registered by the Herdr plugin."""
    norm_id = action_id.lower().strip()

    if norm_id in ["classify", "classify-task"]:
        context = ""
        client = get_herdr_client(socket_path=socket_path)
        if client is not None:
            try:
                async def _read():
                    await client.connect()
                    try:
                        p_id = pane_id
                        if not p_id:
                            cur = await client.get_current_pane()
                            p_id = cur.get("pane_id")
                        if p_id:
                            return await client.read_pane(p_id)
                        return ""
                    finally:
                        await client.close()
                context = asyncio.run(_read())
            except (Exception, BaseException):
                pass

        if not context:
            context = task or f"Task in workspace {workspace_id or 'default'}"

        result = classify_task(context=context)
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    elif norm_id in ["verify", "verify-gate"]:
        from meister.gate import DeterministicGate
        gate = DeterministicGate(repo_path=os.getcwd())
        test_passed, test_output = gate.run_verification()
        diff_summary = gate.get_diff_summary()
        result = gate.evaluate_completion(diff_summary=diff_summary, test_passed=test_passed)
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    elif norm_id in ["orchestrate", "auto-orchestrate"]:
        cfg = _load_cli_config(config_path)
        client = get_herdr_client(socket_path=socket_path)
        bridge = HerdrEventBridge(config=cfg, client=client)

        async def _run():
            return await bridge.run_orchestration_cycle(
                workspace_id=workspace_id,
                architect_pane_id=pane_id,
                task=task,
            )

        try:
            success = asyncio.run(_run())
        except Exception as e:
            click.echo(t("cli.orchestration.error", error=e), err=True)
            sys.exit(1)

        if success:
            click.echo(t("cli.orchestration.success"))
        else:
            click.echo(t("cli.orchestration.failure"), err=True)
            sys.exit(1)
        return

    else:
        click.echo(
            t("cli.herdr_action.unknown", action_id=action_id),
            err=True,
        )
        sys.exit(1)


@main.command("orchestrate", help=t("cli.orchestrate.help"))
@click.argument("resume_id", required=False)
@click.option("--workspace-id", default=None, help=t("cli.orchestrate.workspace_id_help"))
@click.option("--architect-pane-id", default=None, help=t("cli.orchestrate.architect_pane_id_help"))
@click.option("--task", "-t", default=None, help=t("cli.common.direct_task_help"))
@click.option("--plan-file", default=None, help=t("cli.orchestrate.plan_file_help"))
@click.option("--allow-freeform", is_flag=True, default=False, help=t("cli.orchestrate.allow_freeform_help"))
@click.option("--resume", "resume_enabled", is_flag=True, default=False, help=t("cli.orchestrate.resume_help"))
@click.option("--socket-path", default=None, help=t("cli.daemon.socket_path_help"))
@click.option("--config", "-c", "config_path", default=None, help=t("cli.common.config_file_help"))
@click.option("--quiet", "-q", is_flag=True, default=False, help=t("cli.orchestrate.quiet_help"))
@click.option("--no-open", is_flag=True, default=False, help=t("dashboard.no_open_help"))
def orchestrate(
    resume_id,
    workspace_id,
    architect_pane_id,
    task,
    plan_file,
    allow_freeform,
    resume_enabled,
    socket_path,
    config_path,
    quiet,
    no_open,
):
    """Start the autonomous multi-agent orchestration cycle."""
    from meister.plan import load_plan, PlanError, canonical_json

    if resume_id and not resume_enabled:
        click.echo(t("cli.orchestrate.resume_id_requires_resume"), err=True)
        sys.exit(2)
    resume_source = resume_id if resume_id else ("auto" if resume_enabled else None)

    # If --plan-file provided, load and validate before creating any run
    if plan_file:
        try:
            with open(plan_file, "r", encoding="utf-8") as fh:
                raw_plan_text = fh.read()
        except OSError as exc:
            click.echo(t("cli.orchestrate.read_plan_failed", path=plan_file, error=exc), err=True)
            sys.exit(1)
        try:
            validated_tasks = load_plan(raw_plan_text, allow_freeform=allow_freeform)
        except PlanError as exc:
            click.echo(t("cli.orchestrate.invalid_plan"), err=True)
            for msg in exc.messages:
                click.echo(f"  • {msg}", err=True)
            click.echo(
                t("cli.orchestrate.expected_plan_format"),
                err=True,
            )
            sys.exit(2)
        try:
            from meister.plan_analysis import serial_plan_warning

            warning = serial_plan_warning(validated_tasks)
            if warning:
                click.echo(t("cli.orchestrate.serial_plan_warning", warning=warning), err=True)
        except Exception:
            pass
        # Use canonical JSON as the task text so run_id is stable
        task = canonical_json(validated_tasks)
    elif task and not allow_freeform:
        # Validate strict JSON plan from --task
        try:
            validated = load_plan(task, allow_freeform=False)
        except PlanError as exc:
            click.echo(t("cli.orchestrate.invalid_task_plan"), err=True)
            for msg in exc.messages:
                click.echo(f"  • {msg}", err=True)
            click.echo(
                t("cli.orchestrate.allow_freeform_hint"),
                err=True,
            )
            sys.exit(2)
        try:
            from meister.plan_analysis import serial_plan_warning

            warning = serial_plan_warning(validated)
            if warning:
                click.echo(t("cli.orchestrate.serial_plan_warning", warning=warning), err=True)
        except Exception:
            pass
        task = canonical_json(validated)

    cfg = _load_cli_config(config_path)
    from meister.config import validate_config
    issues = validate_config(cfg)
    errors = [iss for iss in issues if iss.level == "error"]
    warnings = [iss for iss in issues if iss.level == "warning"]
    for w in warnings:
        click.echo(t("cli.common.warning_path", path=w.path, message=w.message), err=True)
    if errors:
        click.echo(t("cli.orchestrate.invalid_config"), err=True)
        for err in errors:
            click.echo(t("cli.orchestrate.config_error", path=err.path, message=err.message), err=True)
        sys.exit(2)

    if not no_open:
        _start_dashboard(Path.cwd(), cfg.dashboard)

    if resume_source is None and task:
        from meister.state import StateManager, compute_run_id

        state_manager = StateManager()
        current_run_id = compute_run_id(task, os.getcwd())
        candidate = state_manager.find_resumable_run(os.getcwd(), exclude_run_id=current_run_id)
        if candidate is not None:
            completed_count = sum(
                1
                for subtask in state_manager.get_subtasks(candidate["run_id"])
                if subtask["status"] == "COMPLETED" and subtask.get("integrated_sha")
            )
            click.echo(
                t(
                    "cli.orchestrate.resume_hint",
                    run_id=candidate["run_id"],
                    state=candidate["state"],
                    count=completed_count,
                )
            )

    try:
        host = select_host(cfg, socket_path=socket_path)
    except HostError as error:
        raise click.ClickException(str(error)) from error
    bridge = HerdrEventBridge(config=cfg, host=host)
    from meister.progress import ProgressReporter, render_summary
    from meister.state import StateManager

    reporter = None if quiet else ProgressReporter(retry_max=int(cfg.retry.pane_lost_attempts))
    if reporter is not None:
        add_event_observer(reporter.on_event)

    async def _run():
        cycle_options = dict(
            workspace_id=workspace_id,
            architect_pane_id=architect_pane_id,
            task=task,
            allow_freeform=allow_freeform,
        )
        if resume_source is not None:
            cycle_options["resume_run_id"] = resume_source
            cycle_options["resume_hint_callback"] = click.echo
        elif task is None:
            cycle_options["resume_hint_callback"] = click.echo
        return await bridge.run_orchestration_cycle(**cycle_options)

    cycle_error: Optional[Exception] = None
    error_exit_code = 1
    interrupted = False
    started_at = time.monotonic()
    from meister.worker import install_termination_handlers
    try:
        with install_termination_handlers():
            success = asyncio.run(_run())
    except ResumeRequestError as e:
        cycle_error = e
        error_exit_code = 2
    except KeyboardInterrupt:
        interrupted = True
    except SystemExit as e:
        if e.code not in (128 + int(signal.SIGTERM), 128 + int(signal.SIGHUP)):
            raise
        interrupted = True
    except Exception as e:
        cycle_error = e
    finally:
        if reporter is not None:
            remove_event_observer(reporter.on_event)

    if interrupted:
        interrupted_run_id = bridge.current_run_id or (reporter.run_id if reporter is not None else None)
        if interrupted_run_id:
            click.echo(t("interrupt.cli_message", run_id=interrupted_run_id), err=True)
        else:
            click.echo(t("interrupt.cli_message_no_run"), err=True)
        sys.exit(130)
    if cycle_error is not None:
        if isinstance(cycle_error, ResumeRequestError):
            click.echo(t("cli.common.error", error=cycle_error), err=True)
        else:
            click.echo(t("cli.orchestration.error", error=cycle_error), err=True)
    elif success:
        click.echo(t("cli.orchestration.success"))
    else:
        click.echo(t("cli.orchestration.failure"), err=True)

    if reporter is not None and reporter.run_id:
        click.echo(
            render_summary(
                StateManager(),
                reporter.run_id,
                reporter.durations,
                time.monotonic() - started_at,
            ),
            err=True,
        )

    if cycle_error is not None:
        sys.exit(error_exit_code)
    if not success:
        sys.exit(1)


@main.command("replay", help=t("cli.replay.help"))
@click.argument("run_id")
@click.option("--json", "json_format", is_flag=True, default=False, help=t("cli.replay.json_help"))
def replay(run_id: str, json_format: bool):
    """Replay and deterministically audit events for a run ID."""
    events = get_events_by_run_id(run_id)

    if not events:
        try:
            from meister.state import StateManager
            sm = StateManager()
            run_record = sm.get_run(run_id)
            if not run_record:
                click.echo(t("cli.replay.run_not_found", run_id=run_id), err=True)
                sys.exit(1)
            subtasks = sm.get_subtasks(run_id)
            if json_format:
                click.echo(json.dumps({"run": run_record, "subtasks": subtasks}, indent=2, ensure_ascii=False))
                return
            click.echo(t("cli.replay.sqlite_title", run_id=run_id))
            click.echo(t("cli.replay.task", task=run_record.get("task_prompt")))
            click.echo(t("cli.replay.status", status=run_record.get("state")))
            click.echo(t("cli.replay.subtasks"))
            for st in subtasks:
                click.echo(f"  • {st.get('subtask_id')}: status={st.get('status')} tier={st.get('assigned_tier')} attempts={st.get('attempts')}")
            return
        except Exception:
            click.echo(t("cli.replay.run_not_found", run_id=run_id), err=True)
            sys.exit(1)

    if json_format:
        click.echo(json.dumps(events, indent=2, ensure_ascii=False))
        return

    # Render a readable timeline.
    click.echo(t("cli.replay.title", run_id=run_id))
    click.echo("=" * 80)
    click.echo(
        t(
            "cli.replay.table_header",
            timestamp=t("cli.replay.timestamp"),
            event=t("cli.replay.event"),
            tier=t("cli.replay.tier"),
            task_id=t("cli.replay.task_id"),
            details=t("cli.replay.details"),
        )
    )
    click.echo("-" * 80)

    total_cost = 0.0
    unknown_cost_events = 0
    known_cost_events = 0
    for ev in events:
        ts = str(ev.get("ts") or ev.get("timestamp") or "")[:23]
        event_name = str(ev.get("event") or ev.get("event_type") or "unknown").upper()
        tier = str(ev.get("tier") or ev.get("model") or "-")[:12]
        task_id = str(ev.get("task_id") or "-")[:12]
        cost_source = ev.get("cost_source")
        cost = float(ev.get("cost") or ev.get("cost_usd") or 0.0)
        if cost_source == "unknown":
            unknown_cost_events += 1
        else:
            total_cost += cost
            if cost_source in ("reported", "estimated") or cost != 0:
                known_cost_events += 1

        info_parts = []
        if ev.get("attempt"):
            info_parts.append(f"att={ev.get('attempt')}")
        if ev.get("duration_ms"):
            info_parts.append(f"{ev.get('duration_ms')}ms")
        if cost_source == "unknown":
            info_parts.append(t("cli.replay.unknown_cost"))
        elif cost > 0 or cost_source in ("reported", "estimated"):
            info_parts.append(f"${cost:.6f}")
        if ev.get("classification"):
            info_parts.append(f"class={ev.get('classification')}")
        if ev.get("action"):
            info_parts.append(f"action={ev.get('action')}")
        if ev.get("error"):
            info_parts.append(f"err={str(ev.get('error'))[:25]}")
        if ev.get("status"):
            info_parts.append(f"status={ev.get('status')}")

        info_str = " | ".join(info_parts)
        click.echo(f"{ts:<24} | {event_name:<18} | {tier:<12} | {task_id:<12} | {info_str}")

    click.echo("=" * 80)
    if unknown_cost_events and not known_cost_events:
        total_display = "?"
    elif unknown_cost_events:
        total_display = f"${total_cost:.6f} + ?"
    else:
        total_display = f"${total_cost:.6f}"
    unknown_label = t(
        "cli.replay.unknown_event" if unknown_cost_events == 1 else "cli.replay.unknown_events"
    )
    click.echo(
        t(
            "cli.replay.total",
            count=len(events),
            cost=total_display,
            unknown_count=unknown_cost_events,
            unknown_label=unknown_label,
        )
    )


# ── plan subcommand group ──────────────────────────────────────────────────────

@main.group("plan", help=t("cli.plan.help"))
def plan_group():
    """Manage canonical orchestration plans (import and validate)."""


@plan_group.command("validate", help=t("cli.plan.validate.help"))
@click.argument("plan_json", type=click.Path(exists=True, readable=True))
def plan_validate(plan_json):
    """Validate a canonical plan JSON file and print any errors.

    PLAN_JSON: path to the .json file to validate.
    """
    from meister.plan import load_plan, PlanError
    try:
        with open(plan_json, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        click.echo(t("cli.common.read_failed", path=plan_json, error=exc), err=True)
        sys.exit(1)
    try:
        tasks = load_plan(raw)
    except PlanError as exc:
        click.echo(t("cli.plan.invalid", path=plan_json), err=True)
        for msg in exc.messages:
            click.echo(f"  • {msg}", err=True)
        sys.exit(2)
    click.echo(t("cli.plan.valid", count=len(tasks), path=plan_json))


@plan_group.command("analyze", help=t("cli.plan.analyze.help"))
@click.argument("plan_json", type=click.Path(exists=True, readable=True))
@click.option("--max-workers", type=click.IntRange(min=1), default=None)
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table", show_default=True, help=t("cli.plan.analyze.format_help"))
def plan_analyze(plan_json, max_workers, fmt):
    """Analyze the expected parallelism of a canonical plan."""
    from meister.plan import PlanError, load_plan
    from meister.plan_analysis import analyze_plan, format_analysis_table

    try:
        with open(plan_json, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        click.echo(t("cli.common.read_failed", path=plan_json, error=exc), err=True)
        sys.exit(1)
    try:
        tasks = load_plan(raw)
    except PlanError as exc:
        click.echo(t("cli.plan.invalid", path=plan_json), err=True)
        for msg in exc.messages:
            click.echo(f"  • {msg}", err=True)
        sys.exit(2)

    if max_workers is None:
        max_workers = load_config(None).concurrency.max_parallel_workers
    analysis = analyze_plan(tasks, max_workers)
    if fmt == "json":
        click.echo(json.dumps(analysis, ensure_ascii=False, indent=2))
    else:
        click.echo(format_analysis_table(analysis, max_workers))


@plan_group.command("import", help=t("cli.plan.import.help"))
@click.argument("plan_md", type=click.Path(exists=True, readable=True))
@click.option("--format", "fmt", default="superpowers", show_default=True,
              help=t("cli.plan.import.format_help"))
@click.option("-o", "--output", "output_path", default=None,
              help=t("cli.plan.import.output_help"))
@click.option("--deps", default="sequential", show_default=True,
              type=click.Choice(["sequential", "files"]),
              help=t("cli.plan.import.deps_help"))
@click.option("--allow-unscoped", is_flag=True, default=False,
              help=t("cli.plan.import.allow_unscoped_help"))
def plan_import(plan_md, fmt, output_path, deps, allow_unscoped):
    """Convert a Markdown plan to canonical JSON.

    PLAN_MD: path to the plan file in the input format.

    Print the task table (id, files, dependencies) for human review before
    running with 'meister orchestrate --plan-file'.
    """
    import meister.plan_adapters  # noqa: F401 — ensure all adapters are registered
    from meister.plan import ADAPTERS, PlanError, canonical_json, validate_tasks

    if fmt not in ADAPTERS:
        known = ", ".join(sorted(ADAPTERS.keys())) or t("cli.plan.none")
        click.echo(t("cli.plan.unknown_format", format=fmt, adapters=known), err=True)
        sys.exit(1)

    try:
        with open(plan_md, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        click.echo(t("cli.common.read_failed", path=plan_md, error=exc), err=True)
        sys.exit(1)

    try:
        adapter_fn = ADAPTERS[fmt]
        if fmt == "superpowers":
            cfg = load_config(cwd=os.getcwd())
            tasks = adapter_fn(
                text,
                deps=deps,
                allow_unscoped=allow_unscoped,
                tolerated_files=cfg.scope.tolerated_files,
            )
        else:
            tasks = adapter_fn(text, deps=deps, allow_unscoped=allow_unscoped)
        validate_tasks(tasks)
    except PlanError as exc:
        click.echo(t("cli.plan.convert_failed", format=fmt), err=True)
        for msg in exc.messages:
            click.echo(f"  • {msg}", err=True)
        sys.exit(2)

    # Print human-readable table
    header = t(
        "cli.plan.import.table_header",
        id=t("cli.plan.import.id"),
        files=t("cli.plan.import.files"),
        depends_on="depends_on",
    )
    separator = "-" * max(len(header), 80)
    click.echo(separator)
    click.echo(header)
    click.echo(separator)
    for task in tasks:
        files_str = ", ".join(task.get("target_files") or []) or t("cli.plan.none")
        deps_str = ", ".join(task.get("depends_on") or []) or "—"
        tid = task.get("id", "?")
        click.echo(f"{tid:<12} {files_str:<50} {deps_str}")
    click.echo(separator)
    click.echo(t("cli.plan.imported", count=len(tasks), path=plan_md, format=fmt, deps=deps))

    # Serialize canonical JSON
    out = canonical_json(tasks)
    if output_path:
        try:
            with open(output_path, "w", encoding="utf-8") as fh:
                fh.write(out)
            click.echo(t("cli.plan.saved", path=output_path))
        except OSError as exc:
            click.echo(t("cli.common.write_failed", path=output_path, error=exc), err=True)
            sys.exit(1)
    else:
        click.echo(out)


# ── config subcommand group ───────────────────────────────────────────────────

@main.group("config", help=t("cli.config.help"))
def config_group():
    """Manage and validate the MeisterRouter configuration."""


@config_group.command("show", help=t("cli.config.show.help"))
@click.option("--config-path", "-c", "config_path", default=None, help=t("cli.common.config_file_help"))
@click.option("--json", "json_format", is_flag=True, default=False, help=t("cli.config.json_help"))
def config_show(config_path, json_format):
    """Show the active MeisterRouter configuration."""
    cfg = _load_cli_config(config_path)

    from meister.i18n import get_language

    active_overrides = {
        k: os.environ[k]
        for k in sorted(os.environ.keys())
        if k in ("MEISTER_CONFIG_PATH", "MEISTER_LANG")
    }

    if json_format:
        data = {
            "active_env_overrides": active_overrides,
            "language": get_language(),
            "architect": {
                "effort": cfg.architect.effort,
                "harness": cfg.architect.harness,
                "model": cfg.architect.model,
                "note": t("cli.config.architect_note"),
                "prompt_template": cfg.architect.prompt_template,
            },
            "concurrency": {
                "isolation_mode": cfg.concurrency.isolation_mode,
                "layout_strategy": cfg.concurrency.layout_strategy,
                "max_parallel_workers": cfg.concurrency.max_parallel_workers,
                "parallel_tasks": cfg.concurrency.parallel_tasks,
            },
            "dashboard": {
                "idle_exit_minutes": cfg.dashboard.idle_exit_minutes,
                "open": cfg.dashboard.open,
                "window": {
                    "height": cfg.dashboard.window.height,
                    "width": cfg.dashboard.window.width,
                },
            },
            "environment": {
                "install_dependencies": cfg.environment.install_dependencies,
                "install_timeout_seconds": cfg.environment.install_timeout_seconds,
            },
            "env_overrides": active_overrides,
            "gate": {
                "allow_unverified": cfg.gate.allow_unverified,
                "commands": [
                    {
                        "name": command.name,
                        "required": command.required,
                        "run": command.run,
                        "timeout_seconds": command.timeout_seconds,
                        "ok_exit_codes": command.ok_exit_codes,
                    }
                    for command in cfg.gate.commands
                ],
                "docs_only": {
                    "enabled": cfg.gate.docs_only.enabled,
                    "paths": cfg.gate.docs_only.paths,
                    "commands": [
                        {
                            "name": command.name,
                            "required": command.required,
                            "run": command.run,
                            "timeout_seconds": command.timeout_seconds,
                            "ok_exit_codes": command.ok_exit_codes,
                        }
                        for command in cfg.gate.docs_only.commands
                    ],
                },
                "install": cfg.gate.install,
                "python": cfg.gate.python,
                "repair_attempts": cfg.gate.repair_attempts,
            },
            "master": {
                "api_key_env": cfg.master.api_key_env,
                "model": cfg.master.model,
                "provider": cfg.master.provider,
                "temperature": cfg.master.temperature,
            },
            "router": {
                "mode": cfg.router.mode,
                "timeout_seconds": cfg.router.timeout_seconds,
                "max_attempts": cfg.router.max_attempts,
                "unavailable_cooldown_seconds": cfg.router.unavailable_cooldown_seconds,
                "context_max_chars": cfg.router.context_max_chars,
            },
            "retry": {
                "pane_lost_attempts": cfg.retry.pane_lost_attempts,
                "pane_lost_backoff_seconds": cfg.retry.pane_lost_backoff_seconds,
            },
            "source": cfg.config_source,
            "scope": {"tolerated_files": cfg.scope.tolerated_files},
            "version": cfg.version,
            "workers": {
                "idle_timeout_seconds": cfg.workers.idle_timeout_seconds,
                "max_runtime_seconds": cfg.workers.max_runtime_seconds,
                "disabled": [
                    {
                        "best_for": t.best_for,
                        "cost_per_m_tokens": t.cost_per_m_tokens,
                        "credit_usd": t.credit_usd,
                        "eligible_classes": t.eligible_classes,
                        "enabled": False,
                        "effort": t.effort,
                        "harness": t.harness,
                        "max_retries": t.max_retries,
                        "max_parallel": t.max_parallel,
                        "idle_timeout_seconds": t.idle_timeout_seconds,
                        "max_runtime_seconds": t.max_runtime_seconds,
                        "model": t.model,
                        "name": t.name,
                    }
                    for t in cfg.workers.disabled
                ],
                "tier_order": [
                    {
                        "best_for": t.best_for,
                        "cost_per_m_tokens": t.cost_per_m_tokens,
                        "credit_usd": t.credit_usd,
                        "eligible_classes": t.eligible_classes,
                        "enabled": True,
                        "effort": t.effort,
                        "harness": t.harness,
                        "max_retries": t.max_retries,
                        "max_parallel": t.max_parallel,
                        "idle_timeout_seconds": t.idle_timeout_seconds,
                        "max_runtime_seconds": t.max_runtime_seconds,
                        "model": t.model,
                        "name": t.name,
                        "position": i + 1,
                    }
                    for i, t in enumerate(cfg.workers.tier_order)
                ],
            },
        }
        click.echo(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
        return

    click.echo(t("cli.config.source", source=cfg.config_source) + "\n")
    click.echo(f"Language: {get_language()}\n")
    click.echo(t("cli.config.env_overrides"))
    if active_overrides:
        for k, v in sorted(active_overrides.items()):
            click.echo(f"  {k}={v}")
    else:
        click.echo(t("cli.config.none"))

    click.echo("\nMaster:")
    click.echo(f"  Provider: {cfg.master.provider}")
    click.echo(f"  Model: {cfg.master.model}")
    click.echo(f"  Temperature: {cfg.master.temperature}")
    click.echo(f"  API Key Env: {cfg.master.api_key_env}")

    click.echo("\nRouter:")
    click.echo(f"  Mode: {cfg.router.mode}")
    click.echo(f"  Timeout Seconds: {cfg.router.timeout_seconds}")
    click.echo(f"  Max Attempts: {cfg.router.max_attempts}")
    click.echo(f"  Unavailable Cooldown Seconds: {cfg.router.unavailable_cooldown_seconds}")
    click.echo(f"  Context Max Chars: {cfg.router.context_max_chars}")

    click.echo("\nRetry:")
    click.echo(f"  Pane Lost Attempts: {cfg.retry.pane_lost_attempts}")
    click.echo(f"  Pane Lost Backoff Seconds: {cfg.retry.pane_lost_backoff_seconds}")

    click.echo("\n" + t("cli.config.architect_heading"))
    click.echo(f"  Harness: {cfg.architect.harness}")
    click.echo(f"  Model: {cfg.architect.model}")
    click.echo(f"  Effort: {cfg.architect.effort}")
    click.echo(t("cli.config.architect_note_indented"))

    click.echo("\n" + t("cli.config.enabled_lanes"))
    click.echo(t("cli.config.idle_timeout", seconds=cfg.workers.idle_timeout_seconds))
    click.echo(t("cli.config.max_runtime", seconds=cfg.workers.max_runtime_seconds))
    if cfg.workers.tier_order:
        header = f"  {t('cli.config.position'):<4} {t('cli.config.name'):<16} {'Harness':<12} {t('cli.config.model'):<24} {'Effort':<8} {'Max Retries':<11} {'Max Parallel':<12} {'Idle (s)':<10} {'Runtime (s)':<12} {'Credit USD':<10} {'Classes':<20}"
        click.echo(header)
        click.echo("  " + "-" * (len(header) - 2))
        for i, tier in enumerate(cfg.workers.tier_order):
            effort = "-" if tier.effort is None else tier.effort
            max_parallel = "-" if tier.max_parallel is None else str(tier.max_parallel)
            idle_timeout = "-" if tier.idle_timeout_seconds is None else str(tier.idle_timeout_seconds)
            max_runtime = "-" if tier.max_runtime_seconds is None else str(tier.max_runtime_seconds)
            credit_usd = "-" if tier.credit_usd is None else f"{tier.credit_usd:g}"
            eligible_classes = ",".join(tier.eligible_classes) or "-"
            click.echo(f"  {i + 1:<4} {tier.name:<16} {tier.harness:<12} {tier.model:<24} {effort:<8} {tier.max_retries:<11} {max_parallel:<12} {idle_timeout:<10} {max_runtime:<12} {credit_usd:<10} {eligible_classes:<20}")
    else:
        click.echo(t("cli.config.no_enabled_lanes"))

    click.echo("\n" + t("cli.config.disabled_lanes"))
    if cfg.workers.disabled:
        header = f"  {t('cli.config.name'):<16} {'Harness':<12} {t('cli.config.model'):<24} {'Effort':<8} {'Max Retries':<11} {'Max Parallel':<12} {'Idle (s)':<10} {'Runtime (s)':<12} {'Credit USD':<10} {'Classes':<20}"
        click.echo(header)
        click.echo("  " + "-" * (len(header) - 2))
        for tier in cfg.workers.disabled:
            effort = "-" if tier.effort is None else tier.effort
            max_parallel = "-" if tier.max_parallel is None else str(tier.max_parallel)
            idle_timeout = "-" if tier.idle_timeout_seconds is None else str(tier.idle_timeout_seconds)
            max_runtime = "-" if tier.max_runtime_seconds is None else str(tier.max_runtime_seconds)
            credit_usd = "-" if tier.credit_usd is None else f"{tier.credit_usd:g}"
            eligible_classes = ",".join(tier.eligible_classes) or "-"
            click.echo(f"  {tier.name:<16} {tier.harness:<12} {tier.model:<24} {effort:<8} {tier.max_retries:<11} {max_parallel:<12} {idle_timeout:<10} {max_runtime:<12} {credit_usd:<10} {eligible_classes:<20}")
    else:
        click.echo(t("cli.config.none"))

    click.echo("\n" + t("cli.config.concurrency"))
    click.echo(f"  Parallel Tasks: {cfg.concurrency.parallel_tasks}")
    click.echo(f"  Max Parallel Workers: {cfg.concurrency.max_parallel_workers}")
    click.echo(f"  Layout Strategy: {cfg.concurrency.layout_strategy}")
    click.echo(f"  Isolation Mode: {cfg.concurrency.isolation_mode}\n")
    click.echo("\n" + t("dashboard.heading"))
    click.echo(f"  {t('dashboard.open_label')}: {cfg.dashboard.open}")
    click.echo(f"  {t('dashboard.idle_exit_minutes_label')}: {cfg.dashboard.idle_exit_minutes}")
    click.echo(f"  {t('dashboard.window_width_label')}: {cfg.dashboard.window.width}")
    click.echo(f"  {t('dashboard.window_height_label')}: {cfg.dashboard.window.height}")
    click.echo(t("cli.config.scope"))
    click.echo(f"  Tolerated Files: {', '.join(cfg.scope.tolerated_files)}")
    click.echo("\n" + t("cli.config.environment"))
    click.echo(f"  Install Dependencies: {cfg.environment.install_dependencies}")
    click.echo(f"  Install Timeout Seconds: {cfg.environment.install_timeout_seconds}")
    click.echo("\nGate:")
    click.echo(f"  Install: {cfg.gate.install}")
    click.echo(f"  Python: {cfg.gate.python}")
    click.echo(f"  Allow Unverified: {cfg.gate.allow_unverified}")
    click.echo(f"  Repair Attempts: {cfg.gate.repair_attempts}")
    click.echo(f"  Docs-only enabled: {cfg.gate.docs_only.enabled}")
    click.echo(f"  Docs-only paths: {', '.join(cfg.gate.docs_only.paths)}")
    for command in cfg.gate.commands:
        click.echo(
            f"  Command {command.name}: {command.run} "
            f"(timeout={command.timeout_seconds}, required={command.required}, "
            f"ok_exit_codes={command.ok_exit_codes})"
        )
    for command in cfg.gate.docs_only.commands:
        click.echo(
            f"  Docs-only command {command.name}: {command.run} "
            f"(timeout={command.timeout_seconds}, required={command.required}, "
            f"ok_exit_codes={command.ok_exit_codes})"
        )


@config_group.command("validate", help=t("cli.config.validate.help"))
@click.option("--config-path", "-c", "config_path", default=None, help=t("cli.common.config_file_help"))
def config_validate(config_path):
    """Validate the MeisterRouter configuration."""
    from meister.config import validate_config
    try:
        cfg = _load_cli_config(config_path)
    except FileNotFoundError as e:
        click.echo(t("cli.config.error", error=e), err=True)
        sys.exit(2)
    issues = validate_config(cfg)
    if not issues:
        click.echo(t("cli.config.valid"))
        return
    for iss in issues:
        tag = t(f"cli.config.level.{iss.level}")
        click.echo(t("cli.config.issue", tag=tag, path=iss.path, message=iss.message))
    has_errors = any(iss.level == "error" for iss in issues)
    if has_errors:
        sys.exit(2)


@config_group.command("init", help=t("cli.config.init.help"))
@click.option(
    "--path",
    "-p",
    "target_path",
    default=None,
    help=t("cli.config.init.path_help"),
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help=t("cli.config.init.force_help"),
)
def config_init(target_path, force):
    """Create a meister.config.yaml with router and workers for this project."""
    from meister.setup_cmd import generate_config_yaml_content

    resolved_path = os.path.abspath(target_path or os.path.join(os.getcwd(), "meister.config.yaml"))
    if os.path.exists(resolved_path) and not force:
        click.echo(
            t("cli.config.init.exists", path=resolved_path),
            err=True,
        )
        sys.exit(1)

    content = generate_config_yaml_content()
    os.makedirs(os.path.dirname(resolved_path), exist_ok=True)
    with open(resolved_path, "w", encoding="utf-8") as f:
        f.write(content)

    click.echo(t("cli.config.init.generated", path=resolved_path))


# Aliases for compatibility with direct callers.
@main.command("clean", help=t("cli.clean.help"))
@click.option("--repo", "repo_path", default=".", show_default=True, help=t("cli.clean.repo_help"))
@click.option("--base", default=None, help=t("cli.clean.base_help"))
@click.option("--apply", "apply_changes", is_flag=True, help=t("cli.clean.apply_help"))
@click.option(
    "--archive-and-delete",
    is_flag=True,
    help=t("cli.clean.archive_help"),
)
@click.option("--keep", multiple=True, help=t("cli.clean.keep_help"))
@click.option("--close-stale-runs", is_flag=True, help=t("cli.clean.close_stale_runs_help"))
@click.option("--force-busy", is_flag=True, help=t("cli.clean.force_busy_help"))
@click.option("--json", "json_format", is_flag=True, help=t("cli.clean.json_help"))
def clean(repo_path, base, apply_changes, archive_and_delete, keep, close_stale_runs, force_busy, json_format):
    """Safely preview and remove stale MeisterRouter branches."""
    from meister.clean import (
        CleanBusyError,
        CleanError,
        apply_cleanup,
        list_processes,
        plan_cleanup,
        render_result,
    )

    if close_stale_runs and not apply_changes:
        raise click.ClickException(t("cli.clean.close_stale_requires_apply"))
    try:
        plan = plan_cleanup(
            repo_path,
            base=base,
            keep=keep,
            apply=apply_changes,
            archive_and_delete=archive_and_delete,
        )
        result = None
        if apply_changes:
            result = apply_cleanup(
                plan,
                keep=keep,
                archive_and_delete=archive_and_delete,
                processes=list_processes(),
                close_stale_runs=close_stale_runs,
                force_busy=force_busy,
            )
        click.echo(render_result(plan, result=result, json_format=json_format))
        if result and result["failures"]:
            sys.exit(1)
    except CleanBusyError as error:
        click.echo(t("cli.common.error", error=error), err=True)
        sys.exit(3)
    except CleanError as error:
        raise click.ClickException(str(error))


cmd_init = init
cmd_setup = setup
cmd_classify = classify
cmd_control = control
cmd_dashboard = dashboard
cmd_install_hooks = install_hooks
cmd_models = models
cmd_test = test
cmd_replay = replay


if __name__ == "__main__":
    main()
