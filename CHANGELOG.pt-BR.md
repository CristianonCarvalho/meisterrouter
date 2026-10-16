# Changelog

Este changelog segue o formato [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). O projeto usa versionamento semântico e permanece em `0.x` até o contrato (plano JSON, `meister.config.yaml` e eventos do log) estabilizar. Os números `#N` são pull requests.

## [Unreleased]

## [0.11.0] - 2026-10-16

### Adicionado
- **Monorepos com subpacotes Node passam no gate sem configuração.** Depois de instalar a raiz, o gate instala também cada subdiretório (até 3 níveis) com `package.json` e lockfile próprios, ignorando `node_modules`, `.git`, `.venv`, `dist` e semelhantes, e não faz nada disso quando a raiz declara `workspaces` ou `pnpm-workspace.yaml`.
- **`gate.install` aceita uma lista de comandos** (`["npm ci", "npm --prefix backend ci"]`), executados em ordem e interrompidos na primeira falha. A forma em texto continua valendo. Os comandos rodam sem shell, então `&&` dentro de um único texto nunca funcionou; use a lista.
- **A mensagem de falha do gate diz quando falta dependência** (`Cannot find package`, `Cannot find module`, `Failed to resolve import`, `No module named`), com os nomes e a dica de configurar `gate.install`, para o operador não ler como teste reprovado.
- **`meister worker --timeout`** (segundos, padrão 180) no lugar da espera fixa do Herdr; a mensagem de timeout agora diz que o trabalho parcial fica arquivado em `refs/meister/archive/*`.
- **O reparo do gate agora vê qual teste do vitest falhou.** O resumo da falha reconhece a saída do vitest (`FAIL arquivo > suite > teste`, a linha da asserção e as contagens `Test Files`/`Tests`) em vez de repassar as últimas linhas de um dump de DOM. Os nomes vão para um campo novo, `js_failed_tests`, separado de `failed_tests` para que o retry do pytest e a checagem de escopo não mudem, e para os eventos `gate_repair` e `subtask_rejected`.
- **A timeline mostra por que o gate reprovou a tentativa.** O tooltip imprimia só `Deterministic gate failed in worker worktree:` nas falhas do vitest; agora mostra o teste que falhou, ou o arquivo de teste e a linha tirados do resumo da falha, e mantém o texto antigo quando nada é reconhecível.

### Corrigido
- **`plan import` não descarta mais arquivos de `**Files:**` em silêncio.** Vários caminhos entre crases numa linha valem todos, o texto depois do caminho (`(append ao final)`) é ignorado, `Rewrite` vale como `Modify` e `Delete` é aceito. Linha com verbo conhecido e sem caminho entre crases agora imprime um aviso em vez de sumir.
- **O evento de preparo do worktree de integração leva a run certa.** `worktree_setup_ok` e `worktree_setup_failed` do worktree de integração saíam com o id da run anterior, o que esticava a duração das runs nos relatórios (uma run de 3 minutos aparecia com 70) e creditava a fase `setup` à run errada.

