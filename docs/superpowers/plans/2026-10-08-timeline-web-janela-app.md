# Timeline web por raias com janela app Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde o template diz "Commit", este plano usa **Checkpoint**.

**Goal:** Ao iniciar `meister orchestrate`, abrir uma janela de navegador em modo app (sem abas nem barra de endereço), **uma por projeto**, mostrando o timeline em SVG, ao vivo ou parado, com seleção de qualquer run do projeto e visão de todas elas: raias por via, escalonamento entre vias, caminho crítico e custo acumulado contra a linha-base. Funciona com qualquer harness como orquestrador, sem Mod e sem Herdr.

**Architecture:** Três camadas independentes. (1) Modelo: `timeline_graph.py` deriva raias, arestas, caminho crítico, escalonamentos e série de custo a partir do `Timeline` existente, e `timeline_json.py` serializa tudo. (2) Servidor: o dashboard ganha `/api/timeline`, `/api/timelines`, a página `/timeline` (com seletor de runs, modo ao vivo, modo parado e visão de todas as runs) (SVG desenhado no navegador a partir do JSON) e um arquivo de descoberta `.meister/dashboard.json` por projeto. (3) Janela: `launcher.py` acha um navegador Chromium, sobe o servidor do projeto em porta livre e abre `--app=URL` só se aquele projeto não tiver janela viva.

**Tech Stack:** Python ≥ 3.10 (CI roda 3.10 a 3.13), Flask (já usado), JavaScript puro na página, `click`, `pytest`. Nenhuma dependência nova.

**Spec de origem:** `docs/superpowers/specs/2026-10-05-timeline-tui-design.md` (modelo e regras de derivação do `timeline.py`, que este plano estende sem alterar).

## Decisões fechadas

| Tema | Decisão |
|---|---|
| Forma de abrir | Janela app do Chromium (`--app`); sem Chromium, aba normal; sem tela, só imprime a URL |
| Vários projetos | **Uma janela e um servidor por projeto**, cada um com porta livre, título e cor próprios |
| Navegação | Selecionar qualquer run e vê-la **ao vivo** (se ainda roda) ou **parada** (concluída, ou pausada); visão de todas as runs do projeto; paridade com as teclas do `meister timeline` |
| Dependência de harness | Nenhuma: o Mod do Claude Code e a janela flutuante sem borda ficam fora deste plano |

## Decisões abertas (confirmar antes de executar)

1. Padrão de `dashboard.open`: este plano assume `auto` (abre a janela app quando há Chromium e tela). Alternativa: `never` por padrão, ativado por quem quiser.
2. Baseline de custo na série: reaproveita o cálculo que o dashboard e `meister report` já usam. Se aquele cálculo não expõe a série no tempo, a Tarefa 1 deriva só a curva acumulada e mantém o total idêntico ao do relatório.

## Global Constraints

- Sem dependência nova (nada de `pywebview`, `selenium`, `playwright` ou bibliotecas de imagem); só biblioteca padrão e o que o projeto já usa.
- Compatível com Python 3.10: `from __future__ import annotations` nos módulos novos; sem `match`, sem `datetime.UTC`.
- O modelo e o comando `timeline` **só leem** o log: nunca escrevem nele. Os únicos arquivos novos que o código escreve são `<projeto>/.meister/dashboard.json` (estado do servidor) e o perfil do navegador em `~/.meister/browser-profile`.
- **Sem nomes de modelo no código**: `tests/test_no_hardcoded_models.py` varre `meister/cli.py` e `meister/herdr/tui.py`. A cor e a ordem das vias vêm de `workers.tier_order`, nunca de um nome.
- Strings de interface passam por `t()` com entradas em **todos** os idiomas dos catálogos de `meister/locales/` (o produto é em inglês por padrão, com português preservado); `tests/test_i18n_ratchet.py` deve continuar verde.
- Servidor só em `127.0.0.1`, API somente leitura. Nenhum id recebido por URL vira caminho de arquivo.
- Falha ao abrir servidor ou janela **nunca** interrompe nem atrasa `orchestrate` por mais de 2 segundos.
- Testes sem rede, sem abrir navegador de verdade, sem tocar `~/.meister` (usar `tmp_path` e `monkeypatch`).
- Suíte, `ruff check .` e `mypy meister` limpos.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.

