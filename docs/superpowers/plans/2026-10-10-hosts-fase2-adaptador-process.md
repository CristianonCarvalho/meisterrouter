# Hosts de worker, fase 2: adaptador `process` e seleção `runtime.host` Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde um template diria "Commit", este plano usa **Checkpoint**.
>
> **Só executar depois que o plano `2026-10-10-hosts-fase1-contrato-e-herdr.md` estiver mesclado e executado:** este plano usa `meister/hosts/base.py`, `HerdrHost` e a migração do bridge e do spawner feitas lá. Origem: `docs/superpowers/specs/2026-10-09-worker-host-adapters-design.md`.
>
> **Hot files:** `meister/herdr/bridge.py` é editado pelas Tarefas 2, 6 e 7 (regiões diferentes, em sequência). Não rodar junto de outro plano que mexa nesse arquivo.

**Goal:** Rodar o `meister orchestrate` completo **sem Herdr nem tmux**: os workers viram subprocessos em segundo plano, com log por worker, e a escolha do host vira `runtime.host: auto | process | herdr | tmux` (o `tmux` só é reconhecido aqui; o adaptador vem na fase 4). Com Herdr acessível, `auto` continua escolhendo o Herdr, então nada muda para quem usa hoje.

**Architecture:** `ProcessHost` implementa o contrato com `subprocess.Popen` (novo grupo de processos, `stdin` nulo, saída anexada a um arquivo de log) e usa os auxiliares que já existem em `meister/worker.py` (`kill_process_tree`, `process_start_signature`). As operações de processo ficam atrás de um módulo pequeno, `meister/hosts/_proc.py`, para o Windows entrar depois como segunda implementação sem mexer no adaptador. O código de saída do `run-task`, que no Herdr é gravado pela linha de shell (`echo $? > arquivo.exit`), passa a ser gravado pelo próprio `ProcessHost` no mesmo arquivo, então o bridge não muda nesse ponto. Como o host local não tem eventos empurrados, o laço de espera do bridge varre o `tail` do log atrás de cota ou rate limit.

**Tech Stack:** Python ≥ 3.10, `asyncio`, `subprocess`, `pytest`. Nenhuma dependência nova.

## Decisões fechadas

| Tema | Decisão |
|---|---|
| Padrão | `runtime.host: auto`. Socket do Herdr acessível → `herdr`; senão → `process`. `process` e `herdr` explícitos nunca caem no outro; `herdr` sem socket é erro claro. |
| `tmux` | Aceito na validação da configuração, mas até a fase 4 selecioná-lo termina com erro de "adaptador ainda não disponível". |
| Log do worker | `<log_dir>/workers/<run>_<task>_<tentativa>.log`, onde `<log_dir>` é `meister.logger.get_log_dir()` (o mesmo diretório do `orchestration_log.jsonl`). **Não** vai no `.meister/runs/` do worktree: o worktree é removido na limpeza e o log some junto. O adaptador anexa (`a`) e cada tentativa tem o seu arquivo. |
| Código de saída | `WorkerCommand.exit_file`: o `ProcessHost` grava o código de retorno nesse arquivo quando o processo termina, no mesmo formato de hoje (`<número>\n`). O Herdr ignora o campo (a linha de shell cuida). |
| Interrupção | `interrupt` envia `SIGINT` ao grupo; `close` envia `SIGTERM` e, passado o prazo (5 s), `SIGKILL`. O harness filho é lançado pelo `HarnessWorker` em outra sessão: ele continua sendo encerrado pelo `_reap_worker_harness` do bridge (arquivo de PID), como no Herdr. |
| Ambiente do subprocesso | `os.environ` do orquestrador mais `WorkerCommand.env` (é o que o shell do painel do Herdr herdava hoje). A filtragem de variáveis do harness continua em `build_safe_worker_env`, dentro do `run-task`. |
| Plataforma | Só POSIX nesta fase. Tudo que é específico de Unix fica em `_proc.py`. |

## Global Constraints

- Com Herdr acessível, o comportamento é idêntico ao de hoje; os testes existentes do Herdr passam sem mudar asserções.
- Sem dependência nova; compatível com Python 3.10; `from __future__ import annotations` nos módulos novos.
- Mensagens ao usuário por `t()` com entradas em `meister/locales/en_*.py` **e** `pt_br_*.py`; `tests/test_i18n_ratchet.py`, `tests/test_locales_parity.py` e `tests/test_no_hardcoded_models.py` ficam verdes.
- `ruff check .` e `mypy meister` limpos; a suíte inteira verde ao fim de **cada** tarefa.
- Testes sem rede, sem tocar `~/.meister`, sem chamar CLI de IA; harness e processos simulados com scripts em `tmp_path`.
- Processos de teste sempre encerrados no `finally` (nada órfão); testes que lançam processos usam `posix_only` de `tests/platform_marks.py` se esse módulo já existir, senão `pytest.mark.skipif(sys.platform == "win32", ...)`.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.

