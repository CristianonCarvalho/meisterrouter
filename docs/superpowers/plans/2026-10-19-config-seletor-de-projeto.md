# Tela /config com seletor de projeto Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** spec `docs/superpowers/specs/2026-10-19-config-seletor-de-projeto-design.md` (aprovado pelo dono em 2026-10-19): a configuração continua **por projeto**; a página `/config` passa a mostrar em qual projeto se está configurando e a trocar de projeto numa caixa de seleção; a lista vem de um registro automático e de um botão "Descobrir projetos".

**Goal:** A `/config` mostra o projeto em edição, troca de projeto por uma caixa de seleção, e lê e grava as vias do projeto escolhido (só projetos registrados), com o bloqueio de escrita por projeto; o registro de projetos é preenchido pelos comandos e por "Descobrir projetos" e "Adicionar projeto".

**Architecture:** Um módulo de registro (`meister/projects.py`), o registro automático nos pontos que já resolvem a raiz do projeto, rotas novas no dashboard, as rotas de configuração aceitando o identificador do projeto, e a página com a barra de projeto em duas etapas.

**Tech Stack:** Python ≥ 3.10, Flask, `ruamel.yaml`, `pytest`, `ruff`, `mypy`, HTML/JS puro com `node` nos testes.

## Global Constraints

- Registro: `~/.meister/projects.json` (`os.path.expanduser("~")`), formato `{"version": 1, "projects": [{"path", "name", "last_seen"}]}`, gravação atômica. O identificador de um projeto são os 12 primeiros caracteres hexadecimais do SHA-1 do caminho real; a página e a API só usam o identificador.
- Só projetos do registro podem ser lidos ou gravados pela API; um caminho vindo da rede nunca é usado como destino. Identificador desconhecido: 404 com `reason: "unknown_project"`; diretório que sumiu: 409 com `reason: "project_missing"`.
- Um projeto é aceito se o caminho é um diretório absoluto existente que contém `.meister/`, `meister.config.yaml` ou `.git`; a pasta pessoal e `/` são recusadas.
- As rotas que escrevem (inclusive as de registro de projetos) usam as mesmas proteções da escrita da `/config`: `Content-Type: application/json` mais o cabeçalho `X-Meister-Config: 1` (400 sem eles) e 403 para pedidos com cabeçalhos do Tailscale. As rotas de leitura ficam liberadas.
- Bloqueio de escrita por projeto: 409 `run_active` se o **projeto escolhido** tem run ativa, lida do log dele (`<projeto>/.meister/logs/orchestration_log.jsonl`) com a mesma lógica que `_has_active_run` usa hoje (`meister/dashboard/metrics.py`, `list_runs`); log ausente ou ilegível conta como sem run. Para o projeto atual do dashboard continue usando `_has_active_run()` (os testes existentes a substituem por um simulador).
- Texto novo da página só nos catálogos `en` e `pt-BR` (`cli.config.web.*`), com a chave também na lista de `_config_messages` em `meister/dashboard/server.py`; nenhum texto fixo no HTML ou no JavaScript. Mensagens de erro de API novas em `_MESSAGES` de `meister/dashboard/server.py` no mesmo estilo das existentes.
- Não mudar `meister/config_editor.py` (ele já trabalha com o caminho que receber), o motor de fallback nem as demais seções do `meister.config.yaml`.
- Ao fim de cada tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Registro de projetos e pasta pessoal isolada nos testes

**Files:**
- Create: `meister/projects.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_projects.py`

