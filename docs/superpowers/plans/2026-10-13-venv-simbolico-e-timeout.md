# `.venv` simbólico e saída do worker no timeout Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --deps files`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** adendo `trade_omqs/project/RELATORIO_MEISTER_RUN_MT4.md` (Meister 0.9.1, runs `623c8733` e `815ecf5b`). Os itens 2.1 a 2.3 foram conferidos no código atual (`main` em `30fd9c2`, que já tem o #171).
>
> **O que o código confirmou:**
> - 2.1: com `.venv/` (barra) no `.gitignore` e `.venv` como link simbólico no worktree, `get_modified_files` devolve `.venv` (escopo reprovado) e `commit_worktree` **commita o link** (reproduzido num repositório temporário). O contorno do relatório (`.venv` em `scope.tolerated_files`) faria esse link, que aponta para um caminho absoluto da máquina, entrar na branch do usuário. No `trade_omqs` nenhum `.venv` foi commitado (conferido em todas as refs). O mesmo `git add ... :(exclude).venv` que o #171 corrigiu no arquivamento existe também em `meister/gate.py` (`_worktree_tree_hash`), onde a falha só desliga o cache do gate. Quem cria o link não foi apurado: `grep symlink meister` não acha nada, então foi o worker ou o harness.
> - 2.2: `meister logs` lê `<log_dir>/workers/*.log`, mas só o host `process` grava esse arquivo (`meister/hosts/process.py:96`); o host Herdr ignora `log_file` (`meister/hosts/herdr.py` só tem `read_pane`). Com Herdr a saída fica só no painel, e o evento `worker_timeout` (`meister/engine/bridge.py` por volta da linha 1679) leva `pane_id` e `message`, sem o trecho final da saída. A causa dos dois timeouts ociosos do relatório não foi apurada.
> - 2.3: o `clean` mantém a branch `meister/integration/<run>` porque ela está aberta num worktree que ainda existe (`_worktree_branches`, `meister/clean.py:162`); o `clean` não remove o worktree da run abortada, e o `--close-stale-runs` só fecha runs em RUNNING ou PENDING. Hipótese coerente com a remoção manual do usuário (`git worktree remove` e `branch -D`), não reproduzida com o estado original.
> - 2.4 (plano que cita arquivo fora do repositório) fica no BACKLOG: não há proposta segura.

**Goal:** Um `.venv` ou `venv` no worktree, link ou pasta, nunca conta como mudança do worker nem entra num commit; depois de um timeout o trecho final da saída do worker fica guardado, com Herdr ou não; o `meister clean` de run abortada fica fora (BACKLOG I13).

**Architecture:** Três tarefas independentes (um arquivo de código cada), mais a documentação no fim. Nenhuma usa `:(exclude)` com um caminho ignorado, porque o git recusa isso; a retirada do índice usa `git rm --cached --ignore-unmatch`, que não falha.

**Tech Stack:** Python ≥ 3.10, click, `pytest`. Textos novos entram nos catálogos `en` e `pt-BR` (a paridade é testada).

## Decisões fechadas

- `.venv` e `venv` são tratados sempre como ambiente, não como código: ignorados no cálculo de arquivos modificados e fora de qualquer commit do Meister.
- O trecho final da saída é guardado em `log_file` (para `meister logs`) e em `output_tail` no evento `worker_timeout` (últimas 40 linhas).
- O item 2.3 (`meister clean` de run abortada) ficou de fora: a causa é hipótese, o contorno manual são dois comandos e a correção apagaria worktrees (BACKLOG I13).

### Task 1: `.venv` simbólico não é mudança nem entra no commit

**Files:**
- Modify: `meister/worktree.py`
- Test: `tests/test_worktree_venv_symlink.py`

Crie em `meister/worktree.py` um conjunto `ENVIRONMENT_DIRS = (".venv", "venv")` e um auxiliar que retira esses caminhos do índice com `git rm -r --cached -q --ignore-unmatch -- .venv venv` (não use `:(exclude)`, que falha quando o caminho é ignorado). Use-o em `commit_worktree`, logo depois do `git add -A` e antes de checar se há algo staged. Em `get_modified_files`, descarte `.venv`, `venv` e o que estiver abaixo deles, como já é feito com `.meister`. Teste, num repositório temporário com `.venv/` (com barra) no `.gitignore` e depois com `.venv` (sem barra): um worktree com `.venv` como link simbólico e como pasta, mais um arquivo novo. `get_modified_files` devolve só o arquivo novo, e o commit de `commit_worktree` o contém e não contém `.venv`. Os testes existentes de worktree continuam verdes.

### Task 2: Cálculo da árvore do gate com `.venv` ignorado

**Files:**
- Modify: `meister/gate.py`
- Test: `tests/test_gate_tree_hash_venv.py`

Em `_worktree_tree_hash` (`meister/gate.py`, o `git add -A -- . :(exclude).venv`), o git retorna 1 quando `.venv` está no `.gitignore`, e a função devolve `None` (sem cache do gate). Troque por `git add -A` seguido de `git rm -r --cached -q --ignore-unmatch -- .venv venv` com o mesmo `GIT_INDEX_FILE` temporário, para que o hash exclua o ambiente sem falhar. Teste: com `.venv` no `.gitignore` (com e sem barra, pasta e link simbólico) a função devolve um hash e o hash não muda quando o conteúdo do `.venv` muda.

### Task 3: Timeout guarda o trecho final da saída

**Files:**
- Modify: `meister/engine/bridge.py`
- Test: `tests/test_worker_timeout_output_tail.py`

No ponto em que o bridge trata um timeout do worker (a emissão de `worker_timeout`, por volta da linha 1679, antes de o painel ser fechado), leia as últimas 40 linhas da saída do worker pelo host (`read` do `HerdrHost`, `meister/hosts/herdr.py`; no host `process`, o próprio `log_file`). Acrescente o campo `output_tail` ao evento `worker_timeout` e, se o `log_file` da tentativa ainda não existir (caso do Herdr), grave nele esse trecho, criando a pasta, para que `meister logs` mostre algo. O gate roda o mypy: não passe o campo por `**dict[str, str]` ao `log_event` (o mypy reclama dos parâmetros tipados `duration_ms`, `cost`); use o argumento nomeado `output_tail=tail` (valor `Optional[str]`), com duas chamadas ou um dicionário tipado como `Dict[str, Any]`. Qualquer erro na leitura é engolido (o timeout continua sendo tratado como hoje). Teste com um host falso cujo `read` devolve linhas conhecidas: o evento traz `output_tail` e o arquivo existe com o texto; com `read` levantando exceção, o evento sai sem `output_tail` e sem falha.

### Task 4: Changelog e documentação

**Depends on:** Task 1, Task 2, Task 3

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.pt-BR.md`
- Modify: `docs/advanced.md`
- Modify: `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`

Em `### Fixed` de `[Unreleased]`: `.venv`/`venv` simbólico deixa de contar como mudança e de entrar em commit; o cálculo da árvore do gate funciona com `.venv` ignorado; o evento `worker_timeout` traz `output_tail` e `meister logs` mostra a saída do worker com Herdr. Nas duas versões de `docs/advanced.md` (seção do host dos workers e logs, ou a mais próxima), diga que com Herdr a saída do worker só é gravada em arquivo quando há timeout e onde achá-la, e que `.venv` não precisa mais constar em `scope.tolerated_files`.
