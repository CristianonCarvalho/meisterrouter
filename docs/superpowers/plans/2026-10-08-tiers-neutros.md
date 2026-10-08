# Vias neutras (`tier_N`), esforço por via e liga/desliga de vias Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde o template diz "Commit", este plano usa **Checkpoint**.
>
> **Só executar depois que o PR 1 do plano `2026-10-08-timeline-web-janela-app.md` estiver mesclado:** os dois planos mexem em `meister/cli.py` e nos catálogos `meister/locales/*_cli.py`.

**Goal:** (1) Os nomes das vias deixam de dizer modelo ou harness e passam a dizer só o degrau e a variante (`tier_1`, `tier_1b`, `tier_1c`, `tier_2`, `tier_3`, `tier_3b`); modelo e harness ficam só na configuração. (2) Cada via ganha um `effort` opcional, repassado ao harness. (3) Dá para ligar e desligar vias por comando, sem editar a lista inteira. (4) A via 1 passa a rodar o Haiku 5.5 pelo Copilot.

**Architecture:** Três mudanças pequenas e independentes no que já existe. `WorkerTier` ganha o campo `effort`, validado por harness e traduzido em flag por `build_harness_command`. `workers.enabled` (mapa `nome: bool`) é aplicado depois de ler `tier_order`, movendo vias entre ligadas e desligadas sem mudar a ordem; `meister models --enable/--disable` grava esse mapa no `meister.config.yaml` do projeto. A renomeação é a última e é mecânica (catálogo padrão, exemplos, templates de `config init`, docs, testes).

**Tech Stack:** Python ≥ 3.10 (CI roda 3.10 a 3.13), `click`, `PyYAML`, `pytest`. Nenhuma dependência nova.

**Spec de origem:** conversa de 2026-10-08 com o dono (nomes neutros por degrau e variante; efeito de esforço; liga/desliga). Não há spec em arquivo.

## Decisões fechadas

| Tema | Decisão |
|---|---|
| Nomes | `tier_<degrau>` para a via principal e `tier_<degrau><letra>` para a variante do mesmo degrau (fallback ou paralela). Letra e não ponto: o nome vira argumento de `--model`, chave de evento e parte de nomes de arquivo e rotas. |
| Posição | A ordem de `workers.tier_order` continua mandando no fallback. O número no nome é o degrau, não a posição absoluta; reordenar não obriga a renomear. |
| Catálogo padrão | `tier_1` Haiku 5.5 pelo Copilot (ligada) · `tier_1b` Luna pelo Copilot (ligada) · `tier_1c` Luna pelo Codex (desligada) · `tier_2` Gemini 3.8 Flash pelo `agy` (ligada) · `tier_3` Sonnet 5.5 pelo `claude` (ligada, só ESCALATE) · `tier_3b` Opus 5.5 pelo `claude` (desligada). |
| Compatibilidade | Quebra de nomes: `copilot_luna`, `codex_luna`, `agy_gemini_flash` e `claude_sonnet` deixam de existir. Sem apelidos antigos. Runs antigos continuam legíveis nos relatórios; `meister resume` de run feita com o nome antigo não é suportado. Documentar no CHANGELOG como mudança incompatível. |
| Jev | O Jev recebe, por via, a descrição `modelo via harness ($custo/M): best_for` (veja `meister/jev.py`), então o nome não é a única pista. O pedido é comparar o roteamento antes e depois (orquestrador, fora das tarefas). |
| Esforço | Campo opcional `effort` por via. Ausente = padrão do harness (nada é repassado). |
| Liga/desliga | `meister models --enable NOME` e `--disable NOME`, gravando `workers.enabled` no `meister.config.yaml` do projeto. Vale a partir do próximo run; não afeta run em andamento. |

## Decisões abertas (confirmar antes de executar)

1. `cost_per_m_tokens` da `tier_1`: o preço do Haiku 5.5 tem duas faixas (0,10/0,50 por 1M tokens até 100 mil tokens por pedido e 0,50/2,50 acima disso; combinado 3:1 = 0,20 e 1,00). Medindo as sessões do Copilot, cerca de 5% delas têm contexto médio acima de 100 mil e concentram 39% dos tokens de entrada. Este plano assume **0,50** (conservador), com um comentário explicando as duas faixas; alternativa: 0,20.
2. Os valores aceitos de `effort` por harness vêm do `--help` de cada CLI instalado (veja a Tarefa 1) e podem diferir entre versões; o plano assume a tabela da Tarefa 1.

