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
| 6 | [hosts-fase4-adaptador-tmux](2026-10-10-hosts-fase4-adaptador-tmux.md) | 5 | 3 (feito) | Adaptador `tmux`. O código não toca nos arquivos da fase 3, então **pode rodar em paralelo com o item 5**, com uma ressalva: a Tarefa 5 (documentação) edita `docs/advanced.md` e `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`, os mesmos da Tarefa 7 da fase 3. Rode a Tarefa 5 da fase 4 só depois da Tarefa 7 da fase 3 (ou os dois planos em sequência). Edita `.github/workflows/ci.yml`. |
| 7 | [hosts-fase5-docs-e-instalador](2026-10-10-hosts-fase5-docs-e-instalador.md) | 6 | 3 (feito), 4 (feito), 5 (feito), 6 | O Herdr deixa de ser pré-requisito: `setup`, `install.sh`, README, diagramas, CHANGELOG. Edita `meister/hosts/select.py` e `meister/hosts/__init__.py` (também alterados pela fase 4) e `meister/setup_cmd.py` (também alterado pelo Windows nativo). |
| 8 | windows-falhas-restantes (plano a escrever) | a definir | 4 (feito) | O job do Windows agora roda a suíte inteira (1502 passados, 119 falhas, 4 erros, em 5 min), sem `KeyboardInterrupt`. As falhas são de produto: executáveis do gate no Windows (`ruff`, `pnpm`, `npm`, `yarn` com `WinError 2` e `193`), `test_dashboard_orchestrate`, `test_config`, `test_cli_init`. Ler o log do CI, agrupar por causa e só então tirar o `continue-on-error` do job. |

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
