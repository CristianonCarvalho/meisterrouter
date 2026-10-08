# Vias neutras (`tier_N`), esforço por via e liga/desliga de vias Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import --format superpowers`). Os workers não commitam nem fazem push; o orquestrador verifica e só commita com OK do usuário. Onde o template diz "Commit", este plano usa **Checkpoint**.
>
> **Só executar depois que o PR 1 do plano `2026-10-08-timeline-web-janela-app.md` estiver mesclado:** os dois planos mexem em `meister/cli.py` e nos catálogos `meister/locales/*_cli.py`.

**Goal:** (1) Os nomes das vias deixam de dizer modelo ou harness e passam a dizer só o degrau e a variante (`tier_1`, `tier_1b`, `tier_1c`, `tier_2`, `tier_3`, `tier_3b`); modelo e harness ficam só na configuração. (2) Cada via ganha um `effort` opcional, repassado ao harness. (3) Dá para ligar e desligar vias por comando, sem editar a lista inteira. (4) A via 1 passa a rodar o Haiku 5.5 pelo Copilot.

**Architecture:** Três mudanças pequenas e independentes no que já existe, divididas em tarefas pequenas (política de dividir ao máximo: menos arquivos e menos tokens por pedido). `WorkerTier` ganha o campo `effort`, validado por harness e traduzido em flag por `build_harness_command`. `workers.enabled` (mapa `nome: bool`) é aplicado depois de ler `tier_order`, movendo vias entre ligadas e desligadas sem mudar a ordem; `meister models --enable/--disable` grava esse mapa no `meister.config.yaml` do projeto. A renomeação é a última e é mecânica (catálogo padrão, exemplos, templates de `config init`, docs, testes).

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

1. `cost_per_m_tokens` da `tier_1`: o preço do Haiku 5.5 tem duas faixas (0,10/0,50 por 1M tokens até 100 mil tokens por pedido e 0,50/2,50 acima disso; combinado 3:1 = 0,20 e 1,00). Medindo as sessões do Copilot, cerca de 5% delas têm contexto médio acima de 100 mil e concentram 39% dos tokens de entrada. `tests/test_hygiene.py::test_model_table_matches_catalog` exige custo = (3·entrada + saída)/4 de uma linha da tabela, então o catálogo só aceita 0,20 (faixa até 100 mil) ou 1,00 (faixa acima). Este plano assume **0,20**, com comentário e nota na tabela sobre a faixa de 1,00; alternativa: 1,00 (conservador).
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

1. **`effort` inválido para o harness da via** (por exemplo `max` num harness que não aceita): erro de configuração claro, apontando a via, o harness e os valores aceitos; nunca repassar um valor que o CLI rejeitaria. (Tarefas 1 e 2)
2. **`effort` ausente:** o argv de cada harness fica byte a byte igual ao de hoje. (Tarefa 2)
3. **Desligar a última via ligada**, ligar ou desligar um nome que não existe, e ligar duas vezes: mensagens claras, nada é gravado em caso de erro. (Tarefas 5 e 6)
4. **`meister.config.yaml` existente com comentários e outras chaves:** o comando preserva as outras chaves e cria um `.bak` antes de regravar; arquivo ausente é criado só com o mapa. (Tarefa 5)
5. **`workers.enabled` com via desconhecida** (nome digitado errado à mão): aviso de configuração apontando o nome, sem derrubar o carregamento. (Tarefa 4)
6. **Resíduo dos nomes antigos:** nenhum arquivo versionado (código, testes, exemplos, docs, README, `CLAUDE.md`) cita `copilot_luna`, `codex_luna`, `agy_gemini_flash` ou `claude_sonnet` como nome de via, exceto o CHANGELOG e os registros históricos de run em `docs/models-and-costs.md`. (Tarefas 7 a 15)
7. **Ordem e fallback:** depois da renomeação, a cadeia de fallback e o `eligible_classes: [ESCALATE]` da `tier_3` se comportam como hoje. (Tarefa 7)

---

## Mapa de arquivos

