# Timeline web: seletor de projeto Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com o OK do dono.
>
> **Origem:** pedido do dono em 2026-10-20: a página `/timeline` deve permitir escolher qual projeto ver, como a `/config` já permite (PR #187). Hoje a timeline mostra sempre o projeto onde o dashboard foi iniciado.

**Goal:** A `/timeline` ganha um seletor de projeto (os projetos registrados em `~/.meister/projects.json`); ao escolher um, a página mostra as runs e a timeline daquele projeto, lidas do log e do `meister.config.yaml` dele, só leitura.

**Architecture:** Os leitores de configuração da timeline (preços, vias, tempo de parada) passam a aceitar a pasta de um projeto; as três rotas que a página usa (`/api/runs`, `/api/timelines`, `/api/timeline`) aceitam `?project=<id>` e resolvem o projeto pelo registro, como a `/config`; a página guarda o projeto escolhido na URL e o repassa a todas as chamadas.

**Tech Stack:** Python ≥ 3.10, Flask, JavaScript puro em `timeline.html` (helpers testados via `tests/timeline_js.py`), `pytest`, `ruff`, `mypy`.

## Global Constraints

- Ver outro projeto é leitura pura: nunca criar pasta, arquivo ou estado dentro dele (nada de `.meister/` novo, nada de `last_seen`). Só o projeto servido pelo dashboard continua tendo o estado atualizado, como hoje.
- Sem `?project=` (ou com o id do projeto servido) o comportamento é exatamente o de hoje. Os leitores de configuração, chamados sem argumento, continuam usando o config do diretório atual.
- Projeto: só id do registro (`get_project`). Id desconhecido responde 404 com `error`/`reason` `unknown_project`; projeto que sumiu do disco responde 409 `project_missing`; reaproveite `_resolve_config_project` e as mensagens `reports.dashboard.config_unknown_project` e `config_project_missing`. O formato do corpo de erro é o da `/config` (`_config_error`).
- Log do projeto escolhido: `<projeto>/.meister/logs/orchestration_log.jsonl`. Log ausente se comporta como projeto sem runs (lista vazia, timeline vazia, 404 se um `run_id` foi pedido), nunca como erro 500.
- Config do projeto escolhido: `meister.config.yaml` (ou `.yml`) da pasta dele, lido por caminho explícito; sem arquivo, valores padrão (`MeisterConfig()`), sem cair no config do diretório atual; config ilegível conta como sem vias, como já faz `_catalog_tiers`.
- Texto novo só nos catálogos `en` e `pt-BR` (`cli.timeline.web.*`) e exposto por `_timeline_messages()`; nenhum texto fixo na página.
- Testes que mexem em registro de projetos usam HOME e USERPROFILE isolados (o `tests/conftest.py` já faz isso) e comparam caminhos com `os.path.normcase(os.path.realpath(...))` (o CI roda no Windows).
- Ao fim de cada tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Leitores de configuração por projeto

**Files:**
- Modify: `meister/timeline_cli.py`
- Modify: `meister/timeline_json.py`
- Create: `tests/test_timeline_project_config.py`

Em `meister/timeline_cli.py`, faça `_tiers`, `via_index_from_config`, `price_tables_from_config` e `stale_after_from_config` aceitarem `project_path: Optional[str] = None`. Sem argumento, nada muda (`load_config()` como hoje). Com a pasta, leia `<pasta>/meister.config.yaml` ou `.yml` por caminho explícito (`load_config(config_path=...)`); sem nenhum dos dois, use `MeisterConfig()` padrão. Qualquer exceção segue o tratamento atual de cada função. Em `meister/timeline_json.py`, faça `_catalog_tiers`, `timeline_to_dict` e `timeline_json_for_log` aceitarem o mesmo `project_path` opcional e repassarem aos leitores; em `timeline_json_for_log`, quando `project_path` for dado, é ele (e não o diretório atual) quem define as vias, preços e o tempo de parada. Testes novos: duas pastas de projeto temporárias com configs diferentes (preço e `max_runtime_seconds`) devolvem tabelas diferentes; pasta sem config devolve os padrões e não o config do diretório atual; sem argumento o resultado é igual ao de antes (os testes existentes de `test_timeline_cli.py` seguem passando).

### Task 2: Rotas da timeline aceitam o projeto

**Files:**
- Modify: `meister/dashboard/server.py`
- Create: `tests/test_dashboard_timeline_project.py`

**Depends on:** Task 1

Em `meister/dashboard/server.py`, faça `/api/runs`, `/api/timelines` e `/api/timeline` lerem `request.args.get("project")` e resolverem com `_resolve_config_project`; erro do resolvedor é devolvido como está. Quando o projeto é outro que o servido: o log é `<projeto>/.meister/logs/orchestration_log.jsonl` lido com `iter_events` sem criar nenhuma pasta (não use `get_log_file()`, que cria `.meister/logs`); log ausente ou ilegível equivale a sem runs; os leitores de config da Task 1 recebem a pasta do projeto; `_touch_project_state` não é chamado; o campo `project` do JSON de `/api/timeline` e `/api/timelines` descreve o projeto escolhido. Sem o parâmetro ou com o id do projeto servido, o caminho atual fica intacto. Testes novos (cliente Flask, registro isolado, dois projetos temporários com `orchestration_log.jsonl` de runs diferentes): as três rotas devolvem as runs do projeto pedido e não as do servido; id desconhecido dá 404 `unknown_project`; projeto removido do disco dá 409 `project_missing`; projeto registrado sem log devolve lista vazia e timeline vazia e não cria `.meister` nele; um `run_id` pedido num projeto sem log dá 404; consultar outro projeto não altera o estado (`last_seen`) de nenhum dos dois.

### Task 3: Textos e helpers do seletor

**Files:**
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Modify: `meister/dashboard/server.py`
- Modify: `meister/dashboard/templates/timeline.html`
- Create: `tests/test_timeline_project_js.py`

Adicione as chaves `cli.timeline.web.project_label` ("Project"/"Projeto") e `cli.timeline.web.project_missing` ("{name} (folder missing)"/"{name} (pasta ausente)") nos dois catálogos e na tupla de `_timeline_messages()` (o teste `tests/test_timeline_messages_keys.py` cobre isso). No bloco `// helpers:begin` ... `// helpers:end` de `timeline.html` (funções puras, sem `window`/`document`), acrescente: `projectFromSearch(search)` (lê o parâmetro `project` de uma query string, texto vazio vira `null`), `withProject(url, projectId)` (acrescenta `project=<id>` com `?` ou `&` conforme a URL, `encodeURIComponent`; `null` devolve a URL intacta) e `projectOptions(projects, currentId)` (lista de `{id, name, path, missing}` a partir de `/api/projects`, com `missing` quando `exists === false`, ordenada como veio; o id atual que não está na lista entra como primeiro item). Testes em `tests/test_timeline_project_js.py` via `run_js`, cobrindo os casos de borda (sem parâmetro, vazio, caracteres especiais, URL com e sem `?`).

### Task 4: Seletor na página da timeline

**Files:**
- Modify: `meister/dashboard/templates/timeline.html`
- Create: `tests/test_timeline_project_page.py`

**Depends on:** Task 3

Na barra de cima de `timeline.html`, junto ao nome do projeto, acrescente um `<select id="project-select">` com rótulo `messages.project_label`, no mesmo estilo visual dos outros controles da barra (o da `/config` em `meister/dashboard/templates/config.html` serve de referência para o carregamento de `/api/projects`, a opção desabilitada para projeto ausente e a escolha do projeto atual). A página lê o projeto da URL (`projectFromSearch(location.search)`) ao iniciar e o mantém no parâmetro `project` junto dos demais (`updateUrl`); o projeto servido não precisa aparecer na URL. Todas as chamadas `fetch` de `/api/runs`, `/api/timelines` e `/api/timeline` passam por `withProject`. Ao trocar o projeto: limpa o estado da run escolhida, a lista de runs e o desenho, atualiza a URL e recarrega catálogo e timeline do novo projeto; respostas de um projeto antigo que chegarem depois da troca são descartadas. Projeto desconhecido ou ausente na URL: mostre num aviso o erro devolvido pelo servidor (`error` do corpo) e volte ao projeto servido. A cor do projeto e o título da aba já seguem `data.project` e não mudam. Testes em `tests/test_timeline_project_page.py`: a página renderizada (cliente Flask, os dois idiomas) contém `project-select` e o rótulo traduzido, e o JS da página usa `withProject` nas três URLs (verificação textual do template, como os outros `test_timeline_*_wiring.py`).
