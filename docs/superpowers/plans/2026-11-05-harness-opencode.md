# Harness opencode no Meister Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com o OK do dono.
>
> **Origem:** pergunta do dono em 2026-11-05 ("o sistema já está preparado para usar o opencode?"). Hoje não: `opencode` não aparece em nenhum arquivo do `meister/`, e `KNOWN_HARNESSES` (`meister/config.py`) recusa o nome. O molde é o Kiro CLI, o harness mais recente (`tests/test_kiro_registration.py`, `tests/test_worker_kiro.py`, `tests/test_engine_workers_kiro.py`).
>
> **Observado no opencode 1.19.5 instalado (`/opt/homebrew/bin/opencode`), só em repositório temporário e com modelo gratuito:**
> - `opencode run [message..]` roda sem interface e sai com 0 ao terminar; o prompt vai como argumento posicional.
> - `-m provider/modelo` escolhe o modelo. Modelo ou provedor inexistente: código de saída 1 e `Model not found: ...` no stderr.
> - Sem `--auto` ele já escreveu um arquivo dentro do diretório do projeto; `--auto` aprova as permissões que não estão negadas e evita pergunta fora do diretório (por isso o worker o usa, como o Copilot usa `--allow-all`).
> - `--format json` emite eventos JSON por linha (`step_start`, `text`, `step_finish` com `tokens` e `cost`); o formato padrão é texto. Este plano usa o texto.
> - `--variant` é "esforço de raciocínio específico do provedor": não há lista fixa de valores, então o `effort` do Meister continua recusado para este harness.
> - `opencode models` lista um `provider/modelo` por linha. Provedores autenticados na máquina: `opencode` (modelos gratuitos), `google` (OAuth, via plugin `opencode-antigravity-auth`) e `nvidia` (chave).
>
> **Cuidado fora do escopo do código:** os modelos `google/antigravity-*` usam o login Google do plugin antigravity, o mesmo da assinatura do `agy`. Este plano NÃO cria via nenhuma com eles, e o login do `agy` não deve ser trocado. Qual modelo do opencode vira via (e se algum vira) é decisão do dono depois da integração.
>
> **Não faz parte do plano:** adicionar via `opencode` em `meister/default_config.yaml`, ler uso/custo do opencode em `meister/usage.py`, detectar mensagem de cota do opencode.

**Goal:** aceitar `harness: opencode` numa via do `workers.tier_order`: validação, comando do worker (modo `orchestrate` e `meister worker`), localização do executável e lista de modelos.

