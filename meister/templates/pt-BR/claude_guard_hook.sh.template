#!/usr/bin/env bash
# ==============================================================================
# MeisterRouter — Claude Code PreToolUse Guard Hook
# Managed by MeisterRouter
# Protege edições diretas de código via Edit/Write
# Exit Code 2 bloqueia a execução da ferramenta no Claude Code.
# ==============================================================================

# Workers podem editar a tarefa que receberam. O override também é útil para exceções.
if [ "${MEISTER_IN_PANE}" = "1" ] || [ "${MEISTER_ALLOW_ORCHESTRATOR_EDIT}" = "1" ] || [ -f ".meister/allow_orchestrator" ]; then
    exit 0
fi

# O hook também pode ser executado manualmente, sem stdin.
INPUT=""
IFS= read -r -t 2 -d '' INPUT || true
FILE_PATH=""
if command -v python3 >/dev/null 2>&1 && [ -n "$INPUT" ]; then
    FILE_PATH=$(printf '%s' "$INPUT" | python3 -c '
import json
import sys

try:
    event = json.load(sys.stdin)
    tool_input = event.get("tool_input", {})
    key = "notebook_path" if event.get("tool_name") == "NotebookEdit" else "file_path"
    path = tool_input.get(key, "")
    if isinstance(path, str):
        print(path)
except (AttributeError, TypeError, ValueError):
    pass
' 2>/dev/null) || FILE_PATH=""
fi

# A ausência de um caminho reconhecível é tratada como edição de código.
IS_DOCUMENTATION=0
if [ -n "$FILE_PATH" ]; then
    case "$FILE_PATH" in
        "$PWD"/*) FILE_PATH=${FILE_PATH#"$PWD"/} ;;
    esac
    case "$FILE_PATH" in
        ./*) FILE_PATH=${FILE_PATH#./} ;;
    esac
    case "$FILE_PATH" in
        docs/*|*.md|*.mdx|*.txt) IS_DOCUMENTATION=1 ;;
    esac
    # `docs/../src/app.py` casaria com `docs/*`: um componente `..` nunca conta como documentação
    case "/$FILE_PATH/" in
        */../*) IS_DOCUMENTATION=0 ;;
    esac
fi

if [ "$IS_DOCUMENTATION" = "1" ]; then
    exit 0
fi

MODE="block"
if [ -f ".meister/guard_mode" ]; then
    MODE=$(awk 'NF { print tolower($1); exit }' .meister/guard_mode)
fi

case "$MODE" in
    off)
        exit 0
        ;;
    ask)
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"[MeisterRouter Guard] Edição direta de código: o método de execução é `meister orchestrate`. Permitir esta edição?"}}'
        exit 0
        ;;
    *)
        cat << 'EOF' >&2
❌ [MeisterRouter Guard] Edição direta de código bloqueada!

Você está atuando como Arquiteto. Implemente pelo método `meister orchestrate`;
para pedir confirmação a cada edição de código, use `echo ask > .meister/guard_mode`.
Para desligar o guard, use `echo off > .meister/guard_mode`.

(Para permitir excepcionalmente a edição direta: touch .meister/allow_orchestrator)
EOF
        exit 2
        ;;
esac