| Arquivo | Ação | Tarefa |
|---|---|---|
| `meister/harness_effort.py` | criar | 1 |
| `meister/config.py` | modificar | 1, 4 |
| `meister/worker.py` | modificar | 2 |
| `meister/lane_toggle.py` | criar | 5 |
| `meister/cli.py` | modificar | 3, 6 |
| `meister/default_config.yaml`, `meister.config.example.yaml` | modificar | 3, 7 |
| `meister/locales/en_tiers.py`, `pt_br_tiers.py` | criar/modificar | 1, 4, 6 |
| `meister/locales/en_commands.py`, `pt_br_commands.py` | modificar | 7 |
| `docs/modelos_e_custos.csv` | modificar | 7 |
| `tests/` (novos: `test_lane_effort_config.py`, `test_lane_effort_argv.py`, `test_lane_effort_show.py`, `test_lane_enabled_config.py`, `test_lane_toggle_file.py`, `test_cli_models_toggle.py`, `test_no_legacy_lane_names.py`) | criar | 1 a 6, 12 |
| `tests/` existentes (renomeação) | modificar | 7, 8, 9, 10 |
| `README.md`, `README.pt-BR.md`, `CLAUDE.md` | modificar | 11 |
| `docs/advanced.md`, `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md` | modificar | 12 |
| `docs/execution-manual.md`, `docs/pt-BR/MANUAL_DE_EXECUCAO.md` | modificar | 13 |
| `docs/models-and-costs.md`, `docs/pt-BR/MODELOS_E_CUSTOS.md` | modificar | 14 |
| `CHANGELOG.md`, `CHANGELOG.pt-BR.md` | modificar | 15 |

**Paralelismo esperado:** as Tarefas 2, 3 e 4 só dependem da 1 e não compartilham arquivos de código entre si; as Tarefas 8 a 14 só dependem da 7 e tocam arquivos disjuntos. O verificador de acoplamento do lançador acusa só citações de caminho entre tarefas de documentação (links de um documento para outro, sem arquivo em comum); executar com `MEISTER_PLAN_ALLOW_COUPLING=1` depois de conferir que são só links.

---

### Task 1: `effort` na configuração

**Files:**
- Create: `meister/harness_effort.py`
- Modify: `meister/config.py`
- Create: `meister/locales/en_tiers.py`
- Create: `meister/locales/pt_br_tiers.py`
- Test: `tests/test_lane_effort_config.py`

**Interfaces:**
- Produces em `meister/harness_effort.py`: `HARNESS_EFFORT_VALUES: Dict[str, Tuple[str, ...]]` (valores aceitos por **harness**, nunca por modelo). Confira cada lista no `--help` do CLI instalado (`copilot --help`, `claude --help`, `agy --help`, `codex --help`) e relate o que conferiu; o ponto de partida é: Copilot `none, minimal, low, medium, high, xhigh, max`; Claude `low, medium, high, xhigh, max`; `agy` `low, medium, high, xhigh, max`; Codex `minimal, low, medium, high, xhigh`. O que o CLI não confirmar fica fora da tabela.
- Produces em `config.py`: `WorkerTier.effort: Optional[str] = None`, lido de `workers.tier_order[i].effort` (minúsculas; vazio vira `None`); em `validate_config`, `effort` definido precisa estar entre os valores do harness da via, senão `ConfigIssue("error", f"workers.tier_order[{i}].effort", ...)` citando via, harness e valores aceitos; harness sem tabela não aceita `effort`.
- Textos de erro em `meister/locales/en_tiers.py` e `pt_br_tiers.py` (chaves `tiers.*`, mesmos campos nos dois idiomas, inglês sem letras acentuadas).

