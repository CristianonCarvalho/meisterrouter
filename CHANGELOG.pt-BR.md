# Changelog

Este changelog segue o formato [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). O projeto usa versionamento semântico e permanece em `0.x` até o contrato (plano JSON, `meister.config.yaml` e eventos do log) estabilizar. Os números `#N` são pull requests.

## [Unreleased]

### Changed (incompatible)
- **`meister dashboard --host` fora de loopback exige `--allow-remote`.** O dashboard não tem autenticação, e antes qualquer endereço era aceito em silêncio. Sem a flag, o comando agora termina com erro; com ela, imprime um aviso e sobe. Loopback (`127.0.0.1`, `::1`, `localhost`) não muda. Quem expõe o dashboard na rede precisa acrescentar `--allow-remote`.
- **Python mínimo é 3.10.** O piso declarado era 3.9, que nunca foi testado; `requires-python`, `meister setup` e a matriz do CI agora usam 3.10, a versão que o CI e o mypy já assumiam.
- **Vias com nomes neutros.** Nomes que traziam modelo ou harness foram substituídos; atualize `workers.tier_order`, o `meister.config.yaml`, os argumentos `--model` e qualquer script que os use. Os nomes antigos deixam de existir na configuração padrão.

  | Nome antigo | Nome novo |
  |---|---|
  | `copilot_luna` | `tier_1b` |
  | `codex_luna` | `tier_1c` |
  | `agy_gemini_flash` | `tier_2` |
  | `claude_sonnet` | `tier_3` |

  A nova via `tier_1` (GitHub Copilot CLI com Claude Haiku 5.5) é a primeira escolha por padrão. Modelo, harness e preço continuam na configuração (`meister/default_config.yaml` e `meister.config.yaml`), não no nome da via.

### Adicionado
- **Detecção de teste instável no gate (`gate.flaky_retries`, padrão `2`; `0` desliga).** Quando o gate reprova por testes do pytest, ele reexecuta primeiro só esses testes no mesmo worktree (até `flaky_retries` vezes). Se passarem, o gate completo roda mais uma vez e é a autoridade final: se passar, a tarefa é aprovada com o evento `gate_flaky` listando os testes, e nenhum worker de reparo é aberto. Se um teste continuar falhando, ou a falha não tiver ids de teste (lint, coleta), a reprovação e o fluxo de reparo seguem como antes. Teste instável deixa de custar uma sessão de modelo e vários minutos de reparo.
- **`meister wait [--run-id ID] [--timeout S] [--format text|json]`** bloqueia até uma run terminar e sai com um código que diz como: `0` concluída, `1` falhou, `130` interrompida, `124` timeout, `2` erro de uso. Só lê o log (nunca grava), acompanha runs retomadas e permite que um agente orquestrador seja avisado sem ficar consultando. Veja [docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md).
- **Página sobre os dados enviados ao Jev:** [docs/pt-BR/DADOS_ENVIADOS_AO_JEV.md](docs/pt-BR/DADOS_ENVIADOS_AO_JEV.md) lista, campo a campo, o que `classify` e `control` mandam à OpenRouter e o que nunca sai da máquina (os workers rodam pelos harnesses locais). O contrato é travado por `tests/test_jev_payload_contract.py`: mudar um campo exige atualizar a página.
- **Legibilidade da linha do tempo na web (`/timeline`):** eixo de tempo em minutos, setas de dependência em ângulo reto, tooltips com as durações da fase, da tarefa e da espera, cabeçalho da via com `harness · modelo (esforço)` e um som ao terminar ou falhar uma tarefa, com botão e tecla `s` (desligado por padrão, preferência salva por navegador).
- **`effort` por via:** nível de raciocínio opcional repassado ao harness (os valores dependem do harness: `copilot` e `github-copilot` aceitam nenhum, minimal, low, medium, high, xhigh, max). Sem `effort`, o argv do harness continua idêntico ao de antes.
- **`workers.enabled`:** liga ou desliga vias na configuração, com gravação segura, sem nunca deixar a configuração sem nenhuma via ligada.
- **`meister models --enable` / `--disable`:** liga ou desliga uma via pela linha de comando; a tabela de `models` mostra uma coluna com o esforço de cada via.
- **Claude Haiku 5.5 na via `tier_1`** (harness GitHub Copilot CLI) como primeira via padrão.