## [0.10.0] - 2026-10-13

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
- **`plan validate` e `plan analyze` avisam sobre testes existentes que citam os arquivos da tarefa.** Quando um teste já no repositório cita símbolos de um arquivo de `**Files:**` de uma tarefa, o plano recebe um aviso, para você incluir esse teste em `**Files:**` se a tarefa muda o comportamento dele de propósito.
- **`meister gate`** roda o gate determinístico de verificação de um projeto (`--path`), com código de saída 0 quando passa, 1 quando reprova e 2 em erro de infraestrutura. O hook de pre-commit gerenciado o executa, então o hook e o gate usam os mesmos comandos.
- **Os GIFs do README não sugerem mais que a ferramenta é difícil de usar.** A timeline ao vivo (sem nada digitado) virou a imagem de abertura, e as gravações de terminal foram para uma seção "Por baixo dos panos", um comando em cada, dizendo que a LLM orquestradora as roda e que o usuário não digita nada disso. O início em 60 segundos deixa claro que um pedido em linguagem natural é a interface toda.
- **README reescrito para defender o uso do MeisterRouter, com GIFs novos.** Abertura nova, tabela "o que você deixa de fazer", início em 60 segundos, três gravações reais de terminal (um plano rodando em paralelo, o gate rejeitando e reparando uma tarefa, o fallback de cota) e um GIF mais nítido da timeline ao vivo, em inglês e em português. O Herdr saiu do texto principal para uma seção própria e opcional. O kit de gravação está em `docs/img/src/` (`record.sh`; os workers são simulados, então não gasta cota).
- **A demonstração da timeline tem nomes localizados e neutros.** O `python -m meister.timeline_demo` pega o nome da run e os 12 títulos de tarefa dos catálogos de idioma (inglês ou português) em vez de um texto fixo em português.
- **`meister config init --kiro`.** Gera um `meister.config.yaml` que usa só o Kiro CLI (`router.mode: first`, vias `tier_1`, `tier_1b` e `tier_2` em `kiro-cli`, sem rede nem chave do OpenRouter). O `docs/pt-BR/KIRO.md` passa a apontar para ele.
- **Guia e exemplo para testar lotes Java sem ambiente de homologação.** `docs/pt-BR/TESTES_JAVA_SEM_HOMOLOGACAO.md` e `docs/examples/java-batch-testing/` (Java 8, Maven, WireMock, JUnit 5; 14 testes, sem Docker): fixtures com JSON gravado, site falso com cenários de falha, golden master, dry-run e verificação só de leitura do site real.
- **Timeline: os dois gates e a fila de integração ganham rótulo.** A linha do tempo na web rotula os dois gates de uma tarefa (`gate: worktree da tarefa` e `gate: após integrar a base`), chama a espera antes de integrar de "fila de integração", também na legenda, e deixa de desenhar as fatias de integrate de menos de 2 s entre fases. Veja [docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md#linha-do-tempo-na-web).
- **Kiro CLI como harness (`kiro-cli`, alias `kiro`):** os workers rodam `kiro-cli chat --no-interactive --trust-all-tools`, com `--model` e `--effort` opcionais; uma configuração só com vias do Kiro e `router.mode: first` dispensa Copilot, Claude, Codex e OpenRouter. O fallback por cota não dispara para o Kiro e os créditos dele não aparecem nos relatórios de uso. Veja [docs/pt-BR/KIRO.md](docs/pt-BR/KIRO.md).
- **Java/Maven no gate:** `gate.env` (padrão vazio) define variáveis de ambiente para os comandos do gate, e com `JAVA_HOME` o `bin` dele entra no início do `PATH`; um `pom.xml` é detectado sem `gate.commands` e roda `mvn -B -q test`, resolvendo o Maven pelo `mvnw`/`mvnw.cmd` do projeto, pelo `mvn` do `PATH` e depois por `MAVEN_HOME`/`M2_HOME`; `target/**` passa a ser tolerado por padrão em `scope.tolerated_files`. Veja [docs/pt-BR/JAVA_MAVEN.md](docs/pt-BR/JAVA_MAVEN.md).
- **`retry.worker_error_attempts` (padrão `1`; `0` desliga).** Quando um worker devolve erro (por exemplo, o harness sai com código 1 numa falha transitória), o Meister registra o evento `worker_retry` (`reason: worker_error`) e tenta de novo na mesma via; esgotadas essas tentativas, passa para a próxima via de `workers.tier_order`. A run só falha quando não sobra via. Antes, um único erro do worker derrubava a run inteira apesar de `max_retries` e de haver outras vias. Com `0`, fica o comportamento antigo. Veja [docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md#configuração-avançada).
- **`runtime.host` escolhe onde os workers rodam.** Define o adaptador que hospeda cada worker: `herdr`, `tmux` ou `process`. Veja [docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md).
- **Adaptador `process`:** os workers rodam como processos em segundo plano, sem multiplexador de terminal, então o MeisterRouter funciona sem o Herdr instalado.
- **Adaptador `tmux`:** os workers rodam em panes do tmux, como alternativa ao Herdr.
- **`meister logs`** mostra a saída de um worker, o que importa sobretudo com o adaptador `process`, em que não há pane para olhar.
- **Variável de ambiente `MEISTER_WORKER`** marca um processo como worker do MeisterRouter, qualquer que seja o host. `MEISTER_IN_PANE` continua existindo e é respeitada.
- **Suporte nativo ao Windows (em validação):** camada `meister.osops` com o código específico de cada SO (POSIX em `posix.py`, Windows em `windows.py`), `psutil` como dependência apenas no Windows, correção de `os.kill(pid, 0)` e hooks gravados com final de linha LF. O comportamento no Linux e no macOS não muda.
- **Detecção de teste instável no gate (`gate.flaky_retries`, padrão `2`; `0` desliga).** Quando o gate reprova por testes do pytest, ele reexecuta primeiro só esses testes no mesmo worktree (até `flaky_retries` vezes). Se passarem, o gate completo roda mais uma vez e é a autoridade final: se passar, a tarefa é aprovada com o evento `gate_flaky` listando os testes, e nenhum worker de reparo é aberto. Se um teste continuar falhando, ou a falha não tiver ids de teste (lint, coleta), a reprovação e o fluxo de reparo seguem como antes. Teste instável deixa de custar uma sessão de modelo e vários minutos de reparo.
- **`meister wait [--run-id ID] [--timeout S] [--format text|json]`** bloqueia até uma run terminar e sai com um código que diz como: `0` concluída, `1` falhou, `130` interrompida, `124` timeout, `2` erro de uso. Só lê o log (nunca grava), acompanha runs retomadas e permite que um agente orquestrador seja avisado sem ficar consultando. Veja [docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md).
- **A timeline descreve a run sozinha:** `meister orchestrate --name TEXTO` grava um nome legível da run (`run_name` no `orchestration_start`; sem ele, um nome derivado do plano) mostrado no cabeçalho, no seletor e no título da linha do tempo na web; cada tentativa é desenhada na raia em que rodou, com o fallback lateral distinto do escalonamento; o cabeçalho da via vem dos eventos `worker_spawn` da run (`harness`, `model`, `effort`); e o tooltip da tarefa lista as tentativas com via, resultado e `detail` (arquivos fora do escopo, linha da falha do gate, linha da cota). Só foram acrescentados campos; logs antigos continuam com a exibição anterior. Veja [docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md#linha-do-tempo-na-web).
- **Demonstração da timeline:** `python -m meister.timeline_demo` sobe a timeline web sobre um log sintético, nos modos `static` ou `live` (`--speed` para o live), sem modelo e sem custo. Veja [docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md).
- **Página sobre os dados enviados ao Jev:** [docs/pt-BR/DADOS_ENVIADOS_AO_JEV.md](docs/pt-BR/DADOS_ENVIADOS_AO_JEV.md) lista, campo a campo, o que `classify` e `control` mandam à OpenRouter e o que nunca sai da máquina (os workers rodam pelos harnesses locais). O contrato é travado por `tests/test_jev_payload_contract.py`: mudar um campo exige atualizar a página.
- **Legibilidade da linha do tempo na web (`/timeline`):** eixo de tempo em minutos, setas de dependência em ângulo reto, tooltips com as durações da fase, da tarefa e da espera, cabeçalho da via com `harness · modelo (esforço)` e um som ao terminar ou falhar uma tarefa, com botão e tecla `s` (desligado por padrão, preferência salva por navegador).
- **`effort` por via:** nível de raciocínio opcional repassado ao harness (os valores dependem do harness: `copilot` e `github-copilot` aceitam nenhum, minimal, low, medium, high, xhigh, max). Sem `effort`, o argv do harness continua idêntico ao de antes.
- **`workers.enabled`:** liga ou desliga vias na configuração, com gravação segura, sem nunca deixar a configuração sem nenhuma via ligada.
- **`meister models --enable` / `--disable`:** liga ou desliga uma via pela linha de comando; a tabela de `models` mostra uma coluna com o esforço de cada via.
- **Tela `/config` no dashboard:** mostra as vias efetivas e edita ligar e desligar, ordem, harness, modelo, esforço e `max_parallel`, com a mesma validação do `meister config validate`; grava o `meister.config.yaml` com o `ruamel.yaml` (comentários preservados), `.bak` e troca atômica, oferece **Restaurar padrão** e fica somente leitura com run ativa ou acesso remoto. Um link **Config** fica no cabeçalho da linha do tempo. O `ruamel.yaml` passa a ser dependência de runtime. Veja [docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md#tela-config-vias).
- **`scope.allow_dependency_files` (padrão `false`):** com `true`, uma tarefa pode editar também os arquivos declarados pelas tarefas das quais ela depende, direta ou transitivamente, já integradas antes de ela começar. Arquivos de tarefas sem relação continuam bloqueados, e o `meister.config.yaml` nunca deve ser editado por um worker. Com `false`, o escopo não muda. Veja [docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md](docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md#configuração-avançada).
- **Claude Haiku 5.5 na via `tier_1`** (harness GitHub Copilot CLI) como primeira via padrão.
- **`meister plan new [--tasks N] [--format markdown|json] [-o ARQUIVO] [--project]`** grava a moldura de um plano pronta para preencher: Markdown por padrão (título, `## Global Constraints`, N blocos `### Task K:` com `**Files:**` e `**Depends on:**`) ou o array JSON canônico com `--format json`. Sem edição, passa pelo `meister plan import` e pelo `meister plan validate`. `--project` preenche as Global Constraints com regras lidas do `CLAUDE.md` e do `AGENTS.md` do projeto, se existirem, e nunca inventa nenhuma. Veja [docs/pt-BR/FORMATO_DO_PLANO.md](docs/pt-BR/FORMATO_DO_PLANO.md#meister-plan-new).
- **`meister plan guide [--lang en|pt-BR]`** imprime as regras do formato do plano para o modelo que vai escrevê-lo: o fluxo, muitas tarefas pequenas, `**Files:**` como o escopo conferido pelo gate, `**Depends on:**` só para dependência real, o que não fazer e exemplos em Markdown e JSON. Os modelos do `init` passam a começar o fluxo de plano por `plan guide` e `plan new`. Veja [docs/pt-BR/FORMATO_DO_PLANO.md](docs/pt-BR/FORMATO_DO_PLANO.md#meister-plan-guide).
- **`meister plan import --format auto|markdown`:** o padrão passa a ser `auto`, que detecta um plano de tarefas em Markdown ou um plano JSON canônico; `markdown` é o nome do formato nativo.

### Corrigido
- **Teste fora do escopo que reprova o gate encerra a tarefa com `scope_conflict` sem gastar o reparo.** O trabalho fica arquivado em `refs/meister/archive/*`, e a tarefa não vai para um worker de reparo que não pode editar esse teste.
- **Harness morto por sinal é falha de infraestrutura, não motivo para subir de modelo.** Um processo do harness que termina por sinal (por exemplo SIGKILL ou SIGSEGV) é reportado como erro de infraestrutura e não move a tarefa para um modelo maior.
- **A mensagem de rejeição do gate começa pelos testes que falharam.** As primeiras linhas nomeiam os testes que falharam, antes do restante da saída.
- **Os commits do próprio Meister não passam mais pelos hooks do Git.** Os commits internos de workers e de integração usam `--no-verify`, porque o gate já os verificou antes do commit; o hook pre-commit do projeto não os reprova.
- **Falha de commit do git depois de um gate aprovado é erro de infraestrutura.** Ela é reportada com a saída do git, em vez de como falha do gate.
- **Exceção inesperada encerra a subtask como `FAILED`.** Ela não fica mais em andamento.
- **O arquivamento ignora `.venv`.** O arquivo de trabalho solto não inclui mais o ambiente virtual do projeto.
- **Os links do README para "Como usar" e "Herdr" não abriam.** Os títulos tinham um seletor de emoji invisível (U+FE0F) que o GitHub mantém na âncora. Agora usam emojis sem ele, e o teste de links usa as âncoras do GitHub.
- **Erro de infraestrutura do gate agora fecha a subtask como falha.** O resumo da run mostrava a tarefa como `running` e `0 falhou` depois de erros como "Nenhum teste ou linter detectado" ou Maven ausente; agora mostra `FAILED`. A mensagem de erro também perdeu o ponto duplicado.
- **Projeto sem testes detectáveis não gasta mais um reparo.** Quando o gate não acha testes nem linters e `gate.allow_unverified` está desligado, agora é erro de infraestrutura do gate: a run para com uma mensagem ao dono do projeto, o trabalho do worker é mantido, e o worker deixa de ser mandado editar o `meister.config.yaml`, que é protegido. As linhas de reparo do gate e de erro de infraestrutura no console passam a seguir `language: pt-BR`.
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

- **Um symlink `.venv` ou `venv` deixa de contar como mudança e de entrar em commit.** A detecção de mudanças e os commits ignoram o ambiente virtual do projeto quando ele é um link simbólico.
- **O cálculo da árvore do gate funciona com `.venv` ignorado.** O hash da árvore usado no cache do gate não falha mais quando o `.venv` está ignorado pelo git.
- **`worker_timeout` traz `output_tail`, e `meister logs` mostra a saída do worker com o Herdr.** O evento inclui as últimas linhas da saída do worker; com o Herdr, essa saída só é gravada em arquivo quando ocorre um timeout.

### Outras alterações
- **README mais curto: uma única seção "Comece agora".** "Em 60 segundos", "Instalação" e "Como usar" viraram uma só (dois comandos e uma frase para a sua LLM, com a timeline ao vivo abrindo sozinha); os detalhes foram para `docs/pt-BR/PRIMEIROS_PASSOS.md`. "Por baixo dos panos" agora vem depois de "Escolha os modelos e a ordem deles".
- O motor (`bridge`, `workers`, `dag`, `events`) passou de `meister/herdr/` para `meister/engine/`; `meister/herdr/` fica só com o que é do Herdr. Os caminhos antigos `meister.herdr.bridge|workers|dag|events` continuam por compatibilidade (mesmos objetos de módulo), então imports e patches existentes seguem funcionando.
- **O superpowers é opcional.** O README, a página do formato do plano, os modelos do `init` e o `CLAUDE.md` deste repositório apresentam primeiro o fluxo nativo (`plan guide`, `plan new`, `plan import`, `plan validate`, `orchestrate`) e a skill do superpowers como "se você usa". O formato do plano é o mesmo: `--format superpowers` continua valendo, sem aviso de obsolescência, e dá o mesmo resultado que `markdown`.
- O `meister setup` e o `bin/install.sh` não exigem mais o Herdr: quando ele falta, pulam os passos do Herdr e seguem, e quem tem o Herdr instalado vê os mesmos passos de antes. `MEISTER_ALLOW_NO_HERDR` agora não tem efeito.
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
