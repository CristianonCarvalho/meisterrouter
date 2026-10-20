# Tela /config: salvar em todos os projetos Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com o OK do dono.
>
> **Origem:** pedido do dono em 2026-10-20: um botão "salvar para todos" na `/config` que replica a lista de vias para todos os projetos registrados. A configuração continua por projeto; o botão só grava a mesma lista em cada um.

**Goal:** Na `/config`, um botão **Salvar em todos os projetos** grava a lista de vias em edição no projeto atual e a replica em todos os outros projetos registrados, informando o resultado de cada um.

**Architecture:** Uma rota nova no dashboard (`POST /api/config/replicate`) que reaproveita as checagens da escrita da `/config` para o projeto de origem e, depois de gravar nele, aplica a mesma lista a cada projeto do registro com `apply_lanes`. A página ganha o botão, uma confirmação e um painel de resultado.

**Tech Stack:** Python ≥ 3.10, Flask, `ruamel.yaml`, `pytest`, `ruff`, `mypy`, HTML/JS puro com `node` nos testes.

## Global Constraints

- Não mudar `meister/config_editor.py`: a replicação chama `load_effective_lanes` e `apply_lanes` de cada projeto, como a gravação normal.
- A rota usa as mesmas proteções das outras escritas da `/config` (`_config_write_guard`): JSON com o cabeçalho `X-Meister-Config: 1` (400), recusada à distância (403), projeto de origem do registro (`project` no corpo; 404 `unknown_project`), run ativa no projeto de origem (409 `run_active`), validação (422) e carimbo do arquivo de origem (409 `stale`). Se qualquer uma falhar, nada é gravado em nenhum projeto.
- A lista enviada para os demais projetos é a do pedido sem o campo `original_name` de cada via (as vias casam pelo nome no projeto de destino: as que já existem lá são atualizadas e preservam os campos que a tela não edita; as que não existem são criadas; as que existem só no destino são removidas, como numa gravação normal). Cada destino é lido de novo do disco no momento da gravação (o carimbo vem dessa leitura).
- Só projetos do registro (`list_projects()`) são destino. O projeto de origem é gravado primeiro e não entra duas vezes. Cada destino é tratado de forma independente: o erro de um não impede os outros.
- Estados por destino no resultado: `saved`; `skipped_run_active` (run ativa no log dele, lida com `_project_has_active_run`); `skipped_missing` (a pasta não existe mais); `failed` (qualquer `ConfigEditorError` ou erro de gravação, com a mensagem em `message`). Um destino sem `meister.config.yaml` recebe o arquivo criado com a lista, como a gravação normal já faz.
- Texto novo da página só nos catálogos `en` e `pt-BR` (`cli.config.web.*`), com a chave também na lista de `_config_messages` em `meister/dashboard/server.py`; nenhum texto fixo no HTML ou no JavaScript. Mensagens novas da API só em `_CONFIG_API_MESSAGES`, nos dois idiomas.
- Ao fim de cada tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Rota de replicação das vias

**Files:**
- Modify: `meister/dashboard/server.py`
- Create: `tests/test_dashboard_config_replicate.py`

Em `meister/dashboard/server.py`, adicione `POST /api/config/replicate` logo depois de `api_config_restore_default`.

Fluxo: chame `_config_write_guard()` (a função devolve erro, corpo, documento e projeto de origem); se houver erro, devolva-o. Valide `body.get("lanes")` com `validate_lanes` (só erros, como em `api_config_lanes`) e responda 422 igual a ela. Grave a origem com `apply_lanes(document, lanes, body["stamp"])`; um `ConfigEditorError` vira a resposta de `_config_write_failure` e nada mais é gravado. Depois percorra `list_projects()`, pulando o projeto de origem (compare o `id`), e para cada destino: se `exists` for falso, estado `skipped_missing`; se `_project_has_active_run(projeto)` for verdadeiro, `skipped_run_active`; senão, leia `load_effective_lanes(_project_config_path(projeto))`, monte a lista do destino removendo `original_name` de cada via do pedido, valide com `validate_lanes` (qualquer erro vira `failed` com a primeira mensagem) e grave com `apply_lanes(documento, lista, documento.stamp)`; um `ConfigEditorError` ou `OSError` vira `failed` com `str(exc)`. Capture exceções por destino sem derrubar o laço.

