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
from meister.models import MODEL_PRICING, estimate_cost
from meister.hooks import install_git_hook, install_claude_hook
from meister.logger import get_events_by_run_id, log_event, get_current_run
from meister.config import load_config, ensure_meister_dir
from meister.herdr.client import HerdrSocketClient
from meister.herdr.bridge import HerdrEventBridge

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
@click.option("--no-hooks", is_flag=True, default=False, help="Não instalar hooks de Git ou Claude")
def init(target, type_, no_hooks):
    """Inicializa as regras do MeisterRouter em um projeto existente."""
    target_dir = os.path.abspath(target or ".")
    click.echo(f"🔮 [MeisterRouter] Inicializando regras em: {target_dir}")

    # 1. Copia CLAUDE.md
    if type_ in ["all", "claude"]:
        claude_tpl = os.path.join(TEMPLATES_DIR, "CLAUDE.md.template")
        claude_dst = os.path.join(target_dir, "CLAUDE.md")
        with open(claude_tpl, "r", encoding="utf-8") as f:
            content = f.read()
        with open(claude_dst, "w", encoding="utf-8") as f:
            f.write(content)
        click.echo("  ✅ Criado CLAUDE.md (para Claude Code)")

    # 2. Copia CODEX.md
    if type_ in ["all", "codex"]:
        codex_tpl = os.path.join(TEMPLATES_DIR, "CODEX.md.template")
        codex_dst = os.path.join(target_dir, "CODEX.md")
        with open(codex_tpl, "r", encoding="utf-8") as f:
            content = f.read()
        with open(codex_dst, "w", encoding="utf-8") as f:
            f.write(content)
        click.echo("  ✅ Criado CODEX.md (para OpenAI Codex / Canvas / Agents)")

    # 3. Copia AGENTS.md
    agents_tpl = os.path.join(TEMPLATES_DIR, "AGENTS.md.template")
    agents_dst = os.path.join(target_dir, "AGENTS.md")
    with open(agents_tpl, "r", encoding="utf-8") as f:
        content = f.read()
    with open(agents_dst, "w", encoding="utf-8") as f:
        f.write(content)
    click.echo("  ✅ Criado AGENTS.md (Diretivas universais para agentes)")

    # 4. Cria diretório local .meister com .gitignore para logs
    ensure_meister_dir(target_dir)
    local_meister = os.path.join(target_dir, ".meister", "logs")
    os.makedirs(local_meister, exist_ok=True)
    click.echo("  ✅ Criado diretório de telemetria local (.meister/logs/)")

    # 5. Instala hooks se solicitado
    if not no_hooks:
        ok_git, msg_git = install_git_hook(target_dir)
        if ok_git:
            click.echo(f"  ✅ {msg_git}")
        else:
            click.echo(f"  ⚠️ {msg_git}")

        ok_claude, msg_claude = install_claude_hook(target_dir)
        if ok_claude:
            click.echo(f"  ✅ {msg_claude}")

    click.echo("\n🎉 Projeto configurado com sucesso para Claude Code e Codex!")