- [ ] **Step 1: Escrever os testes que falham.** `effort` válido por harness é aceito; valor fora da lista gera erro apontando a via; harness sem tabela com `effort` gera erro; vazio ou ausente vira `None`.
- [ ] **Step 2: Rodar e ver falhar** (`pytest tests/test_lane_effort_config.py -q`).
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`, `tests/test_no_hardcoded_models.py`, `tests/test_locales_parity.py`.**
- [ ] **Step 5: Checkpoint** (sem commit).

**Depends on:** none

### Task 2: `effort` no comando do harness

**Files:**
- Modify: `meister/worker.py`
- Test: `tests/test_lane_effort_argv.py`

**Interfaces:**
- Consumes: `WorkerTier.effort` e `HARNESS_EFFORT_VALUES` (Tarefa 1).
- Produces: `build_harness_command(harness, cli_bin, model, prompt, cwd, effort: Optional[str] = None)`; com `effort`, acrescenta a flag do harness (Copilot `--reasoning-effort V`; Claude `--effort V`; `agy` `--effort V`; Codex `-c model_reasoning_effort="V"` antes do `-C`/prompt); sem `effort`, o argv é **idêntico** ao atual. O `effort` da via chega ao `build_harness_command` pelo mesmo caminho que hoje entrega o `model` (via resolvida pelo nome, incluindo `--model <via>` e a execução pelo orquestrador).

- [ ] **Step 1: Escrever os testes que falham.** Para cada harness: argv com `effort` (lista literal esperada) e argv sem `effort` igual ao de antes; ponta a ponta com binário falso que grava o argv: uma via com `effort: high` chama o binário com a flag; uma via sem `effort` não.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`, `tests/test_no_hardcoded_models.py`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** Task 1

### Task 3: `effort` visível (`config show`) e documentado nos YAML

**Files:**
- Modify: `meister/cli.py`
- Modify: `meister/default_config.yaml`
- Modify: `meister.config.example.yaml`
- Test: `tests/test_lane_effort_show.py`

**Interfaces:**
- Consumes: `WorkerTier.effort` (Tarefa 1).
- Produces: `meister config show` e seu JSON mostram `effort` por via (`-` ou `null` quando ausente); comentário curto nos dois YAML explicando `effort` (ausente = padrão do harness) e os valores por harness. **Nenhuma via recebe `effort` por padrão.**

- [ ] **Step 1: Escrever os testes que falham** (texto e JSON de `config show` com e sem `effort`; o catálogo padrão continua sem `effort` em nenhuma via).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar** (`cli.py` fino).
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`, `tests/test_no_hardcoded_models.py`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** Task 1

### Task 4: `workers.enabled` na configuração

**Files:**
- Modify: `meister/config.py`
- Modify: `meister/locales/en_tiers.py`
- Modify: `meister/locales/pt_br_tiers.py`
- Test: `tests/test_lane_enabled_config.py`

**Interfaces:**
- Produces em `config.py`: chave `workers.enabled` (mapa `nome_da_via: bool`), aplicada **depois** de ler `tier_order`: `true` move a via para as ligadas e `false` para as desligadas, **preservando a ordem original de `tier_order`**; nome desconhecido gera `ConfigIssue("warning", "workers.enabled.<nome>", ...)` e é ignorado; valor não booleano é erro; resultado sem nenhuma via ligada gera `ConfigIssue("error", "workers.enabled", ...)`.

- [ ] **Step 1: Escrever os testes que falham** (liga uma desligada e desliga uma ligada mantendo a ordem; nome desconhecido gera aviso; não booleano é erro; todas desligadas é erro; sem a chave nada muda).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`, `tests/test_locales_parity.py`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** Task 1

### Task 5: Gravação segura de `workers.enabled` (`lane_toggle.py`)

**Files:**
- Create: `meister/lane_toggle.py`
- Test: `tests/test_lane_toggle_file.py`

**Interfaces:**
- Consumes: `workers.enabled` (Tarefa 4).
- Produces: `apply_toggle(config_path, catalog_names, enabled_now, enable, disable) -> Dict[str, bool]`: valida nomes contra `catalog_names`, recusa desligar a última via ligada, grava só `workers.enabled` no arquivo (`yaml.safe_load`, demais chaves preservadas), com cópia `<arquivo>.bak` antes e gravação atômica (temporário + `os.replace`); arquivo ausente é criado só com o mapa. Qualquer erro levanta `LaneToggleError` com mensagem clara e **não altera nada**. Idempotente.

- [ ] **Step 1: Escrever os testes que falham** (desligar grava o mapa; ligar de novo; idempotência; desligar a última falha sem gravar; nome inexistente falha e lista os válidos; arquivo existente com outras chaves preserva-as e cria `.bak`; arquivo ausente é criado; falha no meio não deixa arquivo pela metade).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar** (módulo puro, sem `click`).
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** Task 4

