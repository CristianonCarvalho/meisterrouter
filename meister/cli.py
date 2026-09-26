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
def control(diff_summary, test_result, attempts, security_sensitive, model):
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
        try:
            from meister.herdr.tui import run_tui_loop
            run_tui_loop()
        except ImportError:
            click.echo("TUI dashboard module not found or not yet implemented.")
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
def worker(model):
    """Inicia worker nativo do MeisterRouter."""
    click.echo(f"MeisterRouter worker starting with tier/model: {model}")


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
                        await client.disconnect()
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
@click.option("--socket-path", default=None, help="Caminho do UNIX domain socket do Herdr")
@click.option("--config", "-c", "config_path", default=None, help="Caminho para arquivo config.yaml")
def herdr_action(action_id, workspace_id, pane_id, socket_path, config_path):
    """Executa ações registradas pelo plugin Herdr."""
    norm_id = action_id.lower().strip()

    if norm_id in ["classify", "classify-task"]:
        context = ""
        if pane_id:
            client = get_herdr_client(socket_path=socket_path)
            if client is not None:
                try:
                    async def _read():
                        await client.connect()
                        try:
                            return await client.read_pane(pane_id)
                        finally:
                            await client.disconnect()
                    context = asyncio.run(_read())
                except Exception:
                    pass

        if not context:
            context = f"Task in workspace {workspace_id or 'default'}"

        result = classify_task(context=context)
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    elif norm_id in ["verify", "verify-gate"]:
        from meister.gate import DeterministicGate
        gate = DeterministicGate(repo_path=os.getcwd())
        test_passed, summary = gate.run_verification()
        result = gate.evaluate_completion(summary, test_passed)
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    elif norm_id in ["orchestrate", "auto-orchestrate"]:
        cfg = load_config(config_path)
        client = get_herdr_client(socket_path=socket_path)
        bridge = HerdrEventBridge(config=cfg, client=client)
        ws = workspace_id or "default"
        pane = pane_id or "architect"

        async def _run():
            return await bridge.run_orchestration_cycle(workspace_id=ws, architect_pane_id=pane)

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
@click.option("--workspace-id", default="default", help="ID do workspace no Herdr")
@click.option("--architect-pane-id", default="architect", help="ID do pane do arquiteto")
@click.option("--socket-path", default=None, help="Caminho do UNIX domain socket do Herdr")
@click.option("--config", "-c", "config_path", default=None, help="Caminho para arquivo config.yaml")
def orchestrate(workspace_id, architect_pane_id, socket_path, config_path):
    """Inicia o ciclo de orquestração autônoma multi-agente."""
    cfg = load_config(config_path)
    client = get_herdr_client(socket_path=socket_path)
    bridge = HerdrEventBridge(config=cfg, client=client)

    async def _run():
        return await bridge.run_orchestration_cycle(
            workspace_id=workspace_id,
            architect_pane_id=architect_pane_id,
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
