# Parser do vitest usa só as linhas FAIL Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** teste ponta a ponta do `gate.flaky_retries` com o vitest 2.1.8 real (projeto temporário com um teste instável). A saída real mostrou que `summarize_gate_failure` coletava também a linha de andamento `× suite > teste 3ms`, sem arquivo e com a duração no fim, além da linha `FAIL src/arquivo.test.ts > suite > teste` do bloco `Failed Tests`. Continuação de `2026-10-16-flaky-vitest-e-nome-da-run.md`.

**Goal:** `js_failed_tests` traz uma entrada por teste que falhou, sempre com o arquivo, sem duração, e a reexecução do vitest filtra só por arquivos reais.

**Architecture:** Uma tarefa, em `meister/gate.py` e nos dois arquivos de teste que já cobrem o parser e a reexecução.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Sem textos novos nos catálogos.

### Task 1: `js_failed_tests` só com linhas `FAIL` e com filtro de arquivo

**Files:**
- Modify: `meister/gate.py`
- Modify: `tests/test_gate_vitest_summary.py`
- Modify: `tests/test_gate_flaky_vitest.py`

A saída real do vitest 2.1.8 para um teste reprovado tem, no andamento, a linha `   × suite > passa so na segunda vez 3ms` (sem arquivo, com a duração) e, no bloco `Failed Tests`, a linha ` FAIL  src/flaky.test.ts > suite > passa so na segunda vez`, seguida de `AssertionError: expected 0 to be greater than or equal to 1`, de um frame ` ❯ src/flaky.test.ts:8:15` e do fim `Test Files  1 failed | 1 passed (2)` e `     Tests  1 failed | 1 passed (2)`. Em `meister/gate.py`, mude `VITEST_FAIL_RE` para reconhecer só linhas que começam com `FAIL` (os marcadores `❯` e `×` deixam de valer como falha de teste), de modo que cada teste reprovado entre uma única vez em `js_failed_tests`, no formato `arquivo > suite > teste`, sem a duração. Remova um sufixo de duração (` 3ms`, ` 1.2s`) do fim do item, se existir. Em `_vitest_failed_files`, descarte do primeiro trecho um prefixo de projeto do vitest no formato `|nome| ` (modo workspace) e ignore itens cujo primeiro trecho não pareça um arquivo (sem ponto ou sem barra); a ordem e a ausência de repetição continuam. O resto de `summarize_gate_failure` (excerpt, contagens, mensagens de erro, ausência de mudança em `failed_tests`) não muda. Nos testes: em `tests/test_gate_vitest_summary.py`, acrescente um caso com a saída real descrita acima (as linhas na ordem em que aparecem) que gera `js_failed_tests == ["src/flaky.test.ts > suite > passa so na segunda vez"]`, sem a linha do `×`, e um caso em que a linha `FAIL` vem com cor ANSI; ajuste os casos existentes que dependiam de `×` ou `❯` para usar linhas `FAIL`. Em `tests/test_gate_flaky_vitest.py`, acrescente: o comando reexecutado recebe só o arquivo real (`src/flaky.test.ts`), uma vez; um item com prefixo `|app| src/x.test.ts > a > b` filtra por `src/x.test.ts`; itens sem arquivo (ex.: `suite > teste`) não geram reexecução.