## Review Focus

1. **Dois projetos ao mesmo tempo:** cada um grava o próprio `.meister/dashboard.json`, usa a própria porta e nunca mostra dados do outro. (Tarefas 3, 7 e 8)
2. **Servidor órfão ou estado velho:** arquivo com `pid` de processo que já morreu, ou pid reaproveitado pelo sistema. A decisão de "está vivo" usa a porta respondendo e o `last_seen`, não só o pid. (Tarefas 3 e 7)
3. **Corrida na subida:** dois `orchestrate` no mesmo projeto iniciando o servidor juntos devem resultar em um servidor só (criação atômica do arquivo de trava). (Tarefa 7)
4. **XSS pelo log:** títulos de tarefa e nomes de via vêm de planos e do log; a página monta o SVG com `createElementNS` e `textContent`, nunca com `innerHTML` nem concatenação de strings. (Tarefa 4)
5. **Ambiente sem tela** (SSH, CI, nuvem, `DISPLAY` e `WAYLAND_DISPLAY` vazios): nenhuma tentativa de abrir navegador; a URL é impressa. (Tarefa 7)
6. **Log inexistente, vazio, truncado ou de run sem `plan_parsed`:** a API devolve um JSON válido com `rows: []` e a página mostra o estado "aguardando a primeira run". (Tarefas 2, 3 e 4)
7. **Navegação entre runs:** run escolhida que ainda roda continua ao vivo e não é trocada pela run nova; run concluída não gera consulta repetida; `run_id` inexistente (hash antigo, run apagada por `meister clean`) volta ao modo ao vivo com aviso, sem erro. (Tarefa 5)

---

## Mapa de arquivos

| Arquivo | Ação | Responsabilidade |
|---|---|---|
| `meister/timeline_graph.py` | criar | Puro: raias por via, arestas, caminho crítico, escalonamentos, série de custo |
| `meister/timeline_json.py` | criar | Serialização estável (`schema: 1`) do `Timeline` e do grafo |
| `meister/dashboard/state.py` | criar | Estado do servidor por projeto: ler, gravar atômico, "está vivo?" |
| `meister/dashboard/server.py` | modificar | `/api/timeline`, `/api/timelines`, `/timeline`, heartbeat, porta livre, saída por ociosidade |
| `meister/dashboard/templates/timeline.html` | criar | Página SVG ao vivo (JS puro) |
| `meister/dashboard/launcher.py` | criar | Detectar Chromium, subir servidor desacoplado, abrir janela app |
| `meister/config.py`, `meister/default_config.yaml` | modificar | Seção `dashboard:` (`open`, `idle_exit_minutes`, `window`) |
| `meister/cli.py` | modificar | `timeline --json`, `orchestrate --no-open` e chamada ao launcher (fino) |
| `meister/locales/*.py` | modificar | Textos novos em todos os idiomas |
| `docs/execution-manual.md`, `docs/pt-BR/MANUAL_DE_EXECUCAO.md` | modificar | Seção da janela e da configuração |
| `tests/test_timeline_graph.py`, `tests/test_timeline_json.py`, `tests/test_dashboard_state.py`, `tests/test_dashboard_timeline_api.py`, `tests/test_dashboard_launcher.py` | criar | Testes |

---

## PR 1: modelo, API, página e navegação entre runs (útil mesmo sem a janela)

### Task 1: Grafo do timeline (`timeline_graph.py`)

**Files:**
- Create: `meister/timeline_graph.py`
- Test: `tests/test_timeline_graph.py`

