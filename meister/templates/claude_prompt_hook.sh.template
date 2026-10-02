#!/usr/bin/env bash
# ==============================================================================
# MeisterRouter — Claude Code UserPromptSubmit Hook
# Injeta diretivas determinísticas de orquestração no contexto do Claude Code
# Managed by MeisterRouter
# ==============================================================================

cat << 'EOF'
[MEISTERROUTER MANDATORY DIRECTIVE]
If MEISTER_IN_PANE=1 is set, or you are implementing an assigned subtask, act as a worker:
implement only the assigned changes, run relevant tests, and do not delegate.
Otherwise, act as architect: plan the work and use the configured orchestration flow for
implementation. Do not implement code directly as architect.
EOF

exit 0