## Global Constraints

- Sem dependência nova; só biblioteca padrão e o que o projeto já usa.
- Compatível com Python 3.10: `from __future__ import annotations` nos módulos novos; sem `match`, sem `datetime.UTC`.
- **Sem nomes de modelo no código**: `tests/test_no_hardcoded_models.py` varre `meister/cli.py` e `meister/herdr/tui.py`; modelo, harness e preço vivem só em `meister/default_config.yaml` e no `meister.config.yaml` do usuário. A tabela de esforço por harness descreve o **harness** (flag e valores), nunca um modelo.
- Strings de interface passam por `t()` com entradas em **todos** os idiomas de `meister/locales/` (inglês por padrão, português preservado); `tests/test_locales_parity.py` e `tests/test_i18n_ratchet.py` ficam verdes. Catálogos novos: `meister/locales/en_tiers.py` e `meister/locales/pt_br_tiers.py` (chaves `tiers.*`).
- Config nova valida no estilo de `ConfigIssue` das demais seções; valor inválido nunca vira exceção solta.
- `effort` ausente deixa o argv do harness **idêntico** ao de hoje.
- Desligar uma via nunca deixa a configuração sem nenhuma via ligada.
- Testes sem rede, sem tocar `~/.meister` e `~/.config`, sem chamar CLI de IA; harness e binários simulados com `tmp_path` e `monkeypatch`.
- Suíte, `ruff check .` e `mypy meister` limpos.
- O worker não commita, não faz push, não toca em outro projeto e não chama CLI de IA.

## Review Focus

1. **`effort` inválido para o harness da via** (por exemplo `max` num harness que não aceita): erro de configuração claro, apontando a via, o harness e os valores aceitos; nunca repassar um valor que o CLI rejeitaria. (Tarefa 1)
2. **`effort` ausente:** o argv de cada harness fica byte a byte igual ao de hoje. (Tarefa 1)
3. **Desligar a última via ligada**, ligar ou desligar um nome que não existe, e ligar duas vezes: mensagens claras, nada é gravado em caso de erro. (Tarefa 2)
4. **`meister.config.yaml` existente com comentários e outras chaves:** o comando preserva as outras chaves e cria um `.bak` antes de regravar; arquivo ausente é criado só com o mapa. (Tarefa 2)
5. **`workers.enabled` com via desconhecida** (nome digitado errado à mão): aviso de configuração apontando o nome, sem derrubar o carregamento. (Tarefa 2)
6. **Resíduo dos nomes antigos:** nenhum arquivo versionado (código, testes, exemplos, docs, README, `CLAUDE.md`) cita `copilot_luna`, `codex_luna`, `agy_gemini_flash` ou `claude_sonnet` como nome de via, exceto o CHANGELOG e os registros históricos de run em `docs/models-and-costs.md`. (Tarefa 3)
7. **Ordem e fallback:** depois da renomeação, a cadeia de fallback e o `eligible_classes: [ESCALATE]` da `tier_3` se comportam como hoje. (Tarefa 3)

---

## Mapa de arquivos

| Arquivo | Ação | Responsabilidade |
|---|---|---|
| `meister/config.py` | modificar | `WorkerTier.effort`, `workers.enabled`, validações |
| `meister/worker.py` | modificar | `effort` repassado a cada harness em `build_harness_command` |
| `meister/cli.py` | modificar | `models --enable/--disable`, coluna de esforço em `models` |
| `meister/default_config.yaml`, `meister.config.example.yaml` | modificar | Catálogo com as 6 vias neutras e `workers.enabled` documentado |
| `meister/locales/en_tiers.py`, `meister/locales/pt_br_tiers.py` | criar | Textos novos |
| `meister/locales/en_commands.py`, `meister/locales/pt_br_commands.py` | modificar | Template de `meister config init` com as vias novas |
| `README.md`, `README.pt-BR.md`, `CLAUDE.md`, `docs/**` | modificar | Nomes novos, esforço, liga/desliga |
| `tests/test_lane_effort.py`, `tests/test_lane_toggle.py` | criar | Testes |
| `CHANGELOG.md`, `CHANGELOG.pt-BR.md` | modificar | Mudança incompatível de nomes e recursos novos |