### Corrigido
- **Retomar uma run agora continua a numeração das tentativas em vez de reiniciar em 1.** O `_execute_subtask_core` começava toda chamada com `attempt_count = 0`, então uma tarefa retomada com o mesmo `run_id` repetia números de tentativa (eventos e arquivos por tentativa, como `<run>_<tarefa>_<N>_task.json`, `.json` e `.exit`) e misturava tentativas antigas e novas na timeline e nos relatórios. Agora ele parte das tentativas já gravadas para a subtarefa (`subtasks.attempts`). Verificado com uma interrupção real e `--resume`: `worker_spawn` com as tentativas 1 e 2.
- **O bridge agora percebe em menos de um segundo quando o processo `run-task` de um worker morre sem gravar resultado.** O comando digitado no pane do worker é embrulhado em `/bin/sh -c` para o shell gravar o código de saída num arquivo `.exit` mesmo que o Python quebre ou falhe no import; o bridge então falha a tentativa (evento `worker_process_exited`, nova tentativa com `reason=process_exit` e o código, e depois falha sem escalar de via) em vez de esperar os 600 s do timeout de inatividade. Medido com um `kill -9` real: detecção em 0,6 a 0,7 s. A linha de progresso agora diz "worker process exited with code N" e "gate repair" em vez do enganoso "pane lost; retry".
- **Interromper o `meister orchestrate` não deixa mais a run "rodando" para sempre.** Ctrl+C, `SIGTERM` ou `SIGHUP` agora gravam o evento `orchestration_end` (`status: interrupted`, código 130), marcam a run como `FAILED` (retomável com `meister orchestrate --resume`), mantêm os worktrees e os ramos, mostram qual run foi interrompida e saem com código 130. Antes, a run continuava `RUNNING` no banco de estado e a timeline a desenhava como em execução.
- **Um harness que termina na hora não esconde mais o erro real no macOS.** `HarnessWorker.run` chamava `os.getpgid` logo depois de iniciar o harness; no macOS isso levanta `ProcessLookupError` quando o harness já saiu, e a tarefa falhava com `[Errno 3] No such process` em vez da mensagem e do código de saída do próprio harness. O grupo de processos agora é o PID (o harness é líder de sessão) e gravar o arquivo de PID nunca derruba a tarefa.
- `test_start_server_port_0_allocates_port_and_cleans_up` deixa de falhar em máquinas lentas: espera maior e reinício do relógio do vigia de inatividade, que testes anteriores podiam deixar velho.
- **A integração não descarta mais trabalho solto.** `rollback_merge`, a reutilização de um worktree de integração (registrado ou não) e o retry com `branch -D` em `create_worktree` agora arquivam alterações soltas e commits não integrados (`refs/meister/archive/*`) antes de qualquer comando git destrutivo (`reset --hard`, `clean -fd`, `branch -D`, `rmtree`). Se o arquivamento falha, o passo não roda; um rollback bloqueado agora falha a subtarefa com erro de `integration`, e não como falha de gate. Coberto por `tests/test_worktree_integration_data_loss.py`.
- **`current_run.json` é gravado de forma atômica**: uma interrupção não deixa mais um arquivo truncado que perde a correlação da run.
- Falhas de limpeza deixam de ser silenciosas: geram `warning`, o evento `cleanup_failure` e a linha "Falhas de limpeza" no `meister report`.
- Linha do tempo na web: uma tentativa ainda aberta quando o worker é reiniciado agora termina no novo spawn, em vez de se estender até o fim do run.
- Um worker que falha ao iniciar por erro de configuração (ex.: `Unknown lane`) passa a gravar o resultado de erro e registrar `worker_task_error` imediatamente, em vez de esperar o timeout de inatividade de 600 s e escalar para outra via.
- O Jev passa a receber chaves opacas das vias (`lane_a`, `lane_b`...) em vez dos nomes: nomes como `tier_N` faziam o Jev ler ranking e escolher uma via mais cara. A resposta é traduzida de volta para o nome real da via, então `recommended_implementer`, `fallback_chain` e a telemetria continuam com os nomes reais.

