# Motivo específico da falha do gate na timeline Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** o tooltip da timeline, numa falha de gate do vitest, mostra só `gate_repair (Deterministic gate failed in worker worktree:)`. O evento `gate_repair` já grava o `failure_summary` com a causa (arquivo de teste e linha), mas a timeline só sabe montar o motivo a partir de `failed_tests`, que é vazio para o vitest. Continuação de `2026-10-15-gate-vitest-e-run-id.md`, que criou a chave `js_failed_tests` em `summarize_gate_failure`.

**Goal:** O tooltip da timeline mostra o motivo específico da reprovação do gate (nome do teste ou arquivo e linha) em vez do cabeçalho genérico.

**Architecture:** Duas tarefas independentes (arquivos diferentes): a ponte grava `js_failed_tests` nos eventos de rejeição, e a timeline escolhe o melhor motivo disponível. Eventos antigos, sem `js_failed_tests`, também melhoram porque a timeline lê o `failure_summary`.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Sem textos novos nos catálogos: reutiliza `gate_repair.timeline.failure_singular` e `gate_repair.timeline.failure_plural`.

### Task 4: A ponte grava `js_failed_tests` nos eventos de rejeição do gate

**Files:**
- Modify: `meister/engine/bridge.py`
- Create: `tests/test_bridge_js_failed_tests.py`

No trecho do `bridge.py` em que `gate_summary = summarize_gate_failure(str(int_err))` alimenta `failed_tests` e `failure_summary` (em torno da linha 2305), leia também `js_failed_tests = gate_summary.get("js_failed_tests", [])`. No `log_event(event_type="gate_repair", ...)` (em torno da linha 2345) e no dicionário `event_kwargs` do evento `subtask_rejected` (em torno da linha 2422, dentro do `if reason in ("gate", "scope", "scope_conflict")`), acrescente `js_failed_tests=js_failed_tests` somente quando a lista não for vazia, para que os eventos de pytest continuem idênticos aos de hoje. Não use `js_failed_tests` na checagem de escopo (`_test_file_path`) nem no retry de pytest: essas lógicas seguem só com `failed_tests`. Testes em `tests/test_bridge_js_failed_tests.py`, no estilo de `tests/test_gate_repair.py` (veja como ele monta a ponte e captura os eventos): com saída de gate do vitest que gera `js_failed_tests`, o evento `gate_repair` e o `subtask_rejected` trazem a chave com os nomes; com saída de pytest nenhum dos dois eventos traz a chave.

### Task 5: A timeline usa o melhor motivo da reprovação do gate

**Files:**
- Modify: `meister/timeline.py`
- Create: `tests/test_timeline_gate_reason.py`

Em `meister/timeline.py` crie `_gate_failure_reason(event)` que devolve, nesta ordem, o primeiro motivo disponível: (1) `_failed_tests_reason(event)` como hoje; (2) o mesmo texto montado a partir de `js_failed_tests`, com as chaves `gate_repair.timeline.failure_singular` e `gate_repair.timeline.failure_plural`, mostrando de cada nome só o último trecho depois de ` > ` (os três primeiros e `+N`, como em `_failed_tests_reason`); (3) uma linha extraída de `failure_summary`: tire os códigos de cor ANSI, procure a primeira linha que comece com `FAIL`, depois a primeira que contenha `<arquivo>.test.<ext>:<linha>` ou `<arquivo>.spec.<ext>:<linha>` (ext `ts`, `tsx`, `js` ou `jsx`), depois a primeira que comece com `AssertionError`, `TestingLibraryElementError`, `TypeError` ou `Error:`; use a linha com `strip()` cortada por `_clip_detail`; (4) `None`. Troque as chamadas a `_failed_tests_reason(event)` em `build_timeline`/auxiliares (nos eventos `gate_repair` e nos eventos terminais, procure por `_failed_tests_reason`) por `_gate_failure_reason(event)`; a função antiga continua existindo e sendo usada pela nova. Não mude o `detail`, os resultados (`outcome`) nem as outras chaves da tentativa. Testes em `tests/test_timeline_gate_reason.py`, no estilo de `tests/test_timeline_repair_reason.py` (reaproveite o auxiliar de eventos de lá): `failed_tests` do pytest continua dando o motivo de hoje; `js_failed_tests` com dois nomes gera o texto plural com os dois últimos trechos; evento só com `failure_summary` do vitest (com cor ANSI) gera a linha `ShareCard.test.tsx:95` ou a linha `FAIL`; `failure_summary` sem nenhum padrão reconhecível mantém o motivo antigo (`gate_repair`); os testes existentes de `tests/test_timeline_repair_reason.py` seguem passando.