---

### Task 1: Campo `effort` por via

**Files:**
- Modify: `meister/config.py`
- Modify: `meister/worker.py`
- Modify: `meister/default_config.yaml`
- Modify: `meister.config.example.yaml`
- Create: `meister/locales/en_tiers.py`
- Create: `meister/locales/pt_br_tiers.py`
- Test: `tests/test_lane_effort.py`

**Interfaces:**
- Produces em `config.py`: `WorkerTier.effort: Optional[str] = None`, lido de `workers.tier_order[i].effort` (texto em minúsculas, vazio vira `None`); validação em `validate_config`: se `effort` está definido e o harness da via tem tabela, o valor precisa estar entre os aceitos pelo harness, senão `ConfigIssue("error", f"workers.tier_order[{i}].effort", ...)` citando via, harness e valores aceitos; harness sem tabela (desconhecido) não aceita `effort` (erro).
- Produces em `worker.py`: `HARNESS_EFFORT_VALUES: Dict[str, Tuple[str, ...]]` (por harness) e `build_harness_command(harness, cli_bin, model, prompt, cwd, effort: Optional[str] = None)`: com `effort`, acrescenta a flag do harness; sem `effort`, o argv é idêntico ao atual. **Os valores e flags abaixo devem ser conferidos no `--help` do CLI instalado antes de fixar**; ajuste a tabela ao que o CLI realmente aceita e relate a diferença:
  - Copilot: `--reasoning-effort <v>` com `none, minimal, low, medium, high, xhigh, max`.
  - Claude: `--effort <v>` com `low, medium, high, xhigh, max`.
  - Antigravity (`agy`): `--effort <v>` com `low, medium, high, xhigh, max`.
  - Codex: `-c model_reasoning_effort="<v>"` com `minimal, low, medium, high, xhigh` (inserir antes do `-C`/prompt).
- O `effort` da via chega ao `build_harness_command` pelo mesmo caminho que hoje entrega o `model` (resolver a via pelo nome e ler `tier.effort`; rotas explícitas por `--model <via>` e a execução pelo orquestrador usam o mesmo caminho).
- `default_config.yaml` e `meister.config.example.yaml`: comentário curto explicando `effort` (ausente = padrão do harness) e os valores por harness; **nenhuma via recebe `effort` por padrão**.

- [ ] **Step 1: Escrever os testes que falham.** Para cada harness: com `effort`, o argv contém a flag certa e o valor; sem `effort`, o argv é idêntico ao de antes (comparar com a lista literal esperada); valor fora da lista do harness gera `ConfigIssue` apontando a via; harness sem tabela com `effort` gera erro; `effort` vazio ou ausente vira `None`; `meister config show --json` mostra `effort` por via; ponta a ponta com binário falso (script que grava o argv): o worker de uma via com `effort: high` chama o binário com a flag.
- [ ] **Step 2: Rodar e ver falhar** (`pytest tests/test_lane_effort.py -q`).
- [ ] **Step 3: Implementar.** Confirmar os valores com `copilot --help`, `claude --help`, `agy --help` e `codex --help` e relatar o que foi conferido; o que o CLI não confirmar fica fora da tabela.
- [ ] **Step 4: Rodar testes, `ruff check .`, `mypy meister`, `tests/test_no_hardcoded_models.py`, `tests/test_locales_parity.py`.**
- [ ] **Step 5: Checkpoint** (sem commit).

**Depends on:** none

### Task 2: Ligar e desligar vias (`workers.enabled` e `meister models --enable/--disable`)

**Files:**
- Modify: `meister/config.py`
- Modify: `meister/cli.py`
- Modify: `meister/default_config.yaml`
- Modify: `meister.config.example.yaml`
- Modify: `meister/locales/en_tiers.py`
- Modify: `meister/locales/pt_br_tiers.py`
- Test: `tests/test_lane_toggle.py`

