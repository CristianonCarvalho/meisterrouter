# `meister report` conta os reparos do gate Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com o OK do dono.
>
> **Origem:** análise de 2026-10-22 do artigo "Solving code review at Anthropic" (guardas: reverts e incidentes). Para saber se `workers.targeted_tests` (PR #191) ajudou ou fez o gate reprovar mais, é preciso comparar reparos do gate por run, e o `meister report` mostra retentativas, escalonamentos e rejeições mas não os reparos. O log já tem o evento `gate_repair` (um por reparo, com `task_id`); falta só contá-lo.

**Goal:** `meister report` passa a mostrar, por run, quantos reparos do gate houve e em quantas tarefas, nos formatos `table`, `json` e `markdown`.

**Architecture:** Duas contagens novas no bloco `attempts` do relatório em `meister/report.py`, lidas dos eventos `gate_repair` já gravados, e duas linhas novas na tabela, com rótulos nos catálogos `en` e `pt-BR`.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`.

## Global Constraints

- Só leitura do log: nenhuma mudança em eventos gravados, no motor, nem em outros comandos.
- Chaves novas em `report["attempts"]`: `gate_repairs` (total de eventos `gate_repair` da run) e `gate_repair_tasks` (quantidade de `task_id` distintos com pelo menos um `gate_repair`). Run sem reparo mostra `0`, nunca `not measured`. Os campos existentes ficam como estão.
- Rótulos novos só nos catálogos `en` e `pt-BR` (`reports.gate_repairs`: "Gate repairs" / "Reparos do gate"; `reports.gate_repair_tasks`: "Tasks with a gate repair" / "Tarefas com reparo do gate"), com a mesma chave nos dois (o `tests/test_i18n_reports.py` confere). As linhas entram na tabela logo depois de "Escalations"/"Escalonamentos".
- Os testes existentes de `tests/test_report.py` seguem passando sem alteração de expectativa, exceto os que comparam o conjunto exato de linhas ou de chaves de `attempts` (ajuste só esses, para incluir as novas).
- Ao fim da tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Contagem de reparos do gate no relatório

**Files:**
- Modify: `meister/report.py`
- Modify: `meister/locales/en_reports.py`
- Modify: `meister/locales/pt_br_reports.py`
- Modify: `tests/test_report.py`

Em `meister/report.py`, onde `report["attempts"]` é montado (junto de `retries` e `escalations`), acrescente `gate_repairs` (número de eventos de tipo `gate_repair` em `selected`) e `gate_repair_tasks` (conjunto de `task_id` desses eventos, contado), usando o mesmo `event_type(event)` das contagens vizinhas. Na tabela, acrescente as duas chaves ao laço que imprime `spawns`, `retries`, `escalations`..., logo depois de `escalations`. Adicione os dois rótulos aos catálogos `en` e `pt-BR`. Testes em `tests/test_report.py`: um log fabricado com 3 eventos `gate_repair` em 2 tarefas e outro run sem nenhum devolve `gate_repairs` 3 e `gate_repair_tasks` 2 no primeiro e `0` e `0` no segundo; a tabela, o `json` e o `markdown` trazem as linhas novas; a comparação de duas runs mostra as duas colunas.
