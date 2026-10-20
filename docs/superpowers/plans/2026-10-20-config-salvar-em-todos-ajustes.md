# Tela /config: ajustes do salvar em todos Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push.
>
> **Origem:** teste no navegador do botão "Salvar em todos": a confirmação mostra o botão "Yes, restore" (texto do botão de restaurar), e o painel "Changes to save" não atualiza quando a edição vem da gaveta da via.

**Goal:** A confirmação do "Salvar em todos" tem o texto certo no botão de confirmar, e o painel "Changes to save" reflete as edições feitas na gaveta.

**Architecture:** Uma chave de texto nova nos dois catálogos e um ajuste de uma linha na função da gaveta.

**Tech Stack:** Python ≥ 3.10, HTML/JS puro, `pytest`.

## Global Constraints

- Texto novo só nos catálogos `en` e `pt-BR` (`cli.config.web.*`), com a chave também na tupla de `_config_messages` em `meister/dashboard/server.py`; nenhum texto fixo no HTML.
- Não mudar o comportamento da confirmação de restaurar nem a rota de replicação.
- Ao fim: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Texto do botão de confirmar e painel de mudanças

**Files:**
- Modify: `meister/dashboard/templates/config.html`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Modify: `meister/dashboard/server.py`
- Modify: `tests/test_dashboard_config_page.py`

1. Crie a chave `cli.config.web.save_all_yes` ("Yes, write to all" em `en`; "Sim, gravar em todos" em `pt-BR`), inclua `save_all_yes` na tupla de `_config_messages` em `meister/dashboard/server.py` e use `{{ messages.save_all_yes }}` no botão `#save-all-yes` de `config.html` no lugar de `messages.confirm_yes`. O botão `#restore-yes` continua com `confirm_yes`.
2. Em `config.html`, a função `onDrawerLaneChanged` chama `renderLanes()`, `renderControls()` e `renderDrawerErrors()`, mas não `renderChanges()`; por isso o painel "Changes to save" fica desatualizado enquanto se edita na gaveta. Acrescente a chamada a `renderChanges()` nessa função.
3. Em `tests/test_dashboard_config_page.py`, teste que a página renderizada tem o botão `#save-all-yes` com o texto novo nos dois idiomas (e não o texto de restaurar), e que o corpo da função `onDrawerLaneChanged` no template chama `renderChanges()`.

**Verification:** `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.
