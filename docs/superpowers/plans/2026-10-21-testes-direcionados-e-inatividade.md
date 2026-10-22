# Testes direcionados no worker e inatividade que vê testes rodando Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com o OK do dono.
>
> **Origem:** análise de custo de 2026-10-21 sobre 311 sessões de workers do Claude. Em 51% das sessões (158) o worker rodou a suíte inteira, 479 vezes no total, embora o gate já a rode depois; cada execução leva minutos e a saída é relida a cada volta. Além disso, enquanto o worker espera um teste longo o pane não muda e o `git status` também não, e o Meister declara inatividade (42 timeouts no log, 17 com trabalho aproveitado), o que pode abrir outra sessão que relê todo o contexto.

**Goal:** (1) O prompt de todo worker passa a pedir que ele rode só os testes dos arquivos que alterou, deixando a suíte inteira, o `ruff` e o `mypy` do repositório para o gate (com chave para desligar). (2) O timeout por inatividade não dispara enquanto houver um executor de testes ou de verificação rodando dentro do processo do harness do worker.

**Architecture:** Uma chave `workers.targeted_tests` (padrão `true`) lida pelo carregador de config e uma frase fixa acrescentada ao prompt por `HarnessWorker`. Uma função nova em `meister/osops` lista os comandos dos descendentes de um processo (POSIX e Windows); o laço de espera do `engine/bridge.py` a usa, a cada verificação de vivacidade, para contar um executor de testes em andamento como atividade. O teto `max_runtime_seconds` continua valendo.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`; `psutil` só no Windows (já é dependência).

## Global Constraints

- Chave: `workers.targeted_tests`, booleano, padrão `true`, em `meister/default_config.yaml` e em `meister.config.example.yaml` (com comentário). Valor que não for booleano é erro de validação do config, com mensagem nova nos catálogos `en` e `pt-BR`, no mesmo estilo de `workers.lean_context`. Com `false`, o prompt do worker fica byte a byte igual ao de hoje.
- A frase nova vai no prompt de todos os harnesses, depois das partes atuais e antes de nada mais; fica em inglês como o resto do prompt. Texto novo de interface só nos catálogos `en` e `pt-BR`.
- Inatividade: só conta como atividade um descendente do processo do harness cujo comando seja um executor reconhecido (lista fixa no `bridge.py`); processos de longa duração que não sejam executores (servidores MCP, etc.) nunca contam. Falha ao inspecionar processos equivale a "sem atividade" e nunca derruba a run. O teto `max_runtime_seconds` e todo o resto do laço ficam como estão.
- Nenhum teste chama CLI real nem executa pytest de verdade como alvo da detecção: use processos dublês ou listas de comandos simuladas. Testes que mexem em HOME usam HOME e USERPROFILE isolados (o `tests/conftest.py` já faz isso) e comparam caminhos com `os.path.normcase(os.path.realpath(...))` (o CI roda no Windows).
- Os testes novos que carregam o config padrão não podem ler o `meister.config.yaml` da raiz do repositório: acrescente os módulos de teste novos que carregam o config ao `CWD_ISOLATED_MODULES` de `tests/conftest.py`.
- Ao fim de cada tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Chave de configuração `workers.targeted_tests`

**Files:**
- Modify: `meister/config.py`
- Modify: `meister/default_config.yaml`
- Modify: `meister.config.example.yaml`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_config_targeted_tests.py`

Adicione `targeted_tests: bool` (padrão `true`, vindo do `default_config.yaml`) a `WorkersConfig` em `meister/config.py`, lida em `_parse_config_dict` junto de `lean_context` e validada em `validate_config` como booleano (erro `ConfigIssue` com a mensagem nova `reports.config.targeted_tests_boolean`, nos dois catálogos, no estilo da de `lean_context`). Documente a chave em `default_config.yaml` e em `meister.config.example.yaml`: `true` manda o worker rodar só os testes dos arquivos que alterou e deixar a suíte inteira e as verificações do repositório para o gate. Em `tests/conftest.py`, acrescente `test_config_targeted_tests` ao `CWD_ISOLATED_MODULES`. Testes: padrão `true`; `false` lido; valor que não é booleano gera erro; `meister config validate` aceita o exemplo; o salvamento de vias do editor (`meister/config_editor.py`) preserva `workers.targeted_tests` num arquivo que já a tem.

### Task 2: Prompt do worker pede testes direcionados

**Files:**
- Modify: `meister/worker.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_worker_targeted_tests.py`

**Depends on:** Task 1

