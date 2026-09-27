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
from typing import Optional

import click

from meister.jev import classify_task, control_cycle, call_decisions
from meister.models import MODEL_PRICING
from meister.hooks import install_git_hook, install_claude_hook
from meister.logger import get_log_file, log_event
from meister.config import load_config
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

    # 4. Cria diretório local .meister para logs se desejado
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
def classify(context, model):
    """Executa a classificação de complexidade da tarefa."""
    try:
        result = classify_task(context=context, model=model)
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
@click.option(
    "--close-worker/--no-close-worker",
    default=True,
    help="Fechar automaticamente o terminal do worker no Herdr se aprovado (COMPLETE)",
)
def control(diff_summary, test_result, attempts, security_sensitive, model, close_worker):
    """Executa a decisão de controle do loop do agente."""
    try:
        result = control_cycle(
            diff_summary=diff_summary,
            test_result=test_result,
            attempts=attempts,
            security_sensitive=security_sensitive,
            model=model,
        )
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))

        # Se a ação for COMPLETE e close_worker for True, fecha o terminal do worker automaticamente
        if result.get("action") == "COMPLETE" and close_worker:
            active_pane_file = os.path.join(os.getcwd(), ".meister", "active_worker_pane.txt")
            if os.path.exists(active_pane_file):
                try:
                    with open(active_pane_file, "r", encoding="utf-8") as f:
                        pane_id = f.read().strip()
                    if pane_id:
                        client = get_herdr_client()
                        if client is not None:
                            async def _close():
                                await client.close_pane(pane_id)
                            asyncio.run(_close())
                            click.echo(f"🧹 [MeisterRouter] Terminal do worker ({pane_id}) fechado automaticamente após aprovação.")
                    os.remove(active_pane_file)
                except Exception as e:
                    logger.debug("Could not auto-close worker pane: %s", e)

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
@click.option("--run-id", default=None, hidden=True, help="ID interno de execução do worker em pane")
def worker(model, task, files, cwd, pane, run_id):
    """Inicia worker nativo do MeisterRouter."""
    from meister.worker import (
        execute_worker_task,
        run_worker_interactive_loop,
        is_herdr_available,
        run_worker_in_herdr_pane,
    )
    import json
    import time

    target_files = [f.strip() for f in files.split(",")] if files else None

    if task:
        # Se estivermos no Herdr e não estivermos já dentro de um pane de worker, abre um terminal visível
        in_pane = os.environ.get("MEISTER_IN_PANE") == "1"
        should_split = pane and not in_pane and is_herdr_available()

        if should_split:
            click.echo(f"🔮 [MeisterRouter] Despachando worker ({model}) para terminal lateral no Herdr...")
            try:
                res = run_worker_in_herdr_pane(model=model, task=task, target_files=target_files, cwd=cwd)
                status = res.get("status", "done")
                click.echo(f"Worker task finished in Herdr pane. Status: {status}")
                if res.get("modified_files"):
                    click.echo(f"Modified files: {res['modified_files']}")
                return
            except Exception as e:
                click.echo(f"Aviso: Não foi possível abrir pane no Herdr ({e}). Executando diretamente...", err=True)

        # Execução direta (dentro do pane recém-aberto ou se o Herdr não estiver rodando)
        click.echo(f"MeisterRouter worker starting task with tier/model: {model}")
        try:
            res = execute_worker_task(model=model, task=task, target_files=target_files, cwd=cwd)
            status = res.get("status", "done")
            click.echo(f"Worker task finished. Status: {status}")
            if res.get("modified_files"):
                click.echo(f"Modified files: {res['modified_files']}")

            # Se estiver rodando dentro de um pane criado pelo Herdr, grava o arquivo de resultado para o pai
            if run_id:
                resolved_cwd = os.path.abspath(cwd or os.getcwd())
                runs_dir = os.path.join(resolved_cwd, ".meister", "runs")
                os.makedirs(runs_dir, exist_ok=True)
                result_file = os.path.join(runs_dir, f"{run_id}.json")
                with open(result_file, "w", encoding="utf-8") as f:
                    json.dump(res, f, ensure_ascii=False, indent=2)
                click.echo("\n🏁 [Worker] Código gerado com sucesso.")
                click.echo("ℹ️ Aguardando verificação determinística e aprovação do orquestrador (meister control)...")

        except Exception as e:
            click.echo(f"Worker failed: {e}", err=True)
            if run_id:
                resolved_cwd = os.path.abspath(cwd or os.getcwd())
                runs_dir = os.path.join(resolved_cwd, ".meister", "runs")
                os.makedirs(runs_dir, exist_ok=True)
                result_file = os.path.join(runs_dir, f"{run_id}.json")
                with open(result_file, "w", encoding="utf-8") as f:
                    json.dump({"status": "error", "error": str(e)}, f, ensure_ascii=False, indent=2)
                click.echo("\n❌ [Worker] Falha na execução da tarefa. Terminal mantido para inspeção de erro.")
            sys.exit(1)
    else:
        click.echo(f"MeisterRouter worker starting with tier/model: {model}")
        run_worker_interactive_loop(model=model, cwd=cwd)


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
        # Check if already running
        if os.path.exists(resolved_pid):
            try:
                with open(resolved_pid, "r") as f:
                    pid = int(f.read().strip())
                if is_pid_alive(pid):
                    click.echo(f"Meister daemon is already running (PID: {pid}).")
                    return
                else:
                    os.remove(resolved_pid)
            except Exception:
                pass

        # Write current PID
        os.makedirs(os.path.dirname(os.path.abspath(resolved_pid)), exist_ok=True)
        with open(resolved_pid, "w") as f:
            f.write(str(os.getpid()))

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


# Aliases para compatibilidade caso chamados diretamente
cmd_init = init
cmd_classify = classify
cmd_control = control
cmd_dashboard = dashboard
cmd_install_hooks = install_hooks
cmd_models = models
cmd_test = test


if __name__ == "__main__":
    main()
