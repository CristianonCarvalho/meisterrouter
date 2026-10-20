# Tela /config: edição pelo Tailscale para logins autorizados Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com o OK do dono.
>
> **Origem:** pedido do dono em 2026-10-20: poder editar a `/config` pelo celular, pelo Tailscale, liberando a gravação só para o login dele. Hoje todo pedido que chega pelo Tailscale é recusado (403) e a página fica só para leitura.

**Goal:** Um pedido que chega pelo Tailscale pode gravar na `/config` quando o login informado pelo Tailscale (cabeçalho `Tailscale-User-Login`) está na lista `dashboard.remote_edit_logins` do config; sem a lista (padrão: vazia), nada muda.

**Architecture:** Uma chave nova de configuração, lida pelo carregador de config; uma função do servidor que decide se o pedido remoto pode gravar; as três checagens que hoje recusam qualquer pedido remoto (a página só leitura, a guarda de escrita da configuração e a de escrita de projetos) passam a usá-la.

**Tech Stack:** Python ≥ 3.10, Flask, `pytest`, `ruff`, `mypy`.

## Global Constraints

- Chave: `dashboard.remote_edit_logins`, lista de textos, padrão `[]`, em `meister/default_config.yaml` e em `meister.config.example.yaml` (com comentário). Valor que não for lista de textos é erro de validação do config, com mensagem nos catálogos `en` e `pt-BR` como as demais chaves de `dashboard`.
- Regra: um pedido remoto (o que `_is_remote_request()` hoje reconhece) só pode gravar se o cabeçalho `Tailscale-User-Login` existir e, comparado sem diferenciar maiúsculas de minúsculas e sem espaços nas pontas, for igual a um item da lista. Pedido remoto sem o cabeçalho, ou com login fora da lista, continua recusado com 403 `remote` e a página continua só leitura (`reason: "remote"`). Pedido local não muda. Lista vazia equivale ao comportamento de hoje.
- Continuam valendo as demais proteções da escrita (JSON com `X-Meister-Config: 1`, projeto registrado, run ativa, carimbo). Esta mudança só afrouxa a recusa por acesso remoto.
- A lista é lida do config do projeto que o dashboard serve (`load_config()`), a cada pedido que precisar dela; um config ilegível conta como lista vazia.
- Texto novo da página só nos catálogos `en` e `pt-BR`; nenhum texto fixo. O aviso de só leitura (`read_only_notice` e o motivo `remote`) continua para quem não está autorizado.
- Ao fim de cada tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Chave de configuração

**Files:**
- Modify: `meister/config.py`
- Modify: `meister/default_config.yaml`
- Modify: `meister.config.example.yaml`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Create: `tests/test_config_remote_edit_logins.py`

Adicione `remote_edit_logins: List[str]` (padrão lista vazia, vindo do `default_config.yaml`) a `DashboardConfig` em `meister/config.py` e leia-a em `load_config` junto das outras chaves de `dashboard`, validando que é uma lista de textos não vazios (erro `ConfigIssue` caso contrário, com mensagem nova nos dois catálogos). Documente a chave com comentário em `default_config.yaml` e em `meister.config.example.yaml` (valor `[]`). Testes: padrão vazio; lista válida lida; valor que não é lista e item que não é texto geram erro; `meister config validate` aceita o exemplo.

### Task 2: Servidor libera a gravação ao login autorizado

**Files:**
- Modify: `meister/dashboard/server.py`
- Create: `tests/test_dashboard_remote_edit.py`

Em `meister/dashboard/server.py`, crie `_remote_edit_allowed()` (verdadeira só quando o pedido é remoto, tem `Tailscale-User-Login` e ele está na lista `load_config().dashboard.remote_edit_logins`, sem diferenciar maiúsculas) e `_remote_refused()` (verdadeira quando `_is_remote_request()` e não `_remote_edit_allowed()`). Troque por `_remote_refused()` os usos de `_is_remote_request()` que hoje recusam a escrita ou marcam a página como só leitura: o motivo `remote` de `_config_payload`, `_config_write_guard` e `_projects_write_guard`. Não mude `_is_remote_request` em si. Testes: com a lista vazia o pedido com cabeçalhos do Tailscale continua em 403 e `reason: "remote"` no GET; com o login na lista (também com maiúsculas diferentes) o PUT de vias grava e o GET não traz `reason: "remote"`; login fora da lista continua 403; pedido remoto sem `Tailscale-User-Login` (só `X-Forwarded-For` e outro cabeçalho `Tailscale-*`) continua 403; pedido local não muda; a escrita de projetos (`POST /api/projects`) segue a mesma regra.