### Task 1: Seção `runtime` na configuração

**Files:**
- Modify: `meister/config.py`
- Modify: `meister/default_config.yaml`
- Modify: `meister/locales/en_reports.py`
- Modify: `meister/locales/pt_br_reports.py`
- Test: `tests/test_runtime_config.py`

**Depends on:** none

Acrescente `RuntimeConfig(host: str)` a `meister/config.py` seguindo o padrão de `ConcurrencyConfig` (`_default_section("runtime")`, carga em `load_config`, campo em `MeisterConfig`) e `runtime: {host: auto}` em `meister/default_config.yaml`. Valores aceitos: `auto`, `process`, `herdr`, `tmux`; qualquer outro vira um `ConfigIssue` de nível erro no caminho `runtime.host`, com mensagem nova em `t()` (inglês e português, chave em `reports.config.*`). Teste: o padrão é `auto`; cada valor válido é aceito; valor inválido gera o erro; o `meister.config.yaml` de um projeto pode sobrepor o padrão.

### Task 2: O comando de worker leva `env`, `log_file` e `exit_file`

**Files:**
- Modify: `meister/hosts/base.py`
- Modify: `meister/herdr/workers.py`
- Modify: `meister/herdr/bridge.py`
- Test: `tests/test_hosts_base.py`
- Test: `tests/test_worker_process_exit.py`

**Depends on:** Task 1

Em `WorkerCommand` acrescente `log_file: str | None = None` e `exit_file: str | None = None`. No bridge, onde `task_dict["command"]`, `["command_str"]` e `["exit_file"]` são montados (~1039–1049), registre também `task_dict["env"]` (o dicionário `MEISTER_IN_PANE`, `MEISTER_LOG_DIR`, `MEISTER_RUN_ID` que hoje vai como prefixo da linha de shell) e `task_dict["log_file"] = os.path.join(get_log_dir(), "workers", f"{run}_{task}_{tentativa}.log")` (o diretório `workers` é criado pelo adaptador, não aqui). Em `WorkerSpawner`, ao montar o `WorkerCommand` a partir do `task_context`, preencha `argv`, `env`, `log_file` e `exit_file` com esses valores. `HerdrHost` ignora os campos novos. Nenhum comportamento muda no Herdr; `tests/test_worker_process_exit.py` continua verde e ganha um caso que afirma os campos no `WorkerCommand`.

### Task 3: Operações de processo atrás de um módulo

**Files:**
- Create: `meister/hosts/_proc.py`
- Test: `tests/test_hosts_proc.py`

**Depends on:** none

Funções POSIX, síncronas e pequenas: `start_detached(argv, *, cwd, env, log_path) -> subprocess.Popen` (novo grupo de processos, `stdin=DEVNULL`, saída e erro anexados a `log_path`, diretório criado se faltar); `is_running(popen) -> bool`; `signal_group(popen, sig) -> None` (tolera processo já encerrado); `terminate_tree(popen, grace=5.0) -> None` (`SIGTERM`, espera até `grace`, depois `SIGKILL`; tolerante a corridas); `process_signature(pid)` e `kill_tree(pgid)` reexportados de `meister.worker` (`process_start_signature`, `kill_process_tree`). Docstring do módulo: ponto de troca para uma implementação de Windows. Testes com scripts Python curtos em `tmp_path`: processo que termina, que ignora `SIGTERM` (cai no `SIGKILL`), que já morreu, e log anexado.

### Task 4: `ProcessHost`

**Files:**
- Create: `meister/hosts/process.py`
- Modify: `meister/hosts/__init__.py`
- Test: `tests/test_hosts_process.py`

**Depends on:** Task 2, Task 3

`ProcessHost(config=None)` implementa o contrato: `capabilities = frozenset()` (sem `visible` nem `push_events`); `start` não faz nada; `spawn(command, layout=...)` usa `_proc.start_detached` com `os.environ` mais `command.env`, `command.log_file` (se ausente, `<get_log_dir()>/workers/<label>.log`; crie o diretório pai) e devolve `WorkerHandle(id=<pid como texto>, aux=<caminho do log>)`, guardando o `Popen` num dicionário interno; ao término do processo, uma tarefa de fundo grava `<código>\n` em `command.exit_file` (se informado) de forma atômica; `alive` → `popen.poll() is None` (e `False` para handle desconhecido); `tail` → últimas N linhas do log (vazio se não existir); `interrupt` → `signal_group(SIGINT)`; `close` → `terminate_tree` (idempotente); `notify` → escreve no stderr com o título; `process_info` → `{"pid": ..., "pgid": ...}`; `current_context` → `None`. Os testes rodam a suíte `HostContract` de `tests/hosts_contract.py` contra o `ProcessHost` com um processo Python real, mais: o arquivo de código de saída é escrito (0 e diferente de 0), o log recebe o que o processo imprime, `close` mata um processo que ignora `SIGTERM`, e dois `spawn` simultâneos têm handles distintos.