Em `meister/worker.py`, faça `HarnessWorker.__init__` guardar `self.targeted_tests` lido de `self.config.workers.targeted_tests` (`False` se a config for `None` ou não tiver a chave: o dublê de config de outros testes não pode mudar de prompt), do mesmo modo que `lean_context`. Em `run_task`, quando `self.targeted_tests` for verdadeiro, acrescente ao fim da lista de partes do prompt (depois do `Instructions:` opcional e do texto de fechamento que já existe, sem alterá-los) a frase: `Verification: run only the tests that cover the files you changed (for example a single test file or a -k filter); do not run the whole test suite or repository-wide linters and type checkers, even if the task text asks for it. The evidence gate runs them in full after you finish and will send failures back to you.` Em `tests/conftest.py`, acrescente `test_worker_targeted_tests` ao `CWD_ISOLATED_MODULES`. Testes (substitua a execução da CLI por um dublê que grava o comando; nenhuma CLI real): com a chave ligada o prompt termina com a frase; desligada, o prompt é idêntico ao anterior para os mesmos argumentos; config `None` não acrescenta a frase; a frase aparece também quando há `extra_instructions` e quando há `## REPAIR` na descrição.

### Task 3: `osops.descendant_commands`

**Files:**
- Modify: `meister/osops/posix.py`
- Modify: `meister/osops/windows.py`
- Modify: `meister/osops/__init__.py`
- Create: `tests/test_osops_descendants.py`

Acrescente `descendant_commands(pid: int) -> list[list[str]]`: os argv de todos os descendentes (filhos, netos...) de `pid`, sem incluir o próprio `pid`. POSIX: leia `ps -axww -o pid=,ppid=,command=` (mesma técnica e mesma tolerância a linha malformada de `list_processes`, com `shlex.split` e ignorando linhas que não separam) e percorra a árvore a partir de `pid`, protegendo contra ciclos; falha do `ps` levanta `ProcessInspectionError`. Windows: `psutil.Process(pid).children(recursive=True)` com `cmdline()`, ignorando os sem linha de comando e os que sumirem no caminho (`psutil.NoSuchProcess`); `pid` inexistente devolve lista vazia; outros erros do psutil viram `ProcessInspectionError`. Exporte a função em `meister/osops/__init__.py` (atribuição e `__all__`, como as demais). Testes em `tests/test_osops_descendants.py`: na POSIX, suba de verdade um processo pai que cria um filho (`sys.executable -c` com um `sleep` curto, e uma identificação única no argv) e confira que o neto aparece e que o próprio pai não; árvore simulada (substitua o `subprocess.run` do `ps` por uma saída fabricada) com ciclo e com linha malformada; `pid` sem filhos devolve lista vazia. Marque como `skipif` na plataforma que não for a da implementação testada.

### Task 4: Inatividade vê executores de teste

**Files:**
- Modify: `meister/engine/bridge.py`
- Create: `tests/test_bridge_idle_checks.py`

**Depends on:** Task 3

Em `meister/engine/bridge.py`, acrescente `_is_check_command(argv: list[str]) -> bool` (função pura) e `_worker_is_running_checks(pid_file: str) -> bool`. `_is_check_command` reconhece, comparando o nome base (sem `.exe`) de elementos do argv e nunca texto dentro de um argumento longo: `pytest`, `py.test`, `vitest`, `jest`, `ruff`, `mypy`, `tsc`, `eslint`; a forma `-m pytest|ruff|mypy` (elemento `-m` seguido do módulo); e os pares `cargo test`, `npm test`, `mvn test`, `go test`. `_worker_is_running_checks` lê o `pid` do arquivo `.harness.json` (o mesmo que `_reap_worker_harness` usa, com `read_atomic_json`), chama `osops.descendant_commands(pid)` e devolve verdadeiro se algum comando for de executor; arquivo ausente, `pid` inválido ou `ProcessInspectionError` devolvem falso, sem levantar exceção. No laço de espera, dentro do bloco que já roda a cada `liveness_interval`, depois das verificações de git e de pane: se `last_activity` ainda não foi renovado nesse ciclo e `_worker_is_running_checks(...)` (chamada por `asyncio.to_thread`, com o `.harness.json` calculado como em `_reap_worker_harness`) for verdadeiro, faça `last_activity = time.monotonic()`. Nada mais no laço muda. Testes em `tests/test_bridge_idle_checks.py`: `_is_check_command` aceita `["python","-m","pytest","tests/"]`, `["/x/.venv/bin/pytest","-q"]`, `["npm","test"]`, `["ruff","check","."]` e rejeita `["claude","-p","rode o pytest aqui"]` (texto dentro de argumento), `["node","server.js"]`, `["python","-m","http.server"]`; `_worker_is_running_checks` com `descendant_commands` substituído por dublês (executor presente, só servidor presente, erro de inspeção, arquivo de pid ausente ou inválido); e um teste do efeito no laço: reutilize os testes de timeout por inatividade já existentes em `tests/` como modelo e mostre que, com um executor de teste "rodando" por meio do dublê, o timeout por inatividade não dispara, enquanto sem ele dispara como antes.
