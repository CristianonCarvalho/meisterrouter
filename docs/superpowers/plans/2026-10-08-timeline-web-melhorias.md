# Melhorias da timeline web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita e publica com o OK do dono. O resultado visual no navegador **não** é coberto pelo gate: o orquestrador confere no Chrome depois da run.

**Goal:** Seis melhorias pedidas pelo dono na timeline web (`/timeline`): minutos no eixo do tempo (M-TL2), setas de dependência legíveis (M-TL4), tooltip com a duração da fase e da task (M-TL5), cabeçalho da via com harness, modelo e esforço (M-TL6), aviso sonoro quando uma task termina (M-TL1) e fim da fase "fantasma" depois de uma run interrompida e retomada (M-TL7).

**Architecture:** O desenho visual vive em `meister/dashboard/templates/timeline.html` (SVG montado com `createElementNS`). Para ter teste de verdade sem navegador, a lógica pura (formatação do eixo, geometria das setas, resumo de tempos, texto do cabeçalho, transições de status) vai para um bloco delimitado `// helpers:begin` ... `// helpers:end` dentro do HTML; um auxiliar de teste extrai esse bloco e o executa com `node` (o teste é pulado se `node` não existir). A API ganha dados de via (`harness`, `model`, `effort`) em `meister/timeline_json.py`, e o modelo (`meister/timeline.py`) deixa de inferir tentativa aberta até "agora" quando já existe uma tentativa posterior. As tarefas do HTML são **sequenciais** (mesmo arquivo); as de backend são independentes.

**Tech Stack:** Python ≥ 3.10 (CI roda 3.10 a 3.13), Flask, `pytest`, JavaScript simples no HTML (sem dependência nova, sem CDN), `node` só para os testes de helpers.

**Spec de origem:** itens M-TL1, M-TL2, M-TL4, M-TL5, M-TL6 e M-TL7 do backlog do dono (conversa de 2026-10-08). Não há spec em arquivo.

## Decisões fechadas

| Tema | Decisão |
|---|---|
| Eixo | Rótulos `+Ns` abaixo de 60 s; `+MmSSs` entre 1 min e 1 h (segundos omitidos quando zero, ex.: `+5m`, `+5m30s`); `+HhMMm` a partir de 1 h. As letras `s`, `m`, `h` são abreviações fixas, como o `+Ns` de hoje. |
| Setas | Saem do fim da task de origem com um trecho horizontal curto, descem/sobem num tronco vertical e chegam ao **início do primeiro segmento que não é espera** da sucessora (não ao começo da linha hachurada), com um trecho horizontal final de pelo menos 16 px antes da ponta. Vários destinos da mesma origem usam troncos em x diferentes (espaçados 6 px) para as linhas não se sobreporem. |
| Tooltip | Mantém título e linha atual; acrescenta: duração da fase sob o cursor (barra), duração total da task (do início do primeiro segmento que não é espera até o fim ou "agora") e, se houver, o tempo de espera. Rótulos por `t()` (catálogo `cli.timeline.web.*`). |
| Cabeçalho da via | `nome` seguido de `harness · modelo (SIGLA)`; se couber na largura da coluna, uma linha; senão, o detalhe vai numa segunda linha menor. Siglas de esforço: `low=L`, `medium=M`, `high=H`, `xhigh=X`, `max=MX`, `minimal=m`, `none=0`; esforço ausente, sem parênteses. Tooltip do cabeçalho com o texto completo. Via que não está mais no catálogo (run antiga): só o nome, sem erro. |
| Som | Botão e tecla `s` ligam/desligam; estado em `localStorage` (com `try/catch`; a página funciona sem ele); padrão desligado. Dois tons (WebAudio, sem arquivo de áudio): conclusão e falha. Nada soa na primeira carga nem ao trocar de run (só em transição de status vista entre duas leituras consecutivas da mesma run). |
| Tentativa aberta | Em `_build_row`, tentativa sem evento terminal que **não é a última** termina no instante em que a tentativa seguinte começa (segmento não inferido) e não deixa uma fase inferida até "agora" nem uma espera extra desde o início da run. |

