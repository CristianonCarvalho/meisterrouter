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
| [2026-10-10-hosts-fase5-docs-e-instalador](2026-10-10-hosts-fase5-docs-e-instalador.md) | PR #140 (2026-10-10): o Herdr passa a opcional: `meister setup` e `bin/install.sh` funcionam sem ele, README (EN e pt-BR), diagramas e CHANGELOG atualizados; 1834 testes. **A série de hosts (fases 1 a 5) está concluída.** Verificado em 2026-10-11 num contêiner Linux limpo (python:3.12-bookworm, sem Herdr nem tmux): `bin/install.sh` a partir de uma cópia do repositório e por `curl \| bash` (clona a `main`) instalam, rodam `meister setup` e entregam `meister 0.9.1`; `tests/test_e2e_process_host.py` e `tests/test_hosts_select.py` passam (28) no host `process` com o pacote instalado |
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
| (extra) timeline: motivo do reparo do gate | PR #157 (2026-10-11): `gate_repair` (reason, detail, testes) passa a fechar a tentativa com `scope`/`gate_rejected` e o detalhe no tooltip; antes saía como `error` sem motivo |
| (extra) gate para Java/Maven | PR #158 (2026-10-11), spec [2026-10-11-java-maven-gate-design](../specs/2026-10-11-java-maven-gate-design.md), run 5c5e189a (4 tarefas): `gate.env`, `target/**` tolerado, detecção de `pom.xml` (`mvnw`, `mvn` no PATH, `MAVEN_HOME`), página `docs/java-maven.md`. Verificado de verdade no Mac (JDK 8 + Maven, gate aprova/reprova, `orchestrate` com Copilot). Não verificado: Windows real e o Maven externo do dono |
| (extra) CI com Maven real | PR #159 (2026-10-11), run f5d0514e (2 tarefas) + ajuste de timeout: `tests/test_gate_maven_real.py` (gate aprova, reprova e sem Maven) roda com `MEISTER_REAL_MAVEN=1` no Windows e no Ubuntu 3.12 da CI; verde no `windows-latest` (cobre `mvn.cmd`, PATHEXT e caminhos do Windows). Não verificado: o Maven externo e o JDK do dono |
| (extra) CI: orchestrate ponta a ponta com Maven real | PR #160 (2026-10-11), run d8a8ed0e: `tests/test_e2e_maven_process_host.py` (host `process`, harness falso, projeto Maven real; gate aprova e integra, gate reprova e bloqueia) roda no passo "Real Maven gate test" do Windows e do Ubuntu 3.12; verde no `windows-latest`. Não verificado: o Maven externo e o JDK do dono, e um CLI de IA real no Windows |
| (extra) harness Kiro CLI | PR #161 (2026-10-11), run 1673ca2b (4 tarefas): `kiro-cli` (alias `kiro`) como harness de worker (`kiro-cli chat --no-interactive --trust-all-tools`), nos dois montadores de comando, na validação e na tela `/config`; `docs/kiro.md` com config só com vias Kiro. Verificado de verdade no Mac (kiro-cli 2.29.1): `orchestrate` só com Kiro (host `process`, reparo do gate com o próprio Kiro) e `meister worker` direto. Não verificado: Windows, mensagem de cota esgotada do Kiro, uso/crédito nos relatórios |
| (extra) timeline: dois gates e fila de integração | PR #162 (2026-10-11), run 93cd39a1 (3 tarefas): rótulos "gate: worktree da tarefa" e "gate: após integrar a base", "fila de integração" (tooltip e legenda) e sem fatias de `integrate` de menos de 2 s entre fases. Conferido no navegador com os dados da run do Kiro |
| (extra) setup com aliases de harness | PR #163 (2026-10-12), worker `tier_1`: `find_cli_binary` resolve os aliases (`kiro`, `github-copilot`) pela mesma tabela `_HARNESS_ALIASES` do `resolve_worker_harness_and_model`, então o `meister setup` acha o `kiro-cli`. A CI do Windows pegou rigidez do teste (`.CMD` x `.cmd`), corrigida com `os.path.normcase` |
| (extra) testar lotes Java sem homologação | PR #164 (2026-10-12): `docs/java-testing-without-staging.md` (+ pt-BR) e `docs/examples/java-batch-testing/` (Java 8, Maven, WireMock 2.35, JUnit 5; 14 testes sem Docker: fixtures JSON, site falso com falhas, golden master, dry-run, perfil `verificacao-leitura`). Verificado no JDK 8 + Maven com duas mutações. A CI do repositório não roda o `pom.xml` do exemplo |
| (extra) `config init --kiro` | PR #165 (2026-10-12), run 3882916b (2 tarefas): `meister config init --kiro` grava um `meister.config.yaml` só com o Kiro (`router.mode: first`, 3 vias `kiro-cli`); `docs/kiro.md` aponta para ele. Verificado com `orchestrate` real no `kiro-cli` 2.29.1 (14 s, gate verde). Achado: gate sem testes detectados manda o worker editar o config protegido (BACKLOG I8) |
| (extra) README convincente + GIFs | PR #166 (2026-10-12): README (en e pt-BR) reescrito (abertura, "o que você deixa de fazer", 60 segundos, provas e limites), Herdr numa seção própria e opcional, GIFs novos (hero, gate, fallback, timeline) em en e pt-BR, kit de gravação em `docs/img/src/`; demo da timeline com nomes localizados (run 6ad71b8c). Achado: BACKLOG I9 (linha do gate fora do idioma) |
| (extra) gate sem testes + linhas do reparo no idioma | PR #167 (2026-10-12), run a43ffd70 (2 tarefas): sem testes nem linters detectados o gate vira erro de infraestrutura (sem reparo, mensagem ao dono, trabalho arquivado; BACKLOG I8), e as linhas de reparo/infraestrutura do gate seguem `language: pt-BR` (I9). Verificado com orchestrate real num projeto sem sinais de teste. Pendente: resumo mostra "running" após erro de infra, ".." na mensagem, prefixo pt "no portão" |
| (extra) README sem comandos na abertura | PR #168 (2026-10-12): a timeline é a imagem de abertura; os GIFs de terminal (parallel, gate, fallback) foram para "Por baixo dos panos", um comando cada; kit `docs/img/src/record.sh` ajustado |
| (extra) erro de infraestrutura do gate fecha a subtask | PR #169 (2026-10-12), run f0d4f62c: o ramo de infraestrutura do bridge transiciona a subtask para FAILED (o resumo mostrava "running" e "0 falhou") e a mensagem perde o ponto duplicado. Verificado no cenário real do I8. Fica de fora: prefixo pt "no portão" (estético, fixado em 5 testes) |
| (extra) âncoras do README | PR #170 (2026-10-12): títulos com U+FE0F (🛠️, 🎛️, 🖥️) quebravam os links `#-how-to-use` e `#-herdr-...` no GitHub; emojis trocados e `tests/test_docs_links.py` agora mantém o U+FE0F na âncora |
| `2026-10-12-pre-commit-hook-e-commit-do-worker.md` | PR #171 (2026-10-12), runs 13b8ccb2 (9 tarefas, 46 min) e 91450170 (1 tarefa): commit interno com `--no-verify`, `WorktreeCommitError` com a saída do git tratado como infraestrutura, subtask FAILED em exceção inesperada, arquivamento com `.venv` ignorado, comando `meister gate`, hook gerenciado usando o gate, aviso do `init --hooks` no idioma ativo. Origem: relatório do trade_omqs. Não verificado: causa do `↻4` e uso num projeto real |
| `2026-10-13-venv-simbolico-e-timeout.md` | PR #172 (2026-10-13), runs e5b13f12 (falhou na Task 3 por mypy no `log_event`), d2774ee6 (retomada, interrompida uma vez por causa desconhecida) e retomada final: `.venv`/`venv` simbólico fora de mudança e de commit (antes era commitado), árvore do gate com `.venv` ignorado, `output_tail` no `worker_timeout` e log do worker com Herdr. Origem: adendo do trade_omqs (item 2.3 ficou no BACKLOG I13, 2.4 no I10, causa do idle timeout no I11). Não verificado: timeout real com Herdr |
| `2026-10-13-conflito-de-escopo-e-sinal-9.md` | PR #173 (2026-10-13), runs 4e2db34c (falhou: a Task 1 não listava os testes de reparo que a regra nova quebrava e a Task 4 reprovou no mypy), 98d15170 (retomada, 5 tarefas, 55 min) e 33431168 (1 tarefa, `2026-10-13-aviso-de-colisao-agrupado.md`): teste fora do escopo encerra com `scope_conflict` sem gastar o reparo, harness morto por sinal vira infraestrutura sem escalar, mensagem do gate começa pelos testes que falharam, aviso de colisão no `plan validate` |
