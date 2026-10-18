# Tela /config: modelos de cada harness em árvore (fase 2) Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** fase 2 do spec `docs/superpowers/specs/2026-10-17-config-cadeia-de-vias-design.md` (seções 6.2 e 6.3) e pedido do dono (2026-10-18): buscar os nomes corretos dos modelos dinamicamente. Um nome de tela (`Haiku 5.5`) foi gravado como modelo da `tier_2b` e falhou nas tentativas; cada harness quer um formato de nome diferente para o mesmo modelo. Fatos medidos em 2026-10-18, sem gastar crédito:
> - `agy models` lista `id<TAB>nome de tela`, por exemplo `gemini-3.8-flash-high	Gemini 3.8 Flash (High)` e `claude-sonnet-5-5-medium	Claude Sonnet 5.5 (Medium)`; a primeira linha é um aviso ("Fetching available models...").
> - O Codex mantém `~/.codex/models_cache.json`: objeto com `fetched_at`, `etag`, `client_version`, `identity` e `models`, lista de objetos com `slug` (o id), `display_name`, `visibility` (`list` aparece no seletor), `context_window`, `default_reasoning_level` e `supported_reasoning_levels`.
> - O Claude CLI não lista modelos: o `--help` documenta os aliases (`haiku`, `sonnet`, `opus`, `fable`) ou o nome completo (`claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`, `claude-fable-5-1`).
> - O Copilot só lista na TUI interativa (`/model`). Os nomes de tela viram ids com a regra "minúsculas e espaços viram hífens" (`Claude Haiku 5.5` → `claude-haiku-5.5`, `GPT-5.4 mini` → `gpt-5.4-mini`); os 18 ids foram confirmados com `copilot --model <id>` (o Copilot recusa ids inexistentes antes de gastar crédito). A leitura de 2026-10-18 está em `~/.meister/model_catalog/copilot.json` (campos `models[]` com `name`, `id`, `group`, `tier`, `context`, `reasoning`, `selected`, `default`, `id_verified`; mais `unavailable` e `local_ollama`).

**Goal:** A gaveta de edição da `/config` mostra, para o harness escolhido, uma árvore `harness → modelos` com o id correto de cada harness, o custo quando conhecido e a origem de cada lista, aceita texto livre e avisa quando o modelo digitado não está na lista; a lista do Copilot pode ser atualizada pelo dono com um comando.

**Architecture:** Um módulo de catálogo em camadas (`meister/model_catalog.py`) sem dependência de Flask, uma rota de leitura no dashboard, a árvore na gaveta e um comando que lê o `/model` do Copilot por um terminal simulado e grava o arquivo de cache.

**Tech Stack:** Python ≥ 3.10 (sem dependência nova; terminal simulado com `pty` da biblioteca padrão, só em sistemas Unix), Flask, `pytest`, `ruff`, `mypy`, HTML/JS puro com `node` nos testes.

## Global Constraints

- Nada deste plano gasta crédito nem faz chamada a modelo: `agy models` e a leitura de arquivos locais são seguros; o Copilot só é lido pelo comando explícito da Task 5, abrindo o `/model` sem enviar prompt algum.
- Falha de uma fonte (comando ausente, tempo esgotado, arquivo ausente ou em outro formato) nunca derruba a resposta: a fonte vem como indisponível e as outras valem. Timeout de 10 s no `agy models`, resultado em cache de processo por 300 s.
- O catálogo devolve, por modelo: `id` (o que vai em `--model`), `label` (nome de tela, opcional), `cost_per_m_tokens` (do catálogo padrão `meister/default_config.yaml` quando o `id` coincide com o `model` de uma via; senão nulo) e `source` (`agy models`, `codex cache`, `claude aliases`, `copilot cache`, `config`).
- Texto novo da página só nos catálogos `en` e `pt-BR` (`cli.config.web.*`), com a chave na lista de `_config_messages` de `meister/dashboard/server.py`; mensagens novas de comando em `meister/locales/en_cli.py` e `pt_br_cli.py`. Nenhum texto fixo no HTML ou no código.
- Não mexer no motor de fallback, no circuit breaker nem nas proteções da escrita da `/config` (403 Tailscale, 409, 400 CSRF). Não escrever arquivo do usuário fora de `~/.meister/model_catalog/` (e respeitar `HOME` nos testes, que usam diretório temporário).
- Ao fim de cada tarefa: `pytest tests/ -q`, `ruff check .` e `mypy meister` limpos.