## Decisões abertas

1. A tecla do som (`s`) não conflita com `[ ] l a p + - ?`; se o dono preferir outra, trocar na Tarefa 6.

## Global Constraints

- Sem dependência nova, sem CDN, sem `innerHTML`: SVG por `createElementNS`/`textContent`; teclas existentes `[ ] l a p + - ?` e as listras de espera inalteradas.
- Compatível com Python 3.10: `from __future__ import annotations` nos módulos novos; sem `match`, sem `datetime.UTC`.
- Textos de interface novos passam por `t()` com entradas em **todos** os idiomas de `meister/locales/` (`en_cli.py` e `pt_br_cli.py`, chaves `cli.timeline.web.*`, e a lista de chaves enviadas à página em `meister/dashboard/server.py`); inglês sem letras acentuadas; `tests/test_locales_parity.py` e `tests/test_i18n_ratchet.py` ficam verdes.
- Os testes de JavaScript usam `node` via subprocesso, sem rede, sem navegador, sem servidor; pulam (`pytest.skip`) se `node` não estiver no `PATH`.
- Testes sem rede, sem tocar `~/.meister` e `~/.config`, sem abrir navegador nem servidor real; a suíte já bloqueia isso (`tests/conftest.py`).
- Suíte, `ruff check .` e `mypy meister` limpos.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.

## Review Focus

1. **Run vazia, sem edges, sem `segments` ou com `segments` só de espera:** nenhuma função nova quebra; a seta cai no início da linha como hoje. (Tarefas 3 e 4)
2. **Tooltip de task ainda rodando** (sem `end`): duração até "agora", sem `NaN` nem valor negativo. (Tarefa 4)
3. **Via sem dados de catálogo** (run antiga, via renomeada): cabeçalho só com o nome, API com campos nulos, sem exceção. (Tarefas 5 e 6)
4. **Som em primeira carga, troca de run e run já terminada:** não toca; `localStorage` bloqueado: a página continua funcionando. (Tarefa 7)
5. **Tentativa aberta anterior a uma retomada:** sem fase inferida até "agora"; a última tentativa aberta continua inferida. (Tarefa 8)
6. **Rótulos longos** (modelo com nome grande, esforço `max`): o cabeçalho não invade a barra da primeira task. (Tarefa 6)

---

## Mapa de arquivos

| Arquivo | Ação | Tarefa |
|---|---|---|
| `meister/dashboard/templates/timeline.html` | modificar | 1, 2, 3, 4, 6, 7 |
| `tests/timeline_js.py` | criar | 1 |
| `tests/test_timeline_js_harness.py`, `tests/test_timeline_axis_labels.py`, `tests/test_timeline_edge_geometry.py`, `tests/test_timeline_tooltip_timing.py`, `tests/test_timeline_lane_header.py`, `tests/test_timeline_sound.py` | criar | 1, 2, 3, 4, 6, 7 |
| `meister/dashboard/server.py`, `meister/locales/en_cli.py`, `meister/locales/pt_br_cli.py` | modificar | 4, 7 |
| `meister/timeline_json.py`, `tests/test_timeline_lane_details.py` | modificar/criar | 5 |
| `meister/timeline.py`, `tests/test_timeline_open_attempts.py` | modificar/criar | 8 |
| `docs/advanced.md`, `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`, `CHANGELOG.md`, `CHANGELOG.pt-BR.md` | modificar | 9 |

---

### Task 1: Bloco de helpers em JavaScript com teste via `node`

**Files:**
- Modify: `meister/dashboard/templates/timeline.html`
- Create: `tests/timeline_js.py`
- Test: `tests/test_timeline_js_harness.py`

