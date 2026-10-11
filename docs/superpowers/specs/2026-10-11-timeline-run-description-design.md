# Timeline: a run se descreve sozinha (nome, vias, tentativas e motivos)

**Status:** implementado em 2026-10-11 (itens 1 a 8; o item 0, tooltip no toque, foi feito à parte em #151). Ficou de fora: o eixo contínuo entre runs (uma run retomada com plano editado continua começando em +0s; a tarefa reaproveitada só indica a run de origem e a duração original).
**Origem:** achados do dono em 2026-10-10 e 11, registrados no `BACKLOG.md`: nome legível da run, raia `tier_3b` sem harness e modelo, tarefa desenhada inteira na última via, run retomada recomeçando em +0s, motivo da rejeição do gate fora da tela.

## 1. Problema
A timeline reconstrói a descrição da run a partir do **config atual**, e não do que a run usou, e junta informação que os eventos já têm mas a tela não mostra:
1. O título da run vem de `orchestration_start.task` (`meister/timeline.py:404`). Em run por `--plan-file` isso é o JSON bruto do plano.
2. `lanes[].harness/model/effort` vêm de `load_config()` no processo do dashboard (`meister/timeline_json.py`, `_catalog_tiers`): uma via desligada ou criada com `-c` aparece sem rótulo (`tier_3b`).
3. Uma tarefa com fallback de via aparece inteira na raia da **última** via (`timeline.html` agrupa as linhas por `r.tier`). A ponte de escalonamento liga o centro das raias, não a tarefa.
4. O intervalo entre uma tentativa que acabou em erro e a seguinte é pintado como `gate`, mesmo quando foi `quota_error`.
5. O motivo de uma rejeição (arquivos fora do escopo, linha do gate que falhou, linha que casou a cota) existe só no texto do erro; o tooltip não o mostra.
6. Uma run retomada com plano editado vira run nova e recomeça em +0s, com as tarefas reaproveitadas como pontos sem duração.

## 2. Objetivos e não objetivos
**Objetivos:** (a) nome da run no cabeçalho e no seletor; (b) rótulo de via a partir dos eventos; (c) cada tentativa na raia da sua via; (d) motivo da rejeição no tooltip; (e) tarefa reaproveitada indica de qual run veio. Compatível com logs antigos (sem os campos novos, tudo cai no comportamento atual).
**Não objetivos:** mudar o formato dos eventos existentes (só acrescentar campos), eixo contínuo entre runs, editar nada pela timeline.

## 3. Design
### 3.1 Eventos (`meister/herdr/bridge.py`, `meister/cli.py`)
- `orchestrate --name TEXT` e `run_name` no `orchestration_start` (campo novo, opcional). Sem `--name`: título derivado do plano (primeiro título de tarefa; o H1 do plano importado quando houver).
- `worker_spawn` (e `worker_start`) ganha `harness`, `model` e `effort` da via usada.
- Eventos de rejeição e de reparo ganham `detail` (até 200 caracteres): arquivos fora do escopo; primeira linha da falha de `ruff`/`mypy`/`pytest`; linha que casou a cota (`matched_line` já existe no `quota_error`).
- `subtask_reused` já traz `source_run_id`; acrescentar a duração original quando conhecida.
### 3.2 Modelo (`meister/timeline.py`, `timeline_graph.py`, `timeline_json.py`)
- `Timeline.title` prefere `run_name`, depois o título derivado; nunca o JSON bruto.
- Cada **tentativa** carrega `via`, `outcome` (`completed`, `error`, `quota`, `timeout`, `gate_rejected`, `scope`) e `detail`. Cada segmento carrega a `via` da sua tentativa. O trecho entre tentativas deixa de ser `gate` quando o fim foi `quota` ou `error`.
- `lanes[]`: harness, model e effort vêm do evento `worker_spawn` mais recente da via; o catálogo do config fica como reserva para eventos antigos.
- API: `rows[].attempts[]` com `{via, outcome, reason, detail, start, end}`.
### 3.3 Página (`timeline.html`)
- Cabeçalho: `MEISTER <projeto> · <nome da run>`; título do documento; seletor com o nome.
- Linhas: a tarefa é desenhada **em cada raia onde uma tentativa rodou**, ligada por uma ponte na altura da linha; fallback lateral (mesmo nível) distinto de escalonamento na legenda e no tooltip.
- Tooltip: lista das tentativas com via, resultado e `detail`.
- Tarefa reaproveitada: marcador "reaproveitada de <run>" e a duração original no tooltip.
- **Item 0 (feito à parte):** tooltip no toque alterna, fecha ao tocar fora, com Escape, rolagem e auto-esconder.

## 4. Riscos
- A mudança de desenho das linhas (3.3) é a maior; só se verifica no navegador. Plano de teste: demonstração sintética (`python -m meister.timeline_demo`) com um caso de fallback entre vias e a verificação por GIF antes e depois.
- Logs antigos: toda leitura dos campos novos tem reserva; teste com um log de runs de 9 e 10/10.
- Desempenho: `rows[].attempts[]` aumenta o JSON; limitar `detail` a 200 caracteres.

## 5. Testes
Eventos novos (bridge e cli); modelo (tentativas, via por segmento, título); API (compatibilidade com log antigo); helpers JavaScript por `node`; demonstração sintética estendida com um fallback e uma rejeição por escopo; paridade dos catálogos e `test_i18n_ratchet`.

## 6. Divisão sugerida em tarefas (pequenas)
1. `--name` e `run_name` (`cli.py`, `bridge.py`) + título derivado.
2. `worker_spawn` com harness/model/effort; `detail` nos eventos de rejeição e reparo.
3. `timeline.py`: título, tentativas com via e outcome; trecho entre tentativas.
4. `timeline_graph.py` e `timeline_json.py`: lanes pelos eventos; `attempts[]` na API; compatibilidade com log antigo.
5. `timeline.html` (helpers puros por `node`): cabeçalho, seletor e título.
6. `timeline.html`: raias por tentativa e pontes; legenda.
7. `timeline.html`: tooltip com tentativas e motivos; marcador de reaproveitada.
8. Demonstração sintética estendida; docs e CHANGELOG.