### Task 6: `meister models --enable/--disable` e coluna de esforço

**Files:**
- Modify: `meister/cli.py`
- Modify: `meister/locales/en_tiers.py`
- Modify: `meister/locales/pt_br_tiers.py`
- Test: `tests/test_cli_models_toggle.py`

**Interfaces:**
- Consumes: `apply_toggle` (Tarefa 5) e `WorkerTier.effort` (Tarefa 1).
- Produces: `meister models [--enable NOME]... [--disable NOME]... [-c ARQUIVO]`; sem as opções, a saída atual não muda, exceto a coluna nova de `effort` (`-` quando ausente). Com elas, chama `apply_toggle` sobre o `meister.config.yaml` do projeto (ou o de `-c`), imprime a lista final e sai com código diferente de zero, sem alterar nada, em qualquer erro.

- [ ] **Step 1: Escrever os testes que falham** (`CliRunner`: `--disable` grava e a saída reflete; `--enable` idempotente; desligar a última falha com código ≠ 0 e arquivo intacto; nome inexistente lista os válidos; coluna de esforço presente).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar** (`cli.py` fino).
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`, `tests/test_no_hardcoded_models.py`, `tests/test_locales_parity.py`, `tests/test_i18n_ratchet.py`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** Task 5, Task 3

### Task 7: Catálogo com as vias neutras (e o que depende dele)

**Files:**
- Modify: `meister/default_config.yaml`
- Modify: `meister.config.example.yaml`
- Modify: `meister/locales/en_commands.py`
- Modify: `meister/locales/pt_br_commands.py`
- Modify: `docs/modelos_e_custos.csv`
- Modify: `tests/`

**Interfaces:**
- Consumes: `effort` e `workers.enabled` (Tarefas 1 a 6; o catálogo não os usa por padrão).
- Produces: o catálogo padrão com as seis vias abaixo, **nesta ordem em `tier_order`**:

| Nome | harness | model | enabled | custo/M | outros campos |
|---|---|---|---|---|---|
| `tier_1` | `copilot` | `claude-haiku-5.5` | sim | 0.20 (Decisão aberta 1; comentário com as duas faixas) | `credit_usd: 0.01`, `max_retries: 2`, `best_for` da via 1 de hoje |
| `tier_1b` | `copilot` | `gpt-6-luna` | sim | 0.20 | `credit_usd: 0.01`, `max_retries: 2`, `best_for` da via 1 de hoje |
| `tier_1c` | `codex` | `gpt-6-luna` | **não** | 0.20 | campos da `codex_luna` de hoje |
| `tier_2` | `agy` | `gemini-3.8-flash-high` | sim | 1.50 | campos da `agy_gemini_flash` de hoje |
| `tier_3` | `claude` | `sonnet` | sim | 4.00 | `eligible_classes: [ESCALATE]`, `max_retries: 1`, campos da `claude_sonnet` de hoje |
| `tier_3b` | `claude` | `opus` | **não** | 8.00 | `eligible_classes: [ESCALATE]`, `max_retries: 1`, `best_for: [architectural_recovery, systemic_regressions]` |

- Correspondência dos nomes antigos: `copilot_luna` → `tier_1b`, `codex_luna` → `tier_1c`, `agy_gemini_flash` → `tier_2`, `claude_sonnet` → `tier_3`; `tier_1` e `tier_3b` são novas.
- Os templates de `meister config init` (`en_commands.py`, `pt_br_commands.py`) ficam idênticos ao `default_config.yaml` (há teste de comparação).
- `docs/modelos_e_custos.csv`: `test_model_table_matches_catalog` exige uma linha por via com custo igual a `(3*entrada + saída)/4`; incluir a linha do Haiku 5.5 (0,10/0,50, combinado 0,20; mencionar a faixa acima de 100 mil na coluna de observação) e a do Opus, e trocar a coluna `vias_meister` pelos nomes novos.
- `tests/`: **somente** os testes que a suíte mostrar dependentes do catálogo padrão (os que leem a lista de vias, a primeira via, `config init`); os testes que só usam o nome antigo como rótulo ficam para as Tarefas 8 a 10. Testes que esperam "a primeira via ativa" passam a esperar `tier_1` com `claude-haiku-5.5`.

- [ ] **Step 1: Atualizar os testes que dependem do catálogo** (6 vias na ordem acima; só `tier_1c` e `tier_3b` desligadas; `eligible_classes` da `tier_3`; `config init` igual ao padrão).
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Suíte inteira, `ruff check .`, `mypy meister`, `tests/test_no_hardcoded_models.py`, `tests/test_hygiene.py`.**
- [ ] **Step 5: Checkpoint.**

**Depends on:** Task 6

### Task 8: Renomear vias nos testes de timeline e relatórios

**Files:**
- Modify: `tests/test_timeline.py`
- Modify: `tests/test_timeline_view.py`
- Modify: `tests/timeline_fixtures.py`
- Modify: `tests/test_dashboard_metrics.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Troca mecânica dos nomes antigos pelos novos (tabela da Tarefa 7). Nesses testes o nome é só rótulo; manter o que cada teste verifica. Arquivo que já estiver sem nome antigo não muda.