**Interfaces:**
- Produces em `timeline.html`: um bloco `// helpers:begin` ... `// helpers:end` dentro do `<script>` principal, sem dependência de `window`/`document`, contendo (movida, sem mudar o comportamento) a função `formatDuration(seconds)`, que usa o objeto `text` da página.
- Produces em `tests/timeline_js.py`: `extract_helpers(html_path=None) -> str` (texto entre os marcadores, erro claro se faltar) e `run_js(expression: str, text: dict | None = None) -> object` (executa `node` em subprocesso com o bloco + `const text = <json>` e devolve o JSON de `expression`); pula via `pytest.skip` se `node` não existir.

- [ ] **Step 1: Escrever o teste que falha.** `formatDuration(5)`, `(75)`, `(3700)` e `(NaN)` com o `text` do catálogo `en`; o bloco existe uma só vez; o bloco não contém `document`, `window` nem `innerHTML`.
- [ ] **Step 2: Rodar e ver falhar** (`pytest tests/test_timeline_js_harness.py -q`).
- [ ] **Step 3: Implementar** (mover `formatDuration` para o bloco e criar o auxiliar).
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`.**
- [ ] **Step 5: Checkpoint** (sem commit).

**Depends on:** none

### Task 2: Minutos no eixo do tempo (M-TL2)

**Files:**
- Modify: `meister/dashboard/templates/timeline.html`
- Test: `tests/test_timeline_axis_labels.py`

**Interfaces:**
- Consumes: `run_js` e o bloco de helpers da Tarefa 1.
- Produces no bloco de helpers: `formatTickLabel(seconds)` com as regras da tabela de decisões (`0` → `+0s`; `59` → `+59s`; `60` → `+1m`; `90` → `+1m30s`; `3600` → `+1h00m`; `3725` → `+1h02m`; negativo ou `NaN` → `+0s`); o eixo usa essa função no lugar de `"+" + tSec + "s"` (e o rótulo equivalente da visão "todas as runs", se houver).

- [ ] **Step 1: Escrever os testes que falham.** Valores acima via `run_js`; o HTML servido não contém mais o literal `"+" + tSec + "s"`.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`.**
- [ ] **Step 5: Checkpoint** (sem commit).

**Depends on:** Task 1

### Task 3: Setas de dependência legíveis (M-TL4)

**Files:**
- Modify: `meister/dashboard/templates/timeline.html`
- Test: `tests/test_timeline_edge_geometry.py`

**Interfaces:**
- Consumes: `run_js` e o bloco de helpers.
- Produces no bloco de helpers: `firstWorkStartX(row, timeToX, fallbackX)` (x do início do primeiro segmento de `row.segments` cuja fase não é `wait`; sem segmentos ou só espera → `fallbackX`) e `edgePathD(sx, sy, dx, dy, laneIndex)` que devolve o `d` de um caminho SVG em cotovelo ortogonal: trecho horizontal curto (≥ 12 px) a partir de `sx`, tronco vertical em `x = sx + 12 + 6 * laneIndex` (limitado para ficar pelo menos 16 px antes de `dx`), trecho horizontal final de ≥ 16 px até `dx`; se `dx - sx` for pequeno demais para isso, cai num caminho reto sem cotovelo. `laneIndex` = posição da seta entre as que saem da mesma origem. O bloco "Dependency edges" usa `firstWorkStartX` como destino e `edgePathD` com o índice por origem; `marker-end` e cores inalterados.

- [ ] **Step 1: Escrever os testes que falham.** `firstWorkStartX` com wait+worker, só wait, vazio; `edgePathD`: começa com `M sx sy`, termina em `dx dy`, tronco distinto para `laneIndex` 0, 1 e 2, trecho final ≥ 16 px, caso estreito sem cotovelo, `sy == dy` (reta).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`.**
- [ ] **Step 5: Checkpoint** (sem commit).

**Depends on:** Task 2

### Task 4: Tooltip com duração da fase e da task (M-TL5)

**Files:**
- Modify: `meister/dashboard/templates/timeline.html`
- Modify: `meister/dashboard/server.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Test: `tests/test_timeline_tooltip_timing.py`