Em `tests/conftest.py` acrescente um fixture automático (`autouse=True`, de função) que redireciona `HOME` e `USERPROFILE` para uma pasta temporária nova em todos os testes, para nenhum teste ler o `~/.meister` real; os testes que já definem `HOME` ou `USERPROFILE` por conta própria continuam funcionando. Crie `meister/projects.py` com: `projects_file() -> str`; `project_id(path) -> str` (SHA-1 do `realpath`, 12 caracteres); `validate_project_path(path) -> str` (devolve o caminho real ou levanta `ProjectError` com um código: `not_absolute`, `not_a_directory`, `not_a_project`, `forbidden`); `register_project(path) -> dict` (valida, acrescenta ou atualiza `last_seen`, grava de forma atômica, devolve `{id, name, path, exists, last_seen}`; o `name` é o nome da pasta); `list_projects() -> list[dict]` (cada item com `exists` calculado agora; arquivo ausente ou ilegível dá lista vazia, sem exceção); `get_project(project_id) -> Optional[dict]`; `discover_projects(base_dir, max_depth=3, limit=200) -> list[dict]` (varre a pasta base absoluta, sem seguir links simbólicos, ignorando `node_modules`, `.git`, `.venv`, `venv`, `dist`, `build`, `__pycache__` e `worktrees`, e devolve os diretórios que contêm `.meister/` ou `meister.config.yaml`, marcando os já registrados); `register_projects(paths) -> list[dict]`. Testes em `tests/test_projects.py` (em diretórios temporários): identificador estável, validação de cada recusa, registrar duas vezes não duplica, projeto ausente do disco marcado, arquivo corrompido, descoberta com profundidade, ignorados e limite, e que o fixture automático isola o arquivo real.

### Task 2: Registro automático nos comandos

**Files:**
- Modify: `meister/dashboard/launcher.py`
- Modify: `meister/dashboard/server.py`
- Create: `tests/test_projects_registration.py`

**Depends on:** Task 1

Em `ensure_dashboard` (`meister/dashboard/launcher.py`, usado por `meister orchestrate` e por `meister config web`), registre a raiz do projeto já resolvida com `register_project`, antes de qualquer outra coisa que possa retornar cedo, dentro de um `try/except` que ignora qualquer falha (registrar nunca derruba nem atrasa o comando). Em `start_server` (`meister/dashboard/server.py`), registre da mesma forma a raiz do projeto do servidor. Nenhuma outra mudança de comportamento. Testes em `tests/test_projects_registration.py`: `ensure_dashboard` registra o projeto (sem subir servidor de verdade, simulando o que os testes de `tests/test_dashboard_launcher.py` já simulam), `start_server` registra, uma falha ao registrar não impede o resultado, e um diretório que não é projeto não é registrado e não quebra. Rode a suíte inteira, `ruff check .` e `mypy meister` ao fim.

### Task 3: Rotas de projetos no dashboard

**Files:**
- Modify: `meister/dashboard/server.py`
- Create: `tests/test_dashboard_projects_api.py`

**Depends on:** Task 2

Acrescente ao dashboard: `GET /api/projects` devolvendo `{projects: [{id, name, path, exists, active_run}], current}` (o `current` é o identificador do projeto em que o dashboard foi iniciado; `active_run` vem do log de cada projeto, com a lógica das Global Constraints); `POST /api/projects` com `{path}` (valida e registra; erros 422 com o código de `ProjectError`); `POST /api/projects/discover` com `{base}` (devolve os candidatos sem registrar; pasta base inválida: 422); `POST /api/projects/register` com `{paths: [...]}` (registra os escolhidos; cada caminho passa pela validação). As três de escrita seguem as proteções das Global Constraints. Em `tests/test_dashboard_projects_api.py` use o cliente Flask com `HOME` temporário: lista com o projeto atual, registro e descoberta, caminho inválido, 400 sem cabeçalhos, 403 com cabeçalho do Tailscale, e `active_run` verdadeiro num projeto cujo log tem uma run sem fim. Rode a suíte inteira, `ruff check .` e `mypy meister` ao fim.

### Task 4: As rotas de configuração aceitam o projeto

**Files:**
- Modify: `meister/dashboard/server.py`
- Modify: `tests/test_dashboard_config_api.py`
- Modify: `tests/test_dashboard_config_models_api.py`

