"""
meister.cli — Interface de linha de comando (CLI) do MeisterRouter.

Comandos:
  init           Inicializa o MeisterRouter em um projeto (gera CLAUDE.md, CODEX.md, AGENTS.md e hooks)
  classify       Classifica uma tarefa via TypeSafe Jev Decisions API
  control        Avalia progresso determinístico e decide próxima ação do ciclo
  dashboard      Inicia o servidor web local de telemetria
  install-hooks  Instala hooks no Git (pre-commit) e Claude Code
  models         Exibe a tabela comparativa de inteligência e custos
  test           Testa a conexão com a API de decisões do OpenRouter
"""

import os
import sys
import json
import argparse
from typing import Optional
from meister.jev import classify_task, control_cycle, call_decisions
from meister.models import MODEL_PRICING
from meister.hooks import install_git_hook, install_claude_hook
from meister.logger import get_log_file, log_event

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def cmd_init(args):
    """Inicializa as regras do MeisterRouter em um projeto existente."""
    target_dir = os.path.abspath(args.target or ".")
    print(f"🔮 [MeisterRouter] Inicializando regras em: {target_dir}")

    # 1. Copia CLAUDE.md
    if args.type in ["all", "claude"]:
        claude_tpl = os.path.join(TEMPLATES_DIR, "CLAUDE.md.template")
        claude_dst = os.path.join(target_dir, "CLAUDE.md")
        with open(claude_tpl, "r", encoding="utf-8") as f:
            content = f.read()
        with open(claude_dst, "w", encoding="utf-8") as f:
            f.write(content)
        print("  ✅ Criado CLAUDE.md (para Claude Code)")

    # 2. Copia CODEX.md
    if args.type in ["all", "codex"]:
        codex_tpl = os.path.join(TEMPLATES_DIR, "CODEX.md.template")
        codex_dst = os.path.join(target_dir, "CODEX.md")
        with open(codex_tpl, "r", encoding="utf-8") as f:
            content = f.read()
        with open(codex_dst, "w", encoding="utf-8") as f:
            f.write(content)
        print("  ✅ Criado CODEX.md (para OpenAI Codex / Canvas / Agents)")

    # 3. Copia AGENTS.md
    agents_tpl = os.path.join(TEMPLATES_DIR, "AGENTS.md.template")
    agents_dst = os.path.join(target_dir, "AGENTS.md")
    with open(agents_tpl, "r", encoding="utf-8") as f:
        content = f.read()
    with open(agents_dst, "w", encoding="utf-8") as f:
        f.write(content)
    print("  ✅ Criado AGENTS.md (Diretivas universais para agentes)")

    # 4. Cria diretório local .meister para logs se desejado
    local_meister = os.path.join(target_dir, ".meister", "logs")
    os.makedirs(local_meister, exist_ok=True)
    print("  ✅ Criado diretório de telemetria local (.meister/logs/)")

    # 5. Instala hooks se solicitado
    if not args.no_hooks:
        ok_git, msg_git = install_git_hook(target_dir)
        if ok_git:
            print(f"  ✅ {msg_git}")
        else:
            print(f"  ⚠️ {msg_git}")

        ok_claude, msg_claude = install_claude_hook(target_dir)
        if ok_claude:
            print(f"  ✅ {msg_claude}")

    print("\n🎉 Projeto configurado com sucesso para Claude Code e Codex!")


def cmd_classify(args):
    """Executa a classificação de complexidade da tarefa."""
    try:
        result = classify_task(context=args.context, model=args.model)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        sys.stderr.write(f"Erro no classify: {e}\n")
        sys.exit(1)


