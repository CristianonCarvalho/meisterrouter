# Reexecução do vitest para arquivo de teste na raiz Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** teste ponta a ponta com o vitest 2.1.8 real. Um teste instável em `raiz.test.ts` (arquivo na raiz do projeto, sem barra no caminho) não é reexecutado: `_vitest_failed_files` só aceita itens cujo primeiro trecho tenha ponto e barra. Continuação de `2026-10-16-vitest-so-linhas-fail.md`.

**Goal:** A reexecução do `gate.flaky_retries` cobre arquivos de teste do vitest em qualquer pasta, inclusive na raiz, sem aceitar nomes de suite como se fossem arquivos.

**Architecture:** Uma tarefa em `meister/gate.py` e no teste que já cobre a reexecução.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Sem textos novos nos catálogos.

### Task 1: `_vitest_failed_files` reconhece arquivo pela extensão

**Files:**
- Modify: `meister/gate.py`
- Modify: `tests/test_gate_flaky_vitest.py`

Em `_vitest_failed_files` (`meister/gate.py`), troque o critério de "parece um arquivo" (hoje exige ponto e barra no primeiro trecho) por: o primeiro trecho, depois de retirado o prefixo `|nome| `, termina numa extensão de script de JavaScript ou TypeScript (`.js`, `.jsx`, `.ts`, `.tsx`, `.mjs`, `.cjs`, `.mts` ou `.cts`, sem diferenciar maiúsculas) e não é vazio; a barra deixa de ser exigida. Itens como `suite > teste` ou `v1.2 > teste` (primeiro trecho sem extensão de script) continuam ignorados. A ordem, a ausência de repetição e o tratamento do prefixo de projeto não mudam. Em `tests/test_gate_flaky_vitest.py`, acrescente: um item `raiz.test.ts > suite > teste` gera a reexecução do arquivo `raiz.test.ts`; `src/a.test.tsx > x` e `|app| lib/b.spec.mts > y` seguem aceitos; `suite > teste` e `v1.2 > teste` não geram reexecução; os testes existentes do arquivo seguem passando.