**Interfaces:**
- Consumes: `formatDuration` e o `run_js`.
- Produces no bloco de helpers: `taskTiming(row, seg, nowMs)` → `{phaseSec, totalSec, waitSec}` (todos `null` quando não calculáveis; nunca `NaN` nem negativo). `phaseSec` = duração do segmento `seg` (fim ausente → `nowMs`); `totalSec` = do início do primeiro segmento que não é `wait` até `row.end` (ou `nowMs` se a task roda); `waitSec` = soma dos segmentos `wait`. `showTaskTooltip` acrescenta linhas só com os valores não nulos, usando rótulos novos `cli.timeline.web.tooltip_phase_duration`, `tooltip_total_duration` e `tooltip_wait_duration` (as três chaves entram na lista de `server.py` e nos dois catálogos; o texto do valor usa `formatDuration`).

- [ ] **Step 1: Escrever os testes que falham.** `taskTiming` com task concluída, task em execução (usa `nowMs`), só espera, `seg` indefinido (barra do nome da task: sem `phaseSec`), fim antes do início (não negativo); as três chaves existem nos dois catálogos e chegam à página.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`, `tests/test_locales_parity.py`.**
- [ ] **Step 5: Checkpoint** (sem commit).

**Depends on:** Task 3

### Task 5: Dados da via na API da timeline (M-TL6, backend)

**Files:**
- Modify: `meister/timeline_json.py`
- Test: `tests/test_timeline_lane_details.py`

**Interfaces:**
- Produces: cada item de `lanes` no JSON da timeline ganha `harness`, `model` e `effort` (strings ou `null`). Função pura `lane_catalog_details(names, tiers)` em `meister/timeline_json.py` que mapeia o nome da via para o `WorkerTier` do catálogo (`workers.tier_order` da configuração do projeto; leia com `load_config` de forma tolerante a erro de configuração: erro → todos `null`). Vias fora do catálogo → `null` nos três campos. O campo `via` e os demais não mudam.

- [ ] **Step 1: Escrever os testes que falham.** Via do catálogo padrão (`tier_1` → `copilot`, o modelo do catálogo, `effort` nulo); via com `effort` configurado; via desconhecida; configuração inválida não levanta; JSON existente intacto (chaves antigas).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`.**
- [ ] **Step 5: Checkpoint** (sem commit).

**Depends on:** none

### Task 6: Cabeçalho da via com harness, modelo e esforço (M-TL6, página)

**Files:**
- Modify: `meister/dashboard/templates/timeline.html`
- Test: `tests/test_timeline_lane_header.py`

**Interfaces:**
- Consumes: campos `harness`, `model`, `effort` da Tarefa 5 e o `run_js`.
- Produces no bloco de helpers: `effortAbbreviation(effort)` (tabela da decisão; desconhecido → o próprio texto em maiúsculas, vazio → `""`), `laneDetailText(lane)` (`"harness · modelo (SIGLA)"`, partes ausentes omitidas; sem harness e sem modelo → `""`) e `laneHeaderLayout(lane, labelWidthPx, charPx)` → `{name, detail, twoLines}`: uma linha quando `nome + "  " + detail` cabe em `labelWidthPx - 16 - 40` (reserva para a utilização em %), senão `twoLines: true`. O desenho da via usa isso: segunda linha menor e `LANE_HEADER_H` maior só quando houver segunda linha; tooltip do cabeçalho (`showTaskTooltip` não serve: crie um tooltip simples reaproveitando o elemento `#tooltip`) com o texto completo.

- [ ] **Step 1: Escrever os testes que falham.** Siglas; `laneDetailText` com tudo, sem esforço, sem modelo, sem nada; `laneHeaderLayout` curto (uma linha), longo (duas) e sem detalhe (uma).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`.**
- [ ] **Step 5: Checkpoint** (sem commit).

**Depends on:** Task 4, Task 5

### Task 7: Aviso sonoro ao terminar uma task (M-TL1)

**Files:**
- Modify: `meister/dashboard/templates/timeline.html`
- Modify: `meister/dashboard/server.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Test: `tests/test_timeline_sound.py`

