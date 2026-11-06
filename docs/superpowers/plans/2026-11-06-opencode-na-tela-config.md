# opencode na tela /config Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com o OK do dono.
>
> **Origem:** o dono tentou salvar a via `tier_2c` (`harness: opencode`) na tela `/config` e levou o erro `config: tier_2c: Harness 'opencode' is invalid. Accepted values: copilot, github-copilot, claude, agy, antigravity, codex, kiro-cli, kiro`. A integração do opencode (PR #197) deixou o harness fora de `HARNESS_EFFORT_VALUES` (`meister/harness_effort.py`) de propósito, porque ele não aceita `effort`, mas essa tabela também é a lista de harnesses válidos do editor (`meister/config_editor.py`, `_lane_field_issues`) e da tela (`harnessOptions()` em `meister/dashboard/templates/config.html`, que o teste `tests/test_config_js_harness.py` amarra à tabela). O `meister config validate` e o `meister orchestrate` aceitam o harness; só o editor recusa.

**Goal:** a tela `/config` aceita e lista `opencode`, sem aceitar `effort` para ele.

**Architecture:** `HARNESS_EFFORT_VALUES` ganha `"opencode": ()` (nenhum esforço aceito), e os dois validadores que hoje tratam "harness ausente da tabela" como "esforço não suportado" passam a tratar também "tabela vazia" assim. O editor passa a aceitar o harness pela própria tabela, e o HTML ganha a mesma entrada.

## Global Constraints

- Um `effort` informado para `opencode` continua sendo erro, com a mensagem `tiers.unsupported_effort` no `meister/config.py` e no `meister/worker.py`; `effort` ausente ou nulo continua válido.
- Nenhum outro harness muda de comportamento. A ordem de `HARNESS_EFFORT_VALUES` é a atual, com `opencode` por último.
- Nenhuma dependência nem chave de configuração nova.
- A documentação (CHANGELOG, índice de planos) é escrita pelo orquestrador depois; não a inclua nas tarefas.
- Ao fim de cada tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Tabela de harnesses e validação de esforço

**Files:**
- Modify: `meister/harness_effort.py`
- Modify: `meister/config.py`
- Modify: `meister/worker.py`
- Modify: `tests/test_opencode_registration.py`

Em `meister/harness_effort.py`, acrescente `"opencode": ()` ao fim de `HARNESS_EFFORT_VALUES`, com um comentário curto dizendo que a tupla vazia significa "harness sem esforço configurável". Em `meister/config.py` (`validate_config`, perto da linha 1505) e em `_validate_harness_effort` (`meister/worker.py`, perto da linha 446), troque a condição `valid_efforts is None` por `not valid_efforts`, para que harness ausente da tabela e harness com tupla vazia caiam na mensagem `tiers.unsupported_effort`; o ramo `elif effort not in valid_efforts` fica como está. Em `tests/test_opencode_registration.py`, mantenha os testes atuais e acrescente: `effort: high` numa via `opencode` é erro com `tiers.unsupported_effort` tanto em `validate_config` quanto em `_validate_harness_effort`; sem `effort` não há erro; `"opencode" in HARNESS_EFFORT_VALUES` e o valor é uma tupla vazia.

### Task 2: Editor e tela `/config`

**Files:**
- Modify: `meister/dashboard/templates/config.html`
- Modify: `tests/test_config_editor.py`

Em `meister/dashboard/templates/config.html`, acrescente `"opencode": [],` ao fim do objeto `HARNESS_EFFORT` (perto da linha 760), depois de `"kiro"`. Não é preciso mudar `meister/config_editor.py`: com a tabela da tarefa 1, `_lane_field_issues` já aceita o harness e trata qualquer `effort` informado como inválido por não estar na tupla vazia. Em `tests/test_config_editor.py`, no estilo de `test_invalid_harness_is_error`, acrescente: uma via `opencode` com `model: google/antigravity-gemini-3.8-flash-high` e sem `effort` não gera erro em `validate_lanes`; a mesma via com `effort: high` gera exatamente um erro em `lanes[N].effort`; e a mensagem de harness inválido para um nome desconhecido (`bogus`) agora lista `opencode` entre os valores aceitos. O teste existente `test_effort_options_follow_harness_effort_py` (`tests/test_config_js_harness.py`) deve passar sem alteração: ele compara `harnessOptions()` e `effortOptions(...)` com a tabela.