**Interfaces:**
- Consumes: `meister.timeline.Timeline`, `TaskRow`, `Segment`; ordem das vias recebida por parâmetro (`via_index: Dict[str, int]`, como `via_index_from_config()` já fornece).
- Produces (dataclasses imutáveis, função pura `build_graph(timeline, via_index, now) -> Graph`):
  - `Lane(via, tasks: list[str], busy_s: float, utilization: float)`: uma raia por via que apareceu na run, na ordem do catálogo; `utilization` = tempo ocupado / duração da run.
  - `Edge(src, dst, critical: bool)`: uma aresta por entrada em `depends_on`.
  - `Escalation(task_id, from_via, to_via, at)`: quando a mesma tarefa mudou de via entre tentativas.
  - `critical_path: list[str]`: a cadeia de tarefas de maior duração acumulada, seguindo `depends_on` e os intervalos reais.
  - `CostPoint(t, actual_usd, baseline_usd)` e `cost_series: list[CostPoint]`.

- [ ] **Step 1: Escrever os testes que falham.** Casos: duas vias e três tarefas com dependências (arestas e caminho crítico esperados); tarefa que muda de via (uma `Escalation`); run sem dependências (caminho crítico = tarefa mais longa); run vazia (grafo vazio, sem exceção); empate no caminho crítico (resultado determinístico, desempata por `task_id`); `cost_series` monotônica e terminando no total do `Summary.cost_usd`.
- [ ] **Step 2: Rodar os testes e ver falhar** (`pytest tests/test_timeline_graph.py -q`).
- [ ] **Step 3: Implementar.** Se `TaskRow` não guardar a via de cada tentativa, ler `tier` dos eventos `worker_spawn` por tentativa dentro de `timeline_graph.py`, sem alterar `timeline.py`. O baseline reutiliza o cálculo que `meister report` já faz; não criar fórmula nova de preço.
- [ ] **Step 4: Rodar testes, `ruff check .`, `mypy meister`.**
- [ ] **Step 5: Checkpoint** (sem commit).

**Depends on:** none

### Task 2: Serialização JSON e `meister timeline --json`

**Files:**
- Create: `meister/timeline_json.py`
- Modify: `meister/cli.py`
- Test: `tests/test_timeline_json.py`

**Interfaces:**
- Consumes: `Timeline` (`meister/timeline.py`), `Graph` (Tarefa 1).
- Produces: `timeline_to_dict(timeline, graph, *, project, now) -> dict` com `schema: 1`, `project {name, hue}`, `run {id, title, status, started_at, ended_at, stalled_since}`, `summary`, `lanes`, `rows` (com `segments`), `edges`, `escalations`, `critical_path`, `cost_series`, `jev`. Datas em ISO 8601 UTC. O matiz do projeto (`hue`, 0 a 359) sai de um hash estável do caminho da raiz, não de `hash()` do Python (que varia por processo).
- Comando: `meister timeline --json [--run-id ID] [--log-dir DIR]` imprime o dicionário e sai; sem log, imprime o JSON com `rows: []` e `run: null`, código 0.

- [ ] **Step 1: Escrever os testes que falham.** Round-trip com `json.dumps`; `schema == 1`; `hue` idêntico em duas execuções e para o mesmo caminho; log inexistente devolve esqueleto válido; `--json` não escreve nada em disco.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.** A opção no `cli.py` fica fina: delega para `timeline_cli`/`timeline_json`.
- [ ] **Step 4: Rodar testes, `ruff check .`, `mypy meister`, `tests/test_no_hardcoded_models.py`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** Task 1

### Task 3: Servidor: `/api/timeline`, heartbeat e estado por projeto

**Files:**
- Create: `meister/dashboard/state.py`
- Modify: `meister/dashboard/server.py`
- Test: `tests/test_dashboard_state.py`
- Test: `tests/test_dashboard_timeline_api.py`