- [ ] **Step 1: Trocar os nomes** nesses cinco arquivos (`git grep` dos quatro nomes antigos, arquivo a arquivo).
- [ ] **Step 2: Suíte inteira, `ruff check .`.**
- [ ] **Step 3: Checkpoint.**

**Depends on:** Task 7

### Task 9: Renomear vias nos testes do bridge, dos tabs e do planejamento

**Files:**
- Modify: `tests/test_herdr_bridge.py`
- Modify: `tests/test_herdr_tabs.py`
- Modify: `tests/test_cli_herdr.py`
- Modify: `tests/test_plan.py`
- Modify: `tests/test_task_runner.py`
- Modify: `tests/test_setup_cmd.py`
- Modify: `tests/test_jev.py`

**Interfaces:**
- Mesma troca mecânica da Tarefa 8 (tabela da Tarefa 7), nesses sete arquivos.

- [ ] **Step 1: Trocar os nomes.**
- [ ] **Step 2: Suíte inteira, `ruff check .`.**
- [ ] **Step 3: Checkpoint.**

**Depends on:** Task 7

### Task 10: Renomear vias nos scripts e na documentação do E2E

**Files:**
- Modify: `tests/e2e/lib.sh`
- Modify: `tests/e2e/run_flow.sh`
- Modify: `tests/e2e/README.md`

**Interfaces:**
- Mesma troca mecânica (tabela da Tarefa 7); os scripts E2E não rodam na suíte, então conferir só a sintaxe com `bash -n`.

- [ ] **Step 1: Trocar os nomes e rodar `bash -n` nos dois scripts.**
- [ ] **Step 2: Suíte inteira.**
- [ ] **Step 3: Checkpoint.**

**Depends on:** Task 7

### Task 11: Nomes novos no README e no `CLAUDE.md`

