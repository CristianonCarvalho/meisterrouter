"""Catálogo de mensagens dos comandos em português (pt-BR)."""

MESSAGES: dict[str, str] = {
    "commands.setup.binding_orchestrate": "MeisterRouter: orquestrar ciclo autônomo",
    "commands.setup.binding_dashboard": "MeisterRouter: dashboard TUI",
    "commands.setup.binding_timeline": "MeisterRouter: linha do tempo (Gantt)",
    "commands.setup.binding_orchestrate_direct": "MeisterRouter: orquestrar ciclo autônomo (direto)",
    "commands.setup.binding_dashboard_direct": "MeisterRouter: dashboard TUI (direto)",
    "commands.setup.binding_timeline_direct": "MeisterRouter: linha do tempo (Gantt) (direto)",
    "commands.setup.no_changes": "sem mudanças",
    "commands.setup.atomic_write_failed": "Falha na escrita atômica do arquivo: {error}",
    "commands.setup.config_check_failed": "Validação 'herdr config check' falhou ({message}). Conteúdo original restaurado.",
    "commands.setup.config_check_default": "herdr config check falhou",
    "commands.setup.reload_warning": "Configuração aplicada, mas servidor Herdr não pôde ser recarregado ({message})",
    "commands.setup.config_reloaded": "Configuração atualizada e recarregada com sucesso no Herdr",
    "commands.setup.plugin_linked": "Plugin dev.meisterrouter.orchestrator já vinculado no Herdr",
    "commands.setup.plugin_would_link": "[simulação] Vincularia plugin a partir de {path}",
    "commands.setup.plugin_manifest_missing": "herdr-plugin.toml não encontrado na raiz do repositório",
    "commands.setup.plugin_manifest_missing_clone": "herdr-plugin.toml não encontrado na raiz; instale a partir do repositório clonado",
    "commands.setup.plugin_link_success": "Plugin dev.meisterrouter.orchestrator vinculado com sucesso no Herdr",
    "commands.setup.plugin_link_failed": "Falha ao vincular plugin no Herdr: {error}",
    "commands.setup.python_incompatible": "Python {version} incompatível (exige >= 3.9)",
    "commands.setup.python_compatible": "Python {version} (>= 3.9)",
    "commands.setup.herdr_missing": "Herdr não encontrado no PATH. Instale com: curl -fsSL https://herdr.dev/install.sh | sh",
    "commands.setup.herdr_found": "Herdr encontrado em {path}",
    "commands.setup.daemon_inactive": "Daemon não está rodando (sobe junto com o Herdr)",
    "commands.setup.daemon_active": "Daemon ativo (PID: {pid})",
    "commands.setup.worker_found": "CLI do harness '{harness}' ({tier}) encontrada: {path}",
    "commands.setup.worker_missing": "CLI do harness '{harness}' ({tier}) não encontrada no PATH",
    "commands.setup.openrouter_configured": "OPENROUTER_API_KEY configurada",
    "commands.setup.openrouter_not_needed": "OPENROUTER_API_KEY ausente, mas router.mode é 'first' (Jev não é utilizado)",
    "commands.setup.openrouter_missing": "OPENROUTER_API_KEY ausente (o Jev não funcionará; use router.mode: first)",
    "commands.setup.config_error": "Configuração ({source}) [erro {path}]: {message}",
    "commands.setup.config_warning": "Configuração ({source}) [aviso {path}]: {message}",
    "commands.setup.config_valid": "Configuração ativa válida ({source})",
    "commands.setup.config_load_failed": "Erro ao carregar configuração: {error}",
    "commands.setup.config_template": """# meister.config.yaml — Configuração local do MeisterRouter para este projeto.
#
# A lista workers.tier_order abaixo SUBSTITUI a lista padrão inteira.
# Para conferir as vias ativas e custos: meister models
# Para validar este arquivo: meister config validate

router:
  # router.mode: jev escolhe a via inicial via Decisions API (OpenRouter);
  # router.mode: first ignora o Jev e usa a 1ª via ativa da lista abaixo, sem chamadas de rede.
  mode: jev
  timeout_seconds: 10
  max_attempts: 2
  unavailable_cooldown_seconds: 300
  context_max_chars: 4000

workers:
  # A ordem define a cadeia de fallback (sempre para a frente).
  # enabled: false desliga uma via do roteamento automático.
  # eligible_classes restringe as classes (SMALL, MEDIUM, HIGH, ESCALATE) que o Jev pode atribuir à via.
  tier_order:
    # Custo da tier_1: combinado 0,20 até 100k tokens por pedido; acima disso é 1,00.
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
    "commands.setup.read_warning": "Aviso ao ler {path}: {error}",
    "commands.setup.dry_run_shortcuts": "🔍 [Simulação] Verificando atalhos em {path}:",
    "commands.setup.shortcut_already": "  ⏭️ Atalho '{shortcut}': já configurado ({command})",
    "commands.setup.shortcut_conflict": "  ⚠️ Conflito no atalho '{shortcut}': vinculado a outro comando ({command})",
    "commands.setup.shortcut_would_add": "  ➕ Atalho '{shortcut}': seria adicionado ao bloco gerenciado",
    "commands.setup.block_would_write": "\nBloco que seria gravado no config.toml do Herdr:",
    "commands.setup.no_shortcut_changes": "\nNenhuma alteração necessária no config.toml do Herdr.",
    "commands.setup.shortcut_conflicts": "Atalhos do Herdr possuem conflito não sobrescrito: {keys}",
    "commands.setup.shortcuts_updated": "Atalhos do Herdr atualizados com sucesso ({keys})",
    "commands.setup.dry_run_project": "🔍 [Simulação] Inicializaria projeto em '{path}' com hooks (--project)",
    "commands.setup.shortcuts_configured": "Atalhos do Herdr configurados ({keys})",
    "commands.setup.report_title": "Relatório do MeisterRouter Setup",
    "commands.setup.next_steps": "Próximos passos:",
    "commands.setup.open_herdr": "  1. Abra o Herdr (herdr)",
    "commands.setup.run_plan": "  2. meister orchestrate --plan-file plano.json",
    "commands.setup.shortcuts_summary": "  3. Atalhos: {keys}",
    "commands.setup.project_tip": "\nDica: para equipar um projeto: meister setup --project",
    "commands.clean.git_failed": "git {args} falhou: {detail}",
    "commands.clean.not_git_repo": "Não é um repositório Git: {path}",
    "commands.clean.base_missing": "Branch base inexistente: {branch}",
    "commands.clean.base_missing_default": "Branch base inexistente: não há 'main' nem 'master'. Use --base BRANCH.",
    "commands.clean.process_inspection_failed": "Não foi possível inspecionar processos: {error}",
    "commands.clean.archive_ref_failed": "Falha ao criar ref de arquivo {ref}: {error}",
    "commands.clean.simulation_apply_error": "O plano está em simulação; gere-o com apply=True para aplicar.",
    "commands.clean.busy": "Há um processo MeisterRouter em execução; use --force-busy para ignorar.",
    "commands.clean.branch_changed": "A branch {branch} mudou desde o planejamento; nenhuma remoção foi feita.",
    "commands.clean.branch_delete_failed": "Falha ao apagar {branch}: {error}",
    "commands.clean.branch": "BRANCH",
    "commands.clean.ahead": "AHEAD",
    "commands.clean.situation": "SITUACAO",
    "commands.clean.action": "ACAO",
    "commands.clean.situation_merged": "merged",
    "commands.clean.situation_equivalent": "equivalente",
    "commands.clean.situation_archived": "arquivada",
    "commands.clean.situation_unmerged": "UNMERGED",
    "commands.clean.action_in_use": "mantida (branch em uso)",
    "commands.clean.action_keep": "mantida (--keep)",
    "commands.clean.action_unmerged": "mantida (não integrada)",
    "commands.clean.action_archive_delete": "arquivar e apagar",
    "commands.clean.action_delete": "apagar",
    "commands.clean.action_in_use_or_keep": "mantida (branch em uso ou --keep)",
    "commands.clean.action_changed": "mantida (branch alterada)",
    "commands.clean.action_archive_failed": "mantida (falha ao arquivar)",
    "commands.clean.action_delete_failed": "falha ao apagar",
    "commands.clean.action_deleted": "apagada",
    "commands.clean.summary": "Resumo: {evaluated} avaliadas, {deleted_label}, {kept} mantidas, {runs} runs fechados, {refs} refs de arquivo preservadas.",
    "commands.clean.would_delete": "{count} seriam apagadas",
    "commands.clean.deleted": "{count} apagadas",
    "commands.clean.error": "Erro: {error}",
    "commands.clean.simulation_message": "Nada foi alterado. Para aplicar: meister clean --repo {repo} --apply",
    "commands.plan.serial_warning": "plano totalmente serial ({tasks} tarefas em {batches} lotes)",
    "commands.plan.file_estimate_warning": "; a análise por arquivo daria {rounds} passos",
    "commands.plan.unscoped_warning": "tarefas sem target_files rodam isoladas: {tasks}",
    "commands.plan.hot_file_warning": "arquivo quente {file} declarado por {count} tarefas",
    "commands.plan.title": "Análise do plano",
    "commands.plan.metrics": "Tarefas: {tasks} | Lotes: {batches} | Largura máxima: {width}",
    "commands.plan.rounds": "Passos sequenciais (até {workers} workers): {rounds}",
    "commands.plan.batches": "Lotes:",
    "commands.plan.batch": "  Lote {index}: {tasks}",
    "commands.plan.none": "  (nenhum)",
    "commands.plan.none_feminine": "  (nenhuma)",
    "commands.plan.critical_path": "Caminho crítico ({length} tarefas): {tasks}",
    "commands.plan.why_not_parallel": "Por que não paralelo:",
    "commands.plan.file_conflict": "{kind}: {tasks} (arquivos: {files})",
    "commands.plan.reason": "{kind}: {tasks}",
    "commands.plan.hot_files": "Arquivos quentes:",
    "commands.plan.unscoped_tasks": "Tarefas sem escopo:",
    "commands.plan.file_estimate": "Estimativa por arquivo (ignora dependência semântica): {batches} lotes; {rounds} passos",
    "commands.plan.warnings": "Avisos:",
    "commands.env.node_command_unknown": "Não foi possível determinar o comando de instalação Node",
    "commands.env.prepare_failed": "Falha ao preparar ambiente com {command}: {error}",
    "commands.env.install_failed": "Instalação {command} falhou (rc={code}): {output}",
    "commands.timeline.no_runs": "nenhum run disponível",
    "commands.timeline.id_min_length": "ID deve ter pelo menos 6 caracteres: {id}",
    "commands.timeline.id_missing": "inexistente",
    "commands.timeline.id_ambiguous": "ambíguo",
    "commands.timeline.runs_available": "ID {status}: {id}. Runs disponíveis: {runs}",
    "commands.timeline.waiting_missing_log": "aguardando o primeiro run (log ainda não existe)",
    "commands.timeline.waiting_first_run": "aguardando o primeiro run",
    "commands.progress.plan": "Plano: {tasks} tarefas em {batches} lotes (run {run_id})",
    "commands.progress.started": "{prefix} iniciada em {tier}",
    "commands.progress.completed": "{prefix} concluida em {tier} ({duration})",
    "commands.progress.reused": "{prefix} reaproveitada do run {run_id}",
    "commands.progress.retry_timeout": "{prefix} timeout; retentativa {retry}/{maximum} em {tier}",
    "commands.progress.retry_lost": "{prefix} pane perdido; retentativa {retry}/{maximum} em {tier}",
    "commands.progress.timeout_idle": "timeout por inatividade ({seconds} s)",
    "commands.progress.timeout_max": "excedeu o teto de {seconds} s",
    "commands.progress.timeout": "{prefix} {timeout} em {tier}; {action}",
    "commands.progress.quota": "{prefix} cota esgotada em {tier}{suffix}",
    "commands.progress.failed": "{prefix} FALHOU: {reason}",
    "commands.progress.unknown_error": "erro desconhecido",
    "commands.progress.summary": "Resumo do run {run_id}: {total} tarefas | {completed} concluidas | {failed} falhou | {reused} reaproveitadas | tempo total {duration}",
    "commands.progress.status_reused": "reaproveitada",
    "commands.progress.status_completed": "concluida",
    "commands.progress.status_failed": "FALHOU",
    "commands.progress.completed_main": "Concluido: a main foi atualizada.",
    "commands.progress.next_step": "Proximo passo: corrija a causa e rode o mesmo comando (ou --resume se editou o plano).",
}
