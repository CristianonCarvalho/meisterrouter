# Windows: base de testes confiável (lote barato) Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde um template diria "Commit", este plano usa **Checkpoint**.
>
> **Origem:** leitura do job informativo do Windows no CI (PR #128, commit `205c92a`): `51 failed, 367 passed, 62 errors`, sessão **interrompida** com cerca de 480 dos cerca de 1550 testes executados. Os 113 itens se dividem em causas de infraestrutura de teste (este plano) e causas de produto (controle de processos, `ps`, `fcntl`), que ficam para depois do adaptador `process` (`docs/superpowers/specs/2026-10-09-worker-host-adapters-design.md`).
>
> **Independe de:** `2026-10-09-ci-e-testes.md` (a Tarefa 1 dele cobre `test_cli_init`, que fica fora deste lote). Não mexe em `meister/` exceto a Tarefa 7.

**Goal:** Fazer o job informativo do Windows rodar a suíte inteira até o fim e mostrar só falhas reais. Nada muda para Linux e macOS: toda adaptação é condicionada a Windows ou é neutra.

**Architecture:** Oito ajustes pequenos e quase todos independentes, só em testes e configuração: limpeza tolerante do diretório temporário, limite de tempo por teste que funcione no Windows, finais de linha dos scripts, marcas `skipif` para o que é só de Unix, uma normalização de caminhos no `dag.py` e correções de suposições de plataforma nos testes.

**Tech Stack:** Python ≥ 3.10 (CI roda 3.10 a 3.13 e uma perna 3.12 no macOS e no Windows), `pytest`, `pytest-xdist`. Dependência nova de desenvolvimento: `pytest-timeout`.

## Decisões fechadas

| Tema | Decisão |
|---|---|
| Watchdog | O `test_watchdog` de `tests/conftest.py` usa `SIGALRM`/`setitimer` e **não faz nada no Windows** (`alarm_signal is None`). Um teste travado ali não tem limite de tempo além dos 20 min do job. A solução é `pytest-timeout` com método `thread`, só no Windows; no Unix o watchdog atual continua. |
| Limpeza do tmp | `TemporaryDirectory(ignore_cleanup_errors=True)` (Python ≥ 3.10). Troca de risco: esconde vazamentos de arquivos abertos (SQLite, logs) como erro de teste. Aceito neste lote; o fechamento correto dos handles é trabalho de produto, depois. |
| Testes só-Unix | Um único módulo `tests/platform_marks.py` define as marcas; os testes usam `skipif`, nunca `try/except` que engula falha. |
| Fora deste lote | `test_cli_init` (plano de CI), os 3 testes de `test_dashboard_orchestrate.py` e os de `test_config.py`/`test_cli_config.py` sobre executáveis (causa ainda não identificada), e todo o controle de processos do produto. |

## Global Constraints

- Sem dependência nova além de `pytest-timeout` (Tarefa 2); compatível com Python 3.10.
- Linux e macOS: nenhum comportamento muda; a suíte inteira, `ruff check .` e `mypy meister` ficam verdes ao fim de **cada** tarefa.
- `tests/test_no_hardcoded_models.py`, `tests/test_python_floor.py` e `tests/test_docs_links.py` continuam passando.
- Não desabilitar nem pular teste que passa em Linux sem ser por plataforma; `skipif` só com `sys.platform == "win32"`.
- Testes sem rede e sem tocar `~/.meister`.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.
- A verificação final é a contagem no job do Windows do CI; localmente só se prova que o Linux não regrediu.

### Task 1: Limpeza do diretório temporário tolerante

**Files:**
- Modify: `tests/conftest.py`

**Depends on:** none

A fixture `tmp_path` (`tests/conftest.py` ~242) apaga a pasta ao fim de cada teste. No Windows, arquivo aberto impede a remoção e 54 dos 113 itens do CI são `PermissionError [WinError 32]` nessa limpeza. Troque `tempfile.TemporaryDirectory(dir=base)` por `tempfile.TemporaryDirectory(dir=base, ignore_cleanup_errors=True)` e atualize a docstring explicando o porquê (Windows mantém arquivos abertos). Mantenha a escolha atual de `base` (`/tmp` quando existir). No Linux nada muda; a suíte inteira deve continuar verde.

### Task 2: Limite de tempo por teste que funciona no Windows

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `tests/conftest.py`
- Test: `tests/test_windows_timeout_mark.py`

**Depends on:** Task 1

Adicione `pytest-timeout>=2.3.0` às dependências de desenvolvimento (`pyproject.toml`, extra `dev`, e `requirements.txt`). Em `tests/conftest.py`, acrescente um hook `pytest_collection_modifyitems` que, **só quando `sys.platform == "win32"`**, aplica a cada teste `pytest.mark.timeout(configured_test_timeout() or 180, method="thread")` (use a mesma configuração `MEISTER_TEST_TIMEOUT` do watchdog atual; `0` desliga). No Unix o hook não faz nada. Crie `tests/test_windows_timeout_mark.py`, que simula `sys.platform = "win32"` por `monkeypatch` e verifica que o hook adiciona a marca e que, com `MEISTER_TEST_TIMEOUT=0`, não adiciona.

### Task 3: Finais de linha dos scripts

**Files:**
- Create: `.gitattributes`
- Test: `tests/test_gitattributes.py`

**Depends on:** none

No checkout do Windows, `git` converte para CRLF e o `bash -n` de `tests/e2e/lib.sh` falha. Crie `.gitattributes` com `*.sh text eol=lf`, `*.template text eol=lf`, `bin/meister text eol=lf` e `bin/cli.js text eol=lf`. O teste lê o arquivo e afirma que essas quatro regras existem (para ninguém removê-las sem perceber).

### Task 4: Marcas só-Unix e testes de sinais

**Files:**
- Create: `tests/platform_marks.py`
- Modify: `tests/test_crash_matrix.py`
- Modify: `tests/test_faults.py`
- Modify: `tests/test_worker_orphan.py`
- Modify: `tests/test_orchestrate_interrupt.py`

**Depends on:** none

Crie `tests/platform_marks.py` com `posix_only = pytest.mark.skipif(sys.platform == "win32", reason="usa sinais/grupos de processos de Unix")`. Aplique `posix_only` aos testes que usam `signal.SIGKILL`, `signal.SIGHUP`, `os.kill(os.getpid(), ...)` ou `ps` nesses quatro arquivos (a matriz de falhas inteira em `test_crash_matrix.py`; os de sinais em `test_orchestrate_interrupt.py` e `test_worker_orphan.py`). Importante: no Windows, `os.kill(os.getpid(), SIGTERM)` termina o próprio processo do teste e é a principal suspeita da interrupção da sessão do CI. Linux e macOS devem rodar exatamente os mesmos testes de antes.

### Task 5: Testes do Herdr que dependem de socket Unix

**Files:**
- Modify: `tests/test_herdr_client.py`
- Modify: `tests/test_herdr_tabs.py`
- Modify: `tests/test_herdr_bridge.py`
- Modify: `tests/test_herdr_wrapper.py`

**Depends on:** Task 4

O servidor falso `tests/mocks/mock_herdr_server.py` usa `asyncio.start_unix_server`, que não existe no Windows (3 itens), e `test_herdr_wrapper.py` nem importa lá. Aplique `posix_only` (de `tests/platform_marks.py`) aos testes que sobem esse servidor ou executam o wrapper `bin/herdr-meister.sh`; não marque os que não dependem disso. Em `test_herdr_wrapper.py`, marque o módulo inteiro com `pytestmark = posix_only` **antes** de qualquer import que falhe. `tests/test_plan.py` importa o mesmo mock mas só deve ser marcado nos testes que realmente sobem o servidor (verifique; se nenhum, não o altere).

### Task 6: Testes que dependem de `ps` e de bash

**Files:**
- Modify: `tests/test_clean.py`
- Modify: `tests/test_e2e_scripts.py`

**Depends on:** Task 4

Aplique `posix_only` ao teste que depende de processos vivos via `ps` (`test_a_live_meister_process_on_the_machine_does_not_leak_into_these_tests`) e aos testes de `tests/test_e2e_scripts.py` que executam scripts de shell (`bash -n`, coleta do diretório `e2e`). Os demais testes desses arquivos continuam rodando em todas as plataformas.

### Task 7: Caminhos-alvo normalizados com barra

**Files:**
- Modify: `meister/herdr/dag.py`
- Test: `tests/test_dag.py`

**Depends on:** none

`SubtaskNode.normalized_target_files` usa `os.path.normpath`, que no Windows troca `/` por `\`; os planos declaram caminhos com `/` e `test_dag_detect_file_conflicts` falha (`['src\\user.py'] == ['src/user.py']`). Normalize com `posixpath.normpath(f.strip().replace("\\", "/"))`. No Linux o resultado é idêntico ao atual. Acrescente um teste em `tests/test_dag.py` com entradas `"src\\user.py"` e `"./src/user.py"` que se normalizam para `src/user.py` e que são detectadas como conflito entre si. Confira por `grep` se `meister/worktree.py` (`scope_violations`) tem o mesmo problema e, **só se tiver**, relate no resumo final sem corrigir aqui.

### Task 8: Suposições de plataforma em três testes

**Files:**
- Modify: `tests/test_dashboard_launcher_helpers.py`
- Modify: `tests/test_dashboard_metrics.py`
- Modify: `tests/test_copilot_adapter.py`

**Depends on:** none

1. `test_find_chromium_macos_preference_order` (4 casos): o `monkeypatch` compara `str(path)` com caminhos POSIX; no Windows `str(Path)` usa `\`. Compare com `Path.as_posix()` (ou use `PurePosixPath`). `test_default_profile_dir_uses_home_without_creating_it`: compare caminhos resolvidos (`Path.resolve()`), porque o runner devolve a forma curta `RUNNER~1`.
2. `test_server_uses_and_prints_selected_log_dir` e `test_project_from_log_uses_the_log_owner_not_the_cwd`: a asserção espera o formato POSIX do caminho na mensagem; construa o valor esperado com `str(Path(...))` em vez de texto fixo, e use `Path.samefile`/`resolve()` onde o teste compara diretórios.
3. `test_copilot_adapter`: o binário falso `fake_bin/copilot` é um script sem extensão e o Windows não o encontra. Crie, quando `sys.platform == "win32"`, um `copilot.cmd` equivalente (o `shutil.which` respeita `PATHEXT`); no Unix mantenha como está.

Em todos, o comportamento no Linux e no macOS fica idêntico.
