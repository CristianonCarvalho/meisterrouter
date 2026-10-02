"""
meister.cli — Interface de linha de comando (CLI) do MeisterRouter.

Comandos:
  init           Inicializa o MeisterRouter em um projeto (gera CLAUDE.md, CODEX.md, AGENTS.md e hooks)
  classify       Classifica uma tarefa via TypeSafe Jev Decisions API
  control        Avalia progresso determinístico e decide próxima ação do ciclo
  dashboard      Inicia o servidor web local de telemetria ou TUI overlay
  install-hooks  Instala hooks no Git (pre-commit) e Claude Code
  models         Exibe a tabela comparativa de inteligência e custos
  test           Testa a conexão com a API de decisões do OpenRouter
  daemon         Gerencia o ciclo de vida do daemon do MeisterRouter para Herdr
  herdr-action   Executa ações integradas do plugin Herdr (classify, verify, orchestrate)
  orchestrate    Inicia o ciclo de orquestração autônoma multi-agente
  worker         Inicia instância de worker do MeisterRouter
"""

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
from typing import Optional

import click

from meister.jev import classify_task, control_cycle, call_decisions
from meister.models import estimate_cost
from meister.hooks import install_git_hook, install_claude_hook
from meister.logger import get_events_by_run_id, log_event, get_current_run
from meister.config import load_config, ensure_meister_dir
from meister.herdr.client import HerdrSocketClient
from meister.herdr.bridge import HerdrEventBridge, ResumeRequestError

