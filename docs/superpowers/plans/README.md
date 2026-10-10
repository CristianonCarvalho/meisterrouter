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
| 1 | [windows-base-de-testes](2026-10-10-windows-base-de-testes.md) | 8 | nada | **Executado em 2026-10-10 (PR #130), parcial.** As 8 tarefas entraram na `main` (suíte local: 1556 passados; Ubuntu e macOS verdes), mas o job do Windows ainda **para com `KeyboardInterrupt`** depois de cerca de 448 dos 1556 testes (`9 failed, 404 passed, 32 skipped, 3 errors`): falta achar o teste que dispara o Ctrl+C e corrigir `test_copilot_adapter` (`copilot.CMD` x `copilot.cmd`), `test_dashboard_orchestrate` (`_ImmediateThread` sem `args`) e os avisos de PATH em `test_config`/`test_cli_config`. Fica na fila até esse remendo. Plano original: Só infraestrutura de teste (e uma normalização em `meister/herdr/dag.py`). Faz o job do Windows no CI rodar a suíte inteira e mostrar falhas reais. Convém **antes** da fase 1, porque a Tarefa 5 acrescenta `skipif` em `tests/test_herdr_*.py`. |
| 2 | [hosts-fase1-contrato-e-herdr](2026-10-10-hosts-fase1-contrato-e-herdr.md) | 8 | nada | Contrato `WorkerHost` e adaptador `HerdrHost`. Refatoração **sem mudança de comportamento**. As Tarefas 5, 6 e 7 editam `meister/herdr/bridge.py` em sequência. |
| 3 | [hosts-fase2-adaptador-process](2026-10-10-hosts-fase2-adaptador-process.md) | 9 | 2 | Adaptador `process`, `runtime.host`, seleção `auto`, log por worker. A partir daqui o Meister roda sem Herdr. |
| 4 | [windows-nativo-camada-de-plataforma](2026-10-10-windows-nativo-camada-de-plataforma.md) | 11 | 1 e 3 | Pacote `meister.osops`. A Tarefa 2 (troca de `os.kill(pid, 0)`) é pequena, independente das demais e pode ser adiantada. |
| 5 | [hosts-fase3-qualquer-harness](2026-10-10-hosts-fase3-qualquer-harness.md) | 7 | 3 | `meister logs`, `MEISTER_WORKER`, manual de segundo plano. Edita `cli.py`, `worker.py` e `bridge.py`: rodar depois do item 4. |
| 6 | [hosts-fase4-adaptador-tmux](2026-10-10-hosts-fase4-adaptador-tmux.md) | 5 | 3 | Adaptador `tmux`. O código não toca nos arquivos da fase 3, então **pode rodar em paralelo com o item 5**, com uma ressalva: a Tarefa 5 (documentação) edita `docs/advanced.md` e `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`, os mesmos da Tarefa 7 da fase 3. Rode a Tarefa 5 da fase 4 só depois da Tarefa 7 da fase 3 (ou os dois planos em sequência). Edita `.github/workflows/ci.yml`. |
| 7 | [hosts-fase5-docs-e-instalador](2026-10-10-hosts-fase5-docs-e-instalador.md) | 6 | 3, 4 e 5 | O Herdr deixa de ser pré-requisito: `setup`, `install.sh`, README, diagramas, CHANGELOG. Edita `meister/hosts/select.py` e `meister/hosts/__init__.py` (também alterados pela fase 4) e `meister/setup_cmd.py` (também alterado pelo Windows nativo). |

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
