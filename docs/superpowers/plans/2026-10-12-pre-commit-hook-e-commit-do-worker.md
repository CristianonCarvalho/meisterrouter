# Hook de pre-commit e commit do worker Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** relatório `trade_omqs/project/RELATORIO_MEISTER_PRECOMMIT_HOOK.md` (Meister 0.9.1, run `a603ff4a`). Os itens 2.1 a 2.4 e 6 do relatório foram conferidos no código atual (`main` em `c855bb7`): `commit_worktree` roda `git commit` sem `--no-verify` (`meister/worktree.py:447`), o template do hook roda `pytest -q` fixo (`git_pre_commit.sh.template`), a exceção sobe crua em `execute_parallel_batch` (`meister/engine/bridge.py:2433`) e o `git add -A -- . :(exclude).venv` do arquivamento (`meister/worktree.py:849`) retorna 1 quando `.venv` está no `.gitignore` (reproduzido à mão).
>
> **Não verificado:** a causa exata do `↻4` (quatro tentativas) do relatório. O código de commit não gasta reparo; o relançamento pode ter somado tentativas de outra forma. Peça o log do run se o `↻` continuar depois deste plano.

**Goal:** O commit interno do Meister nunca é reprovado por um hook de pre-commit; se alguma falha de commit ainda ocorrer, ela mostra a saída do git, não gasta reparo, preserva o trabalho e fecha a subtask como FAILED. O hook gerenciado passa a usar o gate do projeto, e o arquivamento de worktree funciona com `.venv` no `.gitignore`.

**Architecture:** Duas cadeias independentes. Cadeia A (`worktree.py`/`bridge.py`): `--no-verify` no commit interno, exceção própria com a saída do git, tratamento como erro de infraestrutura, subtask FAILED em exceção inesperada, arquivamento com `.venv` ignorado. Cadeia B (`cli.py`/templates): comando `meister gate` que roda os `gate.commands` do projeto, hook gerenciado que o chama, aviso no setup. A tarefa final cobre changelog e docs.

**Tech Stack:** Python ≥ 3.10, click, `pytest`. Textos novos entram nos catálogos `en` e `pt-BR` (a paridade é testada).

## Decisões fechadas

- Commit interno usa `--no-verify` e define `MEISTER_INTERNAL_COMMIT=1`. Vale para qualquer hook (inclusive os do usuário, como husky), porque o commit é do Meister, numa branch descartável, e a árvore já passou no gate. Os commits feitos por humanos continuam passando pelo hook.
- `meister gate` roda os mesmos comandos do gate no diretório atual e sai com 0 ou 1. Sem `--staged`: o git não isola o índice sem um worktree, e o gate já cobre a árvore inteira.
- O hook gerenciado cai na detecção atual (`pytest -q`, `npm test`, `cargo test`) só se o comando `meister` não existir no PATH.

### Task 1: Commit interno não dispara hooks

**Files:**
- Modify: `meister/worktree.py`
- Test: `tests/test_worktree_commit_hook.py`

Em `WorktreeManager.commit_worktree`, passe `--no-verify` ao `git commit` e acrescente `MEISTER_INTERNAL_COMMIT=1` ao `env`. Teste: repositório temporário com `.git/hooks/pre-commit` que sai com 1; um worktree com arquivo novo; `commit_worktree` devolve o hash. Teste também que `git commit` comum no mesmo repositório continua bloqueado pelo hook.

### Task 2: Falha de commit mostra a saída do git

**Depends on:** Task 1

**Files:**
- Modify: `meister/worktree.py`
- Modify: `meister/locales/en_engine.py`
- Modify: `meister/locales/pt_br_engine.py`
- Test: `tests/test_worktree_commit_error.py`

Crie `WorktreeCommitError(RuntimeError)` em `meister/worktree.py`, com os atributos `returncode`, `stdout`, `stderr` e `message`. Em `commit_worktree`, capture `subprocess.CalledProcessError` e levante `WorktreeCommitError`; o texto vem de uma chave nova `engine.worktree.commit_failed` nos dois catálogos e traz o código de saída e as últimas linhas de `stderr` e `stdout`, sem repetir o texto inteiro do commit. Teste: com um hook que imprime uma frase e sai com 1 e `--no-verify` desligado via monkeypatch do comando, a mensagem do erro contém a frase.

### Task 3: Falha de commit é erro de infraestrutura

**Depends on:** Task 2

**Files:**
- Modify: `meister/worktree.py`
- Modify: `meister/locales/en_engine.py`
- Modify: `meister/locales/pt_br_engine.py`
- Test: `tests/test_worktree_commit_infra.py`

No fluxo de `prepare` do worker (a chamada `commit_worktree` logo depois do gate aprovado, `meister/worktree.py` por volta da linha 1532), capture `WorktreeCommitError` e devolva `prepared.early` com o mesmo tratamento do erro de infraestrutura do gate (modelo: o ramo `infrastructure_error` logo acima, `_gate_infrastructure_message` e `_log_gate_infrastructure_error`): sem reparo, trabalho preservado no arquivo `refs/meister/archive/*`, mensagem ao dono dizendo que o trabalho do worker não foi rejeitado. Chaves novas nos dois catálogos. Teste: commit que falha por exceção simulada resulta em `prepared.early` com `passed=False`, sem consumir reparo.