**Interfaces:**
- Consumes: `WorkerTier` (Tarefa 1) e o carregamento atual de `workers.tier_order`/`workers.disabled`.
- Produces em `config.py`: chave `workers.enabled` (mapa `nome_da_via: bool`), aplicada **depois** de ler `tier_order` (e as vias já desligadas): `true` move a via para as ligadas e `false` para as desligadas, **preservando a ordem original de `tier_order`**; nome desconhecido gera `ConfigIssue("warning", "workers.enabled.<nome>", ...)` e é ignorado; valor que não é booleano é erro; se o resultado não deixar nenhuma via ligada, `ConfigIssue("error", "workers.enabled", ...)`.
- Produces em `cli.py`: `meister models [--enable NOME]... [--disable NOME]... [-c ARQUIVO]` (as opções podem repetir). Sem as opções, a saída atual de `models` não muda. Com elas: valida os nomes contra o catálogo efetivo, recusa desligar a última via ligada, grava `workers.enabled` no `meister.config.yaml` do projeto (ou no arquivo de `-c`) e imprime a lista final como `models` já faz. Edição do arquivo: ler com `yaml.safe_load`, atualizar só `workers.enabled`, manter as demais chaves, gravar de forma atômica (arquivo temporário + `os.replace`) depois de copiar o original para `<arquivo>.bak`; se o arquivo não existir, criar só com o mapa. Qualquer erro termina com código diferente de zero e **sem alterar nada**.
- A coluna de status de `meister models` passa a mostrar também o `effort` da via (coluna nova, `-` quando ausente).

- [ ] **Step 1: Escrever os testes que falham.** `workers.enabled` liga uma via desligada e desliga uma ligada, mantendo a ordem de `tier_order`; nome desconhecido gera aviso; valor não booleano gera erro; todas desligadas gera erro; comando: `--disable` grava o mapa e a saída reflete o novo estado; `--enable` de via já ligada é aceito sem mudança (idempotente); desligar a última via ligada falha e não grava; nome inexistente falha e lista os nomes válidos; arquivo existente com outras chaves e comentários: as outras chaves permanecem e há `.bak`; arquivo ausente é criado; falha no meio não deixa arquivo pela metade; `models` sem opções continua com a saída atual (mais a coluna de esforço).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.** Manter `cli.py` fino (a lógica de edição do arquivo em `meister/config.py` ou módulo novo `meister/lane_toggle.py`, a critério do worker, ambos permitidos).
- [ ] **Step 4: Rodar testes, `ruff check .`, `mypy meister`, `tests/test_no_hardcoded_models.py`, `tests/test_locales_parity.py`, `tests/test_i18n_ratchet.py`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** Task 1

### Task 3: Vias neutras e Haiku 5.5 na via 1