def cmd_control(args):
    """Executa a decisão de controle do loop do agente."""
    try:
        result = control_cycle(
            diff_summary=args.diff_summary,
            test_result=args.test_result,
            attempts=args.attempts,
            security_sensitive=args.security_sensitive,
            model=args.model,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        sys.stderr.write(f"Erro no control: {e}\n")
        sys.exit(1)


def cmd_dashboard(args):
    """Inicia o servidor de telemetria."""
    from meister.dashboard.server import start_server
    start_server(host=args.host, port=args.port)


def cmd_install_hooks(args):
    """Instala hooks no Git ou Claude."""
    target_dir = os.path.abspath(args.target or ".")
    if args.git or not args.claude:
        ok, msg = install_git_hook(target_dir)
        print(f"[{'OK' if ok else 'ERRO'}] {msg}")
    if args.claude:
        ok, msg = install_claude_hook(target_dir)
        print(f"[{'OK' if ok else 'ERRO'}] {msg}")


def cmd_models(args):
    """Imprime a tabela de modelos, inteligência e preços."""
    print("\n📊 CATÁLOGO DE MODELOS MEISTERROUTER (Artificial Analysis Benchmark)")
    print("=" * 76)
    print(f"{'MODELO':<30} | {'PAPEL':<18} | {'ÍNDICE':<6} | {'CUSTO 1M':<10}")
    print("-" * 76)
    for key, data in MODEL_PRICING.items():
        if "/" not in key and key not in ["luna", "haiku-4.5", "gemini-3.8-flash", "sonnet-5"]:
            continue
        name = data.get("name", key)
        role = data.get("role", "")
        idx = str(data.get("intelligence_index", "-"))
        cost = f"${data.get('input', 0.0):.2f}/${data.get('output', 0.0):.2f}"
        print(f"{name:<30} | {role:<18} | {idx:<6} | {cost:<10}")
    print("=" * 76)
    print("💡 GPT-6 Luna é o modelo recomendado para workers primários ($0.10 in / $0.50 out).")
    print("💡 Gemini 3.8 Flash é o campeão de automação para tarefas difíceis (Índice 40).\n")


def cmd_test(args):
    """Testa a conectividade com o OpenRouter Decisions API."""
    print("🔌 Testando conexão com TypeSafe Decisions API (via OpenRouter)...")
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
        print("✅ Conexão estabelecida com sucesso!")
        print(f"Resposta do Jev: {json.dumps(res.get('answers'), ensure_ascii=False, indent=2)}")
    except Exception as e:
        print(f"❌ Falha no teste de conexão: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="meister",
        description="MeisterRouter — Autonomous Multi-Model Orchestration Engine for Claude Code & Codex"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Inicializa o MeisterRouter em um projeto")
    p_init.add_argument("--target", "-t", default=".", help="Diretório do projeto de destino (default: atual)")
    p_init.add_argument("--type", choices=["all", "claude", "codex"], default="all", help="Quais regras gerar")
    p_init.add_argument("--no-hooks", action="store_true", help="Não instalar hooks de Git ou Claude")
    p_init.set_defaults(func=cmd_init)

    # classify
    p_cls = subparsers.add_parser("classify", help="Classifica complexidade de uma tarefa")
    p_cls.add_argument("--context", "-c", required=True, help="Descrição da tarefa para o Jev")
    p_cls.add_argument("--model", "-m", help="Sobrescrever modelo Jev padrão")
    p_cls.set_defaults(func=cmd_classify)

    # control
    p_ctrl = subparsers.add_parser("control", help="Avalia evidências e decide próxima ação do ciclo")
    p_ctrl.add_argument("--diff-summary", "-d", required=True, help="Resumo do diff gerado")
    p_ctrl.add_argument("--test-result", "-r", required=True, choices=["pass", "fail", "unknown"], help="Resultado dos testes")
    p_ctrl.add_argument("--attempts", "-a", type=int, default=1, help="Número de tentativas acumuladas")
    p_ctrl.add_argument("--security-sensitive", "-s", action="store_true", help="Se altera código sensível à segurança")
    p_ctrl.add_argument("--model", "-m", help="Sobrescrever modelo Jev padrão")
    p_ctrl.set_defaults(func=cmd_control)

    # dashboard
    p_dash = subparsers.add_parser("dashboard", help="Inicia o servidor de telemetria local")
    p_dash.add_argument("--port", "-p", type=int, default=5050, help="Porta do dashboard (default: 5050)")
    p_dash.add_argument("--host", default="127.0.0.1", help="Host do dashboard (default: 127.0.0.1)")
    p_dash.set_defaults(func=cmd_dashboard)

    # install-hooks
    p_hk = subparsers.add_parser("install-hooks", help="Instala hooks no Git e Claude")
    p_hk.add_argument("--target", "-t", default=".", help="Diretório do repositório")
    p_hk.add_argument("--git", action="store_true", help="Instalar Git pre-commit hook")
    p_hk.add_argument("--claude", action="store_true", help="Instalar Claude Code hook")
    p_hk.set_defaults(func=cmd_install_hooks)

    # models
    p_mod = subparsers.add_parser("models", help="Exibe tabela de modelos e benchmarks")
    p_mod.set_defaults(func=cmd_models)

    # test
    p_tst = subparsers.add_parser("test", help="Testa conectividade com OpenRouter Decisions API")
    p_tst.set_defaults(func=cmd_test)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
