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
| — | (nenhum plano pendente) | — | — | A fila está vazia. Itens novos entram pelo `BACKLOG.md` (fora do repositório) e viram plano quando o dono decidir. |

Resumo: **toda a série (Windows base, hosts fases 1 a 5, Windows nativo e as rodadas de falhas do Windows) está concluída.**

O job do Windows do CI agora **bloqueia** (PR #150, 2026-10-11): `continue-on-error` ficou só no macOS.

Desenho de referência da série de hosts: [`../specs/2026-10-09-worker-host-adapters-design.md`](../specs/2026-10-09-worker-host-adapters-design.md).

## Arquivos quentes (não rodar planos que os editam ao mesmo tempo)

- `meister/engine/bridge.py` (antes `meister/herdr/bridge.py`): hosts fases 1, 2, 3 e Windows nativo.
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
| (Windows, rodadas 1 a 5) item 8 | PRs #138, #141, #142, #144, #147 e #148 (2026-10-10 e 11): o job do Windows foi de **124 falhas e 4 erros para 0 falhas** (1811 passados no #149). Defeitos de produto achados e corrigidos: console em UTF-8 (`meister/console.py`), `select` sobre pipe (toda execução real de worker falhava no Windows: thread leitora com fila), 41 chamadas `subprocess` com `text=True` sem `encoding`, `daemon --stop` e limpeza de órfão com processo não-líder, `ps -axww`. CI: `mypy --platform linux` na perna do Windows. O job do Windows passou a bloquear no PR #150; a rodada 6 (PR #152) folgou as guardas de tempo dos testes para o runner |
| [2026-10-11-config-screen-design](../specs/2026-10-11-config-screen-design.md) (plano de 9 tarefas) | PR #143 (2026-10-11): tela `/config` (`meister/config_editor.py`, API `GET /api/config`, `PUT /api/config/lanes`, `POST /api/config/restore-default`, página e link na timeline); `ruamel.yaml` como dependência; 409/403/422/400 verificados de verdade. Fica fora: custos, roteador, gate e edição remota |
| (extra) `scope.allow_dependency_files` | PR #145: uma tarefa edita arquivos das tarefas de que depende; prova real com o Copilot (ligada integra, desligada falha). Ligada no `meister.config.yaml` do projeto (PR #146) |
| (extra) retentativa do worker | PR #146: `retry.worker_error_attempts` (padrão 1): erro do worker retenta na mesma via e depois cai para a próxima |
| (extra) proteção do config | PR #149: o worker nunca edita o `meister.config.yaml` (`PROTECTED_FILES`), mensagem de escopo manda desfazer, `config_editor` preserva CRLF |
| (extra) correções de 2026-10-10 | PR #136: detector de cota ignora linhas de código e `quota_error` grava `matched_line`. PR #138: console UTF-8 (`meister/console.py`) e testes com suposições de Unix (rodada 1 do item 8) |
| (Windows, rodada 6) | PR #152 (2026-10-11): guardas de tempo dos testes folgadas para o runner do Windows (`asyncio.wait_for(timeout=3)` e afins); inclui o tooltip da timeline no toque (fecha ao tocar fora, `Escape`, rolagem e auto-esconder; antes #151) |
| (extra) timeline com a run descrevendo a si mesma | PR #153 (2026-10-11), spec [2026-10-11-timeline-run-description-design](../specs/2026-10-11-timeline-run-description-design.md), run a8715051 (8 tarefas): `orchestrate --name`, `worker_spawn` com harness/modelo/esforço, cada tentativa na raia da sua via, motivo no tooltip, marcador de tarefa reaproveitada, demo sintética estendida. PR #156 corrigiu 11 chaves de texto que faltavam em `_timeline_messages` (achado na verificação visual) |
| (extra) `plan new` e `plan guide` | PR #154 (2026-10-11), spec [2026-10-11-plan-new-guide-design](../specs/2026-10-11-plan-new-guide-design.md), run 5cb67c94 (7 tarefas): esqueleto de plano em Markdown (`--format json`, `--tasks`, `--project`), guia do formato, `plan import --format auto`; os modelos do `init` apresentam `plan guide` e `plan new` como primeiro passo; superpowers passa a opcional. Correção do Windows: mensagem sem `!r` |
| (extra) renome do motor (fase 6 dos adaptadores) | PR #155 (2026-10-11), run 489d0819 (5 tarefas): `bridge`, `workers`, `dag` e `events` foram para `meister/engine/`; os caminhos antigos em `meister/herdr/` são aliases do mesmo módulo (`sys.modules`); teste de trava contra importar pelo caminho antigo; `flask` continua obrigatório. **A seção 12 do spec dos adaptadores está fechada.** |
