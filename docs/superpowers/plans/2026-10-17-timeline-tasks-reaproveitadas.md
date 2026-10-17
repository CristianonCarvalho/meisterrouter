# Timeline: tasks reaproveitadas na retomada Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** run do projeto chronoastro (somente leitura do log, 2026-10-17). A run `308bd2b6` é uma retomada: as tasks 1 a 8 vêm de `subtask_reused`, cada uma em 0,1 a 0,6 s, todas no início do eixo. Na página aparecem como fiapos roxos de largura quase zero, empilhados, e as arestas de dependência se amontoam sobre eles. Os cartões ficam errados: "Peak parallel" mostra 8 (as 8 reutilizações simultâneas) e a média 1,0. A duração original de cada task reaproveitada já chega em `reused_duration_s` e só aparece no tooltip.

**Goal:** Uma task reaproveitada aparece como um marcador próprio de tamanho fixo ("reaproveitada"), legível e sem parecer trabalho de 0 s; e ela não conta no paralelismo de pico nem na média da run.

**Architecture:** Duas tarefas em arquivos diferentes, executadas em sequência: a de números em `meister/timeline.py` e a de desenho em `meister/dashboard/templates/timeline.html`.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`, HTML/JS puro do painel. Texto novo só na chave de legenda descrita abaixo, nos dois idiomas.

### Task 1: Paralelismo ignora as tasks reaproveitadas

**Files:**
- Modify: `meister/timeline.py`
- Create: `tests/test_timeline_reused_parallelism.py`

Em `_parallelism` (`meister/timeline.py`), pule as linhas com `row.status == "reused"` ao montar as marcas de início e fim: uma task reaproveitada não consumiu um worker nesta run e não entra em `peak_parallel` nem em `avg_parallel`. O resto não muda: `Summary.completed` continua contando `reused` como concluída, os segmentos das linhas reaproveitadas continuam nas linhas, e uma run sem tasks reaproveitadas dá os mesmos números de hoje. Testes em `tests/test_timeline_reused_parallelism.py`, no estilo de `tests/test_timeline.py` (veja `tests/timeline_fixtures.py`): oito eventos `subtask_reused` de uma run, seguidos de uma task real em andamento, dão `peak_parallel == 1` e `avg_parallel == 1.0`; as oito aparecem em `completed`; uma run sem reaproveitadas mantém os valores de um caso já existente.

### Task 2: Marcador de tamanho fixo para a task reaproveitada

**Files:**
- Modify: `meister/dashboard/templates/timeline.html`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Create: `tests/test_timeline_reused_bar_js.py`

Em `timeline.html`, quando `row.status === "reused"`, desenhe no lugar das barras proporcionais dos segmentos um único marcador de largura fixa de 36 px, com o início na posição x do começo da linha, altura `BAR_H`, sem preenchimento sólido, com contorno tracejado em `var(--muted)` e o texto curto "reaproveitada" dentro, se couber (senão sem texto); a duração de 0 s do segmento não decide a largura. Mantenha o tooltip da barra como está (já lista "Reaproveitada de {run}" e a duração original) e mantenha as arestas de dependência saindo do fim do marcador. Acrescente à legenda (o bloco `.legend`, com o mesmo formato dos outros itens e uma classe `swatch-reused` de contorno tracejado) o item da nova chave `cli.timeline.web.legend_reused` ("Reused" em `meister/locales/en_cli.py`, "Reaproveitada" em `meister/locales/pt_br_cli.py`); confira em `tests/test_timeline_messages_keys.py` que a chave nova chega à página (`_timeline_messages` em `meister/dashboard/server.py` expõe todas as chaves `cli.timeline.web.*`). Crie uma função pura `reusedMarkerWidth(row)` no bloco `helpers:begin`/`helpers:end` que devolve 36 para uma linha reaproveitada e `null` para as demais, e use-a no desenho. Testes em `tests/test_timeline_reused_bar_js.py`, no estilo de `tests/test_timeline_tooltip_attempts_js.py` (`run_js`): `reusedMarkerWidth` devolve 36 para `{status: "reused"}` e `null` para `completed`, `running` e `failed`; o HTML servido contém a classe `swatch-reused` e o texto da legenda nos dois idiomas; o bloco de helpers continua aparecendo uma única vez.