**Interfaces:**
- Produces em `state.py`: `read_state(project_root) -> Optional[ServerState]`, `write_state(project_root, state)` (gravação atômica: arquivo temporário + `os.replace`), `is_alive(state, *, now, max_age_s=5.0) -> bool` baseado em `last_seen` recente e `url` respondendo `/api/meta`, nunca só no pid. `ServerState`: `pid`, `port`, `url`, `started_at`, `last_seen`.
- Produces em `server.py`: `GET /api/timeline[?run_id=]` (default: run mais nova) devolvendo `timeline_to_dict`; cada chamada a `/api/timeline` atualiza `last_seen` no estado do projeto; `start_server(..., port=0)` escolhe porta livre e grava `.meister/dashboard.json`; encerra sozinho depois de `idle_exit_minutes` sem chamadas e sem run ativa; remove o arquivo de estado ao sair.
- Mantém `meister dashboard` com porta padrão 5050 e o comportamento atual quando nenhuma opção nova é usada.

- [ ] **Step 1: Escrever os testes que falham.** `read_state` com arquivo corrompido ou ausente devolve `None`; gravação atômica (arquivo nunca lido pela metade); `is_alive` falso para `last_seen` velho; `/api/timeline` com `test_client` para log vazio, log com duas runs e `run_id` desconhecido (404 com JSON); `last_seen` avança após a chamada.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.** Reaproveitar `get_log_file()`/`find_project_root()` já usados pelo servidor, sem mudar a resolução atual.
- [ ] **Step 4: Rodar testes, `ruff check .`, `mypy meister`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** Task 2

### Task 4: Página SVG ao vivo (`/timeline`)

**Files:**
- Create: `meister/dashboard/templates/timeline.html`
- Modify: `meister/dashboard/server.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Test: `tests/test_dashboard_timeline_api.py`

**Interfaces:**
- Consumes: `/api/timeline` (Tarefa 3).
- Produces: rota `/timeline` que renderiza a página com os textos de `t()`. A página consulta `/api/timeline` a cada segundo (a cada 5 segundos com a aba oculta), segue sempre a run mais nova e desenha o SVG no navegador: raias por via, barras por fase (worker, gate, integração, espera), ponte de escalonamento, setas e destaque do caminho crítico, linha "agora" e rio de custo. `<title>` = `Meister · <projeto> · <run>`. Cor de destaque a partir de `project.hue`. Pulso das pontas vivas por animação SMIL do próprio SVG; respeita `prefers-reduced-motion`.
- Visual de referência: o mock aprovado na conversa (raias, setas âmbar, rio de custo, cartões de resumo).

- [ ] **Step 1: Escrever os testes que falham.** `GET /timeline` devolve 200 e HTML com os textos traduzidos; o HTML não contém `innerHTML` nem chama CDN; teste com título malicioso no log (`<img onerror=…>`) garantindo que a página só o insere via `textContent`.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.** JS puro, sem biblioteca. SVG montado com `createElementNS`. Estado vazio: "aguardando a primeira run".
- [ ] **Step 4: Rodar testes, `ruff check .`, `mypy meister`, `tests/test_i18n_ratchet.py`.**
- [ ] **Step 5: Checkpoint** e conferência manual: `meister dashboard` e abrir `/timeline` num projeto com log real.

**Depends on:** Task 3

### Task 5: Seleção de run, modos ao vivo e parado, visão de todas as runs

**Files:**
- Modify: `meister/dashboard/server.py`
- Modify: `meister/dashboard/templates/timeline.html`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Test: `tests/test_dashboard_timeline_api.py`

**Interfaces:**
- Consumes: `/api/timeline`, `/api/runs` (já existe) e `timeline_to_dict` (Tarefa 2).
- Produces:
  - `GET /api/timelines[?limit=N]`: as runs do projeto, da mais nova para a mais antiga, cada uma no mesmo formato de `/api/timeline` (`schema: 1`); `limit` padrão 20, máximo 100. Sem log: `runs: []`.
  - Na página, três modos:
    - **Ao vivo** (padrão): segue a run mais nova e passa sozinha para a próxima quando uma run nova começa.
    - **Run selecionada**: fixa naquela run. Se ainda está em andamento, continua atualizando (ao vivo daquela run) e não salta para uma run mais nova; se terminou, desenha uma vez e não consulta mais.
    - **Pausado**: congela o quadro atual em qualquer modo; a leitura continua em segundo plano e, ao retomar, mostra o estado atual.
  - Seletor de runs: lista recolhível com id curto, título, estado, hora de início, duração e custo; clicar seleciona.
  - Visão **todas as runs**: uma faixa por run, empilhadas, com eixo de tempo comum; a run selecionada fica destacada e clicar numa faixa abre aquela run.
  - Teclas iguais às de `meister timeline`: `[` e `]` run anterior/seguinte, `l` volta ao ao vivo, `a` alterna todas as runs, `p` pausa, `+` e `-` zoom, setas andam no tempo, `?` ajuda.
  - Run na URL como `#run=<id>`, para guardar e reabrir. O id é validado contra a lista de `/api/runs` e nunca é usado como caminho de arquivo; id desconhecido volta ao modo ao vivo com um aviso curto.
  - A lógica de estado (modo, run, pausa, zoom, deslocamento) fica numa função `reduce(state, action)` isolada no JS da página, sem tocar o DOM.