**Files:**
- Modify: `meister/default_config.yaml`
- Modify: `meister.config.example.yaml`
- Modify: `meister/locales/en_commands.py`
- Modify: `meister/locales/pt_br_commands.py`
- Modify: `README.md`
- Modify: `README.pt-BR.md`
- Modify: `CLAUDE.md`
- Modify: `docs/advanced.md`
- Modify: `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`
- Modify: `docs/models-and-costs.md`
- Modify: `docs/pt-BR/MODELOS_E_CUSTOS.md`
- Modify: `docs/modelos_e_custos.csv`
- Modify: `tests/`
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.pt-BR.md`

**Interfaces:**
- Consumes: `effort` e `workers.enabled` (Tarefas 1 e 2).
- Produces: o catálogo padrão com as seis vias abaixo, **nesta ordem em `tier_order`**, e todos os demais arquivos acompanhando:

| Nome | harness | model | enabled | custo/M | outros campos |
|---|---|---|---|---|---|
| `tier_1` | `copilot` | `claude-haiku-5.5` | sim | 0.50 (Decisão aberta 1; comentário com as duas faixas 0,10/0,50 e 0,50/2,50) | `credit_usd: 0.01`, `max_retries: 2`, `best_for` da via 1 de hoje |
| `tier_1b` | `copilot` | `gpt-6-luna` | sim | 0.20 | `credit_usd: 0.01`, `max_retries: 2`, `best_for` da via 1 de hoje |
| `tier_1c` | `codex` | `gpt-6-luna` | **não** | 0.20 | mesmos campos da `codex_luna` de hoje |
| `tier_2` | `agy` | `gemini-3.8-flash-high` | sim | 1.50 | mesmos campos da `agy_gemini_flash` de hoje |
| `tier_3` | `claude` | `sonnet` | sim | 4.00 | `eligible_classes: [ESCALATE]`, `max_retries: 1`, mesmos campos da `claude_sonnet` de hoje |
| `tier_3b` | `claude` | `opus` | **não** | 8.00 | `eligible_classes: [ESCALATE]`, `max_retries: 1`, `best_for: [architectural_recovery, systemic_regressions]` |

- Substituição dos nomes antigos em **todo** arquivo versionado: `copilot_luna` → `tier_1b` (a via que rodava o Luna pelo Copilot), `codex_luna` → `tier_1c`, `agy_gemini_flash` → `tier_2`, `claude_sonnet` → `tier_3`. A via `tier_1` é nova (Haiku). Testes que dependem de "a primeira via" passam a esperar `tier_1`; testes que usam um nome só como rótulo de um dublê podem usar `tier_1b` ou `tier_1`, a critério do worker, mantendo o que cada teste verifica.
- Os templates de `meister config init` (`en_commands.py`, `pt_br_commands.py`) têm que ficar idênticos ao `default_config.yaml` (há teste de comparação).
- `docs/models-and-costs.md` e `docs/pt-BR/MODELOS_E_CUSTOS.md`: tabela de modelos ligada às vias (`test_model_table_matches_catalog`): incluir a linha do Haiku 5.5 (preços 0,10/0,50 e 0,50/2,50, combinado 0,20 a 1,00, fonte OpenRouter conferida em 2026-10-08) e a do Opus; as linhas de **registro histórico** de runs passados (com nomes antigos) permanecem como foram, com uma nota "nomes anteriores às vias neutras".
- Documentação: `docs/advanced.md` e `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md` ganham as seções "Lane names", "Per-lane effort" e "Enabling and disabling lanes" (e o equivalente em português), com exemplos de `effort:`, `workers.enabled` e `meister models --enable/--disable`. O `README` mostra a tabela de `meister models` com os nomes novos.
- CHANGELOG (os dois idiomas, seção `[Unreleased]`): mudança **incompatível** de nomes das vias (tabela de/para), e os recursos novos.

- [ ] **Step 1: Escrever/atualizar os testes que falham** (o catálogo padrão lista as 6 vias na ordem acima, só `tier_1c` e `tier_3b` desligadas; a primeira via ativa é `tier_1` com `claude-haiku-5.5`; `eligible_classes` da `tier_3`; o template de `config init` igual ao padrão; um teste novo que varre os arquivos versionados e falha se algum cita um nome antigo fora das exceções do Review Focus 6).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar** com busca e troca controlada (`git grep` dos quatro nomes antigos) e conferir cada ocorrência de modelo ao lado do nome novo (o `model` da `tier_1` é `claude-haiku-5.5`, o da `tier_1b` é `gpt-6-luna`).
- [ ] **Step 4: Rodar a suíte completa, `ruff check .`, `mypy meister`, `tests/test_no_hardcoded_models.py`, `tests/test_docs_links.py`, `tests/test_hygiene.py`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** Task 2

---

## Verificações do orquestrador (fora das tarefas dos workers)

1. **Antes de executar o plano — linha de base do Jev:** com a `main` atual, rodar `meister classify --context "<texto>"` para 12 pedidos representativos (4 pequenos, 4 médios, 2 altos, 2 de escalonamento) e guardar `classification`, `recommended_implementer` e a confiança em `scratchpad/jev_before.json`.
2. **Depois da Tarefa 3 — comparação:** repetir os mesmos 12 pedidos com a configuração nova e comparar com a linha de base. Critério: a classe (`SMALL/MEDIUM/HIGH/ESCALATE`) não pode mudar por causa dos nomes; a via recomendada precisa corresponder à de antes pelo papel (antiga `copilot_luna` ↔ `tier_1` ou `tier_1b`, `agy_gemini_flash` ↔ `tier_2`, `claude_sonnet` ↔ `tier_3`). Divergências são relatadas ao dono com os textos dos pedidos; não há ajuste silencioso de prompt.
3. **Haiku pelo Copilot de verdade:** uma chamada mínima `copilot --model claude-haiku-5.5 --reasoning-effort low -p "reply ok"` confirma que modelo e flag funcionam.
4. **Ponta a ponta:** `meister models --disable tier_1b` e `--enable tier_1b` num diretório temporário com `meister.config.yaml` real, conferindo a lista e o `.bak`.
5. **Limpeza:** nenhum worktree, ref ou aba do Herdr deixado para trás; `meister clean --apply --archive-and-delete`.