**Interfaces:**
- Consumes: o `run_js` e o bloco de helpers.
- Produces no bloco de helpers: `finishedTransitions(prevStatuses, rows)` → `{completed: [task_id...], failed: [task_id...]}` com as tasks cujo status passou de não terminal para `completed`/`reused` ou `failed` entre as duas leituras; `prevStatuses` nulo ou vazio (primeira carga) → listas vazias; devolve também o novo mapa `{task_id: status}` em `next`. Botão e tecla `s` (ajuda de atalhos incluída) alternam o som; estado em `localStorage` com `try/catch`; dois tons curtos por WebAudio (conclusão e falha), criados só depois de um gesto do usuário; nada soa ao trocar de run. Textos novos (chaves `cli.timeline.web.sound_on`, `sound_off`, `sound_title`, `shortcut_sound`) nos dois catálogos e na lista de `server.py`.

- [ ] **Step 1: Escrever os testes que falham.** `finishedTransitions`: primeira carga sem som, task que conclui, task que falha, task que já estava concluída, task nova já concluída (sem som), run trocada; as quatro chaves existem nos dois idiomas e chegam à página; o HTML tem o botão e o handler da tecla `s`, e `localStorage` só aparece dentro de `try`.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`, `tests/test_locales_parity.py`.**
- [ ] **Step 5: Checkpoint** (sem commit).

**Depends on:** Task 6

### Task 8: Fim da fase fantasma depois de run interrompida e retomada (M-TL7)

**Files:**
- Modify: `meister/timeline.py`
- Test: `tests/test_timeline_open_attempts.py`

**Interfaces:**
- Produces: em `_build_row`, uma tentativa SEM evento terminal que não é a última (existe uma tentativa depois dela) termina no `spawn` da tentativa seguinte, como segmento da fase seguinte à última fase concluída (hoje `_NEXT_PHASE[last_phase]`), **não inferido**, e `previous_end` passa a ser esse instante (sem espera extra desde o início da run). A última tentativa aberta continua como hoje (inferida até `run_end` ou "agora"). Nenhuma mudança no formato do JSON.

- [ ] **Step 1: Escrever os testes que falham.** Eventos sintéticos no padrão de `tests/timeline_fixtures.py`: tentativa 1 com `worker_spawn` e sem fim, tentativa 2 completa → o segmento da 1 termina no spawn da 2, `inferred` falso, sem segmento que vá até "agora", sem `wait` extra; só uma tentativa aberta (última) → inferida como hoje; tentativa aberta seguida de retry normal; duas abertas antes de uma completa.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Suíte inteira (inclui `tests/test_timeline*.py`), `ruff check .`, `mypy meister`.**
- [ ] **Step 5: Checkpoint** (sem commit).

**Depends on:** none

### Task 9: Documentação e CHANGELOG

**Files:**
- Modify: `docs/advanced.md`
- Modify: `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.pt-BR.md`

**Interfaces:** nenhuma; só texto.

- [ ] **Step 1:** Na seção da timeline web de cada guia, documentar: eixo com minutos, setas em cotovelo, tooltip com durações, cabeçalho da via com harness/modelo/esforço (e as siglas), som (botão e tecla `s`, desligado por padrão, preferência por navegador) e a correção da tentativa aberta.
- [ ] **Step 2:** Uma entrada em `[Unreleased]` nos dois CHANGELOG (Added/Fixed; Adicionado/Corrigido).
- [ ] **Step 3: Suíte inteira (`tests/test_docs_links.py`, `tests/test_hygiene.py`), `ruff check .`.**
- [ ] **Step 4: Checkpoint** (sem commit).

**Depends on:** Task 7, Task 8