### Task 5: Seleção do host

**Files:**
- Create: `meister/hosts/select.py`
- Modify: `meister/hosts/__init__.py`
- Modify: `meister/locales/en_engine.py`
- Modify: `meister/locales/pt_br_engine.py`
- Test: `tests/test_hosts_select.py`

**Depends on:** Task 1, Task 4

`select_host(config, *, socket_path=None, client=None) -> WorkerHost`. `auto`: se `meister.worker.is_herdr_available(socket_path)` é verdadeiro, devolve `HerdrHost(client or HerdrSocketClient(socket_path))`, senão `ProcessHost()`. `process`: sempre `ProcessHost()`. `herdr`: exige socket acessível, senão `HostError` com mensagem traduzida (chave nova `engine.hosts.herdr_unavailable`). `tmux`: `HostError` com mensagem traduzida "adaptador ainda não disponível" (chave nova `engine.hosts.tmux_unavailable`; será substituída na fase 4). Testes: cada modo, com `is_herdr_available` simulado; `auto` com e sem socket; mensagens nos dois idiomas.

### Task 6: `orchestrate` usa a seleção

**Files:**
- Modify: `meister/cli.py`
- Modify: `meister/herdr/bridge.py`
- Test: `tests/test_orchestrate_host.py`

**Depends on:** Task 5

Em `orchestrate` (`meister/cli.py` ~1415), troque `get_herdr_client` + `HerdrEventBridge(config=cfg, client=client)` por `host = select_host(cfg, socket_path=socket_path)` + `HerdrEventBridge(config=cfg, host=host)`; erros de `HostError` viram `click.ClickException`. `daemon`, `herdr-action` e o despacho de painel do comando `worker` continuam exclusivos do Herdr e não mudam. No bridge, `run_orchestration_cycle` deve funcionar quando `host.current_context()` devolve `None` (sem `workspace_id` nem painel do arquiteto): o plano vem de `--task`/`--plan-file`, e as mensagens que hoje pedem o painel do arquiteto só aparecem se ele foi informado. Teste: com `runtime.host: process` e um plano de uma tarefa com worker simulado, o ciclo roda do início ao fim sem tocar o Herdr; com `runtime.host: herdr` e sem socket, o comando termina com erro claro.

### Task 7: Cota e rate limit no host sem eventos

**Files:**
- Modify: `meister/herdr/bridge.py`
- Test: `tests/test_bridge_poll_quota.py`

**Depends on:** Task 6

Hoje a cota é detectada por eventos empurrados do Herdr (`handle_herdr_event`, ~570–600) e na leitura final do painel. Quando `CAP_PUSH_EVENTS` não está em `host.capabilities`, o laço de espera de cada worker (região que já consulta `alive` a cada `liveness_interval`, ~1280) também chama `host.tail` nesse intervalo e roda `detect_quota_or_rate_limit` sobre o texto; ao detectar, faz exatamente o que o tratador de eventos faz (`status = "quota_error"`, `record_harness_failure(tier, is_quota=True)`, `interrupt`, sinaliza `_quota_events[...]`). Hosts com `push_events` não mudam. Teste: um host de mentira sem eventos cujo `tail` passa a conter uma mensagem de cota faz o bridge escalar para a próxima via; um que imprime texto parecido com código de teste (falso positivo conhecido) não escala.

### Task 8: Ponta a ponta sem Herdr

**Files:**
- Create: `tests/fixtures/fake_harness.py`
- Create: `tests/test_e2e_process_host.py`

**Depends on:** Task 7

`tests/fixtures/fake_harness.py` é um harness de mentira executável (Python) que, conforme um argumento, escreve um arquivo no worktree e sai com 0, sai com 1 sem resultado, ou imprime uma mensagem de cota. O teste orquestra um plano de duas tarefas com `runtime.host: process` e esse harness no lugar do CLI real (o `run-task` resolve o binário pela configuração das vias, como `tests/test_worker_execution.py` já faz com `fake_bin`): afirma que as duas tarefas são integradas, que o log por worker existe em `<log_dir>/workers/` (e continua existindo depois que o worktree é removido), que o código de saída foi gravado, e que um worker que imprime cota provoca o fallback para a via seguinte. Marque como só-Unix.

### Task 9: Status da fase no documento de design

**Files:**
- Modify: `docs/superpowers/specs/2026-10-09-worker-host-adapters-design.md`

**Depends on:** Task 8

Marque a fase 2 como concluída na seção 10 e registre as decisões efetivas: `WorkerCommand.log_file` e `exit_file`, a seleção `auto`, o log por worker em `<log_dir>/workers/`, a varredura de cota por `tail`. Atualize a seção 11 (riscos): o risco "cota no modo local" passa a "mitigado por varredura periódica do `tail`; validar com o harness real".
