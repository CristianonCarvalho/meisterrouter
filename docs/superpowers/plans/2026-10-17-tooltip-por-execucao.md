# Tooltip da timeline por execução da task Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** run do projeto chronoastro (somente leitura do log, 2026-10-17). A task 1 passou por `tier_1` (copilot sem créditos, 2 tentativas), `tier_1b` (2 tentativas) e `tier_2` (agy), e aparece três vezes na timeline, uma por via. O backend está certo (`build_timeline` devolve 5 `attempt_details`, cada um com sua via, desfecho e motivo). O defeito está em `meister/dashboard/templates/timeline.html`: `showTaskTooltip` recebe a linha inteira da task, então a barra de qualquer via lista as tentativas de todas as vias, e o status e os tempos mostrados são os da task final (`completed`), não os da execução sob o mouse.

**Goal:** O tooltip de uma barra ou de um título da timeline mostra os dados da execução daquela via: as tentativas dela (com a numeração original), o desfecho dela e os tempos dela. Task com uma via só continua como hoje.

**Architecture:** Duas tarefas em sequência, as duas em `meister/dashboard/templates/timeline.html` (bloco `helpers:begin`/`helpers:end` e `showTaskTooltip`), com os testes JS que já usam `tests/timeline_js.py::run_js`.

**Tech Stack:** HTML/JS puro do painel, Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Sem textos novos nos catálogos (reaproveite `outcome_*`, `tooltip_attempt` e as chaves de duração que já existem).

### Task 1: Helper `placementTooltipRow` e numeração original das tentativas

**Files:**
- Modify: `meister/dashboard/templates/timeline.html`
- Modify: `tests/test_timeline_tooltip_attempts_js.py`

No bloco de helpers do `timeline.html` (entre `// helpers:begin` e `// helpers:end`, perto de `rowLanePlacements` e `taskTooltipExtraLines`), crie a função pura `placementTooltipRow(row, via)`. Ela devolve a linha a usar no tooltip daquela via: se `rowLanePlacements(row)` tiver menos de 2 posições, ou `via` for vazio, ou nenhuma posição tiver essa via, devolve o próprio `row` sem mudança. Caso contrário devolve uma cópia de `row` com: `segments` = os segmentos da posição dessa via; `attempt_details` = só as tentativas cuja `via` é essa, cada uma copiada com o campo `n` (número original, base 1, na lista completa de `row.attempt_details`); `end` = o fim do último segmento da posição quando ela não é a última posição da task, senão o próprio `row.end`; `status` = o `outcome` da última tentativa dessa via quando a posição não é a última (por exemplo `error`, `quota`), senão o `row.status` original. Em `attemptTooltipLine(attempt, index)`, use `attempt.n` como número do rótulo quando for inteiro positivo; sem `n`, mantenha `index + 1`. `taskTooltipExtraLines` não muda de contrato (continua recebendo a linha e devolvendo as linhas de tentativa, mais a origem do reaproveitamento). Testes em `tests/test_timeline_tooltip_attempts_js.py`, no estilo dos que já existem ali (`run_js`): uma linha com tentativas `tier_1` (erro), `tier_1`, `tier_1b` (erro), `tier_2` (completa) e segmentos nas três vias, em `placementTooltipRow(row, "tier_1b")`, traz só a tentativa de `tier_1b` com `n == 3` e as linhas de `taskTooltipExtraLines` começam com `Attempt 3:`; a posição `tier_1` traz duas tentativas com `n` 1 e 2 e `status` igual ao desfecho da última delas; a posição `tier_2` (última) mantém `status` e `end` da task; task com uma via só devolve o mesmo objeto; via inexistente devolve o mesmo objeto; os testes existentes do arquivo seguem passando.

### Task 2: `showTaskTooltip` usa a linha da execução

**Depends on:** Task 1

**Files:**
- Modify: `meister/dashboard/templates/timeline.html`
- Modify: `tests/test_timeline_tooltip_timing.py`

Em `showTaskTooltip(event, row, via, phase, seg)` (`meister/dashboard/templates/timeline.html`), calcule `scoped = placementTooltipRow(row, via)` no início e use `scoped` no lugar de `row` para o status mostrado em `tooltip-body`, para `taskTiming(scoped, seg, Date.now())` e para `taskTooltipExtraLines(scoped)`. O título (`row.title || row.task_id`) e o `task_id` continuam os da task. Não mude as chamadas em `showBar` nem em `showTitle` (elas já passam `lane.via`), nem `showLaneTooltip`. Mantenha o uso de `textContent` para tudo que vem de texto de erro. Testes: em `tests/test_timeline_tooltip_timing.py`, no estilo dos que já existem ali, acrescente casos com `taskTiming(placementTooltipRow(row, via), ...)` para a task de três vias: a duração total e a espera da posição `tier_1b` somam só os segmentos dela, e não os da task inteira; task com uma via só dá o mesmo resultado de hoje. Em `tests/test_timeline_tooltip_wiring.py`, acrescente uma verificação de que o HTML contém `placementTooltipRow(row, via)` dentro de `showTaskTooltip` e que o bloco de helpers continua aparecendo uma única vez. Rode `pytest tests/ -q`, `ruff check .` e `mypy meister` ao fim.
