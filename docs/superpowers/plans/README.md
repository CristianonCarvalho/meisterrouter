# Planos: fila de execução e estado

Esta página é o **índice**. Quem for executar um plano (uma pessoa ou outra sessão com o Meister) deve começar por aqui: ela diz o que está pendente, em que ordem e por quê.

Como executar um plano (o fluxo é o do `CLAUDE.md`, seção 05):

```bash
git pull origin main
meister plan import --format superpowers docs/superpowers/plans/<arquivo>.md -o plano.json
meister plan validate plano.json
meister orchestrate --plan-file plano.json
```

**Regra de manutenção:** ao terminar um plano, mova a linha dele da fila para "Executados", com a data, e confira que a suíte inteira passou. Este índice deve ser atualizado no mesmo commit que fecha o plano.

## Fila de execução (pendentes, em ordem)

| # | Plano | Tarefas | Depende de | Observações |
|---|---|---|---|---|
| 8 | windows-falhas-restantes (plano a escrever) | a definir | 4 (feito) | Rodada 1 feita (PR #138): de 124 falhas e 4 erros para **21 falhas e 1 erro** (1580 passados). Restam: `test_usage` 5, `test_worker_instant_exit` 4, `test_task_runner` 4, `test_lane_effort_argv` 2, `test_e2e_process_host` 2 e 1 em cada de `test_worker_early_failure`, `test_tui_without_termios`, `test_osops_posix`, `test_herdr_bridge`, `test_cli_herdr`. Lista em `~/.meister/handoff/WINDOWS_FALHAS_2026-10-10.md`. Depois: tirar o `continue-on-error` do job do Windows. |

Resumo da ordem: **1, 2, 3, 4, depois 5 e 6 (podem andar juntos, respeitada a ressalva da documentação) e 7 por último.**

Passo manual, depois do item 4: quando o job do Windows estiver verde de forma estável, tirar o `continue-on-error` dele em `.github/workflows/ci.yml`.

Desenho de referência da série de hosts: [`../specs/2026-10-09-worker-host-adapters-design.md`](../specs/2026-10-09-worker-host-adapters-design.md).

## Arquivos quentes (não rodar planos que os editam ao mesmo tempo)

- `meister/herdr/bridge.py`: hosts fases 1, 2, 3 e Windows nativo.
- `meister/cli.py`: hosts fases 2 e 3, Windows nativo.
- `meister/worker.py`: hosts fase 3 e Windows nativo.
- `meister/hosts/select.py` e `meister/hosts/__init__.py`: hosts fases 2, 4 e 5.
- `meister/setup_cmd.py`: Windows nativo e hosts fase 5.
- `bin/install.sh`: hosts fase 5.
- `.github/workflows/ci.yml`: hosts fase 4 e o passo manual acima.
- `tests/conftest.py`: Windows base de testes e hosts fase 3.
- `pyproject.toml` e `requirements.txt`: Windows base de testes, Windows nativo e (só `pyproject.toml`) hosts fase 5.
- `docs/advanced.md` e `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`: hosts fases 3 e 4 e Windows nativo.
- `README.md` e `README.pt-BR.md`: Windows nativo e hosts fase 5.

## Executados

Estado verificado pelos artefatos na `main` (o plano não traz uma data própria; a data aqui é a da verificação, 2026-10-10).

| Plano | Evidência na `main` |
|---|---|
| [2026-09-26-meisterrouter-herdr-plugin](2026-09-26-meisterrouter-herdr-plugin.md) | Plugin do Herdr, `meister/herdr/` |
| [2026-10-05-timeline-tui](2026-10-05-timeline-tui.md) | `meister/timeline*.py` |
| [2026-10-08-timeline-web-janela-app](2026-10-08-timeline-web-janela-app.md) | `meister/timeline_graph.py`, `timeline_json.py`, `timeline_app.py` |
| [2026-10-08-tiers-neutros](2026-10-08-tiers-neutros.md) | Vias `tier_1` a `tier_3b` em `meister/default_config.yaml` |
| [2026-10-09-integridade-do-trabalho](2026-10-09-integridade-do-trabalho.md) | `tests/test_worktree_integration_data_loss.py`, evento `cleanup_failure` |
| [2026-10-09-ci-e-testes](2026-10-09-ci-e-testes.md) | Cobertura e `bandit` no `ci.yml`, perna do macOS |
| [2026-10-09-seguranca-e-documentacao](2026-10-09-seguranca-e-documentacao.md) | `--allow-remote`, `docs/jev-data-sent.md` |
| [2026-10-08-timeline-web-melhorias](2026-10-08-timeline-web-melhorias.md) | Commits `subtask(task_1)` a `subtask(task_9)` na `main` (incluindo "Documentação e CHANGELOG"); o `CHANGELOG.md` descreve eixo em minutos, setas, tooltips, cabeçalho da via e aviso sonoro. A suíte não foi rodada nesta verificação. |
| [2026-10-10-hosts-fase1-contrato-e-herdr](2026-10-10-hosts-fase1-contrato-e-herdr.md) | PR #132 (2026-10-10): `meister/hosts/` (contrato `WorkerHost`, `HerdrHost`), `bridge.py` e `workers.py` usando o host; 1595 testes, nenhuma asserção existente alterada. Não verificado: `orchestrate` real com panes vivos do Herdr |
| [2026-10-10-hosts-fase2-adaptador-process](2026-10-10-hosts-fase2-adaptador-process.md) | PR #133 (2026-10-10): `meister/hosts/process.py`, `_proc.py`, `select.py`, seção `runtime` na configuração; 1657 testes; ponta a ponta sem Herdr com harness falso. Tarefa 3b acrescentada na retomada (`killpg` com `PermissionError` no macOS). Não verificado: `orchestrate` real sem Herdr com um CLI de IA de verdade |
| [2026-10-10-windows-base-de-testes](2026-10-10-windows-base-de-testes.md) | PRs #130 e #131 (2026-10-10): base de testes do Windows. Ubuntu e macOS verdes. O `KeyboardInterrupt` do Windows foi resolvido pelo item 4 (PR #135). Restam falhas de produto no Windows (item 8) |
| [2026-10-10-windows-nativo-camada-de-plataforma](2026-10-10-windows-nativo-camada-de-plataforma.md) | PR #135 (2026-10-10): pacote `meister/osops/` (POSIX e Windows), `tests/test_no_raw_posix_calls.py`, host `process` no Windows; 1734 testes. Correções achadas na verificação: `daemon --stop` e limpeza de órfão com processo não-líder (`terminate_pid`, `terminate_group_of`) e `ps -axww`. Windows no CI: sessão completa, 119 falhas restantes. Não verificado: host `process` no Windows com um CLI de IA de verdade |
| [2026-10-10-hosts-fase3-qualquer-harness](2026-10-10-hosts-fase3-qualquer-harness.md) | PR #137 (2026-10-10): `meister/envvars.py` (`MEISTER_WORKER`, com `MEISTER_IN_PANE` mantida), hooks e modelos de instrução, comando `meister logs`, manual; 1786 testes. Tarefas 2, 6 e 7 rodaram na via do Claude (Sonnet) depois de uma falha instantânea do Copilot. Não verificado: `meister logs -f` com worker vivo no host `process` |
| [2026-10-10-hosts-fase4-adaptador-tmux](2026-10-10-hosts-fase4-adaptador-tmux.md) | PR #139 (2026-10-10): `meister/hosts/tmux.py`, seleção `runtime.host: tmux`, tmux instalado no CI; no Ubuntu real, 3 testes ponta a ponta e 25 testes do `TmuxHost` passam sem pular. A run 1b615e9a ficou 1 vez parada por duas runs simultâneas (guard de testes); retomada sozinha |
| [2026-10-10-hosts-fase5-docs-e-instalador](2026-10-10-hosts-fase5-docs-e-instalador.md) | PR #140 (2026-10-10): o Herdr passa a opcional: `meister setup` e `bin/install.sh` funcionam sem ele, README (EN e pt-BR), diagramas e CHANGELOG atualizados; 1834 testes. **A série de hosts (fases 1 a 5) está concluída.** Não verificado: o instalador numa máquina sem Herdr de verdade |
| (extra) correções de 2026-10-10 | PR #136: detector de cota ignora linhas de código e `quota_error` grava `matched_line`. PR #138: console UTF-8 (`meister/console.py`) e testes com suposições de Unix (rodada 1 do item 8) |