**Architecture:** o mesmo caminho do Kiro, sem estrutura nova: um nome conhecido em `config.py`, um ramo em `worker.py`, um ramo em `engine/workers.py` e uma fonte no `model_catalog.py`. Os quatro arquivos de código são diferentes e as tarefas não dependem umas das outras, mas o importador as encadeia em série, o que é o desejado (uma run por vez, suíte verde a cada tarefa).

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`.

## Global Constraints

- Nome do harness: `opencode`, sem apelidos. Executável: `opencode` (também `/opt/homebrew/bin/opencode` e `~/.local/bin/opencode`, como `find_cli_binary` já faz para os outros).
- Comando do worker: `[opencode, "run", "--auto", "-m", <modelo>, <prompt>]`. O `-m` só entra quando o modelo existe e não é `default`; o prompt é sempre o último argumento. Sem `--format`, sem `--variant`, sem `--dir` (o worker já roda no diretório certo).
- `effort` continua recusado para `opencode` (`HARNESS_EFFORT_VALUES` não ganha entrada); o teste confirma a recusa com a mensagem `tiers.unsupported_effort`.
- Nenhuma via `opencode` entra em `meister/default_config.yaml` nem nos modelos de `meister config init`. Nenhuma chave de configuração nova, nenhuma dependência nova.
- Nenhum teste pode chamar o `opencode` de verdade: use `monkeypatch` em `shutil.which` e `subprocess.run`, como os testes do Kiro.
- A documentação (CHANGELOG, índice de planos, `docs/`) é escrita pelo orquestrador depois; não a inclua nas tarefas.
- Ao fim de cada tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Reconhecer o harness na configuração

**Files:**
- Modify: `meister/config.py`
- Create: `tests/test_opencode_registration.py`

Em `meister/config.py`, acrescente `"opencode"` a `KNOWN_HARNESSES` (linha 30) e, em `validate_config`, um ramo `elif h == "opencode":` depois do ramo do Kiro (perto da linha 1707) que emite o mesmo `ConfigIssue` de aviso de `reports.config.executable_not_found` com `executable="opencode"` quando `shutil.which("opencode")` não acha o programa. Teste em `tests/test_opencode_registration.py`, no formato de `tests/test_kiro_registration.py`: `opencode` está em `KNOWN_HARNESSES`; com o executável simulado presente não há aviso no caminho `workers.tier_order[0].harness`; ausente, há exatamente um aviso com o texto traduzido; `effort: high` numa via `opencode` é recusado com `tiers.unsupported_effort` (o texto vem de `t(...)`, não escrito à mão); um harness desconhecido continua recusado.

### Task 2: Comando do worker e localização do executável

**Files:**
- Modify: `meister/worker.py`
- Create: `tests/test_worker_opencode.py`

Em `meister/worker.py`, acrescente `HARNESS_OPENCODE = "opencode"` junto dos outros identificadores (linha 71), `"opencode": HARNESS_OPENCODE` em `_HARNESS_ALIASES`, o ramo `elif harness == HARNESS_OPENCODE: names = ["opencode"]` em `find_cli_binary` (perto da linha 362) e o ramo do comando depois do ramo do Kiro (perto da linha 427): `cmd = [cli_bin, "run", "--auto"]`, `-m <modelo>` quando `model and model != "default"`, e `prompt` por último; o `effort` é ignorado (ele já foi barrado pela validação). Teste em `tests/test_worker_opencode.py`, no formato de `tests/test_worker_kiro.py`: o comando exato com modelo `opencode/big-pickle`; sem modelo e com `default` não há `-m`; o prompt é o último elemento mesmo quando começa com `-`; `find_cli_binary("opencode")` acha o executável simulado e devolve `None` quando ele não existe.

### Task 3: Comando no motor de orquestração

**Files:**
- Modify: `meister/engine/workers.py`
- Create: `tests/test_engine_workers_opencode.py`

Em `resolve_command` de `meister/engine/workers.py`, acrescente, antes do `else` final (depois do ramo do Kiro), o ramo `elif harness == "opencode":`. Com `task_context["description"]`: `["opencode", "run", "--auto"]`, mais `-m <modelo>` quando o modelo existe e não é `default`, e a descrição como último argumento. Sem descrição (painel interativo): `["opencode"]` mais `-m <modelo>` nas mesmas condições. O `args` extra do `task_context` continua sendo acrescentado ao fim pelo código que já existe depois do `if/elif`; não o duplique. Teste em `tests/test_engine_workers_opencode.py`, no formato de `tests/test_engine_workers_kiro.py`: os dois modos com e sem modelo, `default` não gera `-m`, e `task_context["command"]` ainda tem prioridade.

### Task 4: Modelos do opencode no catálogo

**Files:**
- Modify: `meister/model_catalog.py`
- Create: `tests/test_model_catalog_opencode.py`

Em `meister/model_catalog.py`, acrescente `SOURCE_OPENCODE_MODELS = "opencode models"`, `"opencode": "opencode"` em `_HARNESS_GROUPS`, o ramo `if group == "opencode": return [(SOURCE_OPENCODE_MODELS, _opencode_models)]` em `_fixed_sources` e `_opencode_models()`, no molde de `_agy_models`: sem shell, só se `shutil.which("opencode")` achar o programa, `subprocess.run(["opencode", "models"], ...)` com tempo limite de 10 s (constante `OPENCODE_MODELS_TIMEOUT_SECONDS`) e devolve `None` em qualquer falha, saída vazia ou código diferente de 0. Cada linha não vazia vira `_entry(linha, None, SOURCE_OPENCODE_MODELS)`; descarte linhas com espaço (ruído de cor ou aviso), mantendo só as no formato `provider/modelo`. Teste em `tests/test_model_catalog_opencode.py`, no formato de `tests/test_model_catalog_sources.py`, com `subprocess.run` simulado: lista normal, linhas inválidas descartadas, executável ausente devolve a fonte como indisponível sem derrubar as outras, tempo esgotado e saída vazia também, e ids repetidos não aparecem duas vezes.