**Files:**
- Modify: `README.md`
- Modify: `README.pt-BR.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Troca dos nomes antigos pelos novos (tabela da Tarefa 7); a tabela de exemplo de `meister models` do README mostra as seis vias e a coluna de esforço; o exemplo de `meister worker --model` do `CLAUDE.md` usa `tier_1`.

- [ ] **Step 1: Atualizar os três arquivos** (`tests/test_docs_links.py` e `tests/test_hygiene.py` seguem verdes).
- [ ] **Step 2: Checkpoint.**

**Depends on:** Task 7

### Task 12: Documentar nomes, esforço e liga/desliga (comandos avançados)

**Files:**
- Modify: `docs/advanced.md`
- Modify: `docs/pt-BR/INSTALACAO_E_COMANDOS_AVANCADOS.md`

**Interfaces:**
- Troca dos nomes antigos pelos novos e três seções novas nos dois idiomas, atualizados juntos: "Lane names" (convenção `tier_N` e `tier_Nb`), "Per-lane effort" (campo, valores por harness, ausente = padrão) e "Enabling and disabling lanes" (`workers.enabled`, `meister models --enable/--disable`, `.bak`, vale no próximo run).

- [ ] **Step 1: Escrever as seções e conferir `tests/test_docs_links.py`.**
- [ ] **Step 2: Checkpoint.**

**Depends on:** Task 7

### Task 13: Nomes novos no manual de execução

**Files:**
- Modify: `docs/execution-manual.md`
- Modify: `docs/pt-BR/MANUAL_DE_EXECUCAO.md`

**Interfaces:**
- Troca dos nomes antigos pelos novos (tabela da Tarefa 7) nos dois idiomas, mantendo a estrutura.

- [ ] **Step 1: Trocar os nomes e conferir `tests/test_docs_links.py`.**
- [ ] **Step 2: Checkpoint.**

**Depends on:** Task 7

### Task 14: Modelos e custos com as vias novas

**Files:**
- Modify: `docs/models-and-costs.md`
- Modify: `docs/pt-BR/MODELOS_E_CUSTOS.md`

**Interfaces:**
- Troca dos nomes antigos pelos novos nas tabelas atuais; linha do Haiku 5.5 (0,10/0,50 até 100 mil tokens por pedido e 0,50/2,50 acima; combinado 0,20 a 1,00; fonte OpenRouter conferida em 2026-10-08) e a do Opus; nota sobre a faixa de 100 mil na seção de custos. As linhas de **registro histórico** de runs passados (com nomes antigos) permanecem, com a nota "nomes anteriores às vias neutras". As tabelas ficam consistentes com `docs/modelos_e_custos.csv` da Tarefa 7.

- [ ] **Step 1: Atualizar os dois idiomas juntos; `tests/test_hygiene.py` e `tests/test_docs_links.py` seguem verdes.**
- [ ] **Step 2: Checkpoint.**

**Depends on:** Task 7

### Task 15: CHANGELOG e varredura dos nomes antigos

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.pt-BR.md`
- Create: `tests/test_no_legacy_lane_names.py`

**Interfaces:**
- CHANGELOG (os dois idiomas, `[Unreleased]`): mudança **incompatível** de nomes (tabela de/para) e os recursos novos (`effort` por via, `workers.enabled`, `meister models --enable/--disable`, Haiku 5.5 na via 1).
- Teste novo: varre os arquivos versionados (`git ls-files`) e falha se algum cita `copilot_luna`, `codex_luna`, `agy_gemini_flash` ou `claude_sonnet`, exceto `CHANGELOG*.md`, `docs/superpowers/**` e os registros históricos marcados em `docs/models-and-costs.md` e `docs/pt-BR/MODELOS_E_CUSTOS.md`.

- [ ] **Step 1: Escrever o teste e ver falhar se faltar algo; corrigir qualquer resíduo.**
- [ ] **Step 2: Suíte inteira, `ruff check .`.**
- [ ] **Step 3: Checkpoint.**

**Depends on:** Task 8, Task 9, Task 10, Task 11, Task 12, Task 13, Task 14

---

## Verificações do orquestrador (fora das tarefas dos workers)

1. **Antes de executar o plano — linha de base do Jev:** com a `main` atual, rodar `meister classify --context "<texto>"` para 12 pedidos representativos (4 pequenos, 4 médios, 2 altos, 2 de escalonamento) e guardar `classification`, `recommended_implementer` e a confiança em `scratchpad/jev_before.json`.
2. **Depois da Tarefa 7 — comparação:** repetir os mesmos 12 pedidos com a configuração nova e comparar com a linha de base. Critério: a classe (`SMALL/MEDIUM/HIGH/ESCALATE`) não pode mudar por causa dos nomes; a via recomendada precisa corresponder à de antes pelo papel (antiga `copilot_luna` ↔ `tier_1` ou `tier_1b`, `agy_gemini_flash` ↔ `tier_2`, `claude_sonnet` ↔ `tier_3`). Divergências são relatadas ao dono com os textos dos pedidos; não há ajuste silencioso de prompt.
3. **Haiku pelo Copilot de verdade:** uma chamada mínima `copilot --model claude-haiku-5.5 --reasoning-effort low -p "reply ok"` confirma que modelo e flag funcionam.
4. **Ponta a ponta:** `meister models --disable tier_1b` e `--enable tier_1b` num diretório temporário com `meister.config.yaml` real, conferindo a lista e o `.bak`.
5. **Limpeza:** nenhum worktree, ref ou aba do Herdr deixado para trás; `meister clean --apply --archive-and-delete`.