Resposta 200: `{"source": <_config_payload da origem atualizada>, "results": [{"id", "name", "path", "status", "message"}...]}` com um item por destino, na ordem do registro (`message` é a string vazia quando `saved` ou `skipped_*`... use o texto traduzido para os `skipped_*`). Adicione as mensagens traduzidas dos dois estados `skipped_*` em `_CONFIG_API_MESSAGES` (`reports.dashboard.replicate_skipped_run_active`, `reports.dashboard.replicate_skipped_missing`).

Testes em `tests/test_dashboard_config_replicate.py` (use o padrão de fixtures de `tests/test_dashboard_projects_api.py`: HOME e USERPROFILE isolados, projetos criados com `.meister/`, `register_project`, `app.test_client()`): origem e dois destinos com listas diferentes; replicar grava a mesma lista nos três (confira o YAML relido de cada um); a origem sem carimbo válido dá 409 `stale` e nenhum destino muda; run ativa só em um destino o pula (`skipped_run_active`) e os outros são gravados; pasta de destino removida dá `skipped_missing`; um destino com YAML inválido dá `failed` e os outros seguem; sem o cabeçalho `X-Meister-Config` dá 400 e com cabeçalhos do Tailscale dá 403, sem gravar nada; lista inválida dá 422 sem gravar; os campos que a tela não edita de uma via existente no destino são preservados.

### Task 2: Botão, confirmação e resultado na página

**Files:**
- Modify: `meister/dashboard/templates/config.html`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Modify: `meister/dashboard/server.py`
- Modify: `tests/test_dashboard_config_page.py`
- Create: `tests/test_config_js_replicate.py`

Na barra de ações, ao lado de **Save**, adicione o botão `#save-all` (texto `messages.save_all`). Ele fica desabilitado quando `save` estaria desabilitado, quando a página é só leitura, ou quando a lista de projetos registrados e existentes tem um só projeto.

Ao clicar, abra a confirmação `#save-all-confirm` (mesmo estilo de `#restore-confirm`), com o texto que cita o número de projetos de destino e os nomes deles (os registrados, existentes e diferentes do atual), avisa que os com run em andamento serão pulados e que cada arquivo de configuração mantém uma cópia `.bak` quando já existia. Botões Sim e Cancelar (reaproveite `messages.confirm_yes` e `messages.confirm_no`). Ao confirmar, envie `POST /api/config/replicate` com o corpo da gravação normal (`stamp`, `lanes`, `project`), criado por um helper novo `replicateRequest(stamp, lanes, projectId)` dentro do bloco `helpers:begin/end` (testável com `run_js`, ao lado de `saveRequest`).

Resultado: um painel `#save-all-result` lista cada projeto de destino com o estado (gravado, pulado por run em andamento, pulado por pasta ausente, falhou com a mensagem) e um resumo "N gravados, M pulados, K falharam". Depois do sucesso, recarregue a configuração do projeto atual como a gravação normal faz. Se a origem falhar (carimbo antigo, run ativa, lista inválida, acesso remoto), mostre o erro com o mesmo tratamento do Save e nenhum resultado por projeto. Um helper `replicateSummary(results)` no bloco de helpers devolve as contagens por estado.

Textos novos (catálogos `en` e `pt-BR`, chaves `cli.config.web.save_all`, `save_all_confirm`, `save_all_result_title`, `save_all_summary`, `save_all_state_saved`, `save_all_state_run_active`, `save_all_state_missing`, `save_all_state_failed`) e as mesmas chaves, sem o prefixo `cli.config.web.`, na tupla de `_config_messages` em `meister/dashboard/server.py`.

Testes: em `tests/test_config_js_replicate.py` (padrão de `tests/test_config_js_projects.py`) teste `replicateRequest` (método, cabeçalhos de escrita, corpo com `stamp`, `lanes` e `project`) e `replicateSummary`; em `tests/test_dashboard_config_page.py` confira que a página renderiza `#save-all`, `#save-all-confirm` e `#save-all-result` e os textos nos dois idiomas, e que o catálogo `en` e o `pt-BR` têm as mesmas chaves novas.

**Verification:** `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.