**Depends on:** Task 3

`GET /api/config` aceita `?project=<id>` (sem ele, o projeto atual) e devolve também `project: {id, name, path}`; `PUT /api/config/lanes` e `POST /api/config/restore-default` aceitam `project` no corpo. O servidor resolve o identificador **só** pelo registro (`get_project`), usa `<raiz>/meister.config.yaml` desse projeto como arquivo e aplica o bloqueio por projeto das Global Constraints. O comportamento sem `project` não muda (projeto atual, mesmos códigos e mensagens de hoje). Acrescente ao guard de escrita os erros `unknown_project` (404) e `project_missing` (409). Em `tests/test_dashboard_config_api.py` acrescente: ler e gravar as vias de um segundo projeto registrado (o arquivo dele muda e o do projeto atual não), identificador desconhecido, projeto ausente do disco, run ativa no projeto escolhido bloqueia e run ativa só no outro projeto não bloqueia, e um caminho no lugar do identificador é recusado. Confira em `tests/test_dashboard_config_models_api.py` que nada precisou mudar. Rode a suíte inteira, `ruff check .` e `mypy meister` ao fim.

### Task 5: Barra de projeto na página

**Files:**
- Modify: `meister/dashboard/templates/config.html`
- Modify: `meister/dashboard/server.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Modify: `tests/test_dashboard_config_page.py`
- Create: `tests/test_config_js_projects.py`

**Depends on:** Task 4

No alto da página acrescente: a caixa de seleção "Projeto" (carregada de `GET /api/projects`, mostrando o nome e o caminho como dica; projetos ausentes do disco aparecem desabilitados) e a faixa fixa "Configurando: `<caminho>`". Trocar o projeto recarrega as vias com `GET /api/config?project=<id>` e, se houver alterações não salvas, pede confirmação antes (reaproveite o controle de "alterações não salvas" que a página já tem). Todas as chamadas de leitura e escrita da página passam a levar o identificador do projeto escolhido (`project` na consulta ou no corpo). A escolha vale só para a sessão da página. No bloco `helpers:begin`/`helpers:end` acrescente funções puras: `configUrl(projectId)` e `withProject(body, projectId)` (e o que mais for preciso para montar as chamadas). Textos novos nos dois catálogos e na lista de `_config_messages`. Testes: as funções por `run_js`, os textos nos dois idiomas em `tests/test_dashboard_config_page.py`, e que a página servida não traz texto fixo novo.

### Task 6: Painel "Descobrir projetos" e "Adicionar projeto"

**Files:**
- Modify: `meister/dashboard/templates/config.html`
- Modify: `meister/dashboard/server.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Modify: `tests/test_dashboard_config_page.py`
- Modify: `tests/test_config_js_projects.py`

**Depends on:** Task 5

Ao lado da caixa de seleção acrescente os botões "Descobrir projetos" e "Adicionar projeto". "Descobrir projetos" abre um painel com o campo da pasta base, o botão de varrer (`POST /api/projects/discover`), a lista de candidatos com caixas de seleção (os já registrados vêm marcados e desabilitados) e "Registrar selecionados" (`POST /api/projects/register`); ao concluir, a caixa de seleção é recarregada. "Adicionar projeto" tem um campo de pasta e envia `POST /api/projects`. Erros de validação (códigos de `ProjectError`) aparecem junto do campo, com texto traduzido. As chamadas de escrita usam os cabeçalhos de proteção da escrita da `/config` que a página já monta. Com a página em modo somente leitura (acesso remoto), os botões ficam desabilitados. Textos novos nos dois catálogos e na lista de `_config_messages`; funções puras novas no bloco de helpers (por exemplo, a que monta o corpo do registro) com testes em `tests/test_config_js_projects.py`; os textos dos dois idiomas em `tests/test_dashboard_config_page.py`.