- [ ] **Step 1: Escrever os testes que falham.** `/api/timelines`: ordem da mais nova para a mais antiga, `limit` respeitado e limitado a 100, log vazio devolve lista vazia, duas runs devolvem o mesmo formato de `/api/timeline`. HTML de `/timeline`: contém os controles e a tabela de teclas, textos traduzidos, e continua sem `innerHTML` nem CDN.
- [ ] **Step 2: Rodar e ver falhar** (`pytest tests/test_dashboard_timeline_api.py -q`).
- [ ] **Step 3: Implementar.** O JS não tem teste automatizado (sem dependência nova de navegador ou Node); a função `reduce` fica pequena e a Tarefa fecha com o roteiro manual abaixo.
- [ ] **Step 4: Rodar testes, `ruff check .`, `mypy meister`, `tests/test_i18n_ratchet.py`.**
- [ ] **Step 5: Roteiro manual** com um projeto de duas ou mais runs: abrir no ao vivo; selecionar uma run antiga (fica parada); selecionar a run em andamento (continua atualizando); pausar e retomar; `[`, `]`, `l`, `a`; zoom e deslocamento; recarregar a página com `#run=<id>` válido e inválido.
- [ ] **Step 6: Checkpoint.**

**Depends on:** Task 4

---

## PR 2: janela app, uma por projeto

### Task 6: Configuração `dashboard:`

**Files:**
- Modify: `meister/default_config.yaml`
- Modify: `meister/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.dashboard` com `open: auto | app | tab | never` (padrão `auto`), `idle_exit_minutes: int` (padrão 30), `window: {width: int, height: int}` (padrão 1280×800). Valor inválido gera `ConfigIssue` no padrão das demais seções.

- [ ] **Step 1: Escrever os testes que falham** (padrões, valor inválido de `open`, `idle_exit_minutes` não positivo, seção ausente no YAML do usuário).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar** no estilo de `RouterConfig` (`_default_section`).
- [ ] **Step 4: Rodar testes, `ruff check .`, `mypy meister`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** none

### Task 7: Lançador (`launcher.py`)

**Files:**
- Create: `meister/dashboard/launcher.py`
- Test: `tests/test_dashboard_launcher.py`