### Task 4: Exceção inesperada fecha a subtask como FAILED

**Depends on:** Task 3

**Files:**
- Modify: `meister/engine/bridge.py`
- Test: `tests/test_bridge_unexpected_exception_state.py`

Em `execute_parallel_batch` (e no ponto de `execute_subtask` que melhor couber), quando uma subtask levantar exceção, registre a causa com `logger.error(..., exc_info)` e transicione a subtask para `SubtaskState.FAILED` com o erro, dentro de `try/except` e só se houver `active_run_id` (modelo: PR #169, mesma classe de bug). O resumo final não pode mais mostrar `running` para ela. Teste: subtask que levanta `RuntimeError` termina FAILED no resumo.

### Task 5: Arquivamento com `.venv` no `.gitignore`

**Depends on:** Task 4

**Files:**
- Modify: `meister/worktree.py`
- Modify: `tests/test_worktree_archive_uncommitted.py`

Em `meister/worktree.py:849`, o `git add -A -- . :(exclude).venv` falha com código 1 quando `.venv` é ignorado (com a barra ou sem, diretório ou link simbólico). Use `git add -A` simples, que já respeita o `.gitignore`, e mantenha a exclusão de `.venv` só se ele NÃO estiver ignorado (por exemplo, `git check-ignore -q .venv`). Teste novo em `tests/test_worktree_archive_uncommitted.py`: `.gitignore` com `.venv` e depois com `.venv/`, worktree com `.venv` como link simbólico e como diretório; o arquivamento conclui, o worktree é removido e `.venv` não entra no snapshot. Mantenha o teste existente `test_snapshot_excludes_ignored_files_and_venv_symlink` passando.

### Task 6: Comando `meister gate`

**Files:**
- Modify: `meister/cli.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Test: `tests/test_cli_gate.py`

Crie `meister gate [--path DIR]`, que carrega a configuração do projeto, instancia `DeterministicGate` (`meister/gate.py`) e chama `run_verification_ex(path)` (o mesmo caminho do gate do orquestrador, com `gate.commands`, `gate.python`, `gate.env`). Imprime a saída; sai com 0 se `passed`, 1 se reprovou, 2 se for erro de infraestrutura (sem testes detectáveis). Textos de ajuda e mensagens nos dois catálogos. Teste: projeto temporário com `gate.commands: [{name: ok, run: ["true"]}]` sai 0; com `["false"]` sai 1.

### Task 7: Hook gerenciado usa o gate do projeto

**Depends on:** Task 6

**Files:**
- Modify: `meister/templates/en/git_pre_commit.sh.template`
- Modify: `meister/templates/pt-BR/git_pre_commit.sh.template`
- Modify: `meister/hooks.py`
- Test: `tests/test_hooks.py`

Nos dois templates, depois do desvio de "nada staged", se `MEISTER_INTERNAL_COMMIT` estiver definido saia com 0; se `command -v meister` existir, rode `meister gate` e use o código de saída (`TEST_RESULT=fail` se diferente de 0); senão mantenha a detecção atual. Confirme o nome real do diretório pt-BR em `meister/templates/`. Os hooks já instalados continuam "gerenciados" porque o conteúdo do template muda: ajuste `_is_managed_hook` em `meister/hooks.py` só se for preciso para reconhecer a versão anterior e permitir a atualização sem `--force`. Teste: o conteúdo instalado chama `meister gate`; o hook antigo gerenciado é atualizado; um hook de terceiros continua preservado.

### Task 8: Setup avisa quando o hook vai bloquear

**Depends on:** Task 7

**Files:**
- Modify: `meister/cli.py`
- Modify: `meister/locales/en_commands.py`
- Modify: `meister/locales/pt_br_commands.py`
- Test: `tests/test_cli_init_hook_warning.py`

Depois de instalar o hook (`init --hooks`/`install-hooks`, `meister/cli.py` por volta das linhas 248 e 746), rode `DeterministicGate.detect_test_runner` no projeto; se não houver teste detectável nem `gate.commands`, imprima um aviso nos dois idiomas: o hook vai reprovar commits até o dono configurar `gate.commands` em `meister.config.yaml`. O aviso não muda o código de saída. Teste: projeto sem testes imprime o aviso; projeto com `pytest.ini` não imprime.

### Task 9: Changelog e documentação

**Depends on:** Task 5, Task 8

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.pt-BR.md`
- Modify: `docs/advanced.md`
- Modify: `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`

Em `### Fixed` de `[Unreleased]`: commit interno sem hook, erro de commit com a saída do git e tratado como infraestrutura, subtask FAILED em exceção inesperada, arquivamento com `.venv` ignorado. Em `### Added`: `meister gate`. Na seção "Set up a project (`init`, hooks, and guard)" de `docs/advanced.md` e na equivalente em português, explique que o hook gerenciado roda `meister gate` (os mesmos comandos do gate), que o commit do Meister não passa por hooks e como configurar `gate.commands` quando o aviso do Task 8 aparecer. Atualize o índice `docs/superpowers/plans/README.md` não: o orquestrador faz isso.