### Task 1: Catálogo de modelos com as fontes fixas (Claude, Copilot em cache, config)

**Files:**
- Create: `meister/model_catalog.py`
- Create: `tests/test_model_catalog.py`

Crie `meister/model_catalog.py` com `list_models(harness: str) -> dict` que devolve `{"harness", "models": [...], "sources": [{"name", "status"}]}` (status `ok`, `unavailable` ou `empty`), seguindo as Global Constraints. Fontes desta tarefa: **`claude aliases`** (lista curada no módulo: aliases `haiku`, `sonnet`, `opus`, `fable` e ids `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`, `claude-fable-5-1`); **`copilot cache`**: lê `~/.meister/model_catalog/copilot.json` (respeita `HOME`; formato descrito na Origem: use `id` e `name` como `label`; ignore itens sem `id`; arquivo ausente ou inválido é `unavailable`); **`config`**: os modelos já usados pelas vias do catálogo padrão e do config do projeto para esse harness (via `load_config`), como fallback quando as outras fontes não trazem nada. Aceite os nomes de harness como o resto do projeto (`copilot`, `github-copilot`, `claude`, `agy`, `antigravity`, `codex`, `kiro`, `kiro-cli`; use o mesmo agrupamento que `meister/harness_effort.py`). Sem duplicar ids; a primeira fonte vence. Cache de processo de 300 s com função para limpá-lo (usada nos testes). Testes: cada fonte, arquivo do Copilot ausente ou corrompido, custo preenchido quando o `id` coincide com uma via do padrão, harness desconhecido (lista vazia, sem erro) e que nenhum subprocesso é chamado nesta tarefa.

### Task 2: Fontes dinâmicas `agy models` e cache do Codex

**Files:**
- Modify: `meister/model_catalog.py`
- Modify: `tests/test_model_catalog.py`
- Create: `tests/test_model_catalog_sources.py`

**Depends on:** Task 1

Acrescente a fonte **`agy models`**: roda `agy models` (via `subprocess.run`, sem shell, timeout de 10 s, só se o executável existir), descarta a linha de aviso, e cada linha restante `id<TAB>nome` vira `{id, label}`; falha ou tempo esgotado é `unavailable`. Acrescente a fonte **`codex cache`**: lê `~/.codex/models_cache.json` (respeita `HOME`), usa de cada item `models[]` o `slug` como `id` e `display_name` como `label`, só os de `visibility == "list"`; arquivo ausente, JSON inválido ou sem `models` é `unavailable`, sem exceção. Para os harness `agy`/`antigravity` e `codex`, essas fontes vêm antes das de fallback. Testes em `tests/test_model_catalog_sources.py` com o subprocesso e os arquivos simulados (use os formatos reais da Origem): saída normal do `agy models`, saída só com o aviso, executável ausente, timeout, cache do Codex com itens `list` e ocultos, arquivo corrompido, e que a falha de uma fonte não afeta as outras. Nenhum teste chama o `agy` ou lê o `$HOME` reais.

### Task 3: Rota `GET /api/config/models`

**Files:**
- Modify: `meister/dashboard/server.py`
- Create: `tests/test_dashboard_config_models_api.py`

**Depends on:** Task 2

Acrescente `GET /api/config/models?harness=<nome>` ao dashboard: devolve o resultado de `list_models` em JSON, `400` com `reason: "missing_harness"` sem o parâmetro; leitura liberada para qualquer origem (inclusive pedidos com cabeçalhos do Tailscale), como `GET /api/config`; não exige os cabeçalhos de escrita. Não altera nenhuma rota existente. Testes com o cliente Flask e `list_models` simulado: resposta normal, harness ausente, leitura com cabeçalho do Tailscale, e que a rota não aceita escrita (`POST`/`PUT` dão 405).