### Outras alterações
- Alterações em arquivos de escopo tolerado geram eventos `scope_tolerated` e linhas de progresso; este repositório tolera `tests/**`, mas ainda exige que a suíte inteira passe. Seu `meister.config.yaml` usa `router.mode: first` (sem Jev; o fallback de `tier_order` continua ativo).
- Alicerce de internacionalização (`en` padrão, `pt-BR` configurável), códigos de motivo para rejeições no pipeline e catraca de literais acentuados.
- Laço de reparo do gate determinístico (`gate.repair_attempts`) e preservação do diagnóstico da rejeição de subtarefas.
- Encerramento do grupo do harness na saída do worker e recolha de órfãos com validação da identidade do PID antes de retentativas.
- Correção: o timeout por inatividade ou tempo máximo deixa de falhar a subtarefa com `worker_error` quando o resultado de erro foi gravado pela própria limpeza do harness; o caminho do timeout (retry, escalonamento ou aproveitamento do trabalho) tem precedência.
- Janela de aplicativo da linha do tempo por projeto, configurável por `dashboard:`, e opção `orchestrate --no-open`; runs retomadas aparecem em andamento após um novo `orchestration_start`.

## [0.9.1] - 2026-10-07

Versão de ajustes, instalação e documentação depois da 0.9.0 (PRs #78 a #87).

### Adicionado
- **Gate leve opcional para mudanças só de documentação:** `gate.docs_only` (desligado por padrão). Quando todos os arquivos alterados são documentação, o gate pré-merge e o pós-merge rodam `gate.docs_only.commands` em vez da suíte inteira (medido: 1,8 s contra 47,2 s); o gate final de integração sempre roda a suíte inteira (#87).
- **Instalador com versão fixa:** `install.sh --version vN.N.N|latest|main` (ou `MEISTER_VERSION`), por exemplo `curl ... | bash -s -- --version v0.9.1` (#80).
- **Script de entrada do plugin do Herdr** (`bin/herdr-meister.sh`): encontra o CLI `meister` (`PATH`, `~/.local/bin`, Homebrew) e, se ele faltar, mostra uma notificação de instalação (código 127; saída 0 silenciosa no startup) em vez de falhar em silêncio (#83).
- **Documentação em inglês:** `README.md`, tudo em `docs/` e este changelog agora estão em inglês; as versões em português ficam em `README.pt-BR.md`, `docs/pt-BR/` e `CHANGELOG.pt-BR.md`. Um teste mantém válidos os links e âncoras relativos nos dois idiomas (#82, #85).
- **Guia de uso direto, página do formato do plano e instalação por `curl`** (#79).
- **Licença MIT**; o repositório agora é público (#78).

### Alterado
- O manifesto do plugin não declara mais `[[keys.command]]` (o Herdr não os lê); os atalhos são gravados pelo `meister setup` (#81).

### Corrigido
- `install.sh`: só os componentes `/_npx/` e `/node_modules/` do caminho indicam instalação por npx (uma pasta como `~/meu_npxprojeto` era confundida), e a volta de uma tag fixa para a `main` não falha mais (#80, #82).
- Estabilidade dos testes: o teste de timeout do cache do gate (0,1 s para 1 s) e o teste de `max_runtime=0` do bridge não falham mais sob carga (#84, #86).

## [0.9.0] - 2026-10-07

Primeira versão marcada. Consolida o histórico dos PRs #1 a #73 (29/09 a 07/10/2026).

### Adicionado
- **Orquestração:** execução de planos em DAG com worktree por subtarefa, merge `--no-ff` numa branch de integração e fast-forward da `main` só no fim (#1, #4, #6); contrato de plano canônico com adaptador do superpowers (#5); `--resume` que reaproveita tarefas concluídas (#24); retentativa na mesma via quando o pane some (#26); progresso por tarefa e resumo final (#28); `meister plan analyze` e aviso de plano serial (#46); timeout por inatividade com trabalho preservado (#32).
- **Roteamento:** o Jev escolhe a via inicial de cada subtarefa e é o modo padrão, com proteção contra Jev lento (#13, #17); contexto estruturado para o Jev (#33); `max_parallel` por via (#16); `eligible_classes` por via, uma regra determinística sobre a escolha do Jev (#63).
- **Configuração:** catálogo de modelos 100% em configuração, com padrões empacotados (#14, #15); validação e exibição das vias, vias desligáveis, Copilot como primeira via (#12, #19).
- **Gate:** escopo com glob, preparo de ambiente Node e portão configurável (#22); venv do projeto, `{python}` e `ok_exit_codes` (#49); gate pré-merge fora da trava de integração (#47); cache do gate por árvore de arquivos (#66); testes em paralelo com `pytest-xdist` (#64).
- **Custo e medição:** uso e custo reais dos workers e tempo por fase (#43); `meister report` (#44); custo do Copilot por créditos (`credit_usd`) (#51); preços de modelos atualizados (#50).
- **Observabilidade:** dashboard agrupado e analítico, com projeto, run, tarefas legíveis, custo, overhead e pico de workers (#35, #39, #41, #45, #53, #54); linha do tempo (Gantt) em TUI, com modo ao vivo, visão de todos os runs, linha do Jev e estado "sem sinal" (#55, #56, #57, #58, #62); suporte a tarefas rodadas por `meister worker` (#73).
- **Operação:** `meister clean` remove branches antigas com segurança (#34); `meister init` seguro (#20); guard do Claude Code com modos `block`/`ask`/`off` e regra "método de execução = Meister" (#68, #69); manual de execução (#25, #29); diagramas Mermaid (#72) e fluxo ponta a ponta (#18).
- **Instalação automática:** `meister setup` liga o plugin ao Herdr, grava os atalhos num bloco gerenciado do `config.toml` (com backup e validação), confere o ambiente e, com `--project`, equipa o projeto; `meister config init` gera o `meister.config.yaml` com as vias e a ordem; `bin/install.sh` exige o Herdr e chama o `setup` (#76).
- **`meister --version`**, fonte única da versão em `meister/__init__.py` e este changelog (#74).
- **Documentação:** README enxuto (o MeisterRouter é um plugin do Herdr, e o Herdr é pré-requisito), capturas da linha do tempo e do dashboard (#75), diagramas Mermaid atualizados e o manual de execução.

### Corrigido
- Integração: commits do worker feitos no próprio worktree são integrados (#4); a branch de integração é reconstruída por merge (#6); trabalho rejeitado nunca se perde, inclusive mudanças não commitadas (#60).
- Detecção de pane desaparecido com o formato real de eventos do Herdr (#10) e vias com disjuntor aberto são puladas (#7).
- Tarefas sem `target_files` rodam isoladas (#9); rotas `[id]` do Next.js e `--resume` do mesmo run (#31); `Global Constraints` não vaza para cada tarefa (#23); importador do superpowers ignora títulos em blocos de código (#48).
- `npm install` não roda quando não há nada a instalar (#61); worktrees `prunable` não protegem mais a branch (#37).
- Dashboard: tabela legível, chamadas do Jev ligadas às tarefas, ordenação e custo com o catálogo atual (#38, #40, #42, #52).

### Testes e CI
- Matriz de quedas com 13 pontos de falha e scripts E2E com ferramentas reais (#2, #3, #8, #11, #30); watchdog por teste e guarda contra vazamento de branches (#36); testes independentes do relógio e estabilizados sob carga (#21, #27, #59, #65, #67).