logger = logging.getLogger(__name__)
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def is_pid_alive(pid: int) -> bool:
    """Verifica se um processo com o PID fornecido está ativo."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_default_pid_file() -> str:
    """Retorna o caminho padrão do arquivo PID do daemon."""
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
    help="MeisterRouter — Autonomous Multi-Model Orchestration Engine for Claude Code & Codex",
)
def main():
    """Ponto de entrada principal da CLI do MeisterRouter."""
    pass


@main.command("init")
@click.option("--target", "-t", default=".", help="Diretório do projeto de destino (default: atual)")
@click.option(
    "--type",
    "type_",
    type=click.Choice(["all", "claude", "codex"]),
    default="all",
    help="Quais regras gerar",
)
@click.option(
    "--hooks",
    "install_hooks",
    is_flag=True,
    default=False,
    help=(
        "Instala hooks do Git e do Claude. O pre-commit resume alterações em stage e "
        "executa automaticamente os testes disponíveis (npm test, pytest ou cargo test)."
    ),
)
@click.option(
    "--no-hooks",
    is_flag=True,
    default=False,
    help="Opção legada sem efeito; hooks não são instalados por padrão.",
)
@click.option("--force", is_flag=True, default=False, help="Sobrescreve arquivos e hooks existentes.")
def init(target, type_, install_hooks, no_hooks, force):
    """Inicializa as regras do MeisterRouter em um projeto existente."""
    target_dir = os.path.abspath(target or ".")
    click.echo(f"🔮 [MeisterRouter] Inicializando regras em: {target_dir}")
    os.makedirs(target_dir, exist_ok=True)
    created = []
    skipped = []

    rule_files = []
    if type_ in ["all", "claude"]:
        rule_files.append(("CLAUDE.md.template", "CLAUDE.md", "para Claude Code"))
    if type_ in ["all", "codex"]:
        rule_files.append(("CODEX.md.template", "CODEX.md", "para Codex"))
    rule_files.append(("AGENTS.md.template", "AGENTS.md", "diretivas universais para agentes"))
    for template_name, filename, description in rule_files:
        destination = os.path.join(target_dir, filename)
        existed = os.path.exists(destination)
        if existed and not force:
            click.echo(f"  ⏭️ {filename} já existe (não sobrescrito; use --force)")
            skipped.append(filename)
            continue
        template_path = os.path.join(TEMPLATES_DIR, template_name)
        with open(template_path, "r", encoding="utf-8") as template_file:
            content = template_file.read()
        with open(destination, "w", encoding="utf-8") as destination_file:
            destination_file.write(content)
        action = "Sobrescrito" if existed else "Criado"
        click.echo(f"  ✅ {action} {filename} ({description})")
        created.append(filename)

    # 4. Cria diretório local .meister com .gitignore para logs
    ensure_meister_dir(target_dir)
    local_meister = os.path.join(target_dir, ".meister", "logs")
    logs_existed = os.path.isdir(local_meister)
    os.makedirs(local_meister, exist_ok=True)
    if not logs_existed:
        created.append(".meister/logs/")
        click.echo("  ✅ Criado diretório de telemetria local (.meister/logs/)")

    # 5. Instala hooks se solicitado
    if install_hooks:
        ok_git, msg_git = install_git_hook(target_dir, force=force)
        if ok_git:
            click.echo(f"  ✅ {msg_git}")
            created.append("hook Git pre-commit")
        else:
            click.echo(f"  ⏭️ {msg_git}")
            skipped.append("hook Git pre-commit")

        ok_claude, msg_claude = install_claude_hook(target_dir, force=force)
        if ok_claude:
            click.echo(f"  ✅ {msg_claude}")
            created.append("hooks Claude Code")
        else:
            click.echo(f"  ⏭️ {msg_claude}")
            skipped.append("hooks Claude Code")

    summary_created = ", ".join(created) if created else "nenhum item"
    summary_skipped = ", ".join(skipped) if skipped else "nenhum item"
    click.echo(f"\nResumo: criados/instalados: {summary_created}; pulados: {summary_skipped}.")
    if skipped:
        click.echo("Use --force para sobrescrever arquivos de regras e hooks existentes.")
    if not install_hooks:
        click.echo("Hooks não foram instalados; use --hooks para solicitá-los.")
    if no_hooks:
        click.echo("--no-hooks foi aceito por compatibilidade e não altera a opção --hooks.")


@main.command("classify")
@click.option("--context", "-c", required=True, help="Descrição da tarefa para o Jev")
@click.option("--model", "-m", default=None, help="Sobrescrever modelo Jev padrão")
@click.option("--run-id", default=None, help="Correlation ID da execução")
@click.option("--task-id", default=None, help="ID determinístico da tarefa")
def classify(context, model, run_id, task_id):
    """Executa a classificação de complexidade da tarefa."""
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
        sys.stderr.write(f"Erro no classify: {e}\n")
        sys.exit(1)


@main.command("control")
@click.option("--diff-summary", "-d", required=True, help="Resumo do diff gerado")
@click.option(
    "--test-result",
    "-r",
    required=True,
    type=click.Choice(["pass", "fail", "unknown"]),
    help="Resultado dos testes",
)
@click.option("--attempts", "-a", type=int, default=1, help="Número de tentativas acumuladas")
@click.option(
    "--security-sensitive",
    "-s",
    is_flag=True,
    default=False,
    help="Se altera código sensível à segurança",
)
@click.option("--model", "-m", default=None, help="Sobrescrever modelo Jev padrão")
@click.option("--run-id", default=None, help="Correlation ID da execução")
@click.option("--task-id", default=None, help="ID determinístico da tarefa")
@click.option(
    "--close-worker/--no-close-worker",
    default=True,
    help="Fechar automaticamente o terminal do worker no Herdr se aprovado (COMPLETE)",
)
def control(diff_summary, test_result, attempts, security_sensitive, model, run_id, task_id, close_worker):
    """Executa a decisão de controle do loop do agente."""
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
                        click.echo(f"🧹 [MeisterRouter] Terminal do worker ({p_id}) fechado automaticamente após aprovação.")
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
                        click.echo(f"🧹 [MeisterRouter] Terminal do worker ({pane_id}) fechado automaticamente após aprovação.")
                    os.remove(active_pane_file)
                except Exception as e:
                    logger.debug("Could not auto-close legacy worker pane: %s", e)

    except Exception as e:
        sys.stderr.write(f"Erro no control: {e}\n")
        sys.exit(1)


@main.command("dashboard")
@click.option("--port", "-p", type=int, default=5050, help="Porta do dashboard (default: 5050)")
@click.option("--host", default="127.0.0.1", help="Host do dashboard (default: 127.0.0.1)")
@click.option("--tui", is_flag=True, default=False, help="Inicia overlay TUI no Herdr")
def dashboard(port, host, tui):
    """Inicia o servidor de telemetria local ou TUI overlay."""
    if tui:
        from meister.herdr.tui import run_tui_loop
        run_tui_loop()
    else:
        from meister.dashboard.server import start_server
        start_server(host=host, port=port)


@main.command("install-hooks")
@click.option("--target", "-t", default=".", help="Diretório do repositório")
@click.option("--git", is_flag=True, default=False, help="Instalar Git pre-commit hook")
@click.option("--claude", is_flag=True, default=False, help="Instalar Claude Code hook")
def install_hooks(target, git, claude):
    """Instala hooks no Git ou Claude."""
    target_dir = os.path.abspath(target or ".")
    if git or not claude:
        ok, msg = install_git_hook(target_dir)
        click.echo(f"[{'OK' if ok else 'ERRO'}] {msg}")
    if claude:
        ok, msg = install_claude_hook(target_dir)
        click.echo(f"[{'OK' if ok else 'ERRO'}] {msg}")


@main.command("models")
@click.option("--config", "-c", "config_path", default=None, help="Caminho para arquivo config.yaml")
def models(config_path):
    """Imprime as vias e os custos definidos na configuração."""
    cfg = load_config(config_path=config_path)
    click.echo(f"Origem da configuração: {cfg.config_source}")
    click.echo(f"{'#':>3}  {'NOME':<24} {'HARNESS':<16} {'MODELO':<28} {'CUSTO/1M':>10}  STATUS")
    tiers = [
        *((tier, True) for tier in cfg.workers.tier_order),
        *((tier, False) for tier in cfg.workers.disabled),
    ]
    for position, (tier, enabled) in enumerate(tiers, 1):
        status = "ligada" if enabled else "desligada"
        click.echo(
            f"{position:>3}  {tier.name:<24} {tier.harness:<16} {tier.model:<28} "
            f"${tier.cost_per_m_tokens:.3f}  {status}"
        )


@main.command("test")
def test():
    """Testa a conectividade com o OpenRouter Decisions API."""
    click.echo("🔌 Testando conexão com TypeSafe Decisions API (via OpenRouter)...")
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
        click.echo("✅ Conexão estabelecida com sucesso!")
        click.echo(f"Resposta do Jev: {json.dumps(res.get('answers'), ensure_ascii=False, indent=2)}")
    except Exception as e:
        click.echo(f"❌ Falha no teste de conexão: {e}")
        sys.exit(1)


@main.command("worker")
@click.option("--model", "-m", default=None, help="Nome de uma via configurada")
@click.option("--task", "-t", default=None, help="Tarefa de código para execução direta")
@click.option("--files", "-f", default=None, help="Arquivos alvo separados por vírgula")
@click.option("--cwd", default=None, help="Diretório de trabalho")
@click.option("--pane/--no-pane", "pane", default=None, help="Abrir terminal lateral no Herdr em vez de tab")
@click.option("--tab/--no-tab", "tab", default=True, help="Abrir aba dedicada visível no Herdr sem roubar foco (Achado #13, E2E-9)")
@click.option("--split", is_flag=True, default=False, help="Forçar abertura em split pane lateral em vez de tab")
@click.option("--config", "-c", "config_path", default=None, help="Caminho para arquivo config.yaml")
@click.option("--run-id", default=None, help="Correlation ID da execução")
@click.option("--task-id", default=None, help="ID determinístico da tarefa")
def worker(model, task, files, cwd, pane, tab, split, config_path, run_id, task_id):
    """Inicia worker nativo do MeisterRouter."""
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
    cfg = load_config(config_path=config_path, cwd=resolved_cwd)
    valid_tiers = [tier.name for tier in cfg.workers.tier_order]
    if model is None:
        if not valid_tiers:
            raise click.UsageError("Nenhuma via configurada em workers.tier_order")
        model = valid_tiers[0]
    elif model.casefold() not in {name.casefold() for name in [*valid_tiers, *(t.name for t in cfg.workers.disabled)]}:
        listing = ", ".join(valid_tiers)
        if cfg.workers.disabled:
            listing += " (desligadas, só por escolha explícita: " + ", ".join(t.name for t in cfg.workers.disabled) + ")"
        raise click.UsageError(f"Via desconhecida '{model}'. Vias válidas: {listing}")
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
            if should_spawn_in_herdr:
                if use_split_pane:
                    click.echo(f"🔮 [MeisterRouter] Despachando worker ({model}) para terminal lateral no Herdr...")
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
                    click.echo(f"🔮 [MeisterRouter] Despachando worker ({model}) para aba dedicada no Herdr...")
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
                click.echo(f"MeisterRouter worker starting task with tier/model: {model}")
                res = execute_worker_task(model=model, task=task, target_files=target_files, cwd=worker_cwd, config_path=config_path)

            status = res.get("status", "done")
            worker_duration_ms = round((time.monotonic() - worker_start_time) * 1000.0, 2)
            cost_val = res.get("cost")
            if cost_val is None:
                cost_val = estimate_cost(model)

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
                        cost=cost_val,
                        status=status,
                    )
                click.echo(f"❌ [MeisterRouter] Worker terminou com status: {status}", err=True)
                sys.exit(1)

            if not in_pane:
                log_event(
                    event_type="worker_end",
                    run_id=resolved_run_id,
                    task_id=resolved_task_id,
                    tier=model,
                    duration_ms=worker_duration_ms,
                    exit_code=res.get("exit_code", 0),
                    cost=cost_val,
                    status=status,
                    modified_files=res.get("modified_files", []),
                )

            # Se worktree/pipeline estavam ativos, integra determinísticamente (gate -> commit -> merge -> gate -> ff) (E2E-3)
            if pipeline is not None and subtask_wt is not None and wt_mgr is not None:
                ok_int, int_err = pipeline.integrate_subtask(
                    subtask_wt=subtask_wt,
                    target_files=target_files,
                    commit_message=f"worker({model}): {task}",
                )
                wt_mgr.cleanup_worktree(subtask_wt.task_id, force=True)
                subtask_wt = None

                if not ok_int:
                    pipeline.abort_integration()
                    click.echo(f"❌ [MeisterRouter] Falha no portão/merge de integração: {int_err}", err=True)
                    sys.exit(1)

                passed, out = pipeline.validate_final_integration()
                if not passed:
                    pipeline.abort_integration()
                    click.echo(f"❌ [MeisterRouter] Portão de qualidade falhou antes do fast-forward:\n{out}", err=True)
                    sys.exit(1)

                ok_ff, ff_msg = pipeline.apply_fast_forward()
                if not ok_ff:
                    pipeline.abort_integration()
                    click.echo(f"❌ [MeisterRouter] Falha no fast-forward da main: {ff_msg}", err=True)
                    sys.exit(1)

            where = "Herdr pane" if (should_spawn_in_herdr and use_split_pane) else "Herdr tab" if should_spawn_in_herdr else "worker"
            click.echo(f"Worker task finished in {where}. Status: {status}")
            if res.get("modified_files"):
                click.echo(f"Modified files: {res['modified_files']}")

            # Se estiver rodando dentro de um pane criado pelo Herdr, grava o arquivo de resultado para o pai
            if run_id and in_pane:
                runs_dir = os.path.join(resolved_cwd, ".meister", "runs")
                result_file = os.path.join(runs_dir, f"{run_id}.json")
                write_atomic_json(result_file, res)
                click.echo("\n🏁 [Worker] Código gerado com sucesso.")
                click.echo("ℹ️ Aguardando verificação determinística e aprovação do orquestrador (meister control)...")

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
                    cost=0.0,
                    status="infrastructure_error",
                    error=str(e),
                )
            click.echo(f"❌ [MeisterRouter] Erro de infraestrutura no worker ({e}). Abortando sem escalar tier.", err=True)
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
                    cost=estimate_cost(model),
                    status="timeout",
                    error=str(e),
                )
            click.echo(f"❌ [MeisterRouter] Timeout no worker do Herdr ({e}). Reexecução direta bloqueada para evitar trabalho duplicado.", err=True)
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
                    cost=estimate_cost(model),
                    status="error",
                    error=str(e),
                )
            click.echo(f"❌ [MeisterRouter] Falha ao despachar worker ({e}).", err=True)
            if run_id and in_pane:
                runs_dir = os.path.join(resolved_cwd, ".meister", "runs")
                result_file = os.path.join(runs_dir, f"{run_id}.json")
                write_atomic_json(result_file, {"status": "error", "error": str(e)})
                click.echo("\n❌ [Worker] Falha na execução da tarefa. Terminal mantido para inspeção de erro.")
            sys.exit(1)
    else:
        click.echo(f"MeisterRouter worker starting with tier/model: {model}")
        run_worker_interactive_loop(model=model, cwd=cwd)


@main.command("run-task")
@click.argument("task_file", type=click.Path(exists=True))
@click.option("--result-file", default=None, help="Caminho alternativo para o arquivo de resultado")
def run_task(task_file, result_file):
    """Executa uma tarefa descrita em um arquivo task.json de forma segura e atômica."""
    from meister.worker import execute_task_file
    try:
        res = execute_task_file(task_file, result_file=result_file)
        status = res.get("status", "done")
        click.echo(f"Task finished. Status: {status}")
        if res.get("modified_files"):
            click.echo(f"Modified files: {res['modified_files']}")
    except Exception as e:
        click.echo(f"Task failed: {e}", err=True)
        sys.exit(1)


@main.command("daemon")
@click.option("--start", is_flag=True, default=False, help="Inicia o daemon do MeisterRouter para Herdr")
@click.option("--stop", is_flag=True, default=False, help="Para o daemon em execução")
@click.option("--status", is_flag=True, default=False, help="Verifica o status de execução do daemon")
@click.option("--config", "-c", "config_path", default=None, help="Caminho para arquivo config.yaml")
@click.option("--socket-path", default=None, help="Caminho do UNIX domain socket do Herdr")
@click.option("--pid-file", default=None, help="Caminho alternativo para o arquivo PID")
def daemon(start, stop, status, config_path, socket_path, pid_file):
    """Gerencia o ciclo de vida do daemon do MeisterRouter."""
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
            cfg = load_config(config_path)
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


@main.command("herdr-action")
@click.argument("action_id")
@click.option("--workspace-id", default=None, help="ID do workspace no Herdr")
@click.option("--pane-id", default=None, help="ID do pane ativo no Herdr")
@click.option("--task", "-t", default=None, help="Instrução ou tarefa para orquestração direta")
@click.option("--socket-path", default=None, help="Caminho do UNIX domain socket do Herdr")
@click.option("--config", "-c", "config_path", default=None, help="Caminho para arquivo config.yaml")
def herdr_action(action_id, workspace_id, pane_id, task, socket_path, config_path):
    """Executa ações registradas pelo plugin Herdr."""
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
        cfg = load_config(config_path)
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
            click.echo(f"Orchestration cycle encountered error: {e}", err=True)
            sys.exit(1)

        if success:
            click.echo("Orchestration cycle completed successfully.")
        else:
            click.echo("Orchestration cycle failed or incomplete.", err=True)
            sys.exit(1)
        return

    else:
        click.echo(
            f"Error: Unknown Herdr action '{action_id}'. Supported actions: classify, verify, orchestrate.",
            err=True,
        )
        sys.exit(1)


@main.command("orchestrate")
@click.argument("resume_id", required=False)
@click.option("--workspace-id", default=None, help="ID do workspace no Herdr (auto-detectado se omitido)")
@click.option("--architect-pane-id", default=None, help="ID do pane do arquiteto (auto-detectado se omitido)")
@click.option("--task", "-t", default=None, help="Instrução ou tarefa para orquestração direta")
@click.option("--plan-file", default=None, help="Caminho para arquivo JSON de plano canônico (.json)")
@click.option("--allow-freeform", is_flag=True, default=False, help="Aceitar formatos legados (pipe, markdown, texto livre) — sem validação de esquema")
@click.option("--resume", "resume_enabled", is_flag=True, default=False, help="Retomar tarefas concluídas de um run anterior; opcionalmente informe RUN_ID")
@click.option("--socket-path", default=None, help="Caminho do UNIX domain socket do Herdr")
@click.option("--config", "-c", "config_path", default=None, help="Caminho para arquivo config.yaml")
def orchestrate(resume_id, workspace_id, architect_pane_id, task, plan_file, allow_freeform, resume_enabled, socket_path, config_path):
    """Inicia o ciclo de orquestração autônoma multi-agente."""
    from meister.plan import load_plan, PlanError, canonical_json

    if resume_id and not resume_enabled:
        click.echo("Erro: informe RUN_ID somente junto com --resume.", err=True)
        sys.exit(2)
    resume_source = resume_id if resume_id else ("auto" if resume_enabled else None)

    # If --plan-file provided, load and validate before creating any run
    if plan_file:
        try:
            with open(plan_file, "r", encoding="utf-8") as fh:
                raw_plan_text = fh.read()
        except OSError as exc:
            click.echo(f"Erro ao ler --plan-file {plan_file!r}: {exc}", err=True)
            sys.exit(1)
        try:
            validated_tasks = load_plan(raw_plan_text, allow_freeform=allow_freeform)
        except PlanError as exc:
            click.echo("Erro: plano inválido:", err=True)
            for msg in exc.messages:
                click.echo(f"  • {msg}", err=True)
            click.echo(
                "\nFormato esperado: JSON array de objetos com chaves id, description, target_files, depends_on.",
                err=True,
            )
            sys.exit(2)
        # Use canonical JSON as the task text so run_id is stable
        task = canonical_json(validated_tasks)
    elif task and not allow_freeform:
        # Validate strict JSON plan from --task
        try:
            validated = load_plan(task, allow_freeform=False)
            task = canonical_json(validated)
        except PlanError as exc:
            click.echo("Erro: --task não é um plano JSON canônico válido:", err=True)
            for msg in exc.messages:
                click.echo(f"  • {msg}", err=True)
            click.echo(
                "\nUse --allow-freeform para formatos legados (lista markdown, pipe, texto livre).",
                err=True,
            )
            sys.exit(2)

    cfg = load_config(config_path)
    from meister.config import validate_config
    issues = validate_config(cfg)
    errors = [iss for iss in issues if iss.level == "error"]
    warnings = [iss for iss in issues if iss.level == "warning"]
    for w in warnings:
        click.echo(f"AVISO [{w.path}]: {w.message}", err=True)
    if errors:
        click.echo("Erro: configuração inválida:", err=True)
        for err in errors:
            click.echo(f"  • [{err.path}] {err.message}", err=True)
        sys.exit(2)

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
                f"Run {candidate['run_id']} ({candidate['state']}) tem {completed_count} "
                "tarefas concluidas reaproveitaveis; use --resume"
            )

    client = get_herdr_client(socket_path=socket_path)
    bridge = HerdrEventBridge(config=cfg, client=client)

    async def _run():
        cycle_options = dict(
            workspace_id=workspace_id,
            architect_pane_id=architect_pane_id,
            task=task,
            allow_freeform=allow_freeform,
        )
        if resume_source is not None:
            cycle_options["resume_run_id"] = resume_source
        elif task is None:
            cycle_options["resume_hint_callback"] = click.echo
        return await bridge.run_orchestration_cycle(**cycle_options)

    try:
        success = asyncio.run(_run())
    except ResumeRequestError as e:
        click.echo(f"Erro: {e}", err=True)
        sys.exit(2)
    except Exception as e:
        click.echo(f"Orchestration cycle encountered error: {e}", err=True)
        sys.exit(1)

    if success:
        click.echo("Orchestration cycle completed successfully.")
    else:
        click.echo("Orchestration cycle failed or incomplete.", err=True)
        sys.exit(1)


@main.command("replay")
@click.argument("run_id")
@click.option("--json", "json_format", is_flag=True, default=False, help="Exibe eventos em formato JSON puro")
def replay(run_id: str, json_format: bool):
    """Replay e auditoria determinística dos eventos de uma execução pelo run_id (Achado #32)."""
    events = get_events_by_run_id(run_id)

    if not events:
        try:
            from meister.state import StateManager
            sm = StateManager()
            run_record = sm.get_run(run_id)
            if not run_record:
                click.echo(f"Erro: Nenhuma execução encontrada com run_id '{run_id}'.", err=True)
                sys.exit(1)
            subtasks = sm.get_subtasks(run_id)
            if json_format:
                click.echo(json.dumps({"run": run_record, "subtasks": subtasks}, indent=2, ensure_ascii=False))
                return
            click.echo(f"\n🔮 [MeisterRouter] Replay da Execução: {run_id} (via SQLite)")
            click.echo(f"Tarefa: {run_record.get('task_prompt')}")
            click.echo(f"Status: {run_record.get('state')}")
            click.echo("Subtasks:")
            for st in subtasks:
                click.echo(f"  • {st.get('subtask_id')}: status={st.get('status')} tier={st.get('assigned_tier')} attempts={st.get('attempts')}")
            return
        except Exception:
            click.echo(f"Erro: Nenhuma execução encontrada com run_id '{run_id}'.", err=True)
            sys.exit(1)

    if json_format:
        click.echo(json.dumps(events, indent=2, ensure_ascii=False))
        return

    # Formatação amigável de linha do tempo
    click.echo(f"\n🔮 [MeisterRouter] Replay da Execução: {run_id}")
    click.echo("=" * 80)
    click.echo(f"{'TIMESTAMP':<24} | {'EVENTO':<18} | {'TIER':<12} | {'TASK ID':<12} | {'DETALHES'}")
    click.echo("-" * 80)

    total_cost = 0.0
    for ev in events:
        ts = str(ev.get("ts") or ev.get("timestamp") or "")[:23]
        event_name = str(ev.get("event") or ev.get("event_type") or "unknown").upper()
        tier = str(ev.get("tier") or ev.get("model") or "-")[:12]
        task_id = str(ev.get("task_id") or "-")[:12]
        cost = float(ev.get("cost") or ev.get("cost_usd") or 0.0)
        total_cost += cost

        info_parts = []
        if ev.get("attempt"):
            info_parts.append(f"att={ev.get('attempt')}")
        if ev.get("duration_ms"):
            info_parts.append(f"{ev.get('duration_ms')}ms")
        if cost > 0:
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
    click.echo(f"📊 Total de eventos: {len(events)} | Custo total: ${total_cost:.6f}\n")


# ── plan subcommand group ──────────────────────────────────────────────────────

@main.group("plan")
def plan_group():
    """Gerencia planos canônicos de orquestração (importar, validar)."""


@plan_group.command("validate")
@click.argument("plan_json", type=click.Path(exists=True, readable=True))
def plan_validate(plan_json):
    """Valida um arquivo JSON de plano canônico e imprime os erros encontrados.

    PLAN_JSON: caminho para o arquivo .json a validar.
    """
    from meister.plan import load_plan, PlanError
    try:
        with open(plan_json, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        click.echo(f"Erro ao ler {plan_json!r}: {exc}", err=True)
        sys.exit(1)
    try:
        tasks = load_plan(raw)
    except PlanError as exc:
        click.echo(f"Plano inválido: {plan_json}", err=True)
        for msg in exc.messages:
            click.echo(f"  • {msg}", err=True)
        sys.exit(2)
    click.echo(f"✅ Plano válido: {len(tasks)} tarefas em {plan_json}")


@plan_group.command("import")
@click.argument("plan_md", type=click.Path(exists=True, readable=True))
@click.option("--format", "fmt", default="superpowers", show_default=True,
              help="Formato do plano de entrada (ex: superpowers)")
@click.option("-o", "--output", "output_path", default=None,
              help="Caminho de saída .json (padrão: stdout)")
@click.option("--deps", default="sequential", show_default=True,
              type=click.Choice(["sequential", "files"]),
              help="Estratégia de resolução de dependências")
@click.option("--allow-unscoped", is_flag=True, default=False,
              help="Permitir tarefas sem Files: (target_files vazio)")
def plan_import(plan_md, fmt, output_path, deps, allow_unscoped):
    """Converte um arquivo de plano (markdown) para JSON canônico.

    PLAN_MD: caminho para o arquivo de plano no formato de entrada.

    Imprime a tabela de tarefas (id, arquivos, dependências) para revisão
    humana antes de executar com 'meister orchestrate --plan-file'.
    """
    import meister.plan_adapters  # noqa: F401 — ensure all adapters are registered
    from meister.plan import ADAPTERS, PlanError, canonical_json, validate_tasks

    if fmt not in ADAPTERS:
        known = ", ".join(sorted(ADAPTERS.keys())) or "(nenhum)"
        click.echo(f"Erro: formato desconhecido {fmt!r}. Adaptadores disponíveis: {known}", err=True)
        sys.exit(1)

    try:
        with open(plan_md, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        click.echo(f"Erro ao ler {plan_md!r}: {exc}", err=True)
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
        click.echo(f"Erro ao converter plano ({fmt}):", err=True)
        for msg in exc.messages:
            click.echo(f"  • {msg}", err=True)
        sys.exit(2)

    # Print human-readable table
    header = f"{'id':<12} {'arquivos':<50} {'depends_on'}"
    separator = "-" * max(len(header), 80)
    click.echo(separator)
    click.echo(header)
    click.echo(separator)
    for task in tasks:
        files_str = ", ".join(task.get("target_files") or []) or "(nenhum)"
        deps_str = ", ".join(task.get("depends_on") or []) or "—"
        tid = task.get("id", "?")
        click.echo(f"{tid:<12} {files_str:<50} {deps_str}")
    click.echo(separator)
    click.echo(f"{len(tasks)} tarefas importadas de {plan_md!r} (formato: {fmt}, deps: {deps})")

    # Serialize canonical JSON
    out = canonical_json(tasks)
    if output_path:
        try:
            with open(output_path, "w", encoding="utf-8") as fh:
                fh.write(out)
            click.echo(f"Plano salvo em {output_path!r}")
        except OSError as exc:
            click.echo(f"Erro ao escrever {output_path!r}: {exc}", err=True)
            sys.exit(1)
    else:
        click.echo(out)


# ── config subcommand group ───────────────────────────────────────────────────

@main.group("config")
def config_group():
    """Gerencia e valida a configuração do MeisterRouter."""


@config_group.command("show")
@click.option("--config-path", "-c", "config_path", default=None, help="Caminho para arquivo config.yaml")
@click.option("--json", "json_format", is_flag=True, default=False, help="Exibe configuração em formato JSON estável")
def config_show(config_path, json_format):
    """Exibe a configuração ativa do MeisterRouter."""
    cfg = load_config(config_path)

    active_overrides = {
        k: os.environ[k]
        for k in sorted(os.environ.keys())
        if k == "MEISTER_CONFIG_PATH"
    }

    if json_format:
        data = {
            "active_env_overrides": active_overrides,
            "architect": {
                "effort": cfg.architect.effort,
                "harness": cfg.architect.harness,
                "model": cfg.architect.model,
                "note": "(declarado; ainda nao conectado a nenhum fluxo)",
                "prompt_template": cfg.architect.prompt_template,
            },
            "concurrency": {
                "isolation_mode": cfg.concurrency.isolation_mode,
                "layout_strategy": cfg.concurrency.layout_strategy,
                "max_parallel_workers": cfg.concurrency.max_parallel_workers,
                "parallel_tasks": cfg.concurrency.parallel_tasks,
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
                    }
                    for command in cfg.gate.commands
                ],
                "install": cfg.gate.install,
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
            },
            "source": cfg.config_source,
            "scope": {"tolerated_files": cfg.scope.tolerated_files},
            "version": cfg.version,
            "workers": {
                "disabled": [
                    {
                        "best_for": t.best_for,
                        "cost_per_m_tokens": t.cost_per_m_tokens,
                        "enabled": False,
                        "harness": t.harness,
                        "max_retries": t.max_retries,
                        "max_parallel": t.max_parallel,
                        "model": t.model,
                        "name": t.name,
                    }
                    for t in cfg.workers.disabled
                ],
                "tier_order": [
                    {
                        "best_for": t.best_for,
                        "cost_per_m_tokens": t.cost_per_m_tokens,
                        "enabled": True,
                        "harness": t.harness,
                        "max_retries": t.max_retries,
                        "max_parallel": t.max_parallel,
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

    click.echo(f"Origem: {cfg.config_source}\n")
    click.echo("Variáveis de ambiente (overrides ativos):")
    if active_overrides:
        for k, v in sorted(active_overrides.items()):
            click.echo(f"  {k}={v}")
    else:
        click.echo("  (nenhuma)")

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

    click.echo("\nArquiteto / Planejador:")
    click.echo(f"  Harness: {cfg.architect.harness}")
    click.echo(f"  Model: {cfg.architect.model}")
    click.echo(f"  Effort: {cfg.architect.effort}")
    click.echo("  (declarado; ainda nao conectado a nenhum fluxo)")

    click.echo("\nVias ativas (tier_order):")
    if cfg.workers.tier_order:
        header = f"  {'Pos':<4} {'Nome':<16} {'Harness':<12} {'Modelo':<24} {'Max Retries':<11} {'Max Parallel':<12}"
        click.echo(header)
        click.echo("  " + "-" * (len(header) - 2))
        for i, t in enumerate(cfg.workers.tier_order):
            max_parallel = "-" if t.max_parallel is None else str(t.max_parallel)
            click.echo(f"  {i + 1:<4} {t.name:<16} {t.harness:<12} {t.model:<24} {t.max_retries:<11} {max_parallel:<12}")
    else:
        click.echo("  (nenhuma via ativa)")

    click.echo("\nVias desabilitadas:")
    if cfg.workers.disabled:
        header = f"  {'Nome':<16} {'Harness':<12} {'Modelo':<24} {'Max Retries':<11} {'Max Parallel':<12}"
        click.echo(header)
        click.echo("  " + "-" * (len(header) - 2))
        for t in cfg.workers.disabled:
            max_parallel = "-" if t.max_parallel is None else str(t.max_parallel)
            click.echo(f"  {t.name:<16} {t.harness:<12} {t.model:<24} {t.max_retries:<11} {max_parallel:<12}")
    else:
        click.echo("  (nenhuma)")

    click.echo("\nConcorrência:")
    click.echo(f"  Parallel Tasks: {cfg.concurrency.parallel_tasks}")
    click.echo(f"  Max Parallel Workers: {cfg.concurrency.max_parallel_workers}")
    click.echo(f"  Layout Strategy: {cfg.concurrency.layout_strategy}")
    click.echo(f"  Isolation Mode: {cfg.concurrency.isolation_mode}\n")
    click.echo("Escopo:")
    click.echo(f"  Tolerated Files: {', '.join(cfg.scope.tolerated_files)}")
    click.echo("\nAmbiente:")
    click.echo(f"  Install Dependencies: {cfg.environment.install_dependencies}")
    click.echo(f"  Install Timeout Seconds: {cfg.environment.install_timeout_seconds}")
    click.echo("\nGate:")
    click.echo(f"  Install: {cfg.gate.install}")
    click.echo(f"  Allow Unverified: {cfg.gate.allow_unverified}")
    for command in cfg.gate.commands:
        click.echo(
            f"  Command {command.name}: {command.run} "
            f"(timeout={command.timeout_seconds}, required={command.required})"
        )


@config_group.command("validate")
@click.option("--config-path", "-c", "config_path", default=None, help="Caminho para arquivo config.yaml")
def config_validate(config_path):
    """Valida a configuração do MeisterRouter."""
    from meister.config import validate_config
    try:
        cfg = load_config(config_path)
    except FileNotFoundError as e:
        click.echo(f"ERRO: {e}", err=True)
        sys.exit(2)
    issues = validate_config(cfg)
    if not issues:
        click.echo("Configuracao valida.")
        return
    for iss in issues:
        tag = {"error": "ERRO", "warning": "AVISO", "info": "INFO"}[iss.level]
        click.echo(f"{tag} [{iss.path}]: {iss.message}")
    has_errors = any(iss.level == "error" for iss in issues)
    if has_errors:
        sys.exit(2)


# Aliases para compatibilidade caso chamados diretamente
cmd_init = init
cmd_classify = classify
cmd_control = control
cmd_dashboard = dashboard
cmd_install_hooks = install_hooks
cmd_models = models
cmd_test = test
cmd_replay = replay


if __name__ == "__main__":
    main()