### Task 4: Árvore de modelos na gaveta e aviso de modelo desconhecido

**Files:**
- Modify: `meister/dashboard/templates/config.html`
- Modify: `meister/dashboard/server.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Modify: `tests/test_dashboard_config_page.py`
- Create: `tests/test_config_js_models.py`

**Depends on:** Task 3

Na gaveta de edição, no campo de modelo (hoje texto livre), acrescente a árvore do protótipo (`docs/superpowers/specs/2026-10-17-config-cadeia-de-vias-prototipo.html`, função `drawTree`): para o harness da via, busca `GET /api/config/models?harness=...` (uma vez por harness e por abertura da gaveta), mostra os modelos com `label`, custo (`cost_value`) e origem (`source`); clicar num modelo preenche o `model` com o `id` e, quando o custo é conhecido, o custo da via (sem apagar um custo já digitado quando o do catálogo é nulo). O campo de texto livre continua. Se a busca falhar, a árvore mostra o aviso de indisponível e a gaveta segue funcionando. Aviso **"modelo desconhecido"** junto do campo quando o modelo não está nas listas do harness (e só quando alguma lista veio com itens); não bloqueia salvar. No bloco `helpers:begin`/`helpers:end` acrescente funções puras: `modelKnown(models, id)` e `mergeCatalogChoice(lane, model)` (aplica id e custo). Todo texto novo nos dois catálogos e em `_config_messages`. Testes: as funções por `run_js`, os textos nos dois idiomas em `tests/test_dashboard_config_page.py`, e que a página servida não traz texto fixo novo.

### Task 5: Comando que lê o `/model` do Copilot e atualiza o cache

**Files:**
- Modify: `meister/cli.py`
- Create: `meister/copilot_models.py`
- Modify: `meister/locales/en_cli.py`
- Modify: `meister/locales/pt_br_cli.py`
- Create: `tests/test_copilot_models.py`

**Depends on:** Task 1

Crie `meister/copilot_models.py` e o comando `meister config refresh-models copilot` (opt-in; nunca roda sozinho nem ao abrir a página). O módulo abre o `copilot` interativo num terminal simulado (`pty`, tamanho 60x160, respondendo as consultas de terminal que a TUI faz: cores de fundo e de texto por OSC 10/11/4, `CSI ? 996 n` com `CSI ? 997 ; 1 n`, e DECRQM `CSI ? n $ p` com `CSI ? n ; 2 $ y`), numa pasta temporária vazia criada para isso; responde "1. Yes" à pergunta de confiança de pasta (só para a sessão, nunca "lembrar"), digita `/model`, espera a tela, e sai com ESC e Ctrl-C e encerra o processo filho. Remove as sequências ANSI e lê o seletor: grupos `Recent models`, `Recommended models`, `Other models` (linhas `Nome [✓] [(default)] Tier Contexto Esforço`, com `—` para ausente) e `Unavailable models`; ignora o bloco de provedores locais exceto para registrá-lo em `local_ollama`. Cada nome de tela vira `id` (minúsculas, espaços em hífens) e `id_verified: false`; grava `~/.meister/model_catalog/copilot.json` (respeita `HOME`) no formato da Origem, de forma atômica (arquivo temporário e `os.replace`), mantendo o arquivo anterior como `copilot.json.bak`; se a leitura não encontrar nenhum modelo, **não** sobrescreve o arquivo e sai com erro. Sem `copilot` no `PATH` ou fora de Unix, sai com mensagem clara. O comando imprime quantos modelos leu e o caminho do arquivo. Mensagens nos dois catálogos. Testes (sem o Copilot real: um executável falso num diretório temporário que imprime uma tela de seletor com o texto real acima): leitura completa, ids gerados, atomicidade e `.bak`, leitura vazia não sobrescreve, sem `copilot` no `PATH`, e que o comando aparece em `meister config --help`.