@main.command("classify")
@click.option("--context", "-c", required=True, help="Descrição da tarefa para o Jev")
@click.option("--model", "-m", default=None, help="Sobrescrever modelo Jev padrão")
@click.option("--run-id", default=None, help="Correlation ID da execução")
@click.option("--task-id", default=None, help="ID determinístico da tarefa")
def classify(context, model, run_id, task_id):
    """Executa a classificação de complexidade da tarefa."""
    try:
        result = classify_task(context=context, model=model, run_id=run_id, task_id=task_id)
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
        result = control_cycle(
            diff_summary=diff_summary,
            test_result=test_result,
            attempts=attempts,
            security_sensitive=security_sensitive,
            model=model,
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
def models():
    """Imprime a tabela de modelos, inteligência e preços."""
    click.echo("\n📊 CATÁLOGO DE MODELOS MEISTERROUTER (Artificial Analysis Benchmark)")
    click.echo("=" * 76)
    click.echo(f"{'MODELO':<30} | {'PAPEL':<18} | {'ÍNDICE':<6} | {'CUSTO 1M':<10}")
    click.echo("-" * 76)
    for key, data in MODEL_PRICING.items():
        if "/" not in key and key not in ["luna", "haiku-4.5", "gemini-3.8-flash", "sonnet-5"]:
            continue
        name = data.get("name", key)
        role = data.get("role", "")
        idx = str(data.get("intelligence_index", "-"))
        cost = f"${data.get('input', 0.0):.2f}/${data.get('output', 0.0):.2f}"
        click.echo(f"{name:<30} | {role:<18} | {idx:<6} | {cost:<10}")
    click.echo("=" * 76)
    click.echo("💡 GPT-6 Luna é o modelo recomendado para workers primários ($0.10 in / $0.50 out).")
    click.echo("💡 Gemini 3.8 Flash é o campeão de automação para tarefas difíceis (Índice 40).\n")


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
@click.option("--model", "-m", default="luna", help="Nome do modelo ou tier do worker")
@click.option("--task", "-t", default=None, help="Tarefa de código para execução direta")
@click.option("--files", "-f", default=None, help="Arquivos alvo separados por vírgula")
@click.option("--cwd", default=None, help="Diretório de trabalho")
@click.option("--pane/--no-pane", default=True, help="Abrir terminal lateral visível no Herdr se disponível")
@click.option("--tab", is_flag=True, default=False, help="Abrir aba dedicada visível no Herdr sem roubar foco (Achado #13)")
@click.option("--config", "-c", "config_path", default=None, help="Caminho para arquivo config.yaml")
@click.option("--run-id", default=None, help="Correlation ID da execução")
@click.option("--task-id", default=None, help="ID determinístico da tarefa")
def worker(model, task, files, cwd, pane, tab, config_path, run_id, task_id):
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
    ensure_meister_dir(resolved_cwd)

    if task:
        # Se estivermos no Herdr e não estivermos já dentro de um pane de worker, abre uma tab ou terminal visível
        in_pane = os.environ.get("MEISTER_IN_PANE") == "1"
        should_split = (tab or pane) and not in_pane and is_herdr_available()

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
            if should_split:
                if tab:
                    click.echo(f"🔮 [MeisterRouter] Despachando worker ({model}) para aba dedicada no Herdr...")
                    res = run_worker_in_herdr_tab(
                        model=model,
                        task=task,
                        target_files=target_files,
                        cwd=worker_cwd,
                        config_path=config_path,
                        run_id=resolved_run_id,
                        task_id=resolved_task_id,
                    )
                else:
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
                # Execução direta (dentro do pane recém-aberto ou se o Herdr não estiver rodando)
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

            where = "Herdr tab" if tab else "Herdr pane" if should_split else "worker"
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
@click.option("--workspace-id", default=None, help="ID do workspace no Herdr (auto-detectado se omitido)")
@click.option("--architect-pane-id", default=None, help="ID do pane do arquiteto (auto-detectado se omitido)")
@click.option("--task", "-t", default=None, help="Instrução ou tarefa para orquestração direta")
@click.option("--socket-path", default=None, help="Caminho do UNIX domain socket do Herdr")
@click.option("--config", "-c", "config_path", default=None, help="Caminho para arquivo config.yaml")
def orchestrate(workspace_id, architect_pane_id, task, socket_path, config_path):
    """Inicia o ciclo de orquestração autônoma multi-agente."""
    cfg = load_config(config_path)
    client = get_herdr_client(socket_path=socket_path)
    bridge = HerdrEventBridge(config=cfg, client=client)

    async def _run():
        return await bridge.run_orchestration_cycle(
            workspace_id=workspace_id,
            architect_pane_id=architect_pane_id,
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