**Interfaces:**
- Consumes: `state.py` (Tarefa 3), `config.dashboard` (Tarefa 6).
- Produces:
  - `find_chromium(platform, env) -> Optional[list[str]]`: procura, em ordem, os executáveis do Chrome, Chromium, Edge e Brave por sistema (`shutil.which` no Linux; caminhos dos `.app` no macOS; locais comuns no Windows). Firefox e Safari não contam.
  - `has_display(platform, env) -> bool`: falso em SSH, CI e sem `DISPLAY`/`WAYLAND_DISPLAY` no Linux.
  - `build_app_argv(binary, url, profile_dir, width, height) -> list[str]` com `--app=URL`, `--user-data-dir=<perfil compartilhado>`, `--window-size=W,H` e `--no-first-run`.
  - `ensure_dashboard(project_root, config, *, open_window=True) -> Outcome`: lê o estado do projeto; se não houver servidor vivo, sobe um **desacoplado** (`start_new_session=True`, saída para `/dev/null`) com porta livre, protegido por arquivo de trava criado com `O_EXCL` para impedir dois servidores por projeto; se o `last_seen` do projeto for velho, abre a janela; devolve `Outcome(kind, url)` com `kind` em `window | tab | url_only | disabled | failed`. Nunca levanta exceção e respeita um limite de 2 segundos.
  - Perfil compartilhado em `~/.meister/browser-profile`; uma URL por projeto abre uma janela por projeto.

- [ ] **Step 1: Escrever os testes que falham**, tudo com `monkeypatch` de `subprocess.Popen`, `shutil.which` e `webbrowser.open`: sem Chromium cai em aba; sem display devolve `url_only`; `open: never` devolve `disabled`; servidor vivo e janela viva não abre nada; servidor vivo e janela morta só abre a janela; duas chamadas simultâneas sobem um servidor (trava); estado com pid morto é substituído; dois projetos diferentes geram dois estados e duas URLs; exceção em `Popen` vira `failed` sem propagar.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.** As flags do Chromium devem ser confirmadas no código e documentadas em comentário curto; nenhum nome de modelo no módulo.
- [ ] **Step 4: Rodar testes, `ruff check .`, `mypy meister`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** Task 3, Task 6

### Task 8: Integração no `orchestrate`

**Files:**
- Modify: `meister/cli.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Test: `tests/test_dashboard_launcher.py`

**Interfaces:**
- Consumes: `ensure_dashboard` (Tarefa 7).
- Produces: no início de `meister orchestrate`, uma chamada best-effort a `ensure_dashboard`; imprime uma linha com a URL (`Timeline: http://127.0.0.1:PORTA/timeline`) em qualquer resultado diferente de `disabled`; nova opção `--no-open` que equivale a `open: never` naquela execução. A abertura não bloqueia: roda em segundo plano e a orquestração segue sem esperar.

- [ ] **Step 1: Escrever os testes que falham** (com `ensure_dashboard` simulado): chamada feita uma vez por run; `--no-open` impede a chamada; exceção no lançador não altera o código de saída; saída mostra a URL.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar** mantendo `cli.py` fino; `tests/test_no_hardcoded_models.py` deve continuar verde.
- [ ] **Step 4: Rodar a suíte completa, `ruff check .`, `mypy meister`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** Task 7

### Task 9: Documentação

**Files:**
- Modify: `docs/execution-manual.md`
- Modify: `docs/pt-BR/MANUAL_DE_EXECUCAO.md`

**Interfaces:**
- Produces: seção sobre a janela do timeline: o que mostra, `dashboard.open`, `--no-open`, uma janela por projeto, o que acontece sem tela ou sem Chromium, o arquivo `.meister/dashboard.json` e como encerrar um servidor esquecido. Os dois idiomas atualizados juntos.

- [ ] **Step 1: Escrever a seção nos dois idiomas** e conferir que `tests/test_hygiene.py` e os testes de documentação continuam verdes.
- [ ] **Step 2: Checkpoint** e conferência manual ponta a ponta: `meister orchestrate` em dois projetos ao mesmo tempo, duas janelas, cada uma com o próprio título e cor.

**Depends on:** Task 5, Task 8
